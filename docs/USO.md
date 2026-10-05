# Guía de uso — ia-suscription-router

Esta guía explica **cómo se usa** el router, paso a paso y con ejemplos. Para una visión general del diseño, ver el [README](../README.md).

Todos los ejemplos asumen que estás en la carpeta del repo:

```bash
cd ~/Documents/ia-suscription-router
```

Las salidas que se muestran son reales (capturadas con claude 2.1.288, codex 0.160.0 y agy 1.2.16). Los pasos de esta guía —instalación desde cero, registro por MCP y modo interactivo de `manifest refine`— fueron probados de punta a punta.

---

## 1. Qué es y cómo funciona, en 30 segundos

El router **no es un modelo**. Es un programa que decide a cuál de tus CLIs de IA (`claude`, `codex`, `agy`) mandarle cada tarea, y lo ejecuta usando **tu suscripción** de cada uno.

```
tu tarea ──► clasificar ──► consultar manifiesto ──► elegir modelo ──► ejecutar su CLI
            (reglas o       (tus preferencias         (con fallback si     (con tu login
             manager LLM)    por tipo de tarea)        hay rate limit)      y tu cuota)
```

Hay tres conceptos:

| Concepto | Qué es |
|---|---|
| **Modelos** | Los CLIs oficiales instalados y logueados en tu máquina. Se definen en `models.json`. |
| **Manager** | Un modelo **barato que elegís vos** (por defecto Claude Haiku). Clasifica tareas y mantiene el manifiesto. |
| **Manifiesto** | Un archivo con el orden de preferencia de modelos por tipo de tarea. Lo arma el manager y lo ajustás conversando con él. |

El router **nunca lee ni copia tus tokens**: cada CLI usa su propio login.

---

## 2. Requisitos e instalación

### Qué necesitás

| Requisito | Cómo verificarlo |
|---|---|
| Python 3.9 o superior | `python3 --version` |
| Al menos un CLI instalado **y logueado** | Ver tabla de abajo |
| Nada más | El router no tiene dependencias externas (solo librería estándar) |

### Instalar y loguear cada CLI

| CLI | Instalación | Login |
|---|---|---|
| `claude` (Claude Code) | Ver la documentación de Claude Code | Ejecutá `claude` una vez y seguí el login |
| `codex` (OpenAI) | Ver la documentación de Codex CLI | Ejecutá `codex` una vez y seguí el login |
| `agy` (Google Antigravity) | `brew install --cask antigravity-cli` | Ejecutá `agy` **sin argumentos** en una terminal y elegí iniciar sesión con Google |

> **Gemini CLI ya no se usa.** Google lo dio de baja para cuentas individuales (el login falla con *"This client is no longer supported"*). Su reemplazo es `agy`.

No hace falta instalar los tres. Con uno solo el router funciona, aunque con menos para repartir.

### Instalar el router

No hay que instalar nada: es un clon del repo.

```bash
gh repo clone Mgobeaalcoba/ia-suscription-router ~/Documents/ia-suscription-router   # el repo es privado: requiere `gh auth login`
cd ~/Documents/ia-suscription-router
python3 -m unittest discover -s tests    # opcional: debe terminar en OK
```

---

## 3. Modo chat (la forma recomendada de usarlo)

Es la experiencia "como `claude`": abrís el programa y **hablás**. No tenés que recordar comandos.

### Abrirlo

```bash
ia-router
```

(`ia-router` es un enlace a `cli.py`. Si no existe: `ln -sf ~/Documents/ia-suscription-router/cli.py ~/.local/bin/ia-router`, con `~/.local/bin` en tu `PATH`. También funciona `python3 cli.py` o `python3 cli.py chat`.)

La primera vez, si no hay manifiesto, te ofrece armarlo ahí mismo. Después ves esto:

```
 ●─╮     ╦╔═╗   ╦═╗╔═╗╦ ╦╔╦╗╔═╗╦═╗
 ●─┼─◉   ║╠═╣ ─ ╠╦╝║ ║║ ║ ║ ║╣ ╠╦╝  v0.1.0
 ●─╯     ╩╩ ╩   ╩╚═╚═╝╚═╝ ╩ ╚═╝╩╚═  tus suscripciones de IA, ruteadas

 ╭──────────────────────────────────────────────────────────────────────────╮
 │ manager claude                                                           │
 │ modelos ● claude   ● codex   ● antigravity                               │
 │ carpeta ~/Documents/ia-suscription-router                                │
 ╰──────────────────────────────────────────── by Mgobeaalcoba · mgatc.com ─╯
 Hablame normal: tareas o preferencias  ·  /help atajos  ·  /exit salir

ia ❯
```

