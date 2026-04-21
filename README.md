# 2026 泰迪杯 B 题 — 上市公司财报"智能问数"助手

## 结构
```
data/     原始附件（示例数据）
db/       SQLite 数据库 (finance.db) + 校验报告 (validation_report.csv)
result/   图表与 result_2.xlsx / result_3.xlsx
src/      代码
scripts/  数据拉取与一键运行
```

## 环境
```bash
pip install -r requirements.txt
export LLM_API_KEY=sk-xxx
export LLM_BASE_URL=https://api.deepseek.com/v1   # 或通义/GLM 兼容端点
export LLM_MODEL=deepseek-chat
```

## 运行
```bash
python scripts/fetch_data.py            # 从 GitHub 拉取示例数据到 data/
python -m src.task1.build_db            # 任务一：PDF→SQLite 入库 + 校验
python -m src.task2.answer_q4           # 任务二：回答附件4 问题，产出 result_2.xlsx
python -m src.task3.answer_q6           # 任务三：回答附件6 问题，产出 result_3.xlsx
```

## 完成度
- **任务一**：完整实现 — 文件名分类 / 表格抽取 / 字段映射 / 勾稽校验 / SQLite 入库
- **任务二**：骨架 + 样例跑通（B1001/B1002）
- **任务三**：骨架 + 样例跑通（B2003）
