#!/bin/bash
# 서버 실행. 화면 기록/손쉬운 사용 권한은 이 스크립트를 실행한 터미널 앱에 주어야 합니다.
cd "$(dirname "$0")"
export PYTHONUNBUFFERED=1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ ! -x .venv/bin/python ]; then
  echo "먼저 ./setup.sh 를 실행하세요."
  exit 1
fi
exec .venv/bin/python -m app.server "$@"
