"""Calibración objetiva: un set chico de tareas con respuesta verificable por código, corrido sobre TUS suscripciones.

Nada se corrige con un modelo-juez: el código se ejecuta contra tests, lo numérico se compara con el valor esperado
(calculado con soluciones de referencia) y las consignas de escritura se verifican con reglas medibles.

Para gastar poco, las preguntas se agrupan por categoría en UNA llamada por CLI ("### 1 … ### n" en la respuesta):
el costo fijo del arnés de cada CLI (12-14k tokens de entrada) se paga una vez por categoría y no por pregunta.
Claude corre en modo liviano (sin herramientas ni prompt de sistema; ~430 tokens por llamada).

Resultado en <home>/metrics.json: por modelo, aciertos por categoría, latencia, tokens y el id real del modelo.
"""
from __future__ import annotations

import itertools
import json
import math
import os
import random
import re
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import adapters, state

SECTIONS_RULE = ("Respondé SOLO con secciones numeradas: una línea '### 1', luego tu respuesta a la consigna 1; "
                 "una línea '### 2', y así sucesivamente. Sin texto extra.")
SKIP_ERRORS = ("rate_limited", "auth_required", "not_installed")  # no son un fallo del modelo: no cuentan
PERMISSION_RE = re.compile(r"permission that headless mode cannot prompt|auto-denied|permission.{0,40}denied", re.I)  # el CLI quiso usar una herramienta y no hay quién autorice
CODE_TIMEOUT = 5


# ---------- ítems y lotes ----------

@dataclass
class Item:
    category: str
    check: Callable[[str], bool]
    ref: str = ""  # respuesta correcta de referencia: sirve para autoverificar los correctores y simular un modelo perfecto en los tests


@dataclass
class Batch:
    key: str
    prompt: str
    items: List[Item]
    needs_files: bool = False
    workdir: Optional[str] = None  # carpeta con los archivos (imágenes) del lote


