# AGENTS.md

Guía para agentes de IA (Claude Code, Codex, Antigravity, etc.) y personas que contribuyen a **ia-router**. Leela entera antes de tocar código.

## Qué es

Un router en Python que reparte tareas entre los **CLIs oficiales** de IA que el usuario ya paga (`claude`, `codex`, `agy`), decidiendo con **métricas objetivas** y los criterios del usuario. Se usa como chat (`python3 cli.py`) o con subcomandos. Visión general: [README.md](README.md). Uso detallado: [docs/USO.md](docs/USO.md).

## Comandos

```bash
python3 -m unittest discover -s tests       # toda la suite (~250 tests, ~20 s); debe terminar en OK
python3 -m unittest tests.test_scoring      # un archivo
/usr/bin/python3 -m unittest discover -s tests   # en macOS: Python 3.9 del sistema (el mínimo soportado)
python3 cli.py doctor                        # CLIs instalados (no gasta cuota)
ROUTER_HOME=/tmp/prueba python3 cli.py ...   # estado aislado: nunca pruebes sobre ~/.ia-router
```

No hay build ni linter configurados. No agregues dependencias.

## Reglas que no se negocian

1. **Solo librería estándar.** Cero dependencias externas (ni `rich`, `requests`, `pyarrow`…). Compatible con **Python 3.9**: nada de `match`, `X | Y` evaluado en runtime, `zip(strict=)`. Verificá con `/usr/bin/python3` en macOS.
2. **Nunca tocar tokens OAuth** ni leer credenciales de los CLIs. Cada CLI usa su propio login.
3. **Nunca activar flags de "permitir todo"** (`--dangerously-skip-permissions` y similares).
4. **Claves de API por stdin de `curl`, nunca por argv** (se verían en `ps`). Ver `external.http_get`. No loguear ni guardar claves en el repo.
5. **Los tests no usan red, no llaman a CLIs reales y no tocan `~/.ia-router`.** Usan `tests/fake_bin/*` (CLIs falsos que emulan el JSON real), `ROUTER_HOME` temporal y funciones `get`/`sleep` inyectables.
6. **La red y la cuota son decisiones del usuario.** Ni `calibrate` ni `benchmarks refresh` ni ninguna llamada real a un modelo se ejecutan solos ni en tests. Si necesitás verificar contra los servicios reales, avisá antes de gastar cuota y usá `ROUTER_HOME` aparte.
7. **Respetá los servicios públicos:** pedidos de a uno, con pausa, reintentos con espera ante 429 y caché (ver `external.py`).
8. **Textos de cara al usuario en español rioplatense** (vos); identificadores y código en inglés/español como el resto del archivo. Seguí el estilo del código vecino: comentarios escasos que explican el *por qué*.

## Mapa del código

| Módulo | Responsabilidad |
|---|---|
| `cli.py` | Subcomandos y entrada. Sin argumentos abre el chat. |
| `ia_router/core.py` | `load_config` (aplica puntaje y manifiesto), `route`, `ask` (fallback, adjuntos, log). |
| `ia_router/router.py` | Clasificación por reglas en categorías con peso y ranking. |
| `ia_router/adapters.py` | `run_cli`: arma el comando (flags de `usage`, `lean`, `add_dir`), ejecuta sin shell, interpreta el JSON de cada CLI (modelo y tokens), detecta rate limit y falta de login. |
| `ia_router/calibrate.py` | Tareas **verificables por código** agrupadas por categoría, correctores, ejecución en paralelo por CLI y `metrics.json`. |
| `ia_router/scoring.py` | `puntaje = Σ peso × valor` (calidad, velocidad, cuota, confiabilidad), perfil, desempate, tablas explicadas. |
| `ia_router/external.py` | Arena (páginas de arena.ai) y Artificial Analysis (API con clave) como **prior** de calidad. |
| `ia_router/criteria.py` · `select.py` | Cuestionario de criterios y selector ↑/↓ + Enter. |
| `ia_router/manifest.py` | Manifiesto de preferencias (manager LLM), sonda, refinado. |
| `ia_router/chat.py` | Chat: intención por reglas, historial, comandos `/`. |
| `ia_router/editor.py` | Caja de entrada: `Parser` (bytes → eventos), `State` (edición pura), `render_frame` (dibujo) y `LineEditor` (tty real). |
| `ia_router/attachments.py` | Rutas arrastradas: reconocer, normalizar, clasificar. |
| `ia_router/render.py` · `banner.py` | Markdown interpretado (ANSI, sin signos) y encabezado. |
| `ia_router/state.py` | Cooldowns, `log.jsonl`, estadísticas. |
| `ia_router/mcp_server.py` | Servidor MCP stdio. |