En la terminal el logo lleva degradé (naranja → magenta → azul) y cada proveedor su color: `●` encendido si el CLI está instalado, `○` apagado si falta. `mgatc.com` es un link clickeable en las terminales que lo soportan (iTerm2, Terminal de macOS reciente, Ghostty, etc.). Con menos de 60 columnas se muestra una versión compacta.

### La caja de entrada y los archivos

Lo que escribís va en una caja con borde en degradé (como las de Claude Code, Codex y Antigravity), con un texto de ayuda cuando está vacía y, debajo, los atajos y el manager activo. Arriba a la derecha del borde ves el modelo: `auto` o el que fijaste con `/model`.

```
╭──────────────────────────────────────────────────────────────────── auto ─╮
│ ❯ "/Users/vos/Fotos/captura de pantalla.png" ¿qué error muestra?          │
╰────────────────────────────────────────────────────────────────────────────╯
  ⎘ captura de pantalla.png · imagen · 212.3 KB
  ⏎ enviar · ⌥⏎ o \⏎ nueva línea · / comandos · ⌃D salir        manager claude
```

**Archivos: arrastralos a la terminal.** La terminal pega la ruta y el router la deja limpia (absoluta, entre comillas si tiene espacios) y la resalta si el archivo existe; debajo aparece cada adjunto con su tipo y tamaño. También funciona escribir o pegar una ruta (`/ruta/archivo`, `~/archivo`, `./archivo`, `file://…`). Al enviar:

| Tipo | Qué pasa |
|---|---|
| Texto y código (`.txt`, `.md`, `.py`, `.csv`, `.json`… o cualquier archivo en UTF-8 de hasta 200 KB) | Su contenido se anexa a la tarea como contexto; sirve para cualquier modelo. |
| Imágenes, PDF, binarios y carpetas | Se pasa la ruta y el modelo la abre (Claude recibe acceso a esa carpeta con `--add-dir`; Codex la lee desde su sandbox de solo lectura). La tarea suma la categoría `multimodal` si hay imagen o PDF. |

**Antigravity no puede abrir archivos en modo no interactivo** (pide un permiso que no puede pedir), así que con adjuntos que no son texto el router lo descarta del ruteo (`models.json`: `"reads_files": false`). Si lo fijaste con `/model antigravity`, te avisa en lugar de fallar.

**Edición.** Flechas, `Inicio`/`Fin` (o `Ctrl-A`/`Ctrl-E`), `Alt-←/→` para saltar palabras, `Ctrl-W` borra la palabra, `Ctrl-U` hasta el inicio de la línea, `Ctrl-K` hasta el final. **Varias líneas:** `Alt-Enter`, `Ctrl-J` o `\` + Enter; un texto pegado con saltos de línea se respeta. **Historial:** `↑`/`↓` (se guarda entre sesiones en `~/.ia-router/history.jsonl`). **Comandos:** al escribir `/` aparece la lista filtrada; `Tab` o Enter completan, `↑`/`↓` eligen. **Salir:** `Ctrl-D` con la caja vacía, `Ctrl-C` dos veces, o `/exit`. `Ctrl-C` con texto lo borra.

Si no hay terminal (pipe, `TERM=dumb`) o tiene menos de 40 columnas, se usa el prompt simple `ia> ` y todo lo demás funciona igual.

### Qué podés escribir

Cada mensaje cae en una de tres categorías, y el programa lo interpreta solo:

| Tipo | Ejemplos | Qué pasa |
|---|---|---|
| **Tarea** | *"Escribí una función Python que invierta un string"* · *"Redactá un saludo para un cliente"* | Se rutea al mejor modelo según tu manifiesto, se ejecuta y ves la respuesta. |
| **Configuración** | *"Prefiero Claude para todo lo de código"* · *"Usá Codex para todo lo de debugging"* · *"Desactivá antigravity"* · *"Que el manager sea codex"* | El manager barato propone un cambio al manifiesto, te lo muestra y **vos confirmás**. |
| **Consulta** | *"Mostrame el manifiesto"* · *"¿Cómo estoy configurado?"* · *"Qué modelos tengo"* · *"Mostrame las estadísticas"* | Muestra el estado, sin gastar cuota. |

### Una sesión real

```
ia> Escribí una función Python que invierta un string. Solo el código.
… ruteando
── codex (gpt-6.1-sol) · in 15.7k (13.2k caché) · out 32 · coding×2 · 7.6s
def invertir_string(texto: str) -> str:
    return texto[::-1]

ia> Ahora hacela recursiva.
── codex (gpt-6.1-sol) · in 15.9k (13.2k caché) · out 28 · coding×2 · 7.1s
def invertir_string(texto: str) -> str:
    if len(texto) <= 1:
        return texto
    return texto[-1] + invertir_string(texto[:-1])

