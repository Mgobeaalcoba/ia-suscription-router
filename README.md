# llm-router-poc — Prueba de concepto (etapa 0)

Router multi-modelo que **maneja los CLIs oficiales** (`claude`, `codex`, `gemini`) para aprovechar tus suscripciones.
Se puede usar de dos formas: como **CLI propio** (`cli.py`) o como **servidor MCP** dentro de Claude Code, para que Claude delegue subtareas a los otros modelos.

- Sin dependencias externas: solo Python 3.9+ (stdlib).
- **No toca tokens OAuth**: cada CLI usa su propio login y su propia suscripción.
- Probado con CLIs simulados (10 tests). **No fue probado contra tus CLIs reales**: ver "Primer uso".

## Primer uso

```bash
cd ~/Documents/llm-router-poc
python3 cli.py doctor                       # ¿qué CLIs están instalados?
python3 cli.py route "Arreglá este bug en mi función Python"   # solo decide, no ejecuta
python3 cli.py ask "Resumí este documento" -c notas.md --dry-run
python3 cli.py ask "Redactá un mail de seguimiento para un cliente"
python3 -m unittest discover -s tests       # tests con CLIs falsos
```

**Verificá primero** que los flags de `models.json` coincidan con tus versiones (`claude --help`, `codex exec --help`, `gemini --help`). Si difieren, editá `cmd` en `models.json` o usá `ROUTER_CMD_CODEX='["codex","exec","{prompt}"]'`.

## Usarlo desde Claude Code (MCP)

```bash
claude mcp add llm-router -- python3 ~/Documents/llm-router-poc/cli.py mcp
```

Herramientas que ve Claude: `route_task`, `ask_model`, `list_models`. Ejemplo de prompt: *"Pedile a Gemini que resuma estos 3 archivos y a Codex que revise el bug; integrá ambas respuestas."*
Nota: si una tarea tarda mucho, subí el timeout de herramientas MCP de tu cliente (variable `MCP_TOOL_TIMEOUT` en Claude Code).

## Cómo decide

| Paso | Qué hace |
|---|---|
| 1. Clasificar | Reglas ES/EN detectan categorías con peso (coding, debugging, writing, analysis, data, research, math, multimodal, long_context, quick). Con `--llm` usa un modelo barato (Haiku) y cae a reglas si falla. |
| 2. Puntuar | Promedio ponderado de `strengths` de cada modelo en `models.json`. |
| 3. Filtrar | Modelos no instalados o en cooldown quedan al final. |
| 4. Ejecutar | Lanza el CLI del mejor modelo. Si hay rate limit o error, prueba el siguiente (máx. 3). |
| 5. Registrar | Cooldown de 30 min tras un rate limit; log en `~/.llm-router-poc/log.jsonl` (sin el prompt). |

Prompts de más de 100k caracteres viajan por stdin para no exceder el límite de argumentos.

## Archivos

| Archivo | Rol |
|---|---|
| `models.json` | Registro de modelos: comandos, timeouts y fortalezas (**hipótesis**, reemplazalas con tus evals). |
| `llm_router/router.py` | Clasificación y ranking. |
| `llm_router/adapters.py` | Ejecución de CLIs, detección de rate limit. |
| `llm_router/core.py` | Orquestación y fallback. |
| `llm_router/state.py` | Cooldowns y log. |
| `llm_router/mcp_server.py` | Servidor MCP stdio. |
| `tests/` | Tests y CLIs falsos (`tests/fake_bin`). |

## Límites conocidos

| Tema | Detalle |
|---|---|
| Flags de los CLIs | Supuestos, sin verificar contra tus versiones. Los cambios de los CLIs son frecuentes. |
| Gemini | Reportes indican que el login OAuth del Gemini CLI con cuentas AI Pro/Ultra dejó de funcionar desde junio de 2026. Si falla, usá una API key de Gemini. |
| Términos de uso | Pensado para uso personal a ritmo humano. Si algún día lo distribuís a terceros, revisá los términos de cada proveedor (Anthropic exige API key para productos de terceros). |
| Permisos | Claude y Gemini corren en modo no interactivo; Codex con su sandbox por defecto. No se habilitan flags de "permitir todo". |
| Calidad del ruteo | Las reglas son una primera aproximación. El salto real viene de armar tu set de 30–50 tareas y medir (etapa 2). |

## Próximos pasos (etapa 1 y 2)

1. Correr `doctor` y ajustar comandos.
2. Armar `evals/` con tareas tuyas y puntuar cada modelo; volcar los resultados en `strengths`.
3. Streaming de salida y sesiones (continuar conversación).
4. Quota tracker con conteo de uso por ventana, no solo cooldown reactivo.
