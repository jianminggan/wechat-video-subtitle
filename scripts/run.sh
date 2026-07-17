#!/usr/bin/env sh
set -eu

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

if command -v python3 >/dev/null 2>&1; then
  exec python3 -u "$(dirname "$0")/main.py" "$@"
fi

if command -v python >/dev/null 2>&1; then
  exec python -u "$(dirname "$0")/main.py" "$@"
fi

echo "Python 3.10 or newer was not found in PATH." >&2
exit 2
