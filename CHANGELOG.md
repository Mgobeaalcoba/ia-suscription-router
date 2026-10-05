# Cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Las versiones se publican en [PyPI](https://pypi.org/project/ia-router/) y [Homebrew](https://github.com/Mgobeaalcoba/homebrew-tap).

## 0.2.1 — 2026-10-05

### Agregado
- Código público en GitHub con `CONTRIBUTING.md`, firma de commits (DCO) y plantillas de issues y pull requests.
- `CHANGELOG.md` y una tabla «Dónde encontrarlo» (web, PyPI, Homebrew, código, guía, licencia) en el README.
- Metadatos del paquete con `Source`, `Documentation`, `Issues`, `Changelog` y `Homebrew`.

### Cambiado
- El README usa enlaces absolutos para que se vea bien en PyPI, e incluye la captura del encabezado.
- Se aclara que solo se probó en macOS (en Linux debería funcionar, pero no se verificó).

## 0.2.0 — 2026-10-05

Primera versión pública.

- Ruteo por métricas objetivas: precisión (Arena), velocidad y costo (Artificial Analysis, con clave gratuita), con la última foto de Arena incluida.
- Preguntas de prioridades por tipo de tarea; las métricas se actualizan con visibilidad de lo que cambia.
- Chat con caja de entrada propia, archivos arrastrados, modelo exacto y tokens en cada respuesta, y markdown interpretado.
- Instalación con Homebrew y pip/pipx, `ia-router --version`.
- Licencia Apache-2.0 con `NOTICE` de atribución.