ia> Redactá un saludo de una frase para un cliente nuevo.
── claude (claude-sonnet-5-5) · in 16.5k (8.4k caché) · out 72 · writing×1 · 4.1s
...

ia> Prefiero Claude para todo lo de código
… consultando al manager (claude)
Cambios propuestos:
  coding: codex > claude > antigravity  →  claude > codex > antigravity
  debugging: codex > claude > antigravity  →  claude > codex > antigravity
¿Aplico? [S/n]: n
```

(Los tiempos son típicos, de ejemplo.) La línea `── codex (gpt-6.1-sol) · in 15.7k (13.2k caché) · out 32 · coding×2 · 7.6s` dice **qué proveedor respondió y con qué modelo exacto, cuántos tokens de entrada (`in`, con lo que salió de caché entre paréntesis) y de salida (`out`) gastó, por qué categoría y cuánto tardó**. Los tokens y el modelo los informa cada CLI (Claude y Codex en JSON; Antigravity en JSON y su log); si alguno no los informa se muestra `tokens n/d`. `/stats` suma los tokens por proveedor. Si hubo fallback lo indica (`· fallback tras codex`).

### Cómo recuerda la conversación

Los CLIs no tienen memoria entre llamadas, así que el chat **antepone un resumen de los últimos 6 turnos** a cada tarea. Por eso *"Ahora hacela recursiva"* se entiende. Además, un mensaje corto sin tema propio **hereda el tema del anterior** para el ruteo: ese seguimiento fue a Codex (el mismo del turno anterior) y no a un modelo "rápido". En cambio, si cambiás de tema (*"Redactá un saludo…"*), se rutea de nuevo.

`/clear` borra esa memoria. Se pierde al salir (no se guarda la conversación).

### Cuando el mensaje es ambiguo

Si no se puede saber si es una tarea o una instrucción, **te pregunta** en vez de adivinar:

```
ia> usá Codex para revisar este bug
¿Es una instrucción de configuración (c) o una tarea para ejecutar (t)? [c/T]:
```

Enter = tarea. Los mensajes largos, con varias líneas o con bloques de código se tratan siempre como tarea.

### Atajos con `/`

Siempre funcionan, aunque el lenguaje natural no los interprete como querés:

| Atajo | Qué hace |
|---|---|
| `/manifest` | Ver tus preferencias. |
| `/models` · `/models probe` | Estado de los CLIs; con `probe`, llamada real para ver login y latencia (gasta cuota). |
| `/stats` | Éxito y latencia por modelo. |
| `/model codex` · `/model auto` | Fijar un modelo para todo lo que sigue, o volver al ruteo automático. |
| `/manager codex` | Cambiar el modelo barato que clasifica y mantiene el manifiesto. |
| `/llm on\|off` | Clasificar las tareas con el manager (más preciso, un poco más lento) o solo con reglas. |
| `/explain on\|off` | Mostrar la tabla completa de ruteo en cada mensaje. |
| `/calibrate [full]` | Mide el acierto de cada modelo con tareas verificables (pide confirmación antes de gastar). |
| `/scores [categoría]` | Puntaje objetivo por modelo y categoría; con una categoría, el desglose peso × valor. |
| `/criteria` | Preguntas de opción múltiple para ajustar tus criterios de ruteo. |
| `/md on\|off` | Respuestas con el markdown interpretado, como un README en GitHub: títulos, negrita, listas con viñetas, citas, tablas alineadas y bloques de código con fondo y colores, sin ningún signo (`#`, `**`, `` ` ``). `off` = texto crudo. Sin terminal (pipe) o con `NO_COLOR` se muestra crudo. |
| `/setup [preferencias]` | Rearmar el manifiesto desde cero. |
| `/ask texto` · `/config texto` | Forzar la interpretación como tarea o como configuración. |
| `/clear` | Olvidar la conversación. |
| `/help` · `/exit` | Ayuda · salir (también `salir`, Ctrl-D). |

`Ctrl-C` durante una tarea la interrumpe sin cerrar el programa.

### Comandos sueltos (sin abrir el chat)

Todo lo del chat también existe como comandos para usar en scripts o en una sola línea (`ask`, `route`, `manifest`, `doctor`, `stats`…). Se describen en la sección 5.

---

## 4. Primer uso (recorrido completo con comandos)

### Paso 1 — Verificar el entorno

```bash
python3 cli.py doctor
```

(Antes de correr `setup` dice `manifiesto: no`; después, `manifiesto: sí`.)

```
Config: 3 modelos | estado en: /Users/mgobea/.ia-router | manifiesto: no (corré: cli.py setup)

claude   instalado     cooldown=0s
codex    instalado     cooldown=0s
antigravity instalado     cooldown=0s
```

