# Guía de uso — ia-router

Esta guía explica **cómo se usa** el router, paso a paso y con ejemplos. Para una visión general, ver el [README](../README.md). Si sos un agente de IA o vas a contribuir: [AGENTS.md](../AGENTS.md).

Todos los ejemplos asumen que estás en la carpeta del repo:

```bash
cd ~/Documents/ia-suscription-router
```

Las salidas que se muestran son reales (capturadas con claude 2.1.288, codex 0.160.0 y agy 1.2.16, y las métricas de Arena del 2026-10-05). Los 245 tests pasan con Python 3.9 y 3.14.

---

## 1. Qué es y cómo funciona, en 30 segundos

El router **no es un modelo**. Decide a cuál de tus CLIs de IA (`claude`, `codex`, `agy`) mandarle cada tarea, y la ejecuta con **tu suscripción** de cada uno. Y lo decide con **métricas objetivas**, no a ojo.

```
tu tarea ──► clasificar ──► puntaje por modelo ──► elegir el mejor ──► ejecutar su CLI
            (por reglas)    (métricas objetivas    (con fallback si     (con tu login
                             + tus prioridades)     hay rate limit)      y tu cuota)
```

| Concepto | Qué es |
|---|---|
| **Métricas** | Datos publicados por portales respetados: **Arena** (precisión por categoría y precio) y, si tenés su clave gratuita, **Artificial Analysis** (velocidad, precio y benchmarks con respuesta correcta). El software **ya trae incluida la última foto de Arena**: rutea con métricas desde el primer uso. |
| **Prioridades** | Opcional. Respondés unas preguntas (¿en código qué priorizás: precisión, velocidad o costo?) y el ruteo se rearma con eso. |
| **Modelos** | Los CLIs oficiales instalados y logueados en tu máquina. Se definen en `models.json`. |

El router **nunca lee ni copia tus tokens**: cada CLI usa su propio login.

---

## 2. Requisitos e instalación

| Requisito | Cómo verificarlo |
|---|---|
| Python 3.9 o superior | `python3 --version` |
| Al menos un CLI instalado **y logueado** (idealmente los tres) | Ver tabla de abajo |
| `curl` (viene con macOS y Linux) | Para actualizar las métricas |
| Nada más | No hay dependencias externas (solo librería estándar) |

| CLI | Instalación | Login |
|---|---|---|
| `claude` (Claude Code) | Ver la documentación de Claude Code | Ejecutá `claude` una vez y seguí el login |
| `codex` (OpenAI) | Ver la documentación de Codex CLI | Ejecutá `codex` una vez y seguí el login |
| `agy` (Google Antigravity) | `brew install --cask antigravity-cli` | Ejecutá `agy` **sin argumentos** en una terminal y elegí iniciar sesión con Google |

> **Gemini CLI ya no se usa.** Google lo dio de baja para cuentas individuales (el login falla con *"This client is no longer supported"*). Su reemplazo es `agy`.

No hace falta instalar los tres, pero el router reparte entre los que haya. Para instalar el router no hay que hacer nada: es un clon del repo.

```bash
gh repo clone Mgobeaalcoba/ia-suscription-router ~/Documents/ia-suscription-router
cd ~/Documents/ia-suscription-router
python3 -m unittest discover -s tests    # opcional: debe terminar en OK
cp .env.example .env                     # opcional: para activar velocidad y costo (ver 4.2)
ln -s "$PWD/cli.py" ~/.local/bin/ia-router   # opcional: abrirlo como `ia-router` desde cualquier carpeta
```

---

## 3. Modo chat (la forma recomendada de usarlo)

```bash
ia-router      # o: python3 cli.py
```

### Qué pasa al iniciar

El encabezado muestra de dónde vienen las métricas que rigen el ruteo:

```
 ●─╮     ╦╔═╗   ╦═╗╔═╗╦ ╦╔╦╗╔═╗╦═╗
 ●─┼─◉   ║╠═╣ ─ ╠╦╝║ ║║ ║ ║ ║╣ ╠╦╝  v0.1.0
 ●─╯     ╩╩ ╩   ╩╚═╚═╝╚═╝ ╩ ╚═╝╩╚═  tus suscripciones de IA, ruteadas

 ╭──────────────────────────────────────────────────────────────────────────╮
 │ métricas Arena 2026-10-05 (incluida)                                     │
 │ modelos  ● claude   ● codex   ● antigravity                              │
 │ carpeta  ~/Documents/ia-suscription-router                               │
 ╰──────────────────────────────────────────── by Mgobeaalcoba · mgatc.com ─╯
```

