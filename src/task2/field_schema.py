"""共享字段元数据：中文关键词 → (table, column, unit, kind, aliases)。

kind 取值：
  amount   金额（万元）
  percent  百分比（值本身即 12.34 表示 12.34%）
  ratio    比率（百分比的另一叫法）
  count    计数（整数）
  text     非数值
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldMeta:
    table: str
    column: str
    unit: str
    kind: str  # amount | percent | ratio | count | text


# key 为中文主名；同义词表在 SYNONYM_ALIASES
FIELD_META: dict[str, FieldMeta] = {
    # income_sheet
    "利润总额": FieldMeta("income_sheet", "total_profit", "万元", "amount"),
    "净利润": FieldMeta("income_sheet", "net_profit", "万元", "amount"),
    "营业利润": FieldMeta("income_sheet", "operating_profit", "万元", "amount"),
    "营业总收入": FieldMeta("income_sheet", "total_operating_revenue", "万元", "amount"),
    "营业收入": FieldMeta("income_sheet", "total_operating_revenue", "万元", "amount"),
    "主营业务收入": FieldMeta("income_sheet", "total_operating_revenue", "万元", "amount"),
    "营业总支出": FieldMeta("income_sheet", "total_operating_expenses", "万元", "amount"),
    "销售费用": FieldMeta("income_sheet", "operating_expense_selling_expenses", "万元", "amount"),
    "管理费用": FieldMeta("income_sheet", "operating_expense_administrative_expenses", "万元", "amount"),
    "财务费用": FieldMeta("income_sheet", "operating_expense_financial_expenses", "万元", "amount"),
    "研发费用": FieldMeta("income_sheet", "operating_expense_rnd_expenses", "万元", "amount"),
    "营业成本": FieldMeta("income_sheet", "operating_expense_cost_of_sales", "万元", "amount"),
    "税金及附加": FieldMeta("income_sheet", "operating_expense_taxes_and_surcharges", "万元", "amount"),
    "资产减值损失": FieldMeta("income_sheet", "asset_impairment_loss", "万元", "amount"),
    "信用减值损失": FieldMeta("income_sheet", "credit_impairment_loss", "万元", "amount"),
    "净利润同比": FieldMeta("income_sheet", "net_profit_yoy_growth", "%", "percent"),
    "营业收入同比": FieldMeta("income_sheet", "operating_revenue_yoy_growth", "%", "percent"),

    # core_performance_indicators_sheet
    "每股收益": FieldMeta("core_performance_indicators_sheet", "eps", "元", "amount"),
    "基本每股收益": FieldMeta("core_performance_indicators_sheet", "eps", "元", "amount"),
    "EPS": FieldMeta("core_performance_indicators_sheet", "eps", "元", "amount"),
    "ROE": FieldMeta("core_performance_indicators_sheet", "roe", "%", "percent"),
    "净资产收益率": FieldMeta("core_performance_indicators_sheet", "roe", "%", "percent"),
    "加权净资产收益率": FieldMeta("core_performance_indicators_sheet", "roe_weighted_excl_non_recurring", "%", "percent"),
    "毛利率": FieldMeta("core_performance_indicators_sheet", "gross_profit_margin", "%", "percent"),
    "净利率": FieldMeta("core_performance_indicators_sheet", "net_profit_margin", "%", "percent"),
    "扣非净利润": FieldMeta("core_performance_indicators_sheet", "net_profit_excl_non_recurring", "万元", "amount"),
    "每股净资产": FieldMeta("core_performance_indicators_sheet", "net_asset_per_share", "元", "amount"),
    "每股经营现金流": FieldMeta("core_performance_indicators_sheet", "operating_cf_per_share", "元", "amount"),
    "营收环比": FieldMeta("core_performance_indicators_sheet", "operating_revenue_qoq_growth", "%", "percent"),
    "净利润环比": FieldMeta("core_performance_indicators_sheet", "net_profit_qoq_growth", "%", "percent"),

    # balance_sheet
    "总资产": FieldMeta("balance_sheet", "asset_total_assets", "万元", "amount"),
    "总负债": FieldMeta("balance_sheet", "liability_total_liabilities", "万元", "amount"),
    "资产负债率": FieldMeta("balance_sheet", "asset_liability_ratio", "%", "percent"),
    "货币资金": FieldMeta("balance_sheet", "asset_cash_and_cash_equivalents", "万元", "amount"),
    "应收账款": FieldMeta("balance_sheet", "asset_accounts_receivable", "万元", "amount"),
    "存货": FieldMeta("balance_sheet", "asset_inventory", "万元", "amount"),
    "应付账款": FieldMeta("balance_sheet", "liability_accounts_payable", "万元", "amount"),
    "预收款项": FieldMeta("balance_sheet", "liability_advance_from_customers", "万元", "amount"),
    "合同负债": FieldMeta("balance_sheet", "liability_contract_liabilities", "万元", "amount"),
    "短期借款": FieldMeta("balance_sheet", "liability_short_term_loans", "万元", "amount"),
    "未分配利润": FieldMeta("balance_sheet", "equity_unappropriated_profit", "万元", "amount"),
    "所有者权益": FieldMeta("balance_sheet", "equity_total_equity", "万元", "amount"),
    "股东权益": FieldMeta("balance_sheet", "equity_total_equity", "万元", "amount"),
    "交易性金融资产": FieldMeta("balance_sheet", "asset_trading_financial_assets", "万元", "amount"),
    "在建工程": FieldMeta("balance_sheet", "asset_construction_in_progress", "万元", "amount"),

    # cash_flow_sheet
    "经营活动现金流": FieldMeta("cash_flow_sheet", "operating_cf_net_amount", "万元", "amount"),
    "经营现金流": FieldMeta("cash_flow_sheet", "operating_cf_net_amount", "万元", "amount"),
    "投资活动现金流": FieldMeta("cash_flow_sheet", "investing_cf_net_amount", "万元", "amount"),
    "筹资活动现金流": FieldMeta("cash_flow_sheet", "financing_cf_net_amount", "万元", "amount"),
    "融资活动现金流": FieldMeta("cash_flow_sheet", "financing_cf_net_amount", "万元", "amount"),
    "销售商品收到现金": FieldMeta("cash_flow_sheet", "operating_cf_cash_from_sales", "万元", "amount"),
    "投资支付": FieldMeta("cash_flow_sheet", "investing_cf_cash_for_investments", "万元", "amount"),
    "借款取得": FieldMeta("cash_flow_sheet", "financing_cf_cash_from_borrowing", "万元", "amount"),
    "偿还债务": FieldMeta("cash_flow_sheet", "financing_cf_cash_for_debt_repayment", "万元", "amount"),
    "现金流净额": FieldMeta("cash_flow_sheet", "net_cash_flow", "万元", "amount"),
}


# 同义词 → 主名。route 时先按同义词匹配，再查 FIELD_META。
SYNONYM_ALIASES: dict[str, str] = {
    "归母净利润": "净利润",
    "归属母公司净利润": "净利润",
    "净利润(万元)": "净利润",
    "营收": "营业收入",
    "总营收": "营业总收入",
    "营收同比": "营业收入同比",
    "研发投入": "研发费用",
    "R&D": "研发费用",
    "eps": "每股收益",
    "roe": "ROE",
    "净资产收益率(加权)": "加权净资产收益率",
    "总资产额": "总资产",
    "负债总额": "总负债",
    "资产总额": "总资产",
    "账上现金": "货币资金",
    "现金及现金等价物": "货币资金",
    "营业活动现金流量净额": "经营活动现金流",
    "利润": "净利润",
    "利润额": "净利润",
    "盈利": "净利润",
    "销售额": "营业收入",
    "销售收入": "营业收入",
    "利润同比": "净利润同比",
    "营收年同比": "营业收入同比",
    "利润年同比": "净利润同比",
    "收益率": "ROE",
    "投资回报率": "ROE",
    "投资收益率": "ROE",
    "销售毛利率": "毛利率",
    "销售净利率": "净利率",
    "净利润率": "净利率",
    "资产负债比率": "资产负债率",
    "总收入": "营业总收入",
    "营业总收入同比增长率": "营业收入同比",
    "营业收入同比增长率": "营业收入同比",
    "净利润同比增长率": "净利润同比",
    "收入": "营业收入",
    "收入情况": "营业收入",
    "营收情况": "营业收入",
    "经营性现金流量净额": "经营活动现金流",
    "经营性现金流净额": "经营活动现金流",
    "投资性现金流量净额": "投资活动现金流",
    "筹资性现金流量净额": "筹资活动现金流",
    "核心利润指标": "净利润",
    "财务指标": "净利润",
    "研发费用占比": "研发费用",
    "扣非": "扣非净利润",
    "扣非净利润率": "扣非净利润",
    "负债总额": "总负债",
}


def canonical_field(token: str) -> str | None:
    """把用户输入的字段词规范化为 FIELD_META 的 key；无匹配返回 None。"""
    if not token:
        return None
    if token in FIELD_META:
        return token
    lower = token.lower()
    for k in FIELD_META:
        if k.lower() == lower:
            return k
    if token in SYNONYM_ALIASES:
        return SYNONYM_ALIASES[token]
    for alias, canon in SYNONYM_ALIASES.items():
        if alias.lower() == lower:
            return canon
    return None


def all_keywords() -> list[str]:
    """供规则 intent router 的字段扫描用：主名 + 所有别名。"""
    return list(FIELD_META.keys()) + list(SYNONYM_ALIASES.keys())