Esto solo mira si los ejecutables existen. **No** comprueba que estés logueado. Para eso:

```bash
python3 cli.py doctor --probe
```

Hace **una llamada mínima real a cada CLI** (gasta una pizca de cuota) y muestra login, latencia y versión:

```
claude   instalado     cooldown=0s  auth=ok 6.06s 2.1.288 (Claude Code)
codex    instalado     cooldown=0s  auth=ok 6.45s codex-cli 0.160.0
antigravity instalado  cooldown=0s  auth=ok 5.85s 1.2.16
```

Si un modelo está instalado pero sin sesión, verás `auth=missing` y la indicación de qué hacer.

### Paso 2 — Crear tu manifiesto

```bash
python3 cli.py setup
```

En una terminal interactiva te pregunta dos cosas:

1. **Qué modelo usar como manager.** Es el que clasifica tus tareas y arma el manifiesto. Conviene uno barato y rápido. Enter acepta el valor por defecto (`claude`, que usa Haiku).
2. **Tus preferencias**, en lenguaje natural. Por ejemplo: *"Codex para código, Claude para escribir, Antigravity para contexto largo"*. Podés dejarlo vacío.

Después prueba cada CLI y arma el manifiesto. Para hacerlo sin preguntas:

```bash
python3 cli.py setup --manager claude \
  --notes "Prefiero Codex para código y debugging. Claude para escribir y análisis. Antigravity para contexto largo, multimodal e investigación."
```

Salida (resumida):

```
Probando cada CLI con un prompt mínimo...
  claude   instalado=True auth=ok 6.06s 2.1.288 (Claude Code)
  codex    instalado=True auth=ok 6.45s codex-cli 0.160.0
  antigravity instalado=True auth=ok 5.85s 1.2.16

Manifiesto — manager: claude · origen: manager LLM · actualizado: 2026-10-03T17:34:26
Preferencias del usuario: Prefiero Codex para código y debugging. ...

coding        codex > claude > antigravity   Codex es especialista en código (preferencia usuario).
debugging     codex > claude > antigravity   Codex especialista en debugging (preferencia usuario).
writing       claude > antigravity > codex   Claude es el mejor para escritura (preferencia usuario).
analysis      claude > antigravity > codex   Claude especialista (preferencia usuario).
research      antigravity > claude > codex   Antigravity es investigador fuerte + contexto largo.
multimodal    antigravity > claude > codex   Antigravity domina multimodal (preferencia usuario).
long_context  antigravity > claude > codex   Antigravity es experto (preferencia usuario).
...
Guardado en /Users/mgobea/.ia-router/manifest.json
```

Cada fila dice **qué modelo va primero, segundo y tercero** para ese tipo de tarea, y por qué.

Opciones de `setup`:

| Opción | Efecto |
|---|---|
| `--manager claude\|codex\|antigravity` | Elige el manager sin preguntar. |
| `--notes "texto"` | Tus preferencias, sin preguntar. |
| `--no-probe` | No hace llamadas de prueba a los CLIs (no gasta cuota, pero no mide login ni latencia). |

### Paso 3 — Usarlo

```bash
python3 cli.py ask "Escribí una función Python que sume los elementos de una lista. Solo el código."
```

```
Clasificación (reglas, manifiesto: sí): coding×2
modelo   score  estado
codex     10.0  OK             coding×2→10
claude     8.0  OK             coding×2→8
antigravity 6.0  OK            coding×2→6
→ elegido: codex
intento codex: OK (7.98s)

[codex]
def sumar_lista(lista):
    return sum(lista)
```

La tabla va por **stderr** y la respuesta por **stdout**. Así podés redirigir solo la respuesta: `python3 cli.py ask "..." > respuesta.txt`.

---

## 4b. Ruteo objetivo: calibrar y ajustar tus criterios

Las "fortalezas" de `models.json` son hipótesis iniciales. Para reemplazarlas por datos, el router **mide a los modelos que vos tenés**, con tus suscripciones, y combina esas métricas con los criterios que elijas.

### Medir: `calibrate`

```
python3 cli.py calibrate            # o /calibrate en el chat
```

Corre tareas con **respuesta verificable por código** (no hay modelo-juez, así que no hay subjetividad):