Después, el chat resuelve **tres cosas, y siempre te pregunta antes de gastar algo**:

1. **¿Qué modelo usa cada CLI?** Las métricas son de *modelos* (por ejemplo `claude-sonnet-5-5`), no de CLIs. La primera vez ofrece una consulta mínima a cada CLI para saberlo (unos 12k tokens de entrada en codex y agy, pocos en claude). Después lo aprende solo de cada respuesta normal.
2. **¿Las métricas están al día?** Si tienen más de 7 días, te lo dice y ofrece actualizarlas (una vez por día como máximo). Ver 4.5.
3. **¿Querés ajustar las prioridades?** Una sola vez, y solo si hay algo que priorizar (velocidad o costo; ver 4.4). Por defecto responde que no; después queda `/priorities`.

### La caja de entrada y los archivos

Lo que escribís va en una caja con borde en degradé (como las de Claude Code, Codex y Antigravity), con un texto de ayuda cuando está vacía y, debajo, los atajos y la fecha de las métricas. Arriba a la derecha del borde ves el modelo: `auto` o el que fijaste con `/model`.

```
╭──────────────────────────────────────────────────────────────────── auto ─╮
│ ❯ "/Users/vos/Fotos/captura de pantalla.png" ¿qué error muestra?          │
╰────────────────────────────────────────────────────────────────────────────╯
  ⎘ captura de pantalla.png · imagen · 212.3 KB
  ⏎ enviar · ⌥⏎ o \⏎ nueva línea · / comandos · ⌃D salir        métricas 2026-10-05
```

**Archivos: arrastralos a la terminal.** La terminal pega la ruta y el router la deja limpia (absoluta, entre comillas si tiene espacios) y la resalta si el archivo existe; debajo aparece cada adjunto con su tipo y tamaño. También funciona escribir o pegar una ruta (`/ruta/archivo`, `~/archivo`, `./archivo`, `file://…`). Al enviar:

| Tipo | Qué pasa |
|---|---|
| Texto y código (`.txt`, `.md`, `.py`, `.csv`, `.json`… o cualquier archivo en UTF-8 de hasta 200 KB) | Su contenido se anexa a la tarea como contexto; sirve para cualquier modelo. |
| Imágenes, PDF, binarios y carpetas | Se pasa la ruta y el modelo la abre (Claude recibe acceso a esa carpeta con `--add-dir`; Codex la lee desde su sandbox de solo lectura). La tarea suma la categoría `multimodal` si hay imagen o PDF. |

**Antigravity no puede abrir archivos en modo no interactivo** (pide un permiso que no puede pedir), así que con adjuntos que no son texto el router lo descarta del ruteo (`models.json`: `"reads_files": false`). Si lo fijaste con `/model antigravity`, te avisa en lugar de fallar.

