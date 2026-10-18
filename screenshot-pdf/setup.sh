#!/bin/bash
# 맥미니에서 한 번만 실행: OCR 도구, 한국어 OCR 데이터, 파이썬 가상환경 설치
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v brew >/dev/null; then
  echo "Homebrew가 필요합니다: https://brew.sh 에서 설치 후 다시 실행하세요."
  exit 1
fi

brew list --formula ocrmypdf >/dev/null 2>&1 || brew install ocrmypdf

# 한국어 OCR 데이터 (tesseract-lang 전체는 수백 MB라 kor만 받음)
TESSDATA="$(brew --prefix)/share/tessdata"
if [ ! -f "$TESSDATA/kor.traineddata" ]; then
  echo "한국어 OCR 데이터 다운로드..."
  curl -fL --progress-bar -o "$TESSDATA/kor.traineddata" \
    https://github.com/tesseract-ocr/tessdata/raw/main/kor.traineddata
fi

PY="${PYTHON:-python3}"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

chmod +x run.sh start.command
echo
echo "설치 완료. ./run.sh 로 서버를 실행하세요."
