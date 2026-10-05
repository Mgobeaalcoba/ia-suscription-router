# AGENTS.md

Guía para agentes de IA (Claude Code, Codex, Antigravity, etc.) y personas que contribuyen a **ia-router**. Leela entera antes de tocar código.

## Qué es

Un router en Python que reparte tareas entre los **CLIs oficiales** de IA que el usuario ya paga (`claude`, `codex`, `agy`) con un **único ruteo basado en métricas objetivas** de portales respetados (Arena y, con clave gratuita, Artificial Analysis). El software trae incluida la última foto de Arena; al iniciar ofrece actualizarla con visibilidad y, opcionalmente, pregunta qué prioriza el usuario por tipo de tarea. Se usa como chat (`python3 cli.py`) o con subcomandos. Visión general: [README.md](README.md). Uso detallado: [docs/USO.md](docs/USO.md).

**Se decidió simplificar a propósito:** no hay manifiesto con "manager" conversacional, ni calibración con pruebas propias, ni configuración por lenguaje natural. No los reintroduzcas sin que el dueño del repo lo pida (siguen en el historial de git).

## Comandos

```bash
python3 -m unittest discover -s tests       # toda la suite (~248 tests, ~15 s); debe terminar en OK
python3 -m unittest tests.test_scoring      # un archivo
/usr/bin/python3 -m unittest discover -s tests   # en macOS: Python 3.9 del sistema (el mínimo soportado)
python3 cli.py doctor                        # CLIs instalados y qué modelo usa cada uno (no gasta cuota)
python3 cli.py scores                        # qué elige el router y por qué
ROUTER_HOME=/tmp/prueba python3 cli.py ...   # estado aislado: nunca pruebes sobre ~/.ia-router
python3 tools/update_snapshot.py             # MANTENEDORES: regenera ia_router/data/arena.json antes de publicar una versión
```

No hay build ni linter configurados. No agregues dependencias.

## Reglas que no se negocian

1. **Solo librería estándar.** Cero dependencias externas (ni `rich`, `requests`, `python-dotenv`, `pyarrow`…). Compatible con **Python 3.9**: nada de `match`, `X | Y` evaluado en runtime, `zip(strict=)`. Verificá con `/usr/bin/python3` en macOS.
2. **Nunca tocar tokens OAuth** ni leer credenciales de los CLIs. Cada CLI usa su propio login.
3. **Nunca activar flags de "permitir todo"** (`--dangerously-skip-permissions` y similares).
4. **Secretos: solo `.env` (ignorado por git) o el entorno.** El `.env` real **nunca se versiona ni se imprime**; se versiona `.env.example` (sin claves). La clave de Artificial Analysis viaja por **stdin de `curl`, nunca por argv** (se vería en `ps`; ver `metrics.http_get`). No la loguees. Los tests no deben leer ni escribir el `.env` real (parchean `envfile.REPO_ENV`).
5. **Los tests no usan red, no llaman a CLIs reales y no tocan `~/.ia-router`.** Usan `tests/fake_bin/*` (CLIs falsos que emulan el JSON real), `ROUTER_HOME` temporal, `tests/fixtures.py` (un leaderboard de juguete) y funciones `get`/`sleep` inyectables.
6. **La red y la cuota son decisiones del usuario.** Ninguna llamada real a un modelo ni la actualización de métricas se ejecutan solas ni en tests. El chat siempre **pregunta antes** de gastar algo (detectar modelos, actualizar). Si necesitás verificar contra los servicios reales, avisá antes de gastar cuota y usá `ROUTER_HOME` aparte.
7. **Respetá los servicios públicos:** pedidos de a uno, con pausa, reintentos con espera ante 429 y caché (ver `metrics.py`).
8. **Textos de cara al usuario en español rioplatense** (vos); identificadores y código como el resto del archivo. Comentarios escasos que explican el *por qué*.

## Mapa del código

