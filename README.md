# ia-router

Router que reparte tus tareas entre los **CLIs oficiales** de las IA que ya pagás (`claude`, `codex`, `agy` de Antigravity), **decidiendo con métricas objetivas** de portales respetados y no a ojo.

Se abre como `claude`: escribís una tarea y se rutea sola al mejor modelo según las métricas. Si querés, respondés unas preguntas sobre qué priorizás en cada tipo de tarea (precisión, velocidad o costo) y el ruteo se rearma.

- **Ya viene con métricas:** el software trae incluida la última foto de [Arena](https://arena.ai/leaderboard); rutea con datos desde el primer uso, sin red.
- **Actualizar es una acción tuya y se ve:** al iniciar, si las métricas tienen más de 7 días, te ofrece actualizarlas mostrando cada paso y **qué cambió en el ruteo**.
- **Sin dependencias externas:** solo la librería estándar de Python (3.9 o superior; los tests pasan en 3.9 y 3.14).
- **No toca tokens OAuth:** cada CLI usa su propio login y su propia suscripción. Nunca se activan flags de "permitir todo".
- **Por Mgobeaalcoba · [mgatc.com](https://mgatc.com)** — [GitHub](https://github.com/Mgobeaalcoba)

> **Guía de uso completa, con ejemplos y solución de problemas: [docs/USO.md](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/docs/USO.md).**
> Si sos un agente de IA o vas a contribuir: [AGENTS.md](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/AGENTS.md).

![Encabezado de ia-router en la terminal: logo, métricas de Arena y Artificial Analysis, modelos y cuadro de entrada](https://raw.githubusercontent.com/Mgobeaalcoba/ia-suscription-router/main/docs/img/ia-router-header.png)

## Dónde encontrarlo

| | |
|---|---|
| **Web** | [mgatc.com/recursos/ia-router](https://www.mgatc.com/recursos/ia-router/): qué es, casos de uso e instalación |
| **PyPI** | [pypi.org/project/ia-router](https://pypi.org/project/ia-router/): `pipx install ia-router` |
| **Homebrew** | [Mgobeaalcoba/homebrew-tap](https://github.com/Mgobeaalcoba/homebrew-tap): `brew install Mgobeaalcoba/tap/ia-router` |
| **Código** | [github.com/Mgobeaalcoba/ia-suscription-router](https://github.com/Mgobeaalcoba/ia-suscription-router) |
| **Guía de uso** | [docs/USO.md](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/docs/USO.md) |
| **Cambios** | [CHANGELOG.md](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/CHANGELOG.md) |
| **Problemas e ideas** | [Issues](https://github.com/Mgobeaalcoba/ia-suscription-router/issues) |
| **Licencia** | [Apache-2.0](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/LICENSE) · [NOTICE](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/NOTICE) · [cómo citarlo](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/CITATION.cff) |

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

Velocidad y costo van en escala logarítmica (2 puntos menos por cada duplicación frente al mejor de tus modelos). Una dimensión solo cuenta si hay dato para **todos** tus modelos. Detalle en [docs/USO.md](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/docs/USO.md#4-ruteo-por-métricas-objetivas).

## Instalación

Probado en macOS (debería funcionar también en Linux), con Python 3.9 o superior. Hay dos formas de instalarlo, elegí una:

### Opción A · Homebrew (macOS)

```bash
brew install Mgobeaalcoba/tap/ia-router
```

Equivale a `brew tap Mgobeaalcoba/tap && brew install ia-router`. Homebrew instala Python si hace falta.

### Opción B · pip o pipx (cualquier sistema con Python 3.9+)

```bash
pipx install ia-router                  # recomendado: lo instala aislado y deja el comando `ia-router` en tu PATH
python3 -m pip install --user ia-router # alternativa con pip
```

Si no tenés `pipx`: `brew install pipx && pipx ensurepath` (macOS) o `python3 -m pip install --user pipx && python3 -m pipx ensurepath`. Después abrí una terminal nueva.

### Verificar que quedó bien

```bash
ia-router --version     # ia-router 0.2.0
ia-router doctor        # qué CLIs tenés instalados y qué modelo usa cada uno (no gasta cuota)
```

Necesitás **al menos uno** de los CLIs oficiales instalado y logueado (`claude`, `codex` o `agy`); el router no los instala por vos.

### Actualizar y desinstalar

| | Homebrew | pipx | pip |
|---|---|---|---|
| Actualizar | `brew upgrade ia-router` | `pipx upgrade ia-router` | `python3 -m pip install -U ia-router` |
| Desinstalar | `brew uninstall ia-router` | `pipx uninstall ia-router` | `python3 -m pip uninstall ia-router` |

Desinstalar **no borra tus datos** (`~/.ia-router`: métricas descargadas, prioridades, historial). Para empezar de cero: `rm -r ~/.ia-router`.

Si aparece `ia-router: command not found` después de instalar con pip o pipx, falta el directorio de scripts en tu `PATH` (normalmente `~/.local/bin`): `pipx ensurepath` y abrí una terminal nueva.

Desde el código fuente ([Mgobeaalcoba/ia-suscription-router](https://github.com/Mgobeaalcoba/ia-suscription-router)): `git clone https://github.com/Mgobeaalcoba/ia-suscription-router.git && cd ia-suscription-router && python3 cli.py`.

## Primer uso

```bash
ia-router            # abre el chat
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
ia-router metrics refresh        # lee ~11 páginas públicas de arena.ai (≈ 1 minuto)
```

Arena aporta la precisión. Para sumar **velocidad y costo** (y poder priorizarlos) usá la API de [Artificial Analysis](https://artificialanalysis.ai/), que tiene un plan **gratuito** (1.000 pedidos por día):

```bash
mkdir -p ~/.ia-router && cp .env.example ~/.ia-router/.env      # instalado con pip/brew (o `.env` en la carpeta del repo si usás un clon)
# editá ese archivo y pegá tu clave:   ARTIFICIAL_ANALYSIS_API_KEY=tu_clave
ia-router metrics refresh        # ahora también trae velocidad, precio y benchmarks
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
| `ia_router/cli.py` · `cli.py` | Subcomandos (el comando instalado es `ia-router`); `cli.py` es un atajo desde un clon. |
| `pyproject.toml` · `packaging/homebrew/` | Paquete para PyPI y plantilla de la fórmula de Homebrew. |
| `LICENSE` · `NOTICE` · `CITATION.cff` | Apache-2.0, atribución obligatoria y cómo citarlo. |
| `ia_router/data/models.json` | Modelos: comandos, flags de uso, timeouts y estimaciones de último recurso. |
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
| `tests/` | 266 tests y CLIs falsos (`tests/fake_bin`). |

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

## Contribuir

Las contribuciones son bienvenidas: leé [CONTRIBUTING.md](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/CONTRIBUTING.md) (entorno, reglas, y firma de commits con el [DCO](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/DCO): `git commit -s`) y [AGENTS.md](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/AGENTS.md).

## Licencia y cómo citar

[Apache License 2.0](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/LICENSE): podés usarlo, modificarlo y redistribuirlo, **conservando el archivo [NOTICE](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/NOTICE) y la atribución a su autor** (sección 4 de la licencia). Los datos de terceros conservan sus propias licencias (ver más arriba).

Para citarlo en un trabajo: [CITATION.cff](https://github.com/Mgobeaalcoba/ia-suscription-router/blob/main/CITATION.cff) (GitHub lo muestra como *Cite this repository*).

> ia-router, por Mgobeaalcoba (2026). https://www.mgatc.com/recursos/ia-router/
