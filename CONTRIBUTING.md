# Cómo contribuir a ia-router

¡Gracias por querer sumar! ia-router es software libre ([Apache-2.0](LICENSE)) y las contribuciones son bienvenidas: bugs, ideas, documentación, tests y código.

Dónde está todo: [web](https://www.mgatc.com/recursos/ia-router/) · [PyPI](https://pypi.org/project/ia-router/) · [tap de Homebrew](https://github.com/Mgobeaalcoba/homebrew-tap) · [issues](https://github.com/Mgobeaalcoba/ia-suscription-router/issues) · [cambios](CHANGELOG.md).

Antes de empezar, leé [AGENTS.md](AGENTS.md): resume las reglas del proyecto, el mapa del código y las trampas conocidas.

## Qué es fácil de aceptar

- Corrección de bugs, con un test que falle antes y pase después.
- Soporte para otros CLIs de IA (ver "Cómo extender" en `AGENTS.md`).
- Mejoras de documentación y de los mensajes en español.
- Tests que cubran casos que hoy no se prueban.

## Qué conviene hablar antes

Abrí un [*issue*](https://github.com/Mgobeaalcoba/ia-suscription-router/issues/new/choose) antes de empezar si querés cambiar el diseño (cómo se puntúan los modelos, fuentes de métricas nuevas, comandos nuevos). El proyecto se simplificó a propósito: no hay manifiesto con "manager", ni calibración propia, ni configuración por lenguaje natural, y no se van a reincorporar sin una buena razón.

## Preparar el entorno

```bash
git clone https://github.com/Mgobeaalcoba/ia-suscription-router.git
cd ia-suscription-router
python3 -m unittest discover -s tests     # debe terminar en OK (~266 tests, ~15 s)
```

No hay dependencias que instalar: el proyecto usa **solo la librería estándar de Python** y es compatible con **Python 3.9** (probalo con `/usr/bin/python3` en macOS). No agregues dependencias.

## Reglas que se revisan en cada pull request

1. **Tests:** todo cambio de comportamiento trae tests. Los tests **no usan red, no llaman a CLIs reales y no tocan `~/.ia-router`** (usan `tests/fake_bin`, `ROUTER_HOME` temporal y `tests/fixtures.py`).
2. **Sin secretos:** nunca subas un `.env`, claves, tokens ni datos personales. El `.env` está ignorado por git; solo se versiona `.env.example`.
3. **Sin gasto no pedido:** nada debe consumir cuota de un modelo ni consultar la red sin que el usuario lo pida.
4. **Documentación al día:** si cambia un comando, un archivo de estado o una regla, actualizá `README.md`, `docs/USO.md` y `AGENTS.md` en el mismo cambio. Las salidas que se muestran en la guía deben ser **reales**: capturalas, no las inventes.
5. **Español rioplatense** en los textos para el usuario (vos).
6. **Mensajes de commit** en español con prefijo `feat:`, `fix:`, `docs:` o `test:`, que expliquen el *por qué*.

## Licencia de tus aportes y firma de commits (DCO)

Al contribuir aceptás que tu aporte se publica bajo la [Licencia Apache 2.0](LICENSE) del proyecto (sección 5 de la licencia). Tu copyright sobre lo que escribís sigue siendo tuyo.

Para dejar constancia usamos el [Developer Certificate of Origin](DCO) (DCO 1.1): con **firmar tus commits** certificás que tenés derecho a aportar ese código bajo la licencia del proyecto. Es una línea al final del mensaje de commit:

```
Signed-off-by: Tu Nombre <tu@email.com>
```

Git la agrega sola con la opción `-s`:

```bash
git commit -s -m "fix: corrijo el cálculo de ..."
# si te olvidaste de firmar varios commits de tu rama:
git rebase --signoff main
```

Un chequeo automático en cada pull request verifica que todos los commits estén firmados (`tools/check_dco.sh`). Usá tu nombre real y un email con el que puedan contactarte.

## Atribución

Si redistribuís o derivás este software, conservá `LICENSE` y [NOTICE](NOTICE) con la atribución al autor y a los datos de terceros, como exige la licencia. Para citarlo en un trabajo: [CITATION.cff](CITATION.cff).

## Seguridad

Si encontrás un problema de seguridad (por ejemplo, una forma de filtrar una clave de API), **no abras un issue público**: escribile al autor por los canales de [mgatc.com](https://www.mgatc.com).

## Tu pull request

1. Hacé un *fork* y creá una rama con un nombre descriptivo.
2. Cambios chicos y enfocados: un cambio coherente por pull request.
3. Corré los tests y completá la lista de la plantilla del pull request.
4. Respondé las revisiones con commits nuevos; no hace falta reescribir el historial.