| Módulo | Responsabilidad |
|---|---|
| `cli.py` | Subcomandos y entrada (carga `.env` primero). Sin argumentos abre el chat. |
| `ia_router/metrics.py` | **Fuente de verdad de las métricas.** Arena (páginas de arena.ai) y Artificial Analysis (API con clave): descarga con reintentos, foto incluida + caché del usuario (`active()`), emparejamiento por id real y nivel de esfuerzo, y valores 0-10 de precisión, velocidad y costo. |
| `ia_router/data/arena.json` | Foto de Arena incluida en el software (CC BY 4.0). La regenera `tools/update_snapshot.py`. |
| `ia_router/scoring.py` | `puntaje = Σ peso × valor`, perfil de prioridades, tablas explicadas, diferencias al actualizar (`refresh_and_report`). Aplica el puntaje a `strengths`. |
| `ia_router/priorities.py` · `select.py` | Cuestionario por tipo de tarea y selector ↑/↓ + Enter. |
| `ia_router/probe.py` | Sonda de los CLIs y detección del **modelo real** de cada uno. |
| `ia_router/core.py` | `load_config`, `route`, `ask` (fallback, adjuntos, log; registra el id real del modelo). |
| `ia_router/router.py` | Clasificación por reglas en categorías con peso y ranking. |
| `ia_router/adapters.py` | `run_cli`: arma el comando (flags de `usage`, `add_dir`), ejecuta sin shell, interpreta el JSON de cada CLI (modelo y tokens), detecta rate limit y falta de login. |
| `ia_router/chat.py` | Chat: intención por reglas, comandos `/`, **flujo de inicio** (`startup`: detectar modelos, ofrecer actualizar, ofrecer preguntas). |
| `ia_router/editor.py` | Caja de entrada: `Parser` (bytes → eventos), `State` (edición pura), `render_frame` (dibujo) y `LineEditor` (tty real). |
| `ia_router/attachments.py` | Rutas arrastradas: reconocer, normalizar, clasificar. |
| `ia_router/render.py` · `banner.py` | Markdown interpretado (ANSI, sin signos) y encabezado. |
| `ia_router/envfile.py` | Lector mínimo de `.env` (nunca pisa el entorno). |
| `ia_router/state.py` | Cooldowns, `log.jsonl`, ids vistos, banderas de inicio. |
| `ia_router/mcp_server.py` | Servidor MCP stdio. |

Patrón de diseño que conviene mantener: **lógica pura separada del I/O** (p. ej. `State` / `render_frame` / `SelectState` / `metrics.precision_values` no tocan la terminal ni la red y se testean sin ellas).

## Cómo decide el router

Cada modelo recibe, por categoría, un puntaje 0-10 = Σ peso × valor sobre tres dimensiones, **relativas a los modelos del usuario**:

- **Precisión**: Elo de Arena por categoría (+ índice de AA si hay clave). 10 = empata o gana al mejor; las diferencias dentro del margen de error no premian.
- **Velocidad** (solo AA) y **costo** (precio por millón de tokens, AA o Arena): escala **logarítmica** (−2 puntos por duplicación frente al mejor). *No la cambies por una proporcional:* tiene mucho más rango que el Elo y el modelo barato ganaría hasta con prioridad "precisión" (hay un test que lo impide).
- Los pesos salen de `profile.json` (respuestas de `/priorities`: precisión / equilibrado / velocidad / costo) o de los valores por defecto.

Invariantes:
- **Una dimensión solo cuenta si hay dato para TODOS los modelos activos**; si no, no pesa y los pesos se renormalizan. Nunca se mezclan escalas.
- Sin ids de modelo conocidos, o si Arena no cubre una categoría para todos, rige la **estimación de `models.json`** (último recurso, marcada `e`). Sin evidencia, nada se mueve.
- Se empareja por el **id real** del modelo que usa cada CLI, no por el nombre del CLI. Arena publica variantes por esfuerzo; si no coincide se usa la más cercana y se **marca como aproximada**.
- Rige la foto de Arena **más reciente** entre la incluida y la descargada por el usuario.
- Una comparación relativa necesita datos de al menos dos modelos.

