"""中文行标签 → 附件3 英文字段名映射。

每张报表给出：行标签模式（精确/startswith/contains）与对应 schema 字段。
行标签按正则匹配，按列表顺序匹配第一条即生效。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

Matcher = Callable[[str], bool]


def exact(target: str) -> Matcher:
    return lambda s: s.strip() == target


def starts(prefix: str) -> Matcher:
    return lambda s: s.strip().startswith(prefix)


def contains(*keywords: str) -> Matcher:
    return lambda s: all(k in s for k in keywords)


@dataclass
class FieldRule:
    matcher: Matcher
    field: str
    unit: str = "元"  # 源单位


# ========================= 核心业绩指标 =========================
# 来源：报告摘要 / 主要会计数据和财务指标 表
CORE_RULES: list[FieldRule] = [
    FieldRule(exact("基本每股收益（元/股）"), "eps", "元"),
    FieldRule(exact("基本每股收益(元/股)"), "eps", "元"),
    FieldRule(contains("基本每股收益"), "eps", "元"),
    FieldRule(exact("营业收入（元）"), "total_operating_revenue", "元"),
    FieldRule(exact("营业收入(元)"), "total_operating_revenue", "元"),
    FieldRule(exact("营业总收入"), "total_operating_revenue", "元"),
    FieldRule(exact("营业收入"), "total_operating_revenue", "元"),
    FieldRule(exact("归属于上市公司股东的净利润（元）"), "net_profit_10k_yuan", "元"),
    FieldRule(exact("归属于上市公司股东的净利润(元)"), "net_profit_10k_yuan", "元"),
    FieldRule(contains("归属于上市公司股东的净利润"), "net_profit_10k_yuan", "元"),
    FieldRule(exact("归属于上市公司股东的扣除非经常性损益的净利润（元）"), "net_profit_excl_non_recurring", "元"),
    FieldRule(contains("扣除非经常性损益", "净利润"), "net_profit_excl_non_recurring", "元"),
    FieldRule(exact("经营活动产生的现金流量净额（元）"), "operating_cf_per_share_src", "元"),  # 单位不同，不直接映射
    FieldRule(contains("加权平均净资产收益率"), "roe", "%"),
    FieldRule(contains("归属于上市公司股东的净资产"), "equity_total_equity_src", "元"),
    FieldRule(contains("归属于上市公司股东的所有者权益"), "equity_total_equity_src", "元"),
    FieldRule(contains("归属于母公司所有者权益"), "equity_total_equity_src", "元"),
]

# ========================= 合并资产负债表 =========================
BALANCE_RULES: list[FieldRule] = [
    FieldRule(exact("货币资金"), "asset_cash_and_cash_equivalents", "元"),
    FieldRule(exact("应收账款"), "asset_accounts_receivable", "元"),
    FieldRule(exact("存货"), "asset_inventory", "元"),
    FieldRule(exact("交易性金融资产"), "asset_trading_financial_assets", "元"),
    FieldRule(exact("在建工程"), "asset_construction_in_progress", "元"),
    FieldRule(exact("资产总计"), "asset_total_assets", "元"),
    FieldRule(exact("总资产"), "asset_total_assets", "元"),
    FieldRule(exact("应付账款"), "liability_accounts_payable", "元"),
    FieldRule(exact("预收款项"), "liability_advance_from_customers", "元"),
    FieldRule(exact("合同负债"), "liability_contract_liabilities", "元"),
    FieldRule(exact("短期借款"), "liability_short_term_loans", "元"),
    FieldRule(exact("负债合计"), "liability_total_liabilities", "元"),
    FieldRule(exact("总负债"), "liability_total_liabilities", "元"),
    FieldRule(exact("未分配利润"), "equity_unappropriated_profit", "元"),
    FieldRule(exact("所有者权益合计"), "equity_total_equity", "元"),
    FieldRule(exact("股东权益合计"), "equity_total_equity", "元"),
]

# ========================= 合并利润表 =========================
INCOME_RULES: list[FieldRule] = [
    FieldRule(starts("一、营业总收入"), "total_operating_revenue", "元"),
    FieldRule(exact("营业总收入"), "total_operating_revenue", "元"),
    FieldRule(starts("二、营业总成本"), "total_operating_expenses", "元"),
    FieldRule(exact("营业总成本"), "total_operating_expenses", "元"),
    FieldRule(starts("其中：营业成本"), "operating_expense_cost_of_sales", "元"),
    FieldRule(exact("营业成本"), "operating_expense_cost_of_sales", "元"),
    FieldRule(exact("销售费用"), "operating_expense_selling_expenses", "元"),
    FieldRule(exact("管理费用"), "operating_expense_administrative_expenses", "元"),
    FieldRule(exact("财务费用"), "operating_expense_financial_expenses", "元"),
    FieldRule(exact("研发费用"), "operating_expense_rnd_expenses", "元"),
    FieldRule(exact("税金及附加"), "operating_expense_taxes_and_surcharges", "元"),
    FieldRule(starts("加：其他收益"), "other_income", "元"),
    FieldRule(starts("其他收益"), "other_income", "元"),
    FieldRule(starts("三、营业利润"), "operating_profit", "元"),
    FieldRule(exact("营业利润"), "operating_profit", "元"),
    FieldRule(starts("四、利润总额"), "total_profit", "元"),
    FieldRule(exact("利润总额"), "total_profit", "元"),
    FieldRule(starts("五、净利润"), "net_profit", "元"),
    FieldRule(contains("资产减值损失"), "asset_impairment_loss", "元"),
    FieldRule(contains("信用减值损失"), "credit_impairment_loss", "元"),
]

# ========================= 合并现金流量表 =========================
CASHFLOW_RULES: list[FieldRule] = [
    FieldRule(exact("经营活动产生的现金流量净额"), "operating_cf_net_amount", "元"),
    FieldRule(exact("投资活动产生的现金流量净额"), "investing_cf_net_amount", "元"),
    FieldRule(exact("筹资活动产生的现金流量净额"), "financing_cf_net_amount", "元"),
    FieldRule(starts("五、现金及现金等价物净增加额"), "net_cash_flow", "元"),
    FieldRule(exact("现金及现金等价物净增加额"), "net_cash_flow", "元"),
    FieldRule(starts("销售商品、提供劳务收到的现金"), "operating_cf_cash_from_sales", "元"),
    FieldRule(exact("投资支付的现金"), "investing_cf_cash_for_investments", "元"),
    FieldRule(exact("收回投资收到的现金"), "investing_cf_cash_from_investment_recovery", "元"),
    FieldRule(exact("取得借款收到的现金"), "financing_cf_cash_from_borrowing", "元"),
    FieldRule(exact("偿还债务支付的现金"), "financing_cf_cash_for_debt_repayment", "元"),
]


def match_field(row_label: str, rules: list[FieldRule]) -> FieldRule | None:
    for rule in rules:
        if rule.matcher(row_label):
            return rule
    return None