**Edición.** Flechas, `Inicio`/`Fin` (o `Ctrl-A`/`Ctrl-E`), `Alt-←/→` para saltar palabras, `Ctrl-W` borra la palabra, `Ctrl-U` hasta el inicio de la línea, `Ctrl-K` hasta el final. **Varias líneas:** `Alt-Enter`, `Ctrl-J` o `\` + Enter; un texto pegado con saltos de línea se respeta. **Historial:** `↑`/`↓` (se guarda entre sesiones en `~/.ia-router/history.jsonl`). **Comandos:** al escribir `/` aparece la lista filtrada; `Tab` o Enter completan, `↑`/`↓` eligen. **Salir:** `Ctrl-D` con la caja vacía, `Ctrl-C` dos veces, o `/exit`. `Ctrl-C` con texto lo borra.

Si no hay terminal (pipe, `TERM=dumb`) o tiene menos de 40 columnas, se usa el prompt simple `ia> ` y todo lo demás funciona igual.

### Atajos con `/`

Siempre funcionan. Al escribir `/` aparece la lista filtrada.

| Atajo | Qué hace |
|---|---|
| `/models` · `/models probe` | Estado de los CLIs y **qué modelo usa cada uno**; con `probe`, consulta mínima real (login, latencia; gasta una pizca de cuota). |
| `/scores [categoría]` | Puntaje por modelo y categoría; con una categoría, el desglose peso × valor. |
| `/metrics` · `/metrics refresh` | De dónde salen los datos y con qué entrada de cada portal se emparejó cada modelo; con `refresh`, los actualiza mostrando cada paso (`force` = aunque sean recientes). |
| `/priorities` | Preguntas: qué priorizás en cada tipo de tarea (precisión, velocidad o costo). |
| `/model codex` · `/model auto` | Fijar un modelo para todo lo que sigue, o volver al ruteo automático. |
| `/stats` | Éxito, latencia y tokens por modelo. |
| `/explain on\|off` | Mostrar la tabla de ruteo en cada mensaje. |
| `/md on\|off` | Markdown interpretado (como un README en GitHub, sin signos) o texto crudo. Sin terminal (pipe) o con `NO_COLOR` se muestra crudo. |
| `/ask texto` | Forzar la interpretación como tarea. |
| `/clear` | Olvidar la conversación (el chat recuerda los últimos 6 turnos). |
| `/help` · `/exit` | Ayuda · salir (también `salir`, Ctrl-D). |

También entiende consultas en lenguaje natural ("mostrame las estadísticas", "qué modelos tengo", "cómo decide el router"). Todo lo demás es una tarea y se rutea. `Ctrl-C` durante una tarea la interrumpe sin cerrar el programa.

### Comandos sueltos (sin abrir el chat)

Todo lo del chat también existe como comandos para scripts (`ask`, `route`, `scores`, `metrics`, `priorities`, `doctor`, `stats`). Se describen en la sección 5.

---

## 4. Ruteo por métricas objetivas

### 4.1 De dónde salen los datos

| Fuente | Qué aporta | Acceso |
|---|---|---|
| **[Arena](https://arena.ai/leaderboard)** | **Precisión**: Elo por categoría (preferencia humana en comparaciones a ciegas, con control de estilo y margen de error): coding, hard_prompts, expert, math, creative_writing, instruction_following, longer_query y los arenas de visión, búsqueda y webdev. También el **precio** por millón de tokens, cuando lo publica. | Páginas públicas de `arena.ai/leaderboard` (su `robots.txt` las permite). Es el mismo dato del dataset oficial `lmarena-ai/leaderboard-dataset` (CC BY 4.0). Sin clave. |
| **[Artificial Analysis](https://artificialanalysis.ai/)** | **Velocidad** (tokens/s), **precio** y benchmarks con respuesta correcta (índices de coding y math, GPQA, HLE…). | API con **clave gratuita** (ver 4.2). Opcional. |

**El software trae incluida la última foto de Arena** (`ia_router/data/arena.json`, 323 KB), así que no necesita red para rutear con métricas. Cuando actualizás (4.5), se guarda una más nueva en `~/.ia-router/metrics.json` y **siempre rige la más reciente** entre las dos. Nada se consulta solo: actualizar es una acción tuya.

### 4.2 Activar Artificial Analysis (velocidad y costo)

Arena no mide velocidad, y no publica el precio de todos los modelos (por ejemplo, el de `gpt-6.1-sol`). Con la clave de Artificial Analysis se suman **velocidad y costo**, que son las dimensiones que podés priorizar en `/priorities`. Sin ella, el ruteo usa solo precisión.

1. Creá una cuenta gratis en [artificialanalysis.ai](https://artificialanalysis.ai/) y generá una **API key** (plan gratuito: 1.000 pedidos por día).
2. Copiá el archivo de ejemplo y pegá tu clave, sin comillas ni espacios:

   ```bash
   cp .env.example .env
   # editá .env:  ARTIFICIAL_ANALYSIS_API_KEY=tu_clave
   ```

3. Actualizá las métricas: `python3 cli.py metrics refresh` (o `/metrics refresh` en el chat). Verás la línea `Artificial Analysis: N modelos` y `/metrics` mostrará con qué entrada se emparejó cada modelo.

**El `.env` nunca se sube a git** (está en `.gitignore`); lo que se versiona es `.env.example`. Una variable ya definida en tu entorno (`export ARTIFICIAL_ANALYSIS_API_KEY=…`) tiene prioridad sobre el archivo. El router busca `.env` en la carpeta del repo y en `~/.ia-router/.env`. La clave viaja a `curl` por la entrada estándar, **nunca por la línea de comandos**, y no se guarda en ningún otro lado. Artificial Analysis pide atribución: el router la muestra cada vez que usa sus datos.

### 4.3 Cómo se calcula el puntaje

```
puntaje(modelo, categoría) = Σ peso × valor        (valores de 0 a 10, relativos a TUS modelos)
```

| Dimensión | Valor |
|---|---|
| **Precisión** | Elo de Arena en la categoría (promedio de las categorías de Arena que le corresponden, ver 4.6) y, si hay clave, el índice de Artificial Analysis. **10 = empata o gana al mejor de tus modelos**; 100 puntos de Elo de diferencia ≈ 7,2. **Las diferencias dentro del margen de error no premian a nadie.** |
| **Velocidad** | Tokens por segundo publicados por Artificial Analysis. |
| **Costo** | Precio de lista por millón de tokens (3 de entrada : 1 de salida; Artificial Analysis o, si cubre a todos, Arena). Es un **proxy del consumo de cuota**: con una suscripción no pagás por token, pero cuanto más caro el modelo, más rápido se agota. |

Velocidad y costo van en **escala logarítmica**: 10 para el mejor de tus modelos y **2 puntos menos cada vez que otro es el doble de lento o de caro**. (Con una escala proporcional el modelo barato ganaría hasta con "precisión" como prioridad, porque el Elo está muy comprimido: lo probamos y es lo que corrige esta escala.)

Reglas para no engañarse:

- **Se empareja por el modelo real** que usa cada CLI (el que se aprende al usarlo o con `probe`) y por su **nivel de esfuerzo**. Arena publica variantes (`-high`, `-xhigh`, `-max`); se elige la que coincide y, si no existe, la más cercana, marcada con ⚠ **aproximada** en `/metrics`: tu CLI puede correr con otro esfuerzo que el del leaderboard.
- **Una dimensión solo cuenta si hay dato para TODOS tus modelos.** Si falta alguno (por ejemplo el precio de uno), esa dimensión **no pesa** y los pesos se reparten entre las que sí tienen: no se mezclan escalas.
- Si Arena no cubre una categoría para todos tus modelos, **la precisión de esa categoría es la estimación de `models.json`**, marcada con `e` en `/scores`. Es el último recurso.
- Si el router no sabe todavía qué modelo usa cada CLI, rige por completo la estimación de `models.json`.

### 4.4 Tus prioridades: `/priorities`

Seis pasos de opción múltiple con selector (↑/↓ o el número, Enter confirma, Esc cancela; cada respuesta queda resumida en una línea), una por tipo de tarea: **código y debugging**, **escritura y redacción**, **análisis, datos e investigación**, **matemática** y **tareas rápidas**; la última es la confirmación. Las opciones son:

| Opción | Pesos (precisión / velocidad / costo) | Cuándo se ofrece |
|---|---|---|
| **Precisión** | 0,80 / 0,10 / 0,10 | siempre |
| **Equilibrado** | 0,50 / 0,25 / 0,25 | siempre |
| **Velocidad** | 0,40 / 0,50 / 0,10 | solo con datos de velocidad de **todos** tus modelos |
| **Costo** | 0,40 / 0,10 / 0,50 | solo con precio de **todos** tus modelos |

Sin responder, rigen unos pesos por defecto: 0,70 / 0,15 / 0,15 (y 0,40 / 0,50 / 0,10 para tareas rápidas). Si no hay datos de velocidad ni de costo, **no hay nada que priorizar** y `/priorities` lo explica en lugar de hacer preguntas inútiles. Al final muestra cómo queda el ruteo (qué modelo elige para cada categoría) y te deja **Guardar** o **Descartar**. Se guarda en `~/.ia-router/profile.json`, las respuestas anteriores aparecen preseleccionadas, y **el cambio rige de inmediato**: no hay nada que "regenerar".

### 4.5 Actualizar las métricas, con visibilidad

```bash
python3 cli.py metrics refresh      # o /metrics refresh en el chat
```

Muestra cada paso, y al final **qué cambió en el ruteo**. Salida real, con las métricas incluidas del mismo día (por eso no cambia nada):

```
Leyendo los leaderboards de arena.ai (una página por categoría, ~11 pedidos de 2-3 MB, alrededor de un minuto)…
Arena · search/overall: 34 modelos
Arena · text/coding: 408 modelos
…
Arena · webdev/overall: 138 modelos
Artificial Analysis: sin clave (opcional, aporta velocidad). Ver .env.example.
Métricas al día: Arena 2026-10-05 (actualizada)
El ruteo no cambia con estos datos.
```

Cuando algo cambia, en vez de la última línea lista cada diferencia, con este formato:

```
Qué cambió en el ruteo:
  · <categoría>: ahora elige <modelo> (antes <modelo>)  [<modelo> <antes>→<ahora>]
  · <categoría>: <modelo> <antes>→<ahora>        (movimientos de 0,3 puntos o más)
