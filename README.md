# ia-router

Router que reparte tus tareas entre los **CLIs oficiales** de las IA que ya pagás (`claude`, `codex`, `agy` de Antigravity), **decidiendo con métricas objetivas** de portales respetados y no a ojo.

Se abre como `claude`: escribís una tarea y se rutea sola al mejor modelo según las métricas. Si querés, respondés unas preguntas sobre qué priorizás en cada tipo de tarea (precisión, velocidad o costo) y el ruteo se rearma.

- **Ya viene con métricas:** el software trae incluida la última foto de [Arena](https://arena.ai/leaderboard); rutea con datos desde el primer uso, sin red.
- **Actualizar es una acción tuya y se ve:** al iniciar, si las métricas tienen más de 7 días, te ofrece actualizarlas mostrando cada paso y **qué cambió en el ruteo**.
- **Sin dependencias externas:** solo la librería estándar de Python (3.9 o superior; los tests pasan en 3.9 y 3.14).
- **No toca tokens OAuth:** cada CLI usa su propio login y su propia suscripción. Nunca se activan flags de "permitir todo".
- **Por Mgobeaalcoba · [mgatc.com](https://mgatc.com)** — [GitHub](https://github.com/Mgobeaalcoba)

> **Guía de uso completa, con ejemplos y solución de problemas: [docs/USO.md](docs/USO.md).**
> Si sos un agente de IA o vas a contribuir: [AGENTS.md](AGENTS.md).

## Cómo decide

```
tu tarea ──► clasificar ──► puntaje por modelo ──► elegir el mejor ──► ejecutar su CLI
            (por reglas)    (métricas + tus         (con fallback si    (con tu login
                             prioridades)            hay rate limit)     y tu cuota)
```

| Dimensión | De dónde sale |
|---|---|
| **Precisión** | **Arena**: Elo por categoría (coding, hard prompts, math, escritura, contexto largo, visión…) con margen de error. Las diferencias dentro del margen no premian a nadie. |
| **Velocidad** | **Artificial Analysis** (tokens/s). Requiere su clave gratuita. |
| **Costo** | Precio por millón de tokens (Artificial Analysis o Arena): proxy del consumo de cuota. |

Velocidad y costo van en escala logarítmica (2 puntos menos por cada duplicación frente al mejor de tus modelos). Una dimensión solo cuenta si hay dato para **todos** tus modelos. Detalle en [docs/USO.md](docs/USO.md#4-ruteo-por-métricas-objetivas).

## Primer uso

```bash
python3 cli.py            # abre el chat
ln -s "$PWD/cli.py" ~/.local/bin/ia-router   # opcional: abrirlo como `ia-router` desde cualquier carpeta
```

Al abrir te pregunta (y siempre antes de gastar algo): qué modelo usa cada CLI (una consulta mínima a cada uno, solo la primera vez), si querés actualizar las métricas si están viejas, y, una vez, si querés responder las preguntas de prioridades.

```
ia ❯ Arreglá este bug en mi función Python        ← se rutea sola
ia ❯ /scores coding                                ← qué elige el router y por qué
ia ❯ /priorities                                   ← qué priorizás en cada tipo de tarea
ia ❯ /metrics refresh                              ← actualizar las métricas (con visibilidad)
```

## Actualizar las métricas y activar velocidad/costo (`.env`)

```bash
python3 cli.py metrics refresh        # lee ~11 páginas públicas de arena.ai (≈ 1 minuto)
```

Arena aporta la precisión. Para sumar **velocidad y costo** (y poder priorizarlos) usá la API de [Artificial Analysis](https://artificialanalysis.ai/), que tiene un plan **gratuito** (1.000 pedidos por día):

```bash
cp .env.example .env
# editá .env y pegá tu clave:   ARTIFICIAL_ANALYSIS_API_KEY=tu_clave
python3 cli.py metrics refresh        # ahora también trae velocidad, precio y benchmarks
```

El `.env` **nunca se sube a git** (está en `.gitignore`); `.env.example` sí. Una variable ya definida en tu entorno tiene prioridad sobre el archivo. Artificial Analysis pide atribución: el router la muestra cada vez que usa sus datos.

## Comandos

| Comando | Qué hace |
|---|---|
| *(sin argumentos)* / `chat` | Modo conversacional. |
| `ask "tarea" [-m modelo] [-c archivo] [--dry-run]` | Rutea y ejecuta, con fallback. |
| `route "tarea"` | Muestra qué modelo elegiría, sin ejecutar. |
| `scores [categoría]` | Puntaje por modelo y categoría; con una categoría, el desglose. |
| `metrics [refresh] [--force]` | De dónde salen los datos y con qué entrada se emparejó cada modelo; `refresh` los actualiza. |
| `priorities` | Preguntas: qué priorizás en cada tipo de tarea. |
| `doctor [--probe]` | CLIs instalados y qué modelo usa cada uno; con `--probe`, login y latencia reales. |
| `stats` | Éxito, latencia, rate limits y tokens por modelo. |
| `mcp` | Servidor MCP (stdio). |
| `reset-cooldowns` | Limpia cooldowns por rate limit o auth. |

También: caja de entrada propia con historial y varias líneas, **archivos arrastrados** (texto como contexto; imágenes y PDF por ruta), cada respuesta con el **modelo exacto y los tokens**, y markdown interpretado como un README en GitHub.

## Archivos

| Archivo | Rol |
|---|---|
| `cli.py` | Punto de entrada y subcomandos. |
| `models.json` | Modelos: comandos, flags de uso, timeouts y estimaciones de último recurso. |
| `.env.example` | Variables opcionales (clave de Artificial Analysis). Copiar a `.env`. |
| `ia_router/metrics.py` | Arena y Artificial Analysis: descarga, emparejamiento por modelo real y valores 0-10. |
| `ia_router/data/arena.json` | Foto de Arena incluida en el software (CC BY 4.0). |
| `ia_router/scoring.py` · `priorities.py` | Puntaje por categoría, pesos y cuestionario de prioridades. |
| `ia_router/core.py` · `router.py` | Orquestación, clasificación y ranking. |
| `ia_router/adapters.py` · `probe.py` | Ejecución de CLIs (modelo y tokens, rate limit, login) y sonda. |
| `ia_router/chat.py` · `editor.py` · `select.py` | Chat, caja de entrada y selector de opciones. |
| `ia_router/attachments.py` · `render.py` · `banner.py` | Archivos arrastrados, markdown interpretado y encabezado. |
| `ia_router/envfile.py` · `state.py` · `mcp_server.py` | Lector de `.env`, estado y log, servidor MCP. |
| `tools/update_snapshot.py` | Para quien mantiene el repo: regenera la foto de Arena antes de publicar. |
| `tests/` | 248 tests y CLIs falsos (`tests/fake_bin`). |

## Usarlo desde Claude Code (MCP)

```bash
claude mcp add ia-router -- python3 ~/Documents/ia-suscription-router/cli.py mcp
```

Herramientas: `route_task`, `ask_model`, `list_models`.

## Límites conocidos

| Tema | Detalle |
|---|---|
| Arena | Mide preferencia humana, no respuestas correctas, y publica variantes por nivel de esfuerzo que pueden no coincidir con el de tu CLI (se marca como aproximado). Lee páginas públicas de arena.ai: si cambian de formato, lo avisa y sigue con lo que tenía. |
| Modelos de frontera | Las diferencias de precisión suelen caer dentro del margen de error; ahí desempatan velocidad y costo, que requieren la clave de Artificial Analysis. |
| Artificial Analysis | Verificado contra su API real. No publica todos los índices para todos los modelos: el router usa los benchmarks que cubren a los tuyos. Si tu CLI no informa su nivel de esfuerzo, elige el habitual y lo marca como aproximado. |
| Costo | Es el precio de lista por token: un proxy del consumo de cuota, no tu cuota real. |
| Antigravity | `agy -p` no lee el prompt por stdin ni abre archivos por ruta en modo no interactivo, y falla si pide una herramienta que no puede autorizar. |
| Términos de uso | Pensado para uso personal a ritmo humano. Si lo distribuís a terceros, revisá los términos de cada proveedor (Anthropic exige API key para productos de terceros). |
| Chat | El historial recordado son los últimos turnos y no se guarda al salir; no hay streaming de respuestas. |

## Datos de terceros

Las métricas se muestran con su atribución: **Arena** ([arena.ai](https://arena.ai), dataset `leaderboard-dataset`, CC BY 4.0) y **[Artificial Analysis](https://artificialanalysis.ai/)**.

## Licencia

Por definir.
