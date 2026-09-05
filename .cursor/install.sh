#!/usr/bin/env bash
# Cloud Agent 開発環境セットアップスクリプト
# - RealEstate/re_agent_team (FastAPI Web アプリ) と fx_agent_team (CLI) の
#   両方の依存関係を単一の venv (/workspace/.venv) にインストールする。
# - 冪等: 再実行しても安全。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"

echo "==> システムパッケージのインストール (venv / OCR)"
if command -v sudo >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3.12-venv tesseract-ocr
fi

echo "==> Python venv の作成: ${VENV_DIR}"
if [ ! -x "${VENV_DIR}/bin/python" ]; then
  python3 -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

echo "==> pip のアップグレード"
python -m pip install --upgrade pip

echo "==> RealEstate/re_agent_team の依存関係"
pip install -r "${REPO_ROOT}/RealEstate/re_agent_team/requirements.txt"

echo "==> fx_agent_team の依存関係"
pip install -r "${REPO_ROOT}/fx_agent_team/requirements.txt"

echo "==> Playwright Chromium (スクレイピング用, ベストエフォート)"
python -m playwright install chromium || \
  echo "   Chromium のインストールをスキップ (SCRAPE_USE_BROWSER=0 でブラウザ利用を無効化可)"

echo "==> セットアップ完了"