```

(Si falla, te lo dice y sigue con las métricas que tenías.) Se leen unas 11 páginas de arena.ai, de a una y con pausa, con reintentos ante límite de pedidos: **alrededor de un minuto**. Los datos se cachean: si los tuyos tienen menos de 12 horas no vuelve a pedirlos (`--force` para insistir). Si el sitio cambia el formato de sus páginas, lo avisa con un error claro y todo sigue con lo que tenías.

### 4.6 Qué mira cada categoría

| Categoría del router | Arena | Artificial Analysis |
|---|---|---|
| `general` | text/overall | índice de inteligencia |
| `coding` | text/coding + webdev | índice de coding |
| `debugging` | text/coding + hard_prompts | índice de coding |
| `writing` | creative_writing + instruction_following | — |
| `analysis` | hard_prompts + expert | índice de inteligencia |
| `data` | math + coding | índices de coding y math |
| `math` | text/math | índice de math |
| `research` | search arena | índice de inteligencia |
| `long_context` | longer_query | — |
| `multimodal` | vision arena | — |
| `quick` | text/overall | — (pesa la velocidad) |

Si una categoría tiene datos de las dos fuentes, se promedian.

**Qué NO es.** Arena mide **preferencia humana**, no si la respuesta es correcta, y los datos son de modelos genéricos, no de tu CLI con sus herramientas. Los benchmarks de Artificial Analysis sí tienen respuesta correcta, por eso se suman cuando hay clave. La fecha que se muestra es la de la consulta (las páginas no publican la del snapshot).

---

## 5. Los comandos, con ejemplos

### 5.1 `scores` — qué elige el router y por qué

```bash
python3 cli.py scores [categoría]
```

```
categoría             claude         codex   antigravity
────────────────────────────────────────────────────────
coding                 9.5           10.0*           7.8
debugging              9.9           10.0*          10.0*
writing                9.7            9.8           10.0*
analysis               9.9           10.0*           9.8
data                  10.0*          10.0*          10.0*
research               7.0 e          6.0 e          8.0*e
math                   7.0 e          8.0*e          8.0*e
multimodal             9.7           10.0*          10.0*
long_context           9.8            9.9           10.0*
quick                  9.5            9.8           10.0*