| Categoría | Cómo se corrige |
|---|---|
| `coding`, `debugging` | El código que devuelve el modelo se ejecuta contra tests, en un proceso aislado y con timeout. Hay problemas fáciles y difíciles (LRU, Dijkstra, parser de expresiones, N reinas, bugs sutiles…). |
| `math`, `data` | Valor numérico exacto (calculado con soluciones de referencia): cuentas largas, interés compuesto, combinatoria, consultas sobre una tabla. |
| `long_context` | Encontrar el código vigente en un texto largo con distractores y una actualización posterior. |
| `writing` | Consignas con reglas medibles (cantidad de oraciones, rango de palabras, palabras prohibidas, formato exacto, cierre literal). |
| `analysis` | Puzzles de lógica con solución única (verificada por fuerza bruta), calendario y sucesiones. |
| `multimodal` | Una imagen generada con cuadrados de colores: contar, identificar el más grande y ubicar uno. Solo CLIs que abren archivos. |

**Costo.** Antes de gastar nada muestra el costo estimado por modelo y pide confirmación (`--yes` para saltearla). Las preguntas van **agrupadas por categoría en una sola llamada** y Claude corre en modo liviano (sin herramientas ni prompt de sistema), porque casi todo el costo de entrada es el piso fijo de cada CLI (12-14k tokens por llamada en Codex y Antigravity). La salida es mínima.

| Modo | Llamadas por modelo | Tokens de entrada (total, los 3 modelos) |
|---|---|---|
| `calibrate` (rápido, 1 ronda) | 7 | ≈ 200k |
| `calibrate --full` (3 rondas con preguntas distintas) | 19 | ≈ 550k |

También `--models claude,codex` para medir solo algunos y `--seed N` para repetir las mismas preguntas. Las métricas se guardan en `~/.ia-router/metrics.json` junto con el **id real del modelo** que respondió; si cambia de versión, conviene recalibrar.

Notas honestas sobre lo que mide:
- **Con modelos de frontera muchas categorías se saturan** (todos aciertan todo). Cuando pasa, `scores` lo avisa: ahí decide la velocidad, la cuota y la confiabilidad (que también son métricas objetivas) y no la calidad.
- Si un CLI pide una herramienta que el modo no interactivo no puede autorizar (por ejemplo Antigravity para hacer cuentas), la respuesta queda vacía y **cuenta como fallo, marcado como "sin respuesta por permisos"**: es lo que pasaría al usarlo por el router.
- El código de los modelos se ejecuta en tu máquina en un proceso aislado (`python -I`, sin stdin, con timeout, en una carpeta temporal). Son tus propios CLIs y tareas triviales, pero si no querés ejecutar código de modelos, no corras `calibrate`.

### Cómo se calcula el puntaje: `scores`

```
puntaje(modelo, categoría) = Σ peso × valor        (valores de 0 a 10)
```

| Dimensión | De dónde sale |
|---|---|
| **calidad** | Aciertos medidos en esa categoría, mezclados con la estimación inicial como si ésta valiera 2 preguntas más: `(aciertos + 2×estimado) / (preguntas + 2)`. Con pocas muestras pesa más la estimación. |
| **velocidad** | `10 × (más rápido / este modelo)`, con los segundos medios de las llamadas de calibración. |
| **cuota** | `10 × (el que menos tokens gasta / este modelo)`, con los tokens por corrida reales del log (hacen falta 3 corridas). |
| **confiabilidad** | `10 ×` tasa de éxito real del log (o la de la calibración). |

Si falta un dato objetivo, esa dimensión vale lo mismo que la calidad: **sin datos el puntaje es exactamente la estimación inicial**, nada se mueve sin evidencia. `scores` muestra la tabla (con `m` = medido, `e` = estimado); `scores coding` muestra el desglose de cada modelo (peso × valor).

### Ajustar tus criterios: `criteria`

```
python3 cli.py criteria            # o /criteria en el chat
```

Seis preguntas de opción múltiple con selector (↑/↓ o el número, Enter confirma, Esc cancela; cada respuesta queda resumida en una línea):

1. ¿Qué priorizás? **Calidad primero** / Equilibrado / Velocidad primero / Ahorro de cuota. Define los pesos generales.
2. Para código y debugging: máxima calidad / igual que el resto / rapidez razonable.
3. Para tareas cortas: el más rápido / el de mejor calidad.
4. ¿Querés cuidar la cuota de alguna suscripción? (resta 1 punto a ese modelo).
5. Si dos modelos quedan parejos (menos de 0,5 puntos): mejor calidad / más rápido / gasta menos cuota.
6. Resumen con los pesos resultantes y confirmación: **Guardar**, **Guardar y rearmar el manifiesto** (si tenés uno) o **Descartar**.

No hay números inventados: cada respuesta se traduce a pesos explícitos que se muestran antes de guardar. El perfil queda en `~/.ia-router/profile.json` y las respuestas anteriores aparecen preseleccionadas.

### Precedencia

1. **Manifiesto** (tus preferencias explícitas, p. ej. "Codex para todo el código"): manda siempre. 
2. **Criterios** (`criteria`) aplicados al puntaje.
3. **Métricas medidas** (`calibrate`, log de uso).
4. **Estimación inicial** de `models.json`.

