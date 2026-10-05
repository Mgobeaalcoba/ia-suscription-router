# ia-router

Router que reparte tus tareas entre los **CLIs oficiales** de las IA que ya pagás (`claude`, `codex`, `agy` de Antigravity) para aprovechar tus suscripciones, **decidiendo con métricas objetivas** y no a ojo.

Se abre como `claude`: escribís una tarea y se rutea sola al mejor modelo; o le hablás de cómo configurarlo y se ajusta.

- **Sin dependencias externas:** solo la librería estándar de Python (3.9 o superior; los tests pasan en 3.9 y 3.14).
- **No toca tokens OAuth:** cada CLI usa su propio login y su propia suscripción. Nunca se activan flags de "permitir todo".
- **Por Mgobeaalcoba · [mgatc.com](https://mgatc.com)** — [GitHub](https://github.com/Mgobeaalcoba)

> **Guía de uso completa, con ejemplos y solución de problemas: [docs/USO.md](docs/USO.md).**
> Si sos un agente de IA o vas a contribuir: [AGENTS.md](AGENTS.md).

## Qué hace

| | |
|---|---|
| **Chat conversacional** | Caja de entrada propia con marco, historial, varias líneas y paleta de comandos `/`. Tareas, preferencias y consultas en lenguaje natural. |
| **Archivos arrastrados** | Arrastrá archivos a la terminal: se muestran como rutas limpias. El texto se anexa como contexto; imágenes y PDF se pasan por ruta a los modelos que pueden abrirlos. |
| **Modelo y tokens a la vista** | Cada respuesta muestra el proveedor, el **modelo exacto** y los tokens de entrada, caché y salida. |
| **Markdown interpretado** | Títulos, listas, tablas alineadas y bloques de código con colores, como un README en GitHub. |
| **Puntaje objetivo** | `calibrate` mide a tus modelos con **pruebas verificables por código** (sin modelo-juez); `benchmarks` suma Arena y Artificial Analysis como prior; la velocidad, la cuota y la confiabilidad salen de tu uso real. |
| **Tus criterios** | `criteria`: preguntas de opción múltiple (selector ↑/↓ + Enter) que fijan los pesos con los que se combinan las métricas. |
| **Manifiesto** | Un modelo barato que elegís (el *manager*) arma tus preferencias por tipo de tarea y las ajustás conversando. Manda sobre todo lo demás. |
| **Fallback y MCP** | Si hay rate limit o falta de login prueba el siguiente; también funciona como servidor MCP para Claude Code. |

## Primer uso

```bash
python3 cli.py            # abre el chat
ln -s "$PWD/cli.py" ~/.local/bin/ia-router   # opcional: para abrirlo como `ia-router` desde cualquier carpeta
```

Al abrir por primera vez te ofrece armar el manifiesto. Después:

```
ia ❯ Arreglá este bug en mi función Python        ← se rutea sola
ia ❯ Usá Codex para todo lo de código             ← actualiza tu manifiesto (con confirmación)
ia ❯ /calibrate                                   ← mide el acierto de cada modelo
ia ❯ /criteria                                    ← ajustás tus criterios de ruteo
```

Con comandos (para scripts):

```bash
python3 cli.py setup                 # elige el manager, prueba cada CLI y arma el manifiesto
python3 cli.py ask "Escribí una función Python que sume una lista"
python3 cli.py calibrate             # mide a tus modelos (pide confirmación antes de gastar cuota)
python3 cli.py benchmarks refresh    # Arena (y Artificial Analysis si tenés clave gratuita)
python3 cli.py scores coding         # puntaje por modelo con el desglose
python3 cli.py criteria              # cuestionario de criterios
```

## Comandos

| Comando | Qué hace |
|---|---|
| *(sin argumentos)* / `chat` | Modo conversacional. |
| `setup` | Primer uso: manager + sonda + manifiesto. |
| `manifest show\|generate\|refine\|path` | Ver, regenerar o ajustar el manifiesto. |
| `doctor [--probe]` | CLIs instalados; con `--probe`, login, latencia y versión reales. |
| `route "tarea"` | Muestra qué modelo elegiría, sin ejecutar. |
| `ask "tarea" [-m modelo] [-c archivo] [--llm] [--dry-run]` | Rutea y ejecuta, con fallback. |
| `calibrate [--full] [--models a,b] [--yes]` | Pruebas verificables por modelo; muestra el costo y pide confirmación. |
| `benchmarks [refresh] [--force]` | Métricas externas (Arena; Artificial Analysis con clave gratuita). |
| `scores [categoría]` | Puntaje objetivo por modelo y categoría. |
| `criteria` | Cuestionario de criterios de ruteo. |
| `stats` | Éxito, latencia, rate limits y tokens por modelo. |
| `mcp` | Servidor MCP (stdio). |
| `reset-cooldowns` | Limpia cooldowns por rate limit o auth. |

## Cómo decide

| Paso | Qué hace |
|---|---|
| 1. Clasificar | Reglas ES/EN detectan categorías con peso (coding, debugging, writing, analysis, data, research, math, multimodal, long_context, quick). Con `--llm` clasifica el manager. Una imagen o PDF adjunto suma `multimodal`. |
| 2. Puntuar | `puntaje = Σ peso × valor` con calidad, velocidad, cuota y confiabilidad. La calidad es lo medido por `calibrate` mezclado con un prior (Arena / Artificial Analysis, o la estimación de `models.json`). Los pesos salen de tus criterios. |
| 3. Precedencia | **Manifiesto** (preferencias explícitas) > **criterios** > **medido** > **prior externo** > **estimado**. |
| 4. Filtrar | Se descartan modelos no instalados, desactivados, en cooldown, o que no pueden abrir archivos si hay adjuntos que no son texto. |
| 5. Desempatar | Entre modelos a menos de 0,5 puntos, por calidad, velocidad o cuota (según tus criterios). |
| 6. Ejecutar | Lanza el CLI del mejor. Rate limit → cooldown 30 min; sin login → 60 min; prueba el siguiente (máx. 3). |
| 7. Registrar | Log en `~/.ia-router/log.jsonl` (sin el prompt) con modelo, tokens y duración. |

## Archivos

| Archivo | Rol |
|---|---|
| `cli.py` | Punto de entrada y subcomandos. |
| `models.json` | Modelos: comandos, flags de uso/calibración, timeouts, estimaciones iniciales. |
| `ia_router/chat.py` | Chat: intención, historial, comandos `/`. |
| `ia_router/editor.py` | Caja de entrada: editor de línea, paste con corchetes, paleta de comandos. |
| `ia_router/select.py` · `criteria.py` | Selector de opciones y cuestionario de criterios. |
| `ia_router/attachments.py` | Reconocimiento y clasificación de archivos arrastrados. |
| `ia_router/render.py` · `banner.py` | Markdown interpretado y encabezado. |
| `ia_router/core.py` · `router.py` | Orquestación, clasificación y ranking. |
| `ia_router/adapters.py` | Ejecución de CLIs: modelo y tokens, rate limit, login. |
| `ia_router/calibrate.py` | Tareas verificables, correctores y métricas locales. |
| `ia_router/scoring.py` | Puntaje objetivo, pesos del perfil y desempate. |
| `ia_router/external.py` | Arena y Artificial Analysis como prior de calidad. |
| `ia_router/manifest.py` | Manifiesto: sonda, generación y refinado con el manager. |
| `ia_router/state.py` | Cooldowns, log y estadísticas. |
| `ia_router/mcp_server.py` | Servidor MCP stdio. |
| `tests/` | 251 tests y CLIs falsos (`tests/fake_bin`). |

## Usarlo desde Claude Code (MCP)

```bash
claude mcp add ia-router -- python3 ~/Documents/ia-suscription-router/cli.py mcp
```

Herramientas: `route_task`, `ask_model`, `list_models`.

## Límites conocidos

| Tema | Detalle |
|---|---|
| Modelos de frontera | `calibrate` satura varias categorías (todos aciertan todo); ahí decide la velocidad. `research` y `quick` no tienen forma objetiva de corregirse y quedan estimadas. |
| Arena | Mide preferencia humana, no respuestas correctas, y publica variantes por nivel de esfuerzo que pueden no coincidir con el de tu CLI (se marca como aproximado). Lee las páginas públicas de arena.ai: si cambian de formato, lo avisa y se usan las estimaciones. |
| Artificial Analysis | Opcional (clave gratuita). Implementado con el formato de su documentación y tests con datos de ejemplo; **no verificado contra la API real**. |
| Antigravity | `agy -p` no lee el prompt por stdin ni abre archivos por ruta en modo no interactivo, y falla si pide una herramienta que no puede autorizar. |
| Código de los modelos | `calibrate` ejecuta en tu máquina el código que devuelven tus CLIs, aislado y con timeout. |
| Términos de uso | Pensado para uso personal a ritmo humano. Si lo distribuís a terceros, revisá los términos de cada proveedor (Anthropic exige API key para productos de terceros). |
| Chat | El historial recordado son los últimos turnos y no se guarda al salir; no hay streaming de respuestas. |

## Datos de terceros

Las métricas externas se muestran con su atribución: **Arena** ([arena.ai](https://arena.ai), dataset `leaderboard-dataset`, CC BY 4.0) y **[Artificial Analysis](https://artificialanalysis.ai/)**.

## Licencia

Por definir.