Patrón de diseño que conviene mantener: **lógica pura separada del I/O** (p. ej. `State` / `render_frame` / `SelectState` no tocan la terminal y se testean sin ella; el bucle real vive aparte).

## Cómo decide el router

Precedencia del puntaje: **manifiesto** (preferencias explícitas del usuario) > **criterios** (`profile.json`) > **medido** (`calibrate`, log) > **prior externo** (Arena / AA) > **estimado** (`strengths` de `models.json`).

- Si falta un dato objetivo, esa dimensión vale lo mismo que la calidad: **sin evidencia el puntaje es exactamente la estimación inicial**. No lo rompas.
- Una fuente externa solo cuenta para una categoría si cubre a **todos** los modelos activos (no se mezclan escalas).
- Una comparación relativa (velocidad, cuota) necesita datos de **al menos dos** modelos.
- Diferencias de Elo dentro del margen de error no premian a nadie.

## Trampas conocidas (ya nos mordieron)

- **El id real del modelo no siempre está en la salida.** Claude lo da en el JSON; **Codex** solo en el archivo de sesión (`~/.codex/sessions/**/rollout-*`), por eso no se usa `--ephemeral`; **Antigravity** en su log (`--log-file`).
- **Antigravity (`agy`) en modo no interactivo** no lee el prompt por stdin, no abre archivos por ruta y falla con "permission … auto-denied" si el modelo pide una herramienta. `models.json` lo marca `reads_files: false` y `calibrate` lo registra como "sin respuesta por permisos".
- **Claude necesita `--add-dir`** para leer adjuntos fuera de su carpeta de trabajo.
- **El Python de python.org en macOS no trae certificados SSL:** por eso las descargas usan `curl` y caen a `urllib` solo si no existe.
- **El dataset de Arena en Hugging Face** (`datasets-server`) devuelve 429 tras ~30 páginas: no uses ese camino; se leen las páginas por categoría de `arena.ai/leaderboard/...` (ver `external.arena_pages`). Arena publica variantes por esfuerzo (`-high`, `-xhigh`, `-max`) que pueden no ser las de tu CLI: se marca como aproximado.
- **Un benchmark que satura no informa.** Con modelos de frontera muchas categorías dan 100%; `scores` lo avisa. Antes de dar por buena una métrica, verificá que discrimina.
- **Promediar tokens** solo sobre corridas que tienen tokens registrados (`token_runs`), nunca sobre todas.
- **Tests con pty** (`PtySmokeTests`) son sensibles a archivos temporales de otras corridas en paralelo: comparan antes/después, no el estado absoluto.
- `pyte` (emulador de terminal) se usó **a mano** para ver pantallas reales; no es dependencia ni se importa en tests.

## Cómo extender

- **Agregar un modelo:** entrada en `models.json` (`cmd`, `usage` con su `parser` en `adapters.parse_usage`, `reads_files`, `calibration`) + `python3 cli.py doctor --probe`. Si su CLI devuelve JSON distinto, sumá un parser y su fixture en `tests/fake_bin`.
- **Agregar una categoría de tarea:** `router.PATTERNS`, `manifest.CATEGORIES` (se deriva), y los mapas `external.ARENA_MAP` / `AA_MAP`; si es verificable, un lote en `calibrate.py` con **solución de referencia** (los resultados esperados se calculan, no se escriben a mano) y autoverificación en `tests/test_scoring.py`.
- **Agregar una tarea de calibración:** debe ser verificable por código, traer su referencia y tener nivel fácil y difícil. Corré el autotest (referencia pasa, respuesta vacía falla, bug detectado).
- **Agregar una fuente de métricas:** una función `fetch_*` con `get` inyectable, un `match_*` por id real + esfuerzo, y su aporte a `derive_priors`. Con tests offline y atribución en `ATTRIBUTION`.

## Documentación (mantenela al día en el mismo cambio)

- [README.md](README.md): visión general, comandos, archivos, límites.
- [docs/USO.md](docs/USO.md): guía completa. **Las salidas que se muestran deben ser reales**: capturalas, no las inventes.
- Este archivo, si cambia una regla, el mapa o aparece una trampa nueva.
- Actualizá los conteos (tests, versiones) cuando cambien.

## Commits

Mensajes en español con prefijo `feat:`, `fix:`, `docs:` o `test:`, que expliquen el *por qué*. Un cambio coherente por commit. Hacer push o publicar cosas es decisión del dueño del repo.