Si ya tenés un manifiesto, `criteria` te ofrece rearmarlo desde los puntajes; tu versión anterior queda en `manifest.prev.json`.

## 5. Los comandos, con ejemplos

### 5.1 `ask` — rutear y ejecutar

```bash
python3 cli.py ask "TAREA" [-m MODELO] [-c ARCHIVO]... [--llm] [--dry-run]
```

| Opción | Efecto |
|---|---|
| `-m`, `--model` | `auto` (por defecto) o forzar `claude` / `codex` / `antigravity`. |
| `-c`, `--context` | Agrega un archivo como contexto. Se puede repetir. |
| `--llm` | Clasifica la tarea con el manager en vez de solo con reglas. |
| `--dry-run` | Muestra la decisión y el orden de intento, **sin ejecutar** nada. |

**Ejemplos**

```bash
# Automático: el router elige
python3 cli.py ask "Redactá un saludo de una sola frase para un cliente nuevo."

# Forzar un modelo
python3 cli.py ask "Explicame qué hace este código" -m claude -c cli.py

# Con varios archivos de contexto
python3 cli.py ask "Compará estos dos documentos y resumí las diferencias" -c a.md -c b.md

# Contexto muy largo: el router lo manda al modelo de long_context
python3 cli.py ask "Resumí este documento largo" -c transcripcion.txt

# Ver qué haría, sin gastar cuota
python3 cli.py ask "Analizá los pros y contras de migrar a microservicios" --dry-run
```

El `--dry-run` real de ese último ejemplo:

```
Clasificación (reglas, manifiesto: sí): analysis×1
modelo   score  estado
claude    10.0  OK             analysis×1→10
antigravity 8.0 OK             analysis×1→8
codex      6.0  OK             analysis×1→6
→ elegido: claude
(dry-run) orden de intento: ['claude', 'antigravity', 'codex']
```

**Fallback automático.** Si el modelo elegido falla, prueba el siguiente (máximo 3 intentos):

| Situación | Qué hace el router |
|---|---|
| Rate limit (cuota agotada) | Pone ese modelo en *cooldown* 30 min y prueba el siguiente. |
| Sin login | Pone ese modelo en cooldown 60 min y prueba el siguiente. |
| Timeout u otro error | Prueba el siguiente. |

**Código de salida:** `0` si hubo respuesta, `1` si fallaron todos los intentos, `2` si pediste un modelo inexistente.

### 5.2 `route` — solo decidir

Igual que `ask --dry-run`, pero más corto. Útil para entender por qué elige lo que elige.

```bash
python3 cli.py route "Arreglá el bug de esta función Python que falla con un KeyError" --llm
```

```
Clasificación (manager claude, manifiesto: sí): debugging×3, coding×2
modelo   score  estado
codex     10.0  OK             debugging×3→10, coding×2→10
claude     8.0  OK             debugging×3→8, coding×2→8
antigravity 1.0 no disponible  ...
→ elegido: codex
```

Cómo leer la tabla: `debugging×3→10` significa *categoría debugging, peso 3, este modelo tiene puntaje 10 ahí*. El `score` es el promedio ponderado de todas las categorías detectadas.

### 5.3 `manifest` — ver y ajustar tus preferencias

```bash
python3 cli.py manifest show                    # ver el manifiesto actual
python3 cli.py manifest refine "TEXTO"          # cambiarlo con una instrucción
python3 cli.py manifest refine                  # modo interactivo
python3 cli.py manifest generate                # rehacerlo desde cero
python3 cli.py manifest path                    # dónde está el archivo
```

**Cambiar con una instrucción** (el caso más común):

```bash
python3 cli.py manifest refine "Para matemática prefiero Claude antes que Codex"
```

```
Cambios propuestos:
  math: codex > claude  →  claude > codex
Aplicado (versión anterior en /Users/mgobea/.ia-router/manifest.prev.json).
```

Otros ejemplos de instrucciones válidas:

```bash
python3 cli.py manifest refine "No quiero usar Antigravity para nada por ahora"
python3 cli.py manifest refine "Usá Codex para todo lo que tenga que ver con código y tests"
python3 cli.py manifest refine "Para investigación poné primero a Claude"
```

**Modo interactivo.** Sin texto, abre una conversación. Cada propuesta muestra el cambio y pide confirmación:

