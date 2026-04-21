-- 2026 泰迪杯 B 题 — 结构化财报数据库（SQLite 方言，兼容 MySQL 字段类型注释）
-- 字段严格对齐「附件3：数据库-表名及字段说明.xlsx」

PRAGMA foreign_keys = ON;

-- 公司基本信息（附件1）
CREATE TABLE IF NOT EXISTS companies (
    serial_number    INTEGER,
    stock_code       TEXT PRIMARY KEY,
    stock_abbr       TEXT,
    company_name     TEXT,
    english_name     TEXT,
    csrc_industry    TEXT,
    exchange         TEXT,
    security_type    TEXT,
    registered_area  TEXT,
    registered_capital TEXT,
    employee_count   INTEGER,
    management_count INTEGER
);

-- 核心业绩指标表
CREATE TABLE IF NOT EXISTS core_performance_indicators_sheet (
    serial_number                  INTEGER,
    stock_code                     TEXT NOT NULL,
    stock_abbr                     TEXT,
    eps                            NUMERIC,
    total_operating_revenue        NUMERIC,
    operating_revenue_yoy_growth   NUMERIC,
    operating_revenue_qoq_growth   NUMERIC,
    net_profit_10k_yuan            NUMERIC,
    net_profit_yoy_growth          NUMERIC,
    net_profit_qoq_growth          NUMERIC,
    net_asset_per_share            NUMERIC,
    roe                            NUMERIC,
    operating_cf_per_share         NUMERIC,
    net_profit_excl_non_recurring  NUMERIC,
    net_profit_excl_non_recurring_yoy NUMERIC,
    gross_profit_margin            NUMERIC,
    net_profit_margin              NUMERIC,
    roe_weighted_excl_non_recurring NUMERIC,
    report_period                  TEXT NOT NULL,
    report_year                    INTEGER NOT NULL,
    PRIMARY KEY (stock_code, report_year, report_period)
);

-- 资产负债表
CREATE TABLE IF NOT EXISTS balance_sheet (
    serial_number                          INTEGER,
    stock_code                             TEXT NOT NULL,
    stock_abbr                             TEXT,
    asset_cash_and_cash_equivalents        NUMERIC,
    asset_accounts_receivable              NUMERIC,
    asset_inventory                        NUMERIC,
    asset_trading_financial_assets         NUMERIC,
    asset_construction_in_progress         NUMERIC,
    asset_total_assets                     NUMERIC,
    asset_total_assets_yoy_growth          NUMERIC,
    liability_accounts_payable             NUMERIC,
    liability_advance_from_customers       NUMERIC,
    liability_total_liabilities            NUMERIC,
    liability_total_liabilities_yoy_growth NUMERIC,
    liability_contract_liabilities         NUMERIC,
    liability_short_term_loans             NUMERIC,
    asset_liability_ratio                  NUMERIC,
    equity_unappropriated_profit           NUMERIC,
    equity_total_equity                    NUMERIC,
    report_period                          TEXT NOT NULL,
    report_year                            INTEGER NOT NULL,
    PRIMARY KEY (stock_code, report_year, report_period)
);

-- 现金流量表
CREATE TABLE IF NOT EXISTS cash_flow_sheet (
    serial_number                              INTEGER,
    stock_code                                 TEXT NOT NULL,
    stock_abbr                                 TEXT,
    net_cash_flow                              NUMERIC,
    net_cash_flow_yoy_growth                   NUMERIC,
    operating_cf_net_amount                    NUMERIC,
    operating_cf_ratio_of_net_cf               NUMERIC,
    operating_cf_cash_from_sales               NUMERIC,
    investing_cf_net_amount                    NUMERIC,
    investing_cf_ratio_of_net_cf               NUMERIC,
    investing_cf_cash_for_investments          NUMERIC,
    investing_cf_cash_from_investment_recovery NUMERIC,
    financing_cf_cash_from_borrowing           NUMERIC,
    financing_cf_cash_for_debt_repayment       NUMERIC,
    financing_cf_net_amount                    NUMERIC,
    financing_cf_ratio_of_net_cf               NUMERIC,
    report_period                              TEXT NOT NULL,
    report_year                                INTEGER NOT NULL,
    PRIMARY KEY (stock_code, report_year, report_period)
);

-- 利润表
CREATE TABLE IF NOT EXISTS income_sheet (
    serial_number                             INTEGER,
    stock_code                                TEXT NOT NULL,
    stock_abbr                                TEXT,
    net_profit                                NUMERIC,
    net_profit_yoy_growth                     NUMERIC,
    other_income                              NUMERIC,
    total_operating_revenue                   NUMERIC,
    operating_revenue_yoy_growth              NUMERIC,
    operating_expense_cost_of_sales           NUMERIC,
    operating_expense_selling_expenses        NUMERIC,
    operating_expense_administrative_expenses NUMERIC,
    operating_expense_financial_expenses      NUMERIC,
    operating_expense_rnd_expenses            NUMERIC,
    operating_expense_taxes_and_surcharges    NUMERIC,
    total_operating_expenses                  NUMERIC,
    operating_profit                          NUMERIC,
    total_profit                              NUMERIC,
    asset_impairment_loss                     NUMERIC,
    credit_impairment_loss                    NUMERIC,
    report_period                             TEXT NOT NULL,
    report_year                               INTEGER NOT NULL,
    PRIMARY KEY (stock_code, report_year, report_period)
);

-- 校验报告（每个 PDF / 规则一条）
CREATE TABLE IF NOT EXISTS validation_report (
    pdf_path    TEXT,
    stock_code  TEXT,
    report_year INTEGER,
    report_period TEXT,
    table_name  TEXT,
    field_name  TEXT,
    rule        TEXT,
    status      TEXT,
    diff        NUMERIC,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_core_company ON core_performance_indicators_sheet(stock_code);
CREATE INDEX IF NOT EXISTS idx_balance_company ON balance_sheet(stock_code);
CREATE INDEX IF NOT EXISTS idx_cash_flow_company ON cash_flow_sheet(stock_code);
CREATE INDEX IF NOT EXISTS idx_income_company ON income_sheet(stock_code);