* = el que elige el router · e = precisión estimada a mano (Arena no cubre esa categoría para todos tus modelos)
Dimensiones con datos: precisión  ·  velocidad: falta tu clave de Artificial Analysis (.env)
Datos: Arena 2026-10-05 (incluida)  ·  Atribución: Arena (arena.ai), dataset leaderboard-dataset, CC BY 4.0
```

Con una categoría (`scores coding`) agrega el desglose: cada modelo con sus valores, pesos y fuentes (`precisión: Arena text/coding 1536; Arena webdev/overall 1715`).

### 5.2 `metrics` — de dónde salen los datos

```bash
python3 cli.py metrics            # qué entrada de cada portal se emparejó con cada modelo
python3 cli.py metrics refresh    # actualizar (ver 4.5); --force aunque tengas datos recientes
```

```
Métricas: Arena 2026-10-05 (incluida)  ·  Arena incluida en esta versión

claude       modelo del CLI: claude-sonnet-5-5
             Arena → claude-sonnet-5.5-xhigh  ⚠ Arena no publica el mismo nivel de esfuerzo que usa tu CLI: dato aproximado (text: xhigh, vision: xhigh, webdev: high)
codex        modelo del CLI: gpt-6.1-sol
             Arena → gpt-6.1-sol-max  ⚠ Arena no publica el mismo nivel de esfuerzo que usa tu CLI: dato aproximado (text: max, vision: max, webdev: max)
antigravity  modelo del CLI: Gemini 3.8 Flash (High)
             Arena → gemini-3.8-flash-high

