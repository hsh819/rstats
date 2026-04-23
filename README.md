# 2026 泰迪杯 B 题 — 上市公司财报"智能问数"助手

基于附件 1–6 的 66 家中药上市公司数据，交付一套 PDF → SQLite → 自然语言问答 + 可视化 + 研报归因的端到端系统。

## 目录结构
```
data/                  原始附件（fetch_data.py 从 GitHub Release 拉取）
  └─ B题-全部数据/
       ├─ 附件1：中药上市公司基本信息…xlsx   (66 家)
       ├─ 附件2：财务报告/                   (1247 份 PDF)
       ├─ 附件3：数据库-表名及字段说明.xlsx
       ├─ 附件4：问题汇总.xlsx               (任务二 70 题)
       ├─ 附件5：研报数据/                   (473 份 PDF)
       └─ 附件6：问题汇总.xlsx               (任务三 80 题)

db/
  ├─ schema.sql         DDL
  ├─ finance.db         结果库（companies + 4 张财务表 + validation_report）
  ├─ validation_report.csv
  └─ rag_chunks_<hash>.pkl    RAG 索引缓存

result/
  ├─ result_2.xlsx / result_3.xlsx
  ├─ B*_*.jpg           图表（折线/柱状/饼/散点/直方）
  └─ refs/              研报页截图（gitignored）

src/
  ├─ config.py          路径 + LLM 环境变量
  ├─ llm_client.py      OpenAI 兼容客户端（无 KEY 自动降级）
  ├─ task1/             PDF→SQLite：pdf_parser / report_classifier / field_mapper / validator / build_db
  ├─ task2/             NL→SQL：intent_router / field_schema / advanced_rules / answer_q4 / chart / dialogue / prompts / sql_runner
  ├─ task3/             RAG + 归因：rag_index / planner / attribution / paper_image / answer_q6
  └─ utils/             period / cn_number / excel_io

scripts/
  ├─ fetch_data.py      GitHub Release 下载 + GBK 解码解压
  ├─ run_all.sh         一键跑 task1 + task2 + task3
  └─ run_with_llm.sh    有 LLM KEY 时的本地运行入口
```

## 快速开始
```bash
# 1. 依赖
pip install -r requirements.txt

# 2. 拉数据（首次约 30s，解压 ~2GB 到 data/B题-全部数据/）
python scripts/fetch_data.py

# 3. 建库（4 进程并发，1247 PDF 约 40 min）
python -m src.task1.build_db --jobs 4

# 4. 任务二 + 任务三（任务三首次会建 RAG 索引，约 15 min）
python -m src.task2.answer_q4
python -m src.task3.answer_q6
```

## LLM 增强（可选）
无 `LLM_API_KEY` 时系统走规则路径（覆盖率 99% / 95%）。设置 KEY 后自动启用 LLM NL→SQL 与归因总结：
```bash
export LLM_API_KEY=sk-xxx
export LLM_BASE_URL=https://api.deepseek.com/v1   # 或 SiliconFlow/通义/智谱 兼容端点
export LLM_MODEL=deepseek-chat
bash scripts/run_with_llm.sh
```

## 输出契约（附件 7 格式）
### result_2.xlsx（任务二表 2）
`编号 | 问题类型 | 问题 | SQL 查询语句 | 图形格式 | 回答 | 图表`

"回答" 字段为 JSON：
```json
{"问题编号": "B1001", "问题类型": "数据基本查询",
 "子问题": [{"Q": "...", "A": "...", "SQL": "...", "image": "B1001_1.jpg"}]}
```

### result_3.xlsx（任务三表 5）
`编号 | 问题类型 | 问题 | SQL 查询语句 | 图形格式 | 回答 | 图表 | 研报截图`

"回答" JSON：
```json
{"问题编号": "B2003", "问题类型": "归因分析",
 "结构化结果": [{"子任务": "t1", "意图": "trend", "Q": "...", "SQL": "...",
                "图形格式": "line", "结果数据": [...], "image": "B2003_1.jpg"},
                {"子任务": "t2", "意图": "attribution", "Q": "...", "答案": "..."}],
 "references": [{"paper_path": "...", "paper_title": "...", "page": 2,
                 "text": "...", "paper_image": "refs/...jpg"}]}
```

## 覆盖率（规则路径，无 LLM）
| 任务 | 输入规模 | SQL 命中 | 可视化 | 研报引用 |
|---|---|---|---|---|
| 任务一 | 1247 PDF / 66 公司 | 793 行 × 4 表；155 OK / 811 WARN | — | — |
| 任务二 | 70 题 | **69/70 (99%)** | 53 张图 | — |
| 任务三 | 80 题 / 23k chunks | **76/80 (95%)** | 67 张图 | 44/80（175 条） |

规则路径包含 **高级模板**（`src/task2/advanced_rules.py`）处理：
- 多表 JOIN + 列对比（`短期借款 > 货币资金` / `净利润 vs 经营现金流`）
- 跨表数值不一致检测（`core.total_operating_revenue ≠ income.total_operating_revenue`）
- 差值 Top-N（`ABS(扣非 - 净利润)` 排序）
- 百分点差值（`A 增长率 - B 增长率 > 10 个百分点`）
- 比值分布直方图（`经营现金流 / 净利润`）
- 两字段相关性散点
- CAGR 复合增长率分布（Python 端聚合后直方）
- 双年 Top-N 对比（`ROW_NUMBER()` 分区）

## 数据要求
- 附件 2/5 的 PDF 文件名必须符合：
  - 上交所 `<股票代码>_YYYYMMDD_<哈希>.pdf`（披露日启发识别期间）
  - 深交所 `<简称>：YYYY年[第]<周期>报告[摘要][（更正后）].pdf`
- 附件 3 字段说明是 DDL 的唯一真值来源；`src/task1/field_mapper.py` 含中文→英文列名映射
- 附件 5 元数据 `stockCode` 是必填（RAG 召回按 code 过滤）

## 已知限制
- 数据 DB 不全：例如 `asset_liability_ratio` 列 0 非空（部分报告未直接披露），需从 `liability_total / asset_total` 回填（未实现）
- 聚合字段 `MEDIAN` 用 SQLite 内置 `AVG` 近似
- 规则路径不处理：OCR 扫描版 PDF、数据源外问题（如"香雪制药"不在 66 家中）
- B1065 "出口业务占比" 不在 schema（附件 3 未披露）

## Git 工作流
```bash
# 开发分支：claude/teddy-cup-challenge-Z1i6t
git checkout claude/teddy-cup-challenge-Z1i6t
git add result/ src/
git commit -m "..."
git push
```
`result/refs/`、`data/`、`db/finance.db`、`db/rag_chunks_*.pkl` 均已 gitignore。
