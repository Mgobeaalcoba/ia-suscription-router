#!/usr/bin/env bash
# Verifica que todos los commits entre <base> y <head> estén firmados (Signed-off-by) para el DCO. Los merges se ignoran.
#   tools/check_dco.sh origin/main HEAD
set -euo pipefail
BASE="${1:?uso: tools/check_dco.sh <base> <head>}"; HEAD_REF="${2:-HEAD}"
missing=0
for c in $(git rev-list --no-merges "$BASE..$HEAD_REF"); do
  if ! git log -1 --format=%B "$c" | grep -qE '^Signed-off-by: .+ <[^<>@ ]+@[^<>@ ]+>[[:space:]]*$'; then
    echo "Falta la firma DCO en $(git log -1 --format='%h %s' "$c")"; missing=$((missing + 1))
  fi
done
if [[ $missing -gt 0 ]]; then
  echo; echo "$missing commit(s) sin 'Signed-off-by'. Firmá con 'git commit -s' o, para varios, 'git rebase --signoff $BASE'. Ver CONTRIBUTING.md."; exit 1
fi
echo "Todos los commits están firmados (DCO)."