Artificial Analysis no está activo: sin su clave no hay velocidad ni, en general, costo. Ver .env.example.
Fuentes: Arena (arena.ai), dataset leaderboard-dataset, CC BY 4.0
```

### 5.3 `priorities` — qué priorizás

```bash
python3 cli.py priorities
```

Requiere una terminal interactiva (usa el selector con las flechas). Ver 4.4.

### 5.4 `ask` y `route` — rutear y ejecutar

```bash
python3 cli.py ask "TAREA" [-m MODELO] [-c ARCHIVO]... [--dry-run]
python3 cli.py route "TAREA" [-c ARCHIVO]...
```

| Opción | Efecto |
|---|---|
| `-m`, `--model` | `auto` (por defecto) o forzar `claude` / `codex` / `antigravity`. |
| `-c`, `--context` | Agrega un archivo como contexto. Se puede repetir. |
| `--dry-run` | Muestra la decisión y el orden de intento, **sin ejecutar** nada (no gasta cuota). `route` es lo mismo, más corto. |

```bash
python3 cli.py ask "Escribí una función Python que sume una lista" --dry-run
```

```
Clasificación (reglas, métricas: sí): coding×2
modelo   score  estado
codex     10.0  OK             coding×2→10.0
claude    9.54  OK             coding×2→9.54
antigravity  7.78  OK             coding×2→7.78
→ elegido: codex
(dry-run) orden de intento: ['codex', 'claude', 'antigravity']
```

**Qué muestra la respuesta.** Una cabecera con el proveedor, el **modelo exacto** que respondió y los **tokens** de entrada (con lo que salió de caché entre paréntesis) y de salida, y después el texto con el markdown interpretado:

```
[claude (claude-sonnet-5-5) · in 16.5k (8.4k caché) · out 72]
```

Si el CLI no informa el modelo o los tokens, se muestra `tokens n/d`.

**Fallback automático.** Si el modelo elegido falla, prueba el siguiente (máximo 3 intentos):

| Situación | Qué hace el router |
|---|---|
| Rate limit (cuota agotada) | Pone ese modelo en *cooldown* 30 min y prueba el siguiente. |
| Sin login | Pone ese modelo en cooldown 60 min y prueba el siguiente. |
| Timeout u otro error | Prueba el siguiente. |

**Código de salida:** `0` si hubo respuesta, `1` si fallaron todos los intentos, `2` si pediste un modelo inexistente.

### 5.5 `doctor` — diagnóstico

```bash
python3 cli.py doctor            # no gasta cuota
python3 cli.py doctor --probe    # consulta mínima real a cada CLI: login, latencia, versión y modelo
```

```
Config: 3 modelos | estado en: ~/.ia-router | métricas: Arena 2026-10-05 (incluida)

