#!/usr/bin/env bash
# Publishes the current ia-router version to PyPI and updates the Homebrew formula. PUBLIC AND IRREVERSIBLE: PyPI does not allow
# re-uploading a version. Without --yes it does everything EXCEPT upload (tests, build, twine check, checks) as a dry run.
#
#   tools/release.sh          dry run
#   tools/release.sh --yes    publish
#
# Credentials: a PyPI token in ~/.pypirc (user __token__) or in the TWINE_USERNAME / TWINE_PASSWORD variables.
# They never go in the repo. This script does not print the token.
set -euo pipefail
cd "$(dirname "$0")/.."

YES=0; [[ "${1:-}" == "--yes" ]] && YES=1
VERSION=$(python3 -c "import sys; sys.path.insert(0, '.'); import ia_router; print(ia_router.__version__)")
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

say "ia-router $VERSION"
if [[ "$(curl -s -o /dev/null -w '%{http_code}' "https://pypi.org/pypi/ia-router/$VERSION/json")" == "200" ]]; then
  echo "Version $VERSION is ALREADY on PyPI. Bump __version__ (ia_router/__init__.py and CITATION.cff) before publishing." >&2; exit 1
fi
grep -q "version: \"$VERSION\"" CITATION.cff || { echo "CITATION.cff is not at version $VERSION" >&2; exit 1; }
grep -q "^## $VERSION " CHANGELOG.md || { echo "CHANGELOG.md has no '## $VERSION' entry" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "There are uncommitted changes: commit them before publishing." >&2; exit 1; }

say "Tests"
python3 -m unittest discover -s tests 2>&1 | tail -3

say "Build and package verification"
python3 -m venv "$TMP/venv" && "$TMP/venv/bin/pip" install -q build twine
rm -rf dist build ./*.egg-info
"$TMP/venv/bin/python" -m build >/dev/null
"$TMP/venv/bin/twine" check dist/*
if tar -tzf "dist/ia_router-$VERSION.tar.gz" | grep -q -E '(^|/)\.env$'; then echo "The package includes a .env: aborted." >&2; exit 1; fi
for f in LICENSE NOTICE; do tar -tzf "dist/ia_router-$VERSION.tar.gz" | grep -q "/$f$" || { echo "$f is missing from the package" >&2; exit 1; }; done

say "Installing the wheel in a clean environment"
python3 -m venv "$TMP/clean" && "$TMP/clean/bin/pip" install -q "dist/ia_router-$VERSION-py3-none-any.whl"
[[ "$("$TMP/clean/bin/ia-router" --version)" == "ia-router $VERSION" ]] || { echo "--version does not match" >&2; exit 1; }
ROUTER_HOME="$TMP/home" "$TMP/clean/bin/ia-router" scores >/dev/null && echo "ok: ia-router --version and scores"

if [[ $YES -eq 0 ]]; then
  say "DRY RUN finished: all good. To publish: tools/release.sh --yes"; exit 0
fi

say "Uploading to PyPI"
if [[ ! -f "$HOME/.pypirc" && -z "${TWINE_PASSWORD:-}" ]]; then
  echo "No credentials: create ~/.pypirc with your PyPI token (see docs) or export TWINE_USERNAME=__token__ and TWINE_PASSWORD." >&2; exit 1
fi
"$TMP/venv/bin/twine" upload dist/*

say "Waiting for PyPI to publish the source archive"
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
[[ -n "$URL" ]] || { echo "PyPI did not show the sdist in time; retry the tap part by hand." >&2; exit 1; }
echo "sdist: $URL"; echo "sha256: $SHA"

say "Updating the formula in Mgobeaalcoba/homebrew-tap"
git clone -q https://github.com/Mgobeaalcoba/homebrew-tap.git "$TMP/tap"
python3 - "$TMP/tap/Formula/ia-router.rb" "$URL" "$SHA" <<'PY'
import re, sys
p, url, sha = sys.argv[1:]
s = open(p).read()
s = re.sub(r'url "[^"]*"', f'url "{url}"', s, count=1)
s = re.sub(r'sha256 "[^"]*"', f'sha256 "{sha}"', s, count=1)
open(p, "w").write(s)
PY
( cd "$TMP/tap" && sed -i.bak 's/^> Status:.*$/> Status: available./' README.md && rm -f README.md.bak \
  && git add -A && git commit -q -m "ia-router $VERSION" && git push -q origin main )

say "Verifying both installations"
python3 -m venv "$TMP/pip" && "$TMP/pip/bin/pip" install -q "ia-router==$VERSION" && "$TMP/pip/bin/ia-router" --version
if command -v brew >/dev/null; then
  brew tap Mgobeaalcoba/tap >/dev/null 2>&1 || true
  brew update >/dev/null 2>&1 || true
  if brew list --versions ia-router >/dev/null 2>&1; then brew upgrade Mgobeaalcoba/tap/ia-router; else brew install Mgobeaalcoba/tap/ia-router; fi
  [[ "$(ia-router --version)" == "ia-router $VERSION" ]] || { echo "brew ended up on another version: $(ia-router --version)" >&2; exit 1; }
  brew test ia-router || echo "warning: 'brew test' could not run (on some Macs it fails compiling Homebrew development gems); the installation was verified."
fi
say "Published: ia-router $VERSION on PyPI and Homebrew"