```
$ python3 cli.py manifest refine
(muestra el manifiesto)

Decime qué cambiar, en lenguaje natural. Enter vacío para terminar.
feedback> en investigación poné primero a Claude
Cambios propuestos:
  research: antigravity > claude > codex  →  claude > antigravity > codex
¿Aplicar? [S/n]: n          ← rechazado: no cambia nada
feedback> en investigación poné primero a Claude
Cambios propuestos:
  research: antigravity > claude > codex  →  claude > antigravity > codex
¿Aplicar? [S/n]: s          ← aceptado
Aplicado.
feedback>                   ← Enter vacío para terminar
```

Reglas del manifiesto:

- Guarda **solo la versión anterior** (`manifest.prev.json`) y un historial de motivos dentro del archivo (últimos 20 cambios).
- Si el manager devuelve algo inválido, **no cambia nada** y te lo avisa. Reformulá la instrucción.
- `generate` **sobrescribe** el manifiesto (conserva tus notas anteriores). Si querés volver atrás, copiá primero `manifest.prev.json`.

También podés editar `~/.ia-router/manifest.json` a mano. Formato mínimo de una categoría:

```json
"coding": { "prefer": ["codex", "claude", "antigravity"], "why": "mi preferencia" }
```

Para desactivar un modelo por completo, ponelo en la lista `"disabled"` del mismo archivo.

### 5.4 `doctor` — diagnóstico

```bash
python3 cli.py doctor            # rápido, gratis: ¿están instalados?
python3 cli.py doctor --probe    # real: ¿están logueados? ¿qué latencia tienen?
```

### 5.5 `stats` — qué pasó realmente

```bash
python3 cli.py stats
```

```
modelo   corridas  éxito seg. medio rate limits  auth
claude          4   100%        5.6           0     0
antigravity     2   100%        7.3           0     0
codex           1   100%        8.0           0     0
```

Sale del log local (`~/.ia-router/log.jsonl`). El log **no guarda tus prompts**, solo modelo, éxito, duración y errores.

### 5.6 `reset-cooldowns`

Si un modelo quedó en cooldown y ya lo arreglaste (volviste a loguearte, o pasó el límite de cuota):

```bash
python3 cli.py reset-cooldowns
```

---

## 6. Usarlo desde Claude Code (MCP)

Permite que Claude delegue subtareas a los otros modelos dentro de una conversación.

**Registrar el servidor** (una sola vez):

```bash
claude mcp add ia-router -- python3 ~/Documents/ia-suscription-router/cli.py mcp
```

**Herramientas que verá Claude:**

| Herramienta | Qué hace |
|---|---|
| `route_task` | Decide qué modelo conviene, sin ejecutar. Devuelve el ranking con motivos. |
| `ask_model` | Ejecuta una tarea en otro modelo. `model` puede ser `auto` o un modelo concreto. Acepta `context_files` y `timeout_seconds`. |
| `list_models` | Lista modelos, si están instalados y si están en cooldown. |

**Ejemplos de prompts para Claude Code:**

> *"Pedile a Antigravity que resuma estos 3 archivos y a Codex que revise el bug; integrá ambas respuestas."*

> *"Usá `route_task` para decirme qué modelo conviene para migrar esta base de datos."*

> *"Mostrame con `list_models` cuáles están disponibles."*

El servidor MCP **usa el mismo manifiesto** que el CLI. Si tardan mucho las tareas, subí el timeout de herramientas MCP de tu cliente (variable `MCP_TOOL_TIMEOUT` en Claude Code).

---

## 7. Dónde se guarda todo

| Qué | Dónde |
|---|---|
| Manifiesto | `~/.ia-router/manifest.json` |
| Versión anterior del manifiesto | `~/.ia-router/manifest.prev.json` |
| Cooldowns | `~/.ia-router/state.json` |
| Log de ejecuciones (sin prompts) | `~/.ia-router/log.jsonl` |
| Modelos, comandos y timeouts | `models.json` (en el repo) |

Para empezar de cero: `rm -r ~/.ia-router` (borra manifiesto, cooldowns y log).

---

## 8. Configuración avanzada

### 8.1 Variables de entorno

| Variable | Efecto | Ejemplo |
|---|---|---|
| `ROUTER_HOME` | Cambia la carpeta de estado (por defecto `~/.ia-router`). | `ROUTER_HOME=/tmp/prueba python3 cli.py doctor` |
| `ROUTER_MODELS` | Usa otro `models.json`. | `ROUTER_MODELS=~/mis-modelos.json python3 cli.py doctor` |
| `ROUTER_CMD_<MODELO>` | Reemplaza el comando de un modelo (lista JSON). `{prompt}` se sustituye por la tarea. | `ROUTER_CMD_CODEX='["codex","exec","{prompt}"]'` |

`ROUTER_HOME` es útil para probar sin tocar tu manifiesto real.

### 8.2 `models.json`

Cada modelo se define así:

