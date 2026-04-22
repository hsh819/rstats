#!/usr/bin/env bash
# 用 LLM 跑 task2/task3。先 export 你的 LLM 三件套，再执行本脚本。
#
# 用法（在你本地执行，不要在沙盒里）：
#   export LLM_API_KEY=sk-xxxxxxxx
#   export LLM_BASE_URL=https://api.deepseek.com/v1   # 或你的兼容 endpoint
#   export LLM_MODEL=deepseek-chat                     # 或你的模型名
#   bash scripts/run_with_llm.sh
#
# 注意：脚本不会把 KEY 写入任何文件；只在当前进程读取环境变量。
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -z "${LLM_API_KEY:-}" ]]; then
    echo "ERROR: 请先 export LLM_API_KEY=..." >&2
    exit 2
fi

echo "[run_with_llm] base=${LLM_BASE_URL:-默认 deepseek}  model=${LLM_MODEL:-默认 deepseek-chat}"
echo "[run_with_llm] task2 (70 题)…"
python -m src.task2.answer_q4

echo "[run_with_llm] task3 (80 题)…"
python -m src.task3.answer_q6      # 用现有 RAG 缓存，不加 --rebuild

echo "[run_with_llm] 输出："
ls -lh result/result_2.xlsx result/result_3.xlsx
echo
echo "完成后 git add/commit/push："
echo "  git add result/result_2.xlsx result/result_3.xlsx result/B*.jpg"
echo "  git commit -m 'chore(task2,task3): LLM-enhanced 全量结果'"
echo "  git push"
