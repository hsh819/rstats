#!/usr/bin/env bash
# 端到端：拉数据 → 建库 → 跑任务二/三
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d "data/B题-全部数据" ]]; then
    python scripts/fetch_data.py
fi

JOBS="${JOBS:-4}"

echo "[run_all] task1: build SQLite (jobs=$JOBS)"
python -m src.task1.build_db --jobs "$JOBS"

echo "[run_all] task2"
python -m src.task2.answer_q4

echo "[run_all] task3"
python -m src.task3.answer_q6 --rebuild

ls -la result/result_2.xlsx result/result_3.xlsx
