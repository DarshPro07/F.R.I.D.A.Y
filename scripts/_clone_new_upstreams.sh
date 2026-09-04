#!/usr/bin/env bash
# One-off helper: finish the medusa sparse checkout (its www/ docs tree
# exceeds Windows MAX_PATH) and clone smartstore with longpaths.
set -u
cd "$(dirname "$0")/../third_party/upstream" || exit 1

if [ -d medusa ]; then
  cd medusa
  git config core.longpaths true
  git sparse-checkout init --no-cone
  printf '/*\n!/www/\n' > .git/info/sparse-checkout
  git checkout -q HEAD -- . 2>&1 | tail -2
  echo "medusa tracked: $(git ls-files | wc -l)"
  cd ..
fi

if [ ! -d smartstore ]; then
  git clone --depth 1 -q -c core.longpaths=true https://github.com/smartstore/Smartstore smartstore 2>&1 | tail -2
fi

for d in openmausbot medusa smartstore; do
  if [ -d "$d" ]; then
    echo "$d $(git -C "$d" rev-parse HEAD) lic=$(ls "$d" | grep -i '^licen' | head -1)"
  else
    echo "$d MISSING"
  fi
done
