#!/usr/bin/env bash
# 测试数据集端到端：拉数据 → 建库 → 跑 task2/3。所有产出落到 test/ 目录。
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. 拉测试数据（幂等）
if [[ ! -d "data/B题-测试数据" ]]; then
    python scripts/fetch_test_data.py
fi

# 2. 列出测试说明
echo "[run_test] 测试说明文件："
find "data/B题-测试数据" -maxdepth 3 -iname "*测试说明*" -o -iname "*readme*" -o -iname "*说明*" 2>/dev/null | head -10 || true

# 3. 准备测试输出目录
mkdir -p test/db test/refs

# 4. 设环境变量切换到测试集
export DATA_DIR="data/B题-测试数据"
export DB_DIR="test/db"
export RESULT_DIR="test"

JOBS="${JOBS:-4}"

echo "[run_test] task1 (jobs=$JOBS)"
python -m src.task1.build_db --jobs "$JOBS"

echo "[run_test] task2"
python -m src.task2.answer_q4

echo "[run_test] task3"
python -m src.task3.answer_q6 --rebuild

echo "[run_test] 完成。产出："
ls -lh test/result_2.xlsx test/result_3.xlsx 2>/dev/null