## Trampas conocidas (ya nos mordieron)

- **El id real del modelo no siempre está en la salida.** Claude lo da en el JSON; **Codex** solo en el archivo de sesión (`~/.codex/sessions/**/rollout-*`), por eso no se usa `--ephemeral`; **Antigravity** en su log (`--log-file`).
- **Antigravity (`agy`) en modo no interactivo** no lee el prompt por stdin, no abre archivos por ruta y falla con "permission … auto-denied" si el modelo pide una herramienta. `models.json` lo marca `reads_files: false`.
- **Claude necesita `--add-dir`** para leer adjuntos fuera de su carpeta de trabajo.
- **El Python de python.org en macOS no trae certificados SSL:** por eso las descargas usan `curl` y caen a `urllib` solo si no existe.
- **El dataset de Arena en Hugging Face** (`datasets-server`) devuelve 429 tras ~30 páginas: no uses ese camino; se leen las páginas por categoría de `arena.ai/leaderboard/...` (ver `metrics.arena_pages`). Si el formato de esas páginas cambia, `parse_leaderboard` falla con un error claro.
- **Arena no publica el precio de todos los modelos** (p. ej. `gpt-6.1-sol`): el costo suele depender de Artificial Analysis.
- **Artificial Analysis: los campos reales difieren de la documentación.** Se verificó contra la API real (690 modelos): no publica todos los índices (`artificial_analysis_coding_index`, `math_index`) para todos los modelos, pero sí otros benchmarks (`lcr`, `hle`, `scicode`, `terminalbench_v4_0`…). `metrics.AA_MAP` lista varios candidatos por categoría y solo cuentan los que cubren a todos los modelos. Los nombres traen relleno (`Claude Sonnet 5.5 (Max, Default Fallback)`): `metrics.NOISE` lo descarta; si aparece una palabra nueva, agregala ahí con un test.
- **Promediar tokens** solo sobre corridas que tienen tokens registrados (`token_runs`).
- **Tests con pty** (`PtySmokeTests`, `test_select`) comparan antes/después y no el estado absoluto del sistema de archivos.
- `pyte` (emulador de terminal) se usó **a mano** para ver pantallas reales; no es dependencia ni se importa en tests.

## Cómo extender

- **Agregar un modelo:** entrada en `models.json` (`cmd`, `usage` con su `parser` en `adapters.parse_usage`, `reads_files`) + `python3 cli.py doctor --probe`. Si su CLI devuelve JSON distinto, sumá un parser y su fixture en `tests/fake_bin`.
- **Agregar una categoría de tarea:** `router.PATTERNS`, y los mapas `metrics.ARENA_MAP` / `AA_MAP`; si va a tener su propia pregunta, `scoring.GROUPS`.
- **Agregar una fuente de métricas:** una función `fetch_*` con `get` inyectable, un `match_*` por id real + esfuerzo, y su aporte a `precision_values` / `speed_values` / `cost_values`. Con tests offline, atribución en `metrics.ATTRIBUTION` y su variable en `.env.example`.
- **Publicar una versión:** `python3 tools/update_snapshot.py` (regenera la foto de Arena; ~1 minuto), correr los tests y commitear `ia_router/data/arena.json`.

## Documentación (mantenela al día en el mismo cambio)

- [README.md](README.md): visión general, comandos, `.env`, archivos, límites.
- [docs/USO.md](docs/USO.md): guía completa. **Las salidas que se muestran deben ser reales**: capturalas, no las inventes.
- `.env.example`: toda variable nueva va documentada ahí y en la tabla de variables de `docs/USO.md`.
- Este archivo, si cambia una regla, el mapa o aparece una trampa nueva. Actualizá los conteos (tests, versiones) cuando cambien.

## Commits

Mensajes en español con prefijo `feat:`, `fix:`, `docs:` o `test:`, que expliquen el *por qué*. Un cambio coherente por commit. Hacer push o publicar cosas es decisión del dueño del repo.
