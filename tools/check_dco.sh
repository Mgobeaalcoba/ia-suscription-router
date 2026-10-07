#!/usr/bin/env bash
# Checks that every commit between <base> and <head> is signed off (Signed-off-by) for the DCO. Merges are ignored.
#   tools/check_dco.sh origin/main HEAD
set -euo pipefail
BASE="${1:?usage: tools/check_dco.sh <base> <head>}"; HEAD_REF="${2:-HEAD}"
missing=0
for c in $(git rev-list --no-merges "$BASE..$HEAD_REF"); do
  if ! git log -1 --format=%B "$c" | grep -qE '^Signed-off-by: .+ <[^<>@ ]+@[^<>@ ]+>[[:space:]]*$'; then
    echo "Missing DCO sign-off on $(git log -1 --format='%h %s' "$c")"; missing=$((missing + 1))
  fi
done
if [[ $missing -gt 0 ]]; then
  echo; echo "$missing commit(s) without 'Signed-off-by'. Sign off with 'git commit -s' or, for several, 'git rebase --signoff $BASE'. See CONTRIBUTING.md."; exit 1
fi
echo "All commits are signed off (DCO)."