claude       instalado     cooldown=0s  modelo: claude-sonnet-5-5
codex        instalado     cooldown=0s  modelo: gpt-6.1-sol
antigravity  instalado     cooldown=0s  modelo: Gemini 3.8 Flash (High)
```

### 5.6 `stats` y `reset-cooldowns`

```bash
python3 cli.py stats
```

```
modelo       corridas  éxito seg. medio rate limits  auth  tokens in tokens out
claude              5   100%        6.1           0     0      11391        156
antigravity         3   100%        8.9           0     0          0          0
codex               4   100%       60.5           0     0     454613       5960
```

Sale del log local (`~/.ia-router/log.jsonl`). El log **no guarda tus prompts**, solo modelo, **id real del modelo**, tokens, éxito, duración y errores. Los tokens de entrada incluyen lo que salió de caché; las corridas anteriores a que existiera el registro de tokens (como las de `antigravity` acá) suman 0.

Si un modelo quedó en cooldown y ya lo arreglaste (volviste a loguearte, o pasó el límite de cuota): `python3 cli.py reset-cooldowns`.

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

El servidor MCP **usa el mismo ruteo por métricas** que el CLI. Si tardan mucho las tareas, subí el timeout de herramientas MCP de tu cliente (variable `MCP_TOOL_TIMEOUT` en Claude Code).

---

## 7. Dónde se guarda todo

| Qué | Dónde |
|---|---|
| Foto de Arena incluida en el software | `ia_router/data/arena.json` (en el repo) |
| Métricas que descargaste (Arena y Artificial Analysis) | `~/.ia-router/metrics.json` |
| Tus prioridades (`/priorities`) | `~/.ia-router/profile.json` |
| Qué modelo usa cada CLI (lo aprendido) | `~/.ia-router/models_seen.json` |
| Cuándo se te ofreció actualizar / las preguntas | `~/.ia-router/startup.json` |
| Cooldowns | `~/.ia-router/state.json` |
| Log de ejecuciones (sin prompts; con modelo y tokens) | `~/.ia-router/log.jsonl` |
| Historial de lo que escribís en la caja de entrada | `~/.ia-router/history.jsonl` |
| Tu clave de Artificial Analysis | `.env` (en el repo, ignorado por git) o la variable de entorno |
| Modelos, comandos y timeouts | `models.json` (en el repo) |

Para empezar de cero: `rm -r ~/.ia-router`. Para repetir solo una parte, borrá ese archivo (por ejemplo `profile.json` vuelve a los pesos por defecto).

**Si venís de una versión anterior** (con manifiesto y `calibrate`): `manifest.json`, `manifest.prev.json`, `external.json` y los datos viejos de `metrics.json` ya no se usan. `metrics.json` se reemplaza solo la primera vez que actualices; los otros podés borrarlos.

---

## 8. Configuración avanzada

### 8.1 Variables de entorno

| Variable | Efecto | Ejemplo |
|---|---|---|
| `ARTIFICIAL_ANALYSIS_API_KEY` | Clave gratuita de Artificial Analysis (velocidad, costo, benchmarks). Mejor en `.env` (ver 4.2). | `.env`: `ARTIFICIAL_ANALYSIS_API_KEY=…` |
| `ROUTER_HOME` | Cambia la carpeta de estado (por defecto `~/.ia-router`). | `ROUTER_HOME=/tmp/prueba python3 cli.py scores` |
| `ROUTER_MODELS` | Usa otro `models.json`. | `ROUTER_MODELS=~/mis-modelos.json python3 cli.py doctor` |
| `ROUTER_CMD_<MODELO>` | Reemplaza el comando de un modelo (lista JSON). `{prompt}` se sustituye por la tarea. Con un comando propio no se agregan los flags de uso (se muestra `tokens n/d`). | `ROUTER_CMD_CODEX='["codex","exec","{prompt}"]'` |
| `NO_COLOR` | Desactiva colores y estilos (la salida queda como texto plano). | `NO_COLOR=1 ia-router` |
| `CODEX_HOME` | Carpeta de Codex de donde se lee el modelo que usó (si no es `~/.codex`). | |

`ROUTER_HOME` es útil para probar sin tocar tu estado real. Todas se pueden poner en `.env` (ver `.env.example`).

### 8.2 `models.json`

```json
"claude": {
  "label": "Claude (CLI oficial: claude)",
  "cmd": ["claude", "-p", "{prompt}"],
  "cmd_stdin": ["claude", "-p"],
  "usage": {"parser": "claude", "args": ["--output-format", "json"], "at": 1},
  "add_dir": {"args": ["--add-dir", "{dir}"], "at": 1},
  "timeout": 300,
  "strengths": { "general": 8, "coding": 9, "writing": 9 }
}
```

| Campo | Para qué sirve |
|---|---|
| `cmd` | Comando que ejecuta la tarea. `{prompt}` es la tarea. |
| `cmd_stdin` | Comando alternativo cuando el prompt supera 100.000 caracteres (viaja por stdin). Si falta, viaja por argumento. |
| `timeout` | Segundos máximos por intento. |
| `strengths` | Puntajes 0–10 por categoría: **estimaciones de último recurso**. Rigen solo mientras no se sepa qué modelo usa el CLI o si Arena no cubre una categoría para todos tus modelos. |
| `usage` | `{parser, args, at}`: flags que hacen al CLI devolver JSON con el modelo real y los tokens (claude `--output-format json`, codex `--json`, agy `--output-format json --log-file`). `at` es la posición donde se insertan. |
| `add_dir` | Cómo darle acceso a la carpeta de un adjunto (claude: `--add-dir {dir}`). |
| `reads_files` | `false` si el CLI no puede abrir archivos por ruta en modo no interactivo (antigravity): se descarta para imágenes, PDF y binarios adjuntos. |
| `enabled` | `false` para sacar un modelo del reparto. |
| `external` | `{"arena": "nombre-exacto", "aa": "slug"}`: fuerza con qué entrada de los portales se emparejó el modelo. |

Ajustes globales: `cooldown_minutes` (rate limit, 30) y `auth_cooldown_minutes` (sin login, 60).

**Agregar un modelo nuevo:** sumá una entrada en `"models"` con su `cmd` (y su `usage` si su CLI devuelve JSON), corré `python3 cli.py doctor --probe` para confirmar que funciona y qué modelo usa, y `python3 cli.py metrics` para ver si los portales lo tienen.

### 8.3 Categorías de tareas

El router distingue estos tipos de tarea. Son los que aparecen en el manifiesto:

`coding`, `debugging`, `writing`, `analysis`, `data`, `research`, `math`, `multimodal`, `long_context`, `quick`.

Una tarea puede tener varias a la vez (por ejemplo `debugging×3, coding×2`). `long_context` se activa solo cuando tarea + archivos superan 30.000 caracteres (peso fuerte desde 100.000). `quick` se activa con palabras como "rápido", "breve" o "tl;dr", o con tareas muy cortas sin otra categoría.

---

## 9. Problemas comunes

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `scores` dice "Todavía no hay métricas que cubran a tus modelos" | El router no sabe qué modelo usa cada CLI. | `python3 cli.py doctor --probe` (o aceptá la detección al iniciar el chat). |
| `/metrics` dice "Arena → sin coincidencia" para un modelo | Arena no lo tiene con ese nombre. | Forzalo con `"external"` en `models.json`, o usá el nombre exacto que muestra el leaderboard. |
| `/priorities` dice "nada que priorizar" | Sin Artificial Analysis no hay datos de velocidad ni costo. | Activalo (4.2) y corré `metrics refresh`. |
| `metrics refresh` falla o pide esperar | arena.ai cambió el formato de sus páginas, o el sitio limita los pedidos (HTTP 429). | Reintentá más tarde. Mientras tanto el router sigue con las métricas que tenía. |
| `Artificial Analysis: no se pudo consultar` | Clave inválida, cuota diaria agotada (1.000 pedidos) o sin red. | Revisá la clave en `.env`; Arena sigue funcionando. |
| `doctor --probe` muestra `auth=missing` | El CLI está instalado pero sin sesión. | Iniciá sesión en ese CLI (ejecutalo sin argumentos). Después `python3 cli.py reset-cooldowns`. |
| `agy` o `gemini` dicen *"This client is no longer supported"* | Google dio de baja el Gemini CLI. | Usá `agy` (Antigravity) en su lugar. |
| `ningún modelo disponible` | Todos están sin instalar, desactivados o en cooldown. | `python3 cli.py doctor` y, si corresponde, `reset-cooldowns`. |
| Siempre elige el mismo modelo | Es lo que dicen las métricas para esa categoría. | `scores <categoría>` muestra el porqué; `/priorities` lo ajusta; `/model X` lo fija. |
| El router eligió mal una tarea | La clasificación por reglas no la entendió. | `route "tu tarea"` muestra cómo la clasificó; forzá con `-m`. |
| Un archivo arrastrado no se reconoce | La terminal pegó la ruta sin que exista el archivo, o es una palabra suelta. | Las rutas deben empezar con `/`, `~`, `./`, `../` o `file://`. |
| `Error: modelo desconocido` | Pasaste un `-m` que no existe en `models.json`. | Usá `auto` o uno de los modelos listados en el mensaje. |
| Un comando falla con *timeout* | La tarea tardó más que el `timeout` del modelo. | Subí `timeout` en `models.json`. |
| Una tarea tardó minutos y respondió bien | La Mac se durmió a mitad de la llamada. | Corré el chat con `caffeinate -is ia-router`: no se duerme sola mientras esté abierto, pero podés suspenderla a mano. |
| Falla el flag de un CLI tras actualizarlo | Los CLIs cambian seguido sus opciones. | Revisá `<cli> --help` y ajustá `cmd` en `models.json`, o usá `ROUTER_CMD_<MODELO>`. |

