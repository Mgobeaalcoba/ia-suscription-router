#!/usr/bin/env bash
# Publica la versión actual de ia-router en PyPI y actualiza la fórmula de Homebrew. PÚBLICO E IRREVERSIBLE: PyPI no permite
# resubir una versión. Sin --yes hace todo MENOS subir (tests, build, twine check, comprobaciones) para ensayar.
#
#   tools/release.sh          ensayo
#   tools/release.sh --yes    publica
#
# Credenciales: un token de PyPI en ~/.pypirc (usuario __token__) o en las variables TWINE_USERNAME / TWINE_PASSWORD.
# Nunca van en el repo. Este script no imprime el token.
set -euo pipefail
cd "$(dirname "$0")/.."

YES=0; [[ "${1:-}" == "--yes" ]] && YES=1
VERSION=$(python3 -c "import sys; sys.path.insert(0, '.'); import ia_router; print(ia_router.__version__)")
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

say "ia-router $VERSION"
if [[ "$(curl -s -o /dev/null -w '%{http_code}' "https://pypi.org/pypi/ia-router/$VERSION/json")" == "200" ]]; then
  echo "La versión $VERSION YA está en PyPI. Subí __version__ (ia_router/__init__.py y CITATION.cff) antes de publicar." >&2; exit 1
fi
grep -q "version: \"$VERSION\"" CITATION.cff || { echo "CITATION.cff no está en la versión $VERSION" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "Hay cambios sin commitear: commitealos antes de publicar." >&2; exit 1; }

say "Tests"
python3 -m unittest discover -s tests 2>&1 | tail -3

say "Build y verificación del paquete"
python3 -m venv "$TMP/venv" && "$TMP/venv/bin/pip" install -q build twine
rm -rf dist build ./*.egg-info
"$TMP/venv/bin/python" -m build >/dev/null
"$TMP/venv/bin/twine" check dist/*
if tar -tzf "dist/ia_router-$VERSION.tar.gz" | grep -q -E '(^|/)\.env$'; then echo "El paquete incluye un .env: abortado." >&2; exit 1; fi
for f in LICENSE NOTICE; do tar -tzf "dist/ia_router-$VERSION.tar.gz" | grep -q "/$f$" || { echo "Falta $f en el paquete" >&2; exit 1; }; done

say "Instalación del wheel en un entorno limpio"
python3 -m venv "$TMP/clean" && "$TMP/clean/bin/pip" install -q "dist/ia_router-$VERSION-py3-none-any.whl"
[[ "$("$TMP/clean/bin/ia-router" --version)" == "ia-router $VERSION" ]] || { echo "--version no coincide" >&2; exit 1; }
ROUTER_HOME="$TMP/home" "$TMP/clean/bin/ia-router" scores >/dev/null && echo "ok: ia-router --version y scores"

if [[ $YES -eq 0 ]]; then
  say "ENSAYO terminado: todo en orden. Para publicar: tools/release.sh --yes"; exit 0
fi

say "Subiendo a PyPI"
if [[ ! -f "$HOME/.pypirc" && -z "${TWINE_PASSWORD:-}" ]]; then
  echo "No hay credenciales: creá ~/.pypirc con tu token de PyPI (ver docs) o exportá TWINE_USERNAME=__token__ y TWINE_PASSWORD." >&2; exit 1
fi
"$TMP/venv/bin/twine" upload dist/*

say "Esperando que PyPI publique el archivo fuente"
URL=""; SHA=""
for i in $(seq 1 30); do
  read -r URL SHA < <(curl -s "https://pypi.org/pypi/ia-router/$VERSION/json" | python3 -c "
import sys, json
try: d = json.load(sys.stdin)
except Exception: sys.exit(0)
for f in d.get('urls', []):
    if f['packagetype'] == 'sdist': print(f['url'], f['digests']['sha256'])" || true) || true
  [[ -n "$URL" ]] && break; sleep 5
done
[[ -n "$URL" ]] || { echo "PyPI no mostró el sdist a tiempo; reintentá la parte del tap a mano." >&2; exit 1; }
echo "sdist: $URL"; echo "sha256: $SHA"

say "Actualizando la fórmula en Mgobeaalcoba/homebrew-tap"
git clone -q https://github.com/Mgobeaalcoba/homebrew-tap.git "$TMP/tap"
python3 - "$TMP/tap/Formula/ia-router.rb" "$URL" "$SHA" <<'PY'
import re, sys
p, url, sha = sys.argv[1:]
s = open(p).read()
s = re.sub(r'url "[^"]*"', f'url "{url}"', s, count=1)
s = re.sub(r'sha256 "[^"]*"', f'sha256 "{sha}"', s, count=1)
open(p, "w").write(s)
PY
( cd "$TMP/tap" && sed -i.bak 's/^> Estado:.*$/> Estado: disponible./' README.md && rm -f README.md.bak \
  && git add -A && git commit -q -m "ia-router $VERSION" && git push -q origin main )

say "Verificando las dos instalaciones"
python3 -m venv "$TMP/pip" && "$TMP/pip/bin/pip" install -q "ia-router==$VERSION" && "$TMP/pip/bin/ia-router" --version
if command -v brew >/dev/null; then
  brew tap Mgobeaalcoba/tap >/dev/null 2>&1 || true
  brew update >/dev/null 2>&1 || true
  brew install Mgobeaalcoba/tap/ia-router && ia-router --version && brew test ia-router
fi
say "Publicado: ia-router $VERSION en PyPI y Homebrew"
