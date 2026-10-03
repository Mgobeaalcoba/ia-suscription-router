# ia-suscription-router

Router que reparte tus tareas entre los **CLIs oficiales** de las IA que ya pagás (`claude`, `codex`, `agy` de Antigravity) para aprovechar tus suscripciones.

La idea central es el **manifiesto**: un modelo barato que elegís vos (el *manager*) arma un manifiesto de qué modelo conviene para cada tipo de tarea, a partir de **datos objetivos** (CLIs instalados, login, latencia medida, historial de éxito y rate limits) y de **tus preferencias** en lenguaje natural. Después lo ajustás conversando con él.

- Sin dependencias externas: solo Python 3.9+ (stdlib).
- **No toca tokens OAuth**: cada CLI usa su propio login y su propia suscripción.
- Estado: MVP CLI. Verificado con los CLIs reales (claude 2.1.288, codex 0.160.0, agy 1.2.16) 20 tests con CLIs simulados, instalación desde cero, MCP (alta, baja y llamadas reales desde Claude) y `manifest refine` interactivo. Versión de escritorio: pendiente.

> **Guía de uso completa, con ejemplos y solución de problemas: [docs/USO.md](docs/USO.md).**

## Primer uso

```bash
python3 cli.py setup        # elegís el manager, prueba cada CLI y arma el manifiesto
python3 cli.py doctor --probe
python3 cli.py ask "Escribí una función Python que sume una lista"   # rutea y ejecuta
```

`setup` hace una llamada mínima a cada CLI (consume una pizca de cuota). Podés darle tus preferencias:

```bash
python3 cli.py setup --manager claude --notes "Codex para código, Claude para escribir, Antigravity para contexto largo"
```

## Manifiesto (iterable)

```bash
python3 cli.py manifest show
python3 cli.py manifest refine "Para matemática prefiero Claude"   # aplica el cambio y guarda la versión anterior
python3 cli.py manifest refine                                      # modo interactivo: proponer, ver diff, aceptar
python3 cli.py manifest generate                                    # rearmarlo desde cero
```

Vive en `~/.ia-router/manifest.json` (versión anterior en `manifest.prev.json`, historial de cambios dentro). Cada categoría tiene un orden de preferencia (`prefer`) y un motivo (`why`). Si el manager no responde un JSON válido, el router cae a un borrador derivado de `models.json`; nunca se queda sin manifiesto.

## Comandos

| Comando | Qué hace |
|---|---|
| `setup` | Primer uso: manager + sonda + manifiesto. |
| `manifest show\|generate\|refine\|path` | Ver, regenerar o ajustar el manifiesto. |
| `doctor [--probe]` | CLIs instalados; con `--probe`, login, latencia y versión reales. |
| `route "tarea"` | Muestra qué modelo elegiría, sin ejecutar. |
| `ask "tarea" [-m modelo] [-c archivo] [--llm] [--dry-run]` | Rutea y ejecuta, con fallback si hay rate limit o falta de login. |
| `stats` | Éxito, latencia y rate limits por modelo (del log). |
| `mcp` | Servidor MCP (stdio). |
| `reset-cooldowns` | Limpia cooldowns por rate limit o auth. |

## Usarlo desde Claude Code (MCP)

```bash
claude mcp add ia-router -- python3 ~/Documents/ia-suscription-router/cli.py mcp
```

Herramientas: `route_task`, `ask_model`, `list_models`. Si una tarea tarda mucho, subí `MCP_TOOL_TIMEOUT`.

## Cómo decide

| Paso | Qué hace |
|---|---|
| 1. Clasificar | Reglas ES/EN detectan categorías con peso (coding, debugging, writing, analysis, data, research, math, multimodal, long_context, quick). Con `--llm` clasifica el manager. |
| 2. Puntuar | Promedio ponderado por categoría. Con manifiesto, el orden de `prefer` define el puntaje (10, 8, 6...); sin manifiesto, las `strengths` de `models.json`. |
| 3. Filtrar | Modelos no instalados, desactivados en el manifiesto o en cooldown quedan al final. |
| 4. Ejecutar | Lanza el CLI del mejor modelo. Rate limit → cooldown 30 min. Sin login → cooldown 60 min. Prueba el siguiente (máx. 3). |
| 5. Registrar | Log en `~/.ia-router/log.jsonl` (sin el prompt) que alimenta `stats` y el manifiesto. |

## Archivos

| Archivo | Rol |
|---|---|
| `models.json` | Modelos: comandos, `cheap_cmd` (modo barato del manager), timeouts y `strengths` iniciales (hipótesis). |
| `ia_router/manifest.py` | Manifiesto: sonda, generación y refinado con el manager, aplicación al router. |
| `ia_router/router.py` | Clasificación y ranking. |
| `ia_router/adapters.py` | Ejecución de CLIs; detección de rate limit y de falta de login. |
| `ia_router/core.py` | Orquestación, manager y fallback. |
| `ia_router/state.py` | Cooldowns, log y estadísticas. |
| `ia_router/mcp_server.py` | Servidor MCP stdio. |
| `tests/` | Tests y CLIs falsos (`tests/fake_bin`). |

## Antigravity y Gemini CLI

Google dio de baja el Gemini CLI para cuentas individuales y lo reemplazó por **Antigravity** (`agy`). Instalación: `brew install --cask antigravity-cli`; login: ejecutar `agy` una vez en una terminal. `agy -p` funciona por subproceso, pero **no lee el prompt por stdin** en modo headless (necesitaría permiso de shell), así que los prompts grandes viajan por argumento. No se usa `--dangerously-skip-permissions`.

## Límites conocidos

| Tema | Detalle |
|---|---|
| Modelo barato de agy | `gemini-3.8-flash-low` funciona hoy; los nombres cambian seguido (`agy models`). |
| Codex como manager | No tiene `cheap_cmd`: usa su modelo por defecto. |
| Calidad del ruteo | Las reglas son una aproximación; el manifiesto mejora con tus preferencias, pero no con mediciones propias todavía (evals). |
| Latencia en `quick` | La sonda mide 1 llamada; diferencias de décimas de segundo son ruido. |
| Términos de uso | Pensado para uso personal a ritmo humano. Si lo distribuís a terceros, revisá los términos de cada proveedor (Anthropic exige API key para productos de terceros). |
| Permisos | Los CLIs corren en modo no interactivo con sus permisos por defecto; no se habilitan flags de "permitir todo". |

## Próximos pasos

1. `evals/`: tareas propias puntuadas por modelo, para alimentar el manifiesto con datos y no solo con preferencias.
2. Sesiones (continuar conversación) y streaming de salida.
3. Quota tracker por ventana de tiempo, no solo cooldown reactivo.
4. Versión de escritorio sobre `ia_router` (la lógica ya está separada de `cli.py`).