---

## 10. Límites que conviene conocer

- **Es para uso personal.** Corre con tus suscripciones, a ritmo humano. Si algún día lo distribuís a terceros, revisá los términos de cada proveedor (Anthropic, por ejemplo, exige API key para productos de terceros).
- **Cada `ask` y cada `doctor --probe` gastan cuota real.** `route`, `scores`, `metrics` y `--dry-run` no gastan. Actualizar las métricas solo lee sitios públicos (y la API de Artificial Analysis con tu clave).
- **Las métricas miden modelos, no tu CLI.** Arena mide preferencia humana, no respuestas correctas, y publica variantes por nivel de esfuerzo que pueden no coincidir con el de tu CLI (se marca como aproximado). Con modelos de frontera las diferencias suelen caer dentro del margen de error: el desempate lo dan velocidad y costo, si los activás.
- **El costo es un proxy**: precio de lista por token, no tu cuota real de suscripción.
- **Artificial Analysis está implementado con el formato de su documentación y tests con datos de ejemplo; no se verificó contra la API real.** Con tu clave, `metrics refresh` lo verifica.
- **`agy` no lee el prompt por stdin ni abre archivos por ruta** en modo no interactivo, y falla si el modelo pide una herramienta que no puede autorizar.
- **Seguridad:** los CLIs corren en modo no interactivo con sus permisos por defecto. El router **no** activa flags de "permitir todo".
- **Todavía no hay** sesiones persistentes (el chat recuerda los últimos turnos pero no se guarda al salir), streaming de salida ni versión de escritorio.