def sections(text: str) -> Dict[int, str]:
    """{n: contenido} a partir de '### n'. Tolera '## 1', '### 1.', '1)' al inicio de línea con almohadilla."""
    parts = re.split(r"(?m)^\s*#{1,6}\s*(\d+)\s*[.:)\-]?\s*$", text or "")
    return {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def first_number(text: str) -> Optional[float]:
    t = re.sub(r"(?<=\d)\.(?=\d{3}(\D|$))", "", text or "")  # 1.234 -> 1234
    m = re.search(r"-?\d+(?:[.,]\d+)?", t)
    return float(m.group(0).replace(",", ".")) if m else None


def numbers(text: str) -> List[float]:
    t = re.sub(r"(?<=\d)\.(?=\d{3}(\D|$))", "", text or "")
    return [float(x.replace(",", ".")) for x in re.findall(r"-?\d+(?:[.,]\d+)?", t)]


def num_check(expected: float, tol: float = 0.01) -> Callable[[str], bool]:
    """Acierta si el primer o el último número de la respuesta es el esperado ('1184' o '25×34+334 = 1184')."""
    def check(ans: str) -> bool:
        nums = numbers(ans)
        return bool(nums) and (abs(nums[0] - expected) <= tol or abs(nums[-1] - expected) <= tol)
    return check


def word_check(expected: str, others: List[str] = ()) -> Callable[[str], bool]:
    norm = lambda s: "".join(c for c in unicodedata.normalize("NFD", s.lower()) if not unicodedata.combining(c))
    def check(ans: str) -> bool:
        a = norm(ans)
        return bool(re.search(rf"\b{re.escape(norm(expected))}\b", a)) and not any(re.search(rf"\b{re.escape(norm(o))}\b", a) for o in others)
    return check


# ---------- código: se ejecuta contra tests ----------

def extract_code(ans: str) -> str:
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", ans or "", re.S)
    return (m.group(1) if m else ans or "").strip()


_HARNESS = '''
import json, sys
{code}

_ok = True
for _args, _expected in json.loads(sys.argv[1]):
    try:
        _got = json.loads(json.dumps({fname}(*_args)))
    except Exception:
        _ok = False
        break
    if _got != _expected:
        _ok = False
        break
print("OK" if _ok else "FAIL")
'''


def run_tests(code: str, fname: str, cases: List[list]) -> bool:
    """Ejecuta el código del modelo contra los casos [(args, esperado)] en un proceso aparte, con timeout.
    Es código de TUS propios CLIs sobre tareas triviales; aun así corre aislado (-I), sin stdin y en una carpeta temporal."""
    if not code:
        return False
    with tempfile.TemporaryDirectory(prefix="ia-router-code-") as d:
        f = Path(d) / "solucion.py"
        f.write_text(_HARNESS.format(code=code, fname=fname), encoding="utf-8")
        try:
            p = subprocess.run([sys.executable, "-I", str(f), json.dumps(cases)], cwd=d, capture_output=True, text=True,
                               timeout=CODE_TIMEOUT, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return False
    return p.returncode == 0 and p.stdout.strip().endswith("OK")


def _expected(ref: str, fname: str, arg_cases: List[list]) -> List[list]:
    ns: Dict = {}
    exec(ref, ns)  # solución de referencia propia del paquete, no del modelo
    return [[a, json.loads(json.dumps(ns[fname](*a)))] for a in arg_cases]


# Cada problema trae su solución de referencia: de ahí salen los resultados esperados (nada escrito a mano) y los tests
# del propio corrector. Hay problemas fáciles y difíciles en el mismo lote para que la medición no se sature en 100%.

# nombre, firma, consigna, casos de entrada, solución de referencia
CODING_EASY = [
    ("es_palindromo", "es_palindromo(s)", "devuelve True si el texto es un palíndromo, ignorando mayúsculas y todo lo que no sea letra o número",
     [["Anita lava la tina"], ["Hola"], ["A man, a plan, a canal: Panama"], [""], ["ab"]],
     "def es_palindromo(s):\n    t = [c.lower() for c in s if c.isalnum()]\n    return t == t[::-1]"),
    ("fizzbuzz", "fizzbuzz(n)", "devuelve la lista de strings del 1 al n: 'FizzBuzz' si es múltiplo de 15, 'Fizz' si lo es de 3, 'Buzz' si lo es de 5, y si no el número como string",
     [[1], [5], [15], [0]],
     "def fizzbuzz(n):\n    return ['FizzBuzz' if i % 15 == 0 else 'Fizz' if i % 3 == 0 else 'Buzz' if i % 5 == 0 else str(i) for i in range(1, n + 1)]"),
    ("romano_a_entero", "romano_a_entero(s)", "convierte un número romano (I, V, X, L, C, D, M, con notación sustractiva) a entero",
     [["III"], ["IV"], ["IX"], ["LVIII"], ["MCMXCIV"]],
     "def romano_a_entero(s):\n    v = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}\n    t = 0\n    for i, c in enumerate(s):\n        t += -v[c] if i + 1 < len(s) and v[c] < v[s[i + 1]] else v[c]\n    return t"),
    ("trocear", "trocear(lista, n)", "divide la lista en sublistas consecutivas de tamaño n (la última puede ser más corta) y devuelve la lista de sublistas",
     [[[1, 2, 3, 4, 5], 2], [[], 3], [[1, 2, 3], 3], [[1, 2, 3], 5]],
     "def trocear(l, n):\n    return [l[i:i + n] for i in range(0, len(l), n)]"),
]
CODING_HARD = [
    ("lru", "lru(capacidad, operaciones)", "simula una caché LRU de esa capacidad. `operaciones` es una lista de ['put', clave, valor] o ['get', clave]. Devuelve la lista con el resultado de cada 'get' (el valor, o -1 si la clave no está). Un 'put' sobre una clave existente actualiza su valor y la marca como la más recientemente usada; un 'get' exitoso también la marca como reciente; si al insertar se supera la capacidad se elimina la menos recientemente usada",
     [[2, [["put", "a", 1], ["put", "b", 2], ["get", "a"], ["put", "c", 3], ["get", "b"], ["get", "c"], ["get", "a"]]],
      [1, [["put", "x", 1], ["put", "y", 2], ["get", "x"], ["get", "y"]]],
      [2, [["put", "a", 1], ["put", "b", 2], ["put", "a", 9], ["put", "c", 3], ["get", "a"], ["get", "b"]]],
      [3, []]],
     "from collections import OrderedDict\ndef lru(cap, ops):\n    d, out = OrderedDict(), []\n    for op in ops:\n        if op[0] == 'put':\n            d[op[1]] = op[2]\n            d.move_to_end(op[1])\n            if len(d) > cap:\n                d.popitem(last=False)\n        else:\n            if op[1] in d:\n                d.move_to_end(op[1])\n                out.append(d[op[1]])\n            else:\n                out.append(-1)\n    return out"),
    ("camino_minimo", "camino_minimo(grafo, origen, destino)", "recibe un grafo dirigido como dict {nodo: [[vecino, peso], ...]} con pesos positivos (los nodos son strings) y devuelve la distancia mínima de origen a destino, o -1 si no hay camino",
     [[{"a": [["b", 7], ["c", 9], ["f", 14]], "b": [["c", 10], ["d", 15]], "c": [["d", 11], ["f", 2]], "d": [["e", 6]], "f": [["e", 9]], "e": []}, "a", "e"],
      [{"a": [["b", 1]], "b": [], "c": [["a", 1]]}, "a", "c"], [{"a": []}, "a", "a"], [{"a": [["b", 5], ["c", 1]], "c": [["b", 1]], "b": []}, "a", "b"]],
     "import heapq\ndef camino_minimo(g, o, d):\n    dist, h = {o: 0}, [(0, o)]\n    while h:\n        c, u = heapq.heappop(h)\n        if u == d:\n            return c\n        if c > dist.get(u, 1e18):\n            continue\n        for v, w in g.get(u, []):\n            if c + w < dist.get(v, 1e18):\n                dist[v] = c + w\n                heapq.heappush(h, (c + w, v))\n    return -1"),
    ("calcular", "calcular(expresion)", "evalúa una expresión aritmética con números enteros, + - * /, paréntesis y espacios, respetando la precedencia y el menos unario (por ejemplo '-(2+3)*4'), y devuelve el resultado como número (la división es la normal, no entera)",
     [["2+3*4"], ["(1+2)*(3+4)"], ["-(2+3)*4"], ["10/4"], ["2*(3+(4-1))*2"], [" 7 - 2 - 1 "], ["8/2/2"]],
     "import re\ndef calcular(e):\n    t = re.findall(r'\\d+|[-+*/()]', e)\n    i = [0]\n    def peek():\n        return t[i[0]] if i[0] < len(t) else None\n    def nxt():\n        i[0] += 1\n        return t[i[0] - 1]\n    def expr():\n        v = term()\n        while peek() in ('+', '-'):\n            v = v + term() if nxt() == '+' else v - term()\n        return v\n    def term():\n        v = unary()\n        while peek() in ('*', '/'):\n            v = v * unary() if nxt() == '*' else v / unary()\n        return v\n    def unary():\n        if peek() == '-':\n            nxt()\n            return -unary()\n        if peek() == '(':\n            nxt()\n            v = expr()\n            nxt()\n            return v\n        return int(nxt())\n    return expr()"),
    ("n_reinas", "n_reinas(n)", "devuelve la cantidad de formas distintas de ubicar n reinas en un tablero de n×n sin que se ataquen entre sí",
     [[1], [4], [5], [6], [8]],
     "def n_reinas(n):\n    def go(r, cols, d1, d2):\n        if r == n:\n            return 1\n        return sum(go(r + 1, cols | {c}, d1 | {r - c}, d2 | {r + c}) for c in range(n) if c not in cols and r - c not in d1 and r + c not in d2)\n    return go(0, frozenset(), frozenset(), frozenset())"),
    ("particiones", "particiones(n)", "devuelve de cuántas formas se puede escribir n como suma de enteros positivos sin importar el orden (por ejemplo 4 = 4 = 3+1 = 2+2 = 2+1+1 = 1+1+1+1, son 5 formas)",
     [[1], [4], [7], [20], [0]],
     "def particiones(n):\n    dp = [1] + [0] * n\n    for k in range(1, n + 1):\n        for s in range(k, n + 1):\n            dp[s] += dp[s - k]\n    return dp[n]"),
    ("distancia_edicion", "distancia_edicion(a, b)", "devuelve la distancia de Levenshtein entre los dos textos (mínimo de inserciones, borrados y sustituciones de un carácter para convertir a en b)",
     [["kitten", "sitting"], ["", "abc"], ["flaw", "lawn"], ["igual", "igual"], ["intention", "execution"]],
     "def distancia_edicion(a, b):\n    p = list(range(len(b) + 1))\n    for i, x in enumerate(a, 1):\n        c = [i]\n        for j, y in enumerate(b, 1):\n            c.append(min(p[j] + 1, c[j - 1] + 1, p[j - 1] + (x != y)))\n        p = c\n    return p[-1]"),
    ("subcadena_unica", "subcadena_unica(s)", "devuelve el largo de la subcadena contigua más larga que no repite ningún carácter",
     [["abcabcbb"], ["bbbbb"], ["pwwkew"], [""], ["dvdf"], ["abba"]],
     "def subcadena_unica(s):\n    last, best, lo = {}, 0, 0\n    for i, c in enumerate(s):\n        if c in last and last[c] >= lo:\n            lo = last[c] + 1\n        last[c] = i\n        best = max(best, i - lo + 1)\n    return best"),
]
# nombre, firma, comportamiento esperado, código con bug, casos, referencia corregida
DEBUG_EASY = [
    ("promedio", "promedio(nums)", "devuelve el promedio de la lista, o 0 si está vacía", "def promedio(nums):\n    return sum(nums) / len(nums)",
     [[[1, 2, 3]], [[]], [[10]]], "def promedio(nums):\n    return sum(nums) / len(nums) if nums else 0"),
    ("es_bisiesto", "es_bisiesto(anio)", "devuelve True si el año es bisiesto según el calendario gregoriano", "def es_bisiesto(anio):\n    return anio % 4 == 0 and anio % 100 != 0",
     [[2000], [1900], [2024], [2023], [2100]], "def es_bisiesto(anio):\n    return anio % 4 == 0 and (anio % 100 != 0 or anio % 400 == 0)"),
    ("contar_vocales", "contar_vocales(s)", "cuenta las vocales a, e, i, o, u del texto, en minúscula o mayúscula (sin acentos)",
     "def contar_vocales(s):\n    return sum(1 for c in s if c in 'aeiou')",
     [["Hola Mundo"], ["AEIOU"], [""], ["xyz"]], "def contar_vocales(s):\n    return sum(1 for c in s if c.lower() in 'aeiou')"),
]
DEBUG_HARD = [
    ("busqueda_binaria", "busqueda_binaria(lista, x)", "recibe una lista ordenada de menor a mayor y devuelve el índice de x, o -1 si no está",
     "def busqueda_binaria(lista, x):\n    lo, hi = 0, len(lista) - 1\n    while lo < hi:\n        mid = (lo + hi) // 2\n        if lista[mid] == x:\n            return mid\n        if lista[mid] < x:\n            lo = mid + 1\n        else:\n            hi = mid - 1\n    return -1",
     [[[1, 3, 5, 7], 7], [[1], 1], [[1, 3], 1], [[], 1], [[1, 3, 5], 4], [[2, 4, 6, 8, 10], 10]],
     "def busqueda_binaria(lista, x):\n    lo, hi = 0, len(lista) - 1\n    while lo <= hi:\n        mid = (lo + hi) // 2\n        if lista[mid] == x:\n            return mid\n        if lista[mid] < x:\n            lo = mid + 1\n        else:\n            hi = mid - 1\n    return -1"),
    ("mediana", "mediana(nums)", "devuelve la mediana de la lista de números (si la cantidad es par, el promedio de los dos centrales)",
     "def mediana(nums):\n    n = len(nums)\n    return nums[n // 2]",
     [[[3, 1, 2]], [[4, 1, 3, 2]], [[5]], [[10, 2, 38, 23, 38, 23, 21]], [[1, 2]]],
     "def mediana(nums):\n    s = sorted(nums)\n    n = len(s)\n    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2"),
    ("rotar", "rotar(lista, k)", "devuelve la lista rotada k posiciones hacia la derecha (el último elemento pasa al principio); k puede ser 0, mayor que el largo o negativo (rota a la izquierda); la lista vacía devuelve []",
     "def rotar(lista, k):\n    return lista[-k:] + lista[:-k]",
     [[[1, 2, 3, 4, 5], 2], [[1, 2, 3], 0], [[1, 2, 3], 7], [[1, 2, 3, 4], -1], [[], 3], [[1, 2, 3], 3]],
     "def rotar(lista, k):\n    if not lista:\n        return []\n    k %= len(lista)\n    return lista[-k:] + lista[:-k] if k else lista[:]"),
    ("anagramas", "anagramas(palabras)", "agrupa las palabras que son anagramas entre sí; devuelve una lista de grupos, cada grupo ordenado alfabéticamente, y los grupos ordenados por su primera palabra",
     "def anagramas(palabras):\n    g = {}\n    for p in palabras:\n        g.setdefault(''.join(sorted(p)), []).append(p)\n    return list(g.values())",
     [[["roma", "amor", "mora", "sol", "los", "luz"]], [[]], [["a"]], [["ab", "ba", "ab"]]],
     "def anagramas(palabras):\n    g = {}\n    for p in palabras:\n        g.setdefault(''.join(sorted(p)), []).append(p)\n    return sorted(sorted(v) for v in g.values())"),
]


def _code_batch(rng: random.Random, rnd: int) -> Batch:
    coding = [CODING_EASY[rnd % len(CODING_EASY)]] + [CODING_HARD[(rnd * 2 + i) % len(CODING_HARD)] for i in range(2)]
    debug = [DEBUG_EASY[rnd % len(DEBUG_EASY)], DEBUG_HARD[rnd % len(DEBUG_HARD)]]
    parts, items = [SECTIONS_RULE, "Cada respuesta es solo el código Python de la función, en un bloque ```python, sin explicación.", ""], []
    n = 1
    for name, sig, spec, cases, ref in coding:
        parts.append(f"Consigna {n}: escribí la función `{sig}` que {spec}.")
        tests = _expected(ref, name, cases)
        items.append(Item("coding", lambda a, name=name, tests=tests: run_tests(extract_code(a), name, tests), f"```python\n{ref}\n```"))
        n += 1
    for name, sig, spec, buggy, cases, ref in debug:
        parts.append(f"Consigna {n}: la función `{sig}` debe {spec}, pero tiene un bug. Devolvé la función corregida completa (mismo nombre).\n```python\n{buggy}\n```")
        tests = _expected(ref, name, cases)
        items.append(Item("debugging", lambda a, name=name, tests=tests: run_tests(extract_code(a), name, tests), f"```python\n{ref}\n```"))
        n += 1
    return Batch("code", "\n".join(parts), items)


# ---------- matemática y datos ----------

def _math_items(rng: random.Random):
    """Un problema fácil y dos difíciles (varios pasos o cuentas largas: ahí los modelos sin herramientas empiezan a fallar)."""
    a, b, c = rng.randint(12, 49), rng.randint(12, 49), rng.randint(100, 900)
    g, x, y = rng.randint(3, 9), rng.choice([4, 5, 7, 9]), rng.choice([8, 11, 13])
    easy = [
        (f"Calculá {a} × {b} + {c}.", a * b + c),
        (f"¿Cuál es el máximo común divisor de {g * x} y {g * y}?", math.gcd(g * x, g * y)),
    ]
    cap, rate, yrs = rng.choice([1000, 2500, 4000, 12000]), rng.choice([3, 4, 5, 7]), rng.randint(5, 9)
    hi, p, q = rng.randint(300, 900), rng.choice([4, 6, 7, 9]), rng.choice([5, 8, 11, 13])
    base, ex = rng.randint(3, 9), rng.randint(1500, 2999)
    nf = rng.randint(12, 18)
    n, k = rng.randint(20, 30), rng.randint(5, 8)
    hard = [
        (f"Un capital de {cap} crece con interés compuesto anual del {rate}% durante {yrs} años (capitalización anual). ¿Cuál es el monto final? Redondeá a 2 decimales.",
         round(cap * (1 + rate / 100) ** yrs, 2), 0.011),
        (f"¿Cuántos números enteros entre 1 y {hi} (inclusive) son múltiplos de {p} o de {q}, o de ambos?", sum(1 for i in range(1, hi + 1) if i % p == 0 or i % q == 0), 0.01),
        (f"¿Cuál es la cifra de las unidades de {base}^{ex}?", pow(base, ex, 10), 0.01),
        (f"¿Cuál es la suma de los dígitos de {nf}! (factorial de {nf})?", sum(int(d) for d in str(math.factorial(nf))), 0.01),
        (f"¿De cuántas formas se pueden elegir {k} personas de un grupo de {n}, sin importar el orden?", math.comb(n, k), 0.01),
    ]
    return [(*rng.choice(easy), 0.01)] + rng.sample(hard, 2)


def _data_items(rng: random.Random):
    names = rng.sample(["lápiz", "cuaderno", "regla", "mochila", "goma", "carpeta", "tijera", "resaltador", "compás", "marcador"], 8)
    rows = [(nm, rng.randint(3, 40), rng.choice([2, 3, 4, 5, 8, 10, 12, 15, 20, 25])) for nm in names]
    rev = {n: u * p for n, u, p in rows}
    ordered = sorted(rev.values())
    if ordered[-1] == ordered[-2]:  # sin empates en la respuesta
        return _data_items(rng)
    csv = "producto,unidades,precio\n" + "\n".join(f"{n},{u},{p}" for n, u, p in rows)
    top, thr, pthr = max(rev, key=rev.get), rng.choice([10, 15, 20]), rng.choice([5, 8, 10])
    tot_u, tot_r = sum(u for _, u, _ in rows), sum(rev.values())
    easy = [
        ("¿Cuál es el total de unidades?", num_check(tot_u), str(tot_u)),
        (f"¿Cuántos productos tienen más de {thr} unidades?", num_check(sum(1 for _, u, _ in rows if u > thr)), str(sum(1 for _, u, _ in rows if u > thr))),
    ]
    hard = [
        ("¿Cuál es el precio promedio ponderado por unidades (ingreso total dividido unidades totales)? Redondeá a 2 decimales.", num_check(round(tot_r / tot_u, 2), 0.011), str(round(tot_r / tot_u, 2))),
        ("¿Qué producto tiene el mayor ingreso (unidades × precio)? Respondé solo el nombre.", word_check(top), top),
        ("¿Cuál es la diferencia entre el mayor y el menor ingreso (unidades × precio) entre los productos?", num_check(ordered[-1] - ordered[0]), str(ordered[-1] - ordered[0])),
        (f"¿Cuántas unidades suman en total los productos con precio mayor a {pthr}?", num_check(sum(u for _, u, p in rows if p > pthr)), str(sum(u for _, u, p in rows if p > pthr))),
    ]
    return csv, [rng.choice(easy)] + rng.sample(hard, 2)


def _math_batch(rng: random.Random, rnd: int) -> Batch:
    math_items = _math_items(rng)
    csv, data_items = _data_items(rng)
    parts = [SECTIONS_RULE, "Cada respuesta es solo el resultado final (un número o un nombre), sin cuentas.", ""]
    items, n = [], 1
    for q, exp, tol in math_items:
        parts.append(f"Consigna {n}: {q}")
        items.append(Item("math", num_check(exp, tol), str(exp)))
        n += 1
    parts.append(f"\nTabla de ventas:\n{csv}\n")
    for q, chk, ref in data_items:
        parts.append(f"Consigna {n}: {q} (usá la tabla)")
        items.append(Item("data", chk, ref))
        n += 1
    return Batch("math", "\n".join(parts), items)


# ---------- contexto largo, con distractores y una actualización ----------

_FILLER = ["La reunión semanal se movió al jueves por la mañana.", "El proveedor confirmó la entrega para la semana próxima.",
           "Se actualizó el manual interno con los nuevos procedimientos.", "El equipo revisó los indicadores del trimestre sin novedades.",
           "La sala de reuniones del segundo piso estará en mantenimiento.", "Recordatorio: completar la encuesta de satisfacción antes del viernes.",
           "El informe anterior fue archivado en la carpeta compartida.", "Se agendó una capacitación opcional sobre herramientas de planilla.",
           "La política de viáticos no tuvo cambios este período.", "El cliente pidió ajustar el calendario de hitos del proyecto."]
_PROJECTS = ["Aurora", "Bahía", "Cóndor", "Delta", "Ébano", "Fénix", "Granito", "Horizonte"]


def _context_batch(rng: random.Random, rnd: int) -> Batch:
    size = [14_000, 22_000, 32_000][min(rnd, 2)]
    pr = rng.sample(_PROJECTS, 3)
    codes = {p: [rng.randint(1000, 9999) for _ in range(3)] for p in pr}
    # cada proyecto: [código vigente, distractor / valor viejo, otro dato]
    facts = [  # (proyecto, frases en orden de aparición)
        (pr[0], [f"El código de respaldo del proyecto {pr[0]} es {codes[pr[0]][1]}.", f"El código de acceso del proyecto {pr[0]} es {codes[pr[0]][0]}."]),
        (pr[1], [f"El código de acceso del proyecto {pr[1]} es {codes[pr[1]][1]}.", f"Actualización: el código de acceso del proyecto {pr[1]} cambió a {codes[pr[1]][0]}."]),
        (pr[2], [f"El código de acceso del proyecto {pr[2]} es {codes[pr[2]][0]}.", f"El código de acceso del proyecto {rng.choice([p for p in _PROJECTS if p not in pr])} es {codes[pr[2]][2]}."]),
    ]
    sents, total = [], 0
    while total < size:
        s = rng.choice(_FILLER)
        sents.append(s)
        total += len(s) + 1
    slots = sorted(rng.sample(range(len(sents)), 6))
    seq = [s for _, ss in facts for s in ss]
    order = list(range(6))
    rng.shuffle(order)
    # las dos frases de cada proyecto conservan su orden relativo (la actualización va después de lo viejo)
    pos = {}
    for k, (_, ss) in enumerate(facts):
        a, b = sorted(slots[o] for o in order[2 * k: 2 * k + 2])
        pos[2 * k], pos[2 * k + 1] = a, b
    for idx in sorted(pos, key=lambda i: -pos[i]):
        sents.insert(pos[idx], seq[idx])
    parts = [SECTIONS_RULE, "Cada respuesta es solo el número de 4 cifras vigente.", "", "TEXTO:", " ".join(sents), "",
             *[f"Consigna {i}: ¿Cuál es el código de acceso vigente del proyecto {p}?" for i, p in enumerate(pr, 1)]]
    return Batch("context", "\n".join(parts), [Item("long_context", lambda a, c=codes[p][0]: str(c) in re.sub(r"[\s.,]", "", a or ""), str(codes[p][0])) for p in pr])


# ---------- escritura: consignas con reglas medibles ----------

_TOPICS = ["el café de la mañana", "una caminata bajo la lluvia", "el primer día de trabajo", "una biblioteca vieja", "el mercado del barrio", "un viaje en tren"]


def _sentences(t: str) -> int:
    return len([x for x in re.split(r"(?<=[.!?…])\s+", t.strip()) if re.search(r"\w", x)])


def _words(t: str) -> int:
    return len(re.findall(r"\b\w+\b", t))


def _starts_with(t: str, letter: str) -> bool:
    parts = [x.strip(" \"'«»¿¡-") for x in re.split(r"(?<=[.!?…])\s+", t.strip()) if re.search(r"\w", x)]
    return bool(parts) and all(p[:1].lower() == letter.lower() for p in parts)


def _writing_batch(rng: random.Random, rnd: int) -> Batch:
    t = rng.sample(_TOPICS, 6)
    n, lo = rng.choice([2, 3, 4]), rng.choice([30, 40, 50])
    w, k = rng.choice(["siempre", "casa", "tiempo", "luz"]), rng.choice([2, 3])
    L, nb = rng.choice(["M", "P", "C", "S"]), rng.choice([3, 4, 5])
    n5, w5, k5 = rng.choice([3, 4]), rng.choice(["casa", "tiempo", "luz"]), rng.choice([2, 3])
    easy = [
        (f"Escribí exactamente {n} oraciones sobre {t[0]}. Sin listas ni títulos.", lambda a: _sentences(a) == n and not re.search(r"(?m)^\s*([-*•]|\d+[.)])\s", a), " ".join(["Hay un café."] * n)),
        (f"Escribí un saludo en una sola línea y TODO EN MAYÚSCULAS para un cliente nuevo, relacionado con {t[1]}.", lambda a: a.strip() == a.strip().upper() and "\n" not in a.strip() and _words(a) >= 4, "BIENVENIDO ESTIMADO CLIENTE NUEVO"),
    ]
    hard = [
        (f"Escribí exactamente 3 oraciones sobre {t[2]} y hacé que CADA oración empiece con la letra {L}.", lambda a: _sentences(a) == 3 and _starts_with(a, L), f"{L}ira el cielo. {L}ira el mar. {L}ira la luz."),
        (f"Escribí un texto sobre {t[3]} de entre {lo} y {lo + 8} palabras que NO contenga las palabras «muy», «cosa» ni «algo» y que termine con una pregunta.",
         lambda a: lo <= _words(a) <= lo + 8 and not re.search(r"\b(muy|cosa|algo)\b", a.lower()) and a.strip().endswith("?"), " ".join(["palabra"] * lo) + " ¿no?"),
        (f"Escribí dos líneas (separadas por un salto de línea) sobre {t[4]}, todo en minúsculas y sin ningún signo de puntuación (ni comas, ni puntos, ni preguntas).",
         lambda a: len([x for x in a.strip().split("\n") if x.strip()]) == 2 and a == a.lower() and not re.search(r"[.,;:!?¿¡]", a), "una linea sin signos\notra linea sin signos"),
        (f"Escribí una lista de exactamente {nb} viñetas sobre {t[5]}: cada viñeta empieza con «- » y tiene entre 3 y 6 palabras. Nada más que la lista.",
         lambda a: len([x for x in a.strip().split("\n") if x.strip()]) == nb and all(x.strip().startswith("- ") and 3 <= _words(x) <= 6 for x in a.strip().split("\n") if x.strip()), "\n".join(["- una vez dos tres"] * nb)),
        (f"Escribí un texto sobre {t[0]} con exactamente {n5} oraciones, que contenga la palabra «{w5}» exactamente {k5} veces y que termine con la frase exacta: FIN DEL MENSAJE",
         lambda a: _sentences(a) == n5 and len(re.findall(rf"\b{w5}\b", a.lower())) == k5 and a.strip().endswith("FIN DEL MENSAJE"),
         ". ".join([" ".join([w5] * k5)] + ["Todo sigue bien"] * (n5 - 2) + ["FIN DEL MENSAJE"])),
    ]
    chosen = [rng.choice(easy)] + rng.sample(hard, 3)
    parts = [SECTIONS_RULE, "Cada respuesta es solo el texto pedido.", ""] + [f"Consigna {i}: {q}" for i, (q, *_) in enumerate(chosen, 1)]
    return Batch("writing", "\n".join(parts), [Item("writing", c, ref) for _, c, ref in chosen])


# ---------- razonamiento ----------

_PEOPLE = ["Ana", "Beto", "Carla", "Dante", "Elena", "Fabio", "Gala", "Hugo"]
_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _perm_puzzle(rng: random.Random):
    """Cinco personas en fila con reglas; la respuesta a '¿quién está en la posición q?' es única (se verifica por fuerza bruta)."""
    people = rng.sample(_PEOPLE, 5)
    target = people[:]
    rng.shuffle(target)
    pos = {p: i for i, p in enumerate(target)}
    q = rng.randrange(5)

    def rules():
        a, b = rng.sample(people, 2)
        return rng.choice([
            (f"{a} está en algún lugar a la izquierda de {b}.", lambda ps, a=a, b=b: ps[a] < ps[b]),
            (f"{a} no está en ninguno de los dos extremos.", lambda ps, a=a: 0 < ps[a] < 4),
            (f"{a} está justo a la izquierda de {b} (pegados).", lambda ps, a=a, b=b: ps[b] == ps[a] + 1),
            (f"{a} y {b} no están uno al lado del otro.", lambda ps, a=a, b=b: abs(ps[a] - ps[b]) > 1),
            (f"{a} está en una posición impar (1, 3 o 5).", lambda ps, a=a: ps[a] % 2 == 0),
        ])

    chosen: List = []
    for _ in range(60):
        text, fn = rules()
        if not fn(pos) or any(text == t for t, _ in chosen):
            continue
        chosen.append((text, fn))
        valid = [pm for pm in itertools.permutations(people) if all(f({p: i for i, p in enumerate(pm)}) for _, f in chosen)]
        if valid and len({pm[q] for pm in valid}) == 1:
            return chosen, q, target[q], people
    return _perm_puzzle(rng)


def _logic_batch(rng: random.Random, rnd: int) -> Batch:
    chosen, q, who, people = _perm_puzzle(rng)
    start, nd = rng.randrange(7), rng.randint(10, 45)
    a0, r, c = rng.randint(2, 6), rng.choice([2, 3]), rng.randint(1, 4)
    seq = [a0]
    for _ in range(5):
        seq.append(seq[-1] * r + c)
    qs = [
        ("Cinco personas (" + ", ".join(people) + ") están en fila, de izquierda a derecha (posiciones 1 a 5). " + " ".join(t for t, _ in chosen)
         + f" ¿Quién está en la posición {q + 1}? Respondé solo el nombre.", word_check(who, [p for p in people if p != who]), who),
        (f"Si hoy es {_DAYS[start]}, ¿qué día de la semana será dentro de {nd} días? Respondé solo el día.", word_check(_DAYS[(start + nd) % 7]), _DAYS[(start + nd) % 7]),
        (f"En la sucesión {', '.join(map(str, seq[:3]))}, …, cada término se obtiene del anterior multiplicándolo por {r} y sumando {c}. ¿Cuál es el sexto término?",
         num_check(seq[5]), str(seq[5])),
    ]
    parts = [SECTIONS_RULE, "Cada respuesta es solo el resultado final.", ""] + [f"Consigna {i}: {q_}" for i, (q_, *_) in enumerate(qs, 1)]
    return Batch("logic", "\n".join(parts), [Item("analysis", c_, ref) for _, c_, ref in qs])


# ---------- multimodal: contar y ubicar cuadrados en una imagen ----------

_COLORS = {"rojo": (220, 20, 20), "verde": (20, 160, 40), "azul": (30, 60, 220), "amarillo": (240, 220, 20), "negro": (10, 10, 10), "blanco": (255, 255, 255)}


def draw_png(path: Path, size: int, rects: List) -> None:
    """PNG RGB de size×size con fondo blanco y rectángulos (x0, y0, x1, y1, color)."""
    px = [[_COLORS["blanco"]] * size for _ in range(size)]
    for x0, y0, x1, y1, col in rects:
        for y in range(y0, y1):
            for x in range(x0, x1):
                px[y][x] = _COLORS[col]
    raw = b"".join(b"\x00" + b"".join(bytes(c) for c in row) for row in px)

    def chunk(t: bytes, d: bytes) -> bytes:
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _vision_batch(rng: random.Random, rnd: int, workdir: str) -> Batch:
    kr, kb = rng.randint(2, 6), rng.randint(2, 6)
    cells = rng.sample([(r, c) for r in range(6) for c in range(6)], kr + kb + 2)  # grilla de 6×6 celdas de 24 px
    big_color = rng.choice(["negro", "amarillo"])
    green_cell, big_cell, rest = cells[0], cells[1], cells[2:]
    rects = []
    for i, (r, c) in enumerate(rest):
        rects.append((c * 24 + 6, r * 24 + 6, c * 24 + 18, r * 24 + 18, "rojo" if i < kr else "azul"))
    rects.append((green_cell[1] * 24 + 6, green_cell[0] * 24 + 6, green_cell[1] * 24 + 18, green_cell[0] * 24 + 18, "verde"))
    rects.append((big_cell[1] * 24 + 2, big_cell[0] * 24 + 2, big_cell[1] * 24 + 22, big_cell[0] * 24 + 22, big_color))
    img = Path(workdir) / f"cuadrados_{rnd}.png"
    draw_png(img, 144, rects)
    half = "arriba" if green_cell[0] < 3 else "abajo"
    parts = [SECTIONS_RULE, "Cada respuesta es solo un número o una palabra.", "",
             f"Abrí la imagen {img}: son cuadrados de colores sobre fondo blanco (rojo, azul, verde, y uno más grande de otro color).",
             "Consigna 1: ¿Cuántos cuadrados ROJOS hay?", "Consigna 2: ¿Cuántos cuadrados AZULES hay?",
             "Consigna 3: ¿De qué color es el cuadrado más grande? (rojo, azul, verde, amarillo o negro)",
             "Consigna 4: El único cuadrado verde, ¿está en la mitad de arriba o en la de abajo de la imagen? Respondé «arriba» o «abajo»."]
    items = [Item("multimodal", num_check(kr), str(kr)), Item("multimodal", num_check(kb), str(kb)),
             Item("multimodal", word_check(big_color, [o for o in _COLORS if o not in (big_color, "blanco")]), big_color),
             Item("multimodal", word_check(half, ["arriba" if half == "abajo" else "abajo"]), half)]
    return Batch("vision", "\n".join(parts), items, True, workdir)


def build_batches(seed: int, rnd: int, workdir: str) -> List[Batch]:
    mk = lambda kind: random.Random(f"{seed}-{rnd}-{kind}")
    return [_code_batch(mk("code"), rnd), _math_batch(mk("math"), rnd), _context_batch(mk("context"), rnd),
            _writing_batch(mk("writing"), rnd), _logic_batch(mk("logic"), rnd), _vision_batch(mk("vision"), rnd, workdir)]


def perfect_answer(batch: Batch) -> str:
    """Respuesta de un modelo perfecto, con el formato pedido (para autoverificar y para tests)."""
    return "\n".join(f"### {i}\n{it.ref}" for i, it in enumerate(batch.items, 1))


# ---------- ejecución ----------

PING = "Respondé solo: OK"


def estimate(cfg: Dict, names: List[str], rounds: int) -> Dict[str, Dict]:
    """Costo estimado por modelo antes de correr: llamadas y tokens de entrada (el piso fijo de cada CLI domina)."""
    out = {}
    with tempfile.TemporaryDirectory() as d:
        batches = build_batches(1, 0, d)
    for n in names:
        spec = cfg["models"][n]
        usable = [b for b in batches if not (b.needs_files and not spec.get("reads_files", True))]
        floor = (spec.get("calibration") or {}).get("overhead_in", 12_000)
        calls = 1 + rounds * len(usable)
        prompt_tokens = sum(len(b.prompt) // 3 for b in usable) * rounds
        files = sum(1 for b in usable if b.needs_files) * rounds
        extra = (spec.get("calibration") or {}).get("files_extra_in", 0)
        out[n] = {"calls": calls, "tokens_in": calls * floor + prompt_tokens + files * extra}
    return out


def _run_model(name: str, spec: Dict, rounds: int, seed: int, say: Callable[[str], None]) -> Dict:
    t0 = time.time()
    entry: Dict = {"model_id": None, "calibrated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "rounds": rounds, "seed": seed,
                   "categories": {}, "calls": [], "skipped": []}
    cats = entry["categories"]

    def record(key: str, res: Dict) -> None:
        entry["model_id"] = res.get("model_id") or entry["model_id"]
        entry["calls"].append({"key": key, "ok": res["ok"], "seconds": res["seconds"], "tokens": res.get("tokens"), "error": res.get("error")})

    with tempfile.TemporaryDirectory(prefix="ia-router-cal-") as work:
        res = adapters.run_cli(name, spec, PING, cwd=work, usage=True, lean=True)
        record("ping", res)
        if not res["ok"]:
            entry["skipped"].append(f"ping: {res.get('error')}")
            say(f"{name}: no responde ({res.get('error')}); se omite")
            return entry
        entry["latency_s"], entry["ping_tokens"] = res["seconds"], res.get("tokens")
        say(f"{name}: responde ({res['seconds']}s, {entry['model_id'] or 'modelo ?'})")
        for rnd in range(rounds):
            for b in build_batches(seed, rnd, work):
                if b.needs_files and not spec.get("reads_files", True):
                    if b.key not in entry["skipped"]:
                        entry["skipped"].append(b.key)
                    continue
                res = adapters.run_cli(name, spec, b.prompt, cwd=work, usage=True, lean=not b.needs_files, extra_dirs=[work] if b.needs_files else None,
                                       timeout=max(spec.get("timeout", 300), 120))
                record(f"{b.key}#{rnd + 1}", res)
                if not res["ok"] and res.get("error") in SKIP_ERRORS:
                    entry["skipped"].append(f"{b.key}: {res['error']}")
                    say(f"{name}: {b.key} omitido ({res['error']})")
                    continue
                secs = sections(res["output"] if res["ok"] else "")
                blocked = not res["ok"] and bool(PERMISSION_RE.search(res.get("error") or ""))
                hits = 0
                for i, item in enumerate(b.items, 1):
                    try:
                        ok = bool(item.check(secs.get(i, "")))
                    except Exception:
                        ok = False
                    c = cats.setdefault(item.category, {"passed": 0, "total": 0})
                    c["total"] += 1
                    c["passed"] += ok
                    c["blocked"] = c.get("blocked", 0) + (1 if blocked else 0)
                    hits += ok
                say(f"{name}: {b.key}#{rnd + 1} {hits}/{len(b.items)} ({res['seconds']}s)" + ("  ← sin respuesta: pidió una herramienta que el modo no interactivo no puede autorizar" if blocked else ""))
    entry["total_seconds"] = round(time.time() - t0, 1)
    return entry


def calibrate(cfg: Dict, names: List[str], rounds: int = 1, seed: Optional[int] = None, say: Callable[[str], None] = print) -> Dict[str, Dict]:
    """Corre la calibración (un hilo por CLI: son independientes) y guarda metrics.json. Devuelve {modelo: entrada}."""
    seed = seed if seed is not None else int(time.time()) // 86400
    lock_say = (lambda s: say(s))
    with ThreadPoolExecutor(max_workers=max(1, len(names))) as ex:
        futs = {n: ex.submit(_run_model, n, cfg["models"][n], rounds, seed, lock_say) for n in names}
        results = {n: f.result() for n, f in futs.items()}
    data = load()
    data.setdefault("models", {}).update({n: r for n, r in results.items() if r["categories"] or r.get("latency_s")})
    save(data)
    return results


# ---------- uso desde el CLI y el chat ----------

def available_models(cfg: Dict, wanted: Optional[List[str]] = None) -> List[str]:
    names = [n for n, sp in cfg["models"].items() if sp.get("enabled", True) and adapters.is_available(n, sp) and state.cooldown_remaining(n) <= 0]
    return [n for n in names if not wanted or n in wanted]


def format_estimate(cfg: Dict, est: Dict[str, Dict], rounds: int) -> str:
    lines = [f"{'modelo':<13}{'llamadas':>9}{'tokens de entrada':>19}"]
    for n, e in est.items():
        lines.append(f"{n:<13}{e['calls']:>9}{'≈ ' + format(e['tokens_in'], ',').replace(',', '.'):>19}")
    total_calls, total_tok = sum(e["calls"] for e in est.values()), sum(e["tokens_in"] for e in est.values())
    lines.append(f"{'total':<13}{total_calls:>9}{'≈ ' + format(total_tok, ',').replace(',', '.'):>19}")
    lines.append("La salida es mínima (unos cientos de tokens en total). Casi todo el costo de entrada es el piso fijo de cada CLI;"
                 " por eso las preguntas van agrupadas por categoría. " + ("Ronda única (rápida)." if rounds == 1 else f"{rounds} rondas con preguntas distintas (completa)."))
    return "\n".join(lines)


def run_with_confirmation(cfg: Dict, rounds: int = 1, wanted: Optional[List[str]] = None, seed: Optional[int] = None,
                          confirm: Callable[[str], bool] = lambda q: True, say: Callable[[str], None] = print) -> Optional[Dict[str, Dict]]:
    names = available_models(cfg, wanted)
    if not names:
        say("No hay modelos disponibles para calibrar (¿instalados? ¿en cooldown? ¿desactivados?).")
        return None
    say(format_estimate(cfg, estimate(cfg, names, rounds), rounds))
    if not confirm("¿Corro la calibración?"):
        say("Cancelado: no gasté nada.")
        return None
    results = calibrate(cfg, names, rounds, seed, say)
    say("\nResultado (aciertos por categoría):")
    for n, r in results.items():
        cats = "  ".join(f"{c} {v['passed']}/{v['total']}" for c, v in sorted(r["categories"].items())) or "sin datos"
        extra = f"  · omitido: {', '.join(r['skipped'])}" if r["skipped"] else ""
        blocked = [c for c, v in sorted(r["categories"].items()) if v.get("blocked")]
        if blocked:
            extra += f"  · sin respuesta por permisos (quiso usar una herramienta): {', '.join(blocked)}"
        say(f"  {n:<12} {r.get('model_id') or '?':<24} {cats}{extra}")
    return results


# ---------- persistencia ----------

def path() -> Path:
    return state.home() / "metrics.json"


def load() -> Dict:
    try:
        d = json.loads(path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"models": {}}
    except (OSError, ValueError):
        return {"models": {}}


def save(data: Dict) -> None:
    state.home().mkdir(parents=True, exist_ok=True)
    tmp = path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path())
