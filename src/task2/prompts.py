"""任务二 prompt 模板：NL → 意图 / SQL / 回答。"""
from __future__ import annotations

# ========= schema 简化描述 =========
DB_SCHEMA_BRIEF = """\
# 四张核心表（SQLite，金额单位：万元；同比/毛利率/净利率等为百分比数字本身，如 12.34 表示 12.34%）

## companies（公司基本信息）
stock_code TEXT PK, stock_abbr, company_name, english_name, csrc_industry,
exchange, security_type, registered_area, registered_capital, employee_count, management_count

## core_performance_indicators_sheet（核心业绩指标）
stock_code, stock_abbr, report_year INT, report_period TEXT,  -- period ∈ {Q1, HY, Q3, FY}
eps, total_operating_revenue, operating_revenue_yoy_growth, operating_revenue_qoq_growth,
net_profit_10k_yuan, net_profit_yoy_growth, net_profit_qoq_growth,
net_asset_per_share, roe, operating_cf_per_share,
net_profit_excl_non_recurring, net_profit_excl_non_recurring_yoy,
gross_profit_margin, net_profit_margin, roe_weighted_excl_non_recurring

## balance_sheet（资产负债表）
stock_code, stock_abbr, report_year, report_period,
asset_cash_and_cash_equivalents, asset_accounts_receivable, asset_inventory,
asset_trading_financial_assets, asset_construction_in_progress,
asset_total_assets, asset_total_assets_yoy_growth,
liability_accounts_payable, liability_advance_from_customers,
liability_total_liabilities, liability_total_liabilities_yoy_growth,
liability_contract_liabilities, liability_short_term_loans,
asset_liability_ratio,
equity_unappropriated_profit, equity_total_equity

## income_sheet（利润表）
stock_code, stock_abbr, report_year, report_period,
net_profit, net_profit_yoy_growth, other_income,
total_operating_revenue, operating_revenue_yoy_growth,
operating_expense_cost_of_sales, operating_expense_selling_expenses,
operating_expense_administrative_expenses, operating_expense_financial_expenses,
operating_expense_rnd_expenses, operating_expense_taxes_and_surcharges,
total_operating_expenses, operating_profit, total_profit,
asset_impairment_loss, credit_impairment_loss

## cash_flow_sheet（现金流量表）
stock_code, stock_abbr, report_year, report_period,
net_cash_flow, net_cash_flow_yoy_growth,
operating_cf_net_amount, operating_cf_ratio_of_net_cf, operating_cf_cash_from_sales,
investing_cf_net_amount, investing_cf_ratio_of_net_cf,
investing_cf_cash_for_investments, investing_cf_cash_from_investment_recovery,
financing_cf_cash_from_borrowing, financing_cf_cash_for_debt_repayment,
financing_cf_net_amount, financing_cf_ratio_of_net_cf
"""

INTENT_PROMPT = """\
你是一个财报数据分析助手。根据用户问题判断意图，返回 JSON：
{
  "intent": "query | trend | rank | compare | clarify",
  "entities": { "company": [...], "year": [...], "period": [...], "field": [...] },
  "need_clarify": bool,
  "clarify_question": "若缺信息给出一个简短澄清问题，否则空串"
}

意图定义：
- query  明确指标数值（单点）。
- trend  时间序列趋势。
- rank   多家公司排名 / top-N。
- compare 两家公司对比。
- clarify 信息不足，需澄清。

period ∈ {Q1, HY, Q3, FY}；year 为 4 位整数；company 是 A 股简称或股票代码。
"""

NL2SQL_PROMPT_TEMPLATE = """\
你是一个 SQL 生成器。仅基于下面的数据库 schema，生成一条 SQLite `SELECT` 语句回答用户问题。

{schema}

约束：
1. 仅生成单条 SELECT 语句；禁止 INSERT / UPDATE / DELETE / DROP / ATTACH。
2. 金额字段单位为「万元」，不要再自行乘除。
3. 若涉及公司名称，优先用 stock_abbr 匹配。
4. 返回 JSON：{{"sql": "…", "chart_type": "line|bar|pie|table"}}。

已知实体：{entities}
用户问题：{question}
"""

ANSWER_PROMPT_TEMPLATE = """\
你是一个财经助手，请根据「问题」和「SQL 查询结果」写一段中文回答，不超过 80 字。
若为单点查询：直接给出数值，带单位（万元 / %）。
若为趋势：总结变化方向与量级。
若为排名：列出 top 3 ~ 5。
返回 JSON：{{"answer": "…"}}。

问题：{question}
SQL：{sql}
结果：{result}
"""