```json
"claude": {
  "label": "Claude (CLI oficial: claude)",
  "cmd": ["claude", "-p", "{prompt}"],
  "cmd_stdin": ["claude", "-p"],
  "cheap_cmd": ["claude", "-p", "--model", "haiku", "{prompt}"],
  "timeout": 300,
  "strengths": { "general": 8, "coding": 9, "writing": 9 }
}
```

| Campo | Para qué sirve |
|---|---|
| `cmd` | Comando que ejecuta la tarea. `{prompt}` es la tarea. |
| `cmd_stdin` | Comando alternativo cuando el prompt supera 100.000 caracteres (viaja por stdin). Si falta, viaja por argumento. |
| `cheap_cmd` | Comando barato, usado **cuando este modelo actúa de manager**. |
| `timeout` | Segundos máximos por intento. |
| `strengths` | Puntajes 0–10 por categoría. Son **hipótesis iniciales**: cuando existe un manifiesto, este los reemplaza. |

Ajustes globales: `cooldown_minutes` (rate limit, 30), `auth_cooldown_minutes` (sin login, 60), `default_manager` (`claude`).

**Agregar un modelo nuevo:** sumá una entrada en `"models"` con su `cmd`, corré `python3 cli.py doctor --probe` para confirmar que funciona y luego `python3 cli.py manifest generate` para que el manager lo incluya.

### 8.3 Categorías de tareas

El router distingue estos tipos de tarea. Son los que aparecen en el manifiesto:

`coding`, `debugging`, `writing`, `analysis`, `data`, `research`, `math`, `multimodal`, `long_context`, `quick`.

Una tarea puede tener varias a la vez (por ejemplo `debugging×3, coding×2`). `long_context` se activa solo cuando tarea + archivos superan 30.000 caracteres (peso fuerte desde 100.000). `quick` se activa con palabras como "rápido", "breve" o "tl;dr", o con tareas muy cortas sin otra categoría.

---

## 9. Problemas comunes

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `doctor --probe` muestra `auth=missing` | El CLI está instalado pero sin sesión. | Iniciá sesión en ese CLI (ejecutalo sin argumentos). Después `python3 cli.py reset-cooldowns`. |
| `agy` o `gemini` dicen *"This client is no longer supported"* | Google dio de baja el Gemini CLI. | Usá `agy` (Antigravity) en su lugar. |
| `ningún modelo disponible` | Todos están sin instalar, desactivados o en cooldown. | `python3 cli.py doctor` y, si corresponde, `reset-cooldowns`. |
| Un modelo "no disponible" en la tabla | No instalado, desactivado en el manifiesto, o en cooldown. | Revisá `doctor` y la lista `disabled` del manifiesto. |
| Siempre elige el mismo modelo | Es lo que dice tu manifiesto. | `manifest show` y ajustalo con `manifest refine`. |
| El router eligió mal una tarea | La clasificación por reglas no la entendió. | Probá `--llm` para que clasifique el manager, o forzá con `-m`. |
| `manifest refine` dice que no devolvió un manifiesto válido | El manager respondió algo no parseable. | Reformulá la instrucción; no se cambió nada. |
| `Error: modelo desconocido` | Pasaste un `-m` que no existe en `models.json`. | Usá `auto` o uno de los modelos listados en el mensaje. |
| Un comando falla con *timeout* | La tarea tardó más que el `timeout` del modelo. | Subí `timeout` en `models.json`. |
| Una tarea tardó minutos y respondió bien | La Mac se durmió a mitad de la llamada. | Corré el chat con `caffeinate -is ia-router`: no se duerme sola mientras esté abierto, pero podés suspenderla a mano (botón de bloqueo o menú ). |
| Falla el flag de un CLI tras actualizarlo | Los CLIs cambian seguido sus opciones. | Revisá `<cli> --help` y ajustá `cmd` en `models.json`, o usá `ROUTER_CMD_<MODELO>`. |

---

## 10. Límites que conviene conocer

- **Es para uso personal.** Corre con tus suscripciones, a ritmo humano. Si algún día lo distribuís a terceros, revisá los términos de cada proveedor (Anthropic, por ejemplo, exige API key para productos de terceros).
- **Cada `ask` y cada `setup` gastan cuota real** de tus suscripciones. `route` y `--dry-run` no gastan.
- **`agy` no lee el prompt por stdin** en modo headless, así que los prompts muy grandes viajan por argumento.
- **Seguridad:** los CLIs corren en modo no interactivo con sus permisos por defecto. El router **no** activa flags de "permitir todo".
- **El manifiesto refleja preferencias, no mediciones propias.** Todavía no hay evals que lo calibren con resultados medidos.
- **Todavía no hay** sesiones (continuar una conversación), streaming de salida ni versión de escritorio.
