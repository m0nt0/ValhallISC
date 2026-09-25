# sourced by the build scripts: record the git revision in irisfs/_build.py
SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
git diff --quiet HEAD 2>/dev/null || SHA="${SHA}-dirty"
printf 'GIT_SHA = "%s"\n' "$SHA" > src/irisfs/_build.py
