#!/usr/bin/env bash
# RUBI 런처 — 항상 프로젝트 venv 의 파이썬으로 실행한다.
# 사용:  ./run.sh run --serve sample_app/static --source sample_app/static
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "[RUBI] venv 가 없습니다. 먼저 설치하세요:" >&2
  echo "  python3 -m venv .venv" >&2
  echo "  ./.venv/bin/pip install -r requirements.txt" >&2
  echo "  ./.venv/bin/python -m playwright install chromium" >&2
  exit 1
fi

# playwright/anthropic 설치 여부 빠른 확인
if ! "$PY" -c "import playwright, anthropic" 2>/dev/null; then
  echo "[RUBI] 의존성이 빠졌습니다. 설치 중…" >&2
  "$PY" -m pip install --quiet -r "$DIR/requirements.txt"
  "$PY" -m playwright install chromium
fi

exec "$PY" -m rubi "$@"
