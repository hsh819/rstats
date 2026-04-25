"""高级规则：处理 rule_nl2sql 覆盖不到的场景。

本模块针对"多表 JOIN + 列对比 / 分布直方图 / 散点 / 两期对比 / CAGR 分布"
这类复杂查询，用模板化 SQL 生成。与 rule_nl2sql 互补：answer_q4 会先试 LLM，
失败则依次尝试 advanced_rules.try_build(intent, question) 和 rule_nl2sql(intent)。

每个 pattern 返回 (sql, chart_type) 或 None。
"""
from __future__ import annotations

import re
from typing import Optional

from .field_schema import FIELD_META, canonical_field
from . import intent_router


# ===== 通用工具 =====
def _extract_fields(q: str, k: int = 2) -> list[str]:
    """从问题里提取 k 个字段候选。按位置出现顺序；同位置取最长关键字；按 column 去重。"""
    from .field_schema import all_keywords

    # 按关键字长度降序（长词优先），所以 "总资产同比" 先匹配，"总资产" 后匹配
    kws = sorted(all_keywords(), key=len, reverse=True)
    consumed: list[tuple[int, int]] = []  # 已被长词占用的位置区间
    matches: list[tuple[int, str, str]] = []  # (pos, canonical, column)

    def overlaps(a0, a1):
        return any(not (a1 <= s or a0 >= e) for s, e in consumed)

    for kw in kws:
        start = 0
        while True:
            pos = q.find(kw, start)
            if pos < 0:
                break
            end = pos + len(kw)
            if overlaps(pos, end):
                start = end
                continue
            consumed.append((pos, end))
            canon = canonical_field(kw)
            if canon and canon in FIELD_META:
                col = FIELD_META[canon].column
                # 同 column 已出现过就跳过
                if not any(c == col for _, _, c in matches):
                    matches.append((pos, canon, col))
            start = end
    matches.sort(key=lambda x: x[0])
    return [canon for _, canon, _ in matches[:k]]


def _period_order_sql() -> str:
    return "CASE report_period WHEN 'Q1' THEN 1 WHEN 'HY' THEN 2 WHEN 'Q3' THEN 3 WHEN 'FY' THEN 4 END"


def _year_period_where(intent: intent_router.Intent, default_period: str = "Q3") -> str:
    """统一生成 report_year/report_period 过滤。默认 Q3。"""
    parts: list[str] = []
    if intent.years:
        parts.append(f"report_year IN ({','.join(str(y) for y in intent.years)})")
    if intent.periods:
        parts.append(f"report_period IN ({','.join(chr(39)+p+chr(39) for p in intent.periods)})")
    elif default_period:
        parts.append(f"report_period='{default_period}'")
    return " AND ".join(parts)


# ===== Pattern 1: 两字段跨表比较（A > B）=====
def _pat_two_field_compare(intent: intent_router.Intent, q: str) -> Optional[tuple[str, str]]:
    """如 "短期借款 金额超过 货币资金 金额的公司" → WHERE A > B。

    识别条件：问题含 "超过|大于|>|高于|多于" 且两字段 canonical_name 不同。
    """
    if not any(w in q for w in ("超过", "大于", "高于", "多于")):
        return None
    fields = _extract_fields(q, k=3)
    if len(fields) < 2:
        return None
    f1, f2 = FIELD_META[fields[0]], FIELD_META[fields[1]]
    # 同量纲才能比较：都 amount 或都 percent
    if f1.kind != f2.kind:
        return None

    yp = _year_period_where(intent)
    # 同一张表：直接 WHERE
    if f1.table == f2.table:
        sql = (
            f"SELECT stock_abbr, stock_code, report_year, report_period, {f1.column}, {f2.column} "
            f"FROM {f1.table} "
            f"WHERE {yp + ' AND ' if yp else ''}{f1.column} IS NOT NULL AND {f2.column} IS NOT NULL "
            f"AND {f1.column} > {f2.column} "
            f"ORDER BY ({f1.column} - {f2.column}) DESC LIMIT 50"
        )
    else:
        sql = (
            f"SELECT t1.stock_abbr, t1.stock_code, t1.report_year, t1.report_period, "
            f"t1.{f1.column}, t2.{f2.column} "
            f"FROM {f1.table} t1 JOIN {f2.table} t2 "
            f"USING (stock_code, report_year, report_period) "
            f"WHERE {('('+yp.replace('report_year', 't1.report_year').replace('report_period', 't1.report_period')+') AND ') if yp else ''}"
            f"t1.{f1.column} IS NOT NULL AND t2.{f2.column} IS NOT NULL "
            f"AND t1.{f1.column} > t2.{f2.column} "
            f"ORDER BY (t1.{f1.column} - t2.{f2.column}) DESC LIMIT 50"
        )
    return sql, "bar"


# ===== Pattern 2: 跨表不一致 =====
def _pat_cross_table_inconsistency(intent: intent_router.Intent, q: str) -> Optional[tuple[str, str]]:
    """如 "核心业绩指标表的营业总收入 与 利润表的营业总收入 不一致的公司"。"""
    if "不一致" not in q and "差值" not in q and "差异" not in q:
        return None
    if "营业总收入" in q or "营业收入" in q:
        # core vs income 的 total_operating_revenue
        yp = _year_period_where(intent)
        yp_clause = f"({yp.replace('report_year', 'c.report_year').replace('report_period', 'c.report_period')}) AND " if yp else ""
        sql = (
            "SELECT c.stock_abbr, c.stock_code, c.report_year, c.report_period, "
            "c.total_operating_revenue AS core_营业总收入, "
            "i.total_operating_revenue AS income_营业总收入, "
            "ABS(c.total_operating_revenue - i.total_operating_revenue) AS 差值绝对值 "
            "FROM core_performance_indicators_sheet c "
            "JOIN income_sheet i USING (stock_code, report_year, report_period) "
            f"WHERE {yp_clause}"
            "c.total_operating_revenue IS NOT NULL AND i.total_operating_revenue IS NOT NULL "
            "AND ABS(c.total_operating_revenue - i.total_operating_revenue) > 1 "
            "ORDER BY 差值绝对值 DESC LIMIT 50"
        )
        return sql, "bar"
    return None


# ===== Pattern 3: 两字段差值 Top-N =====
def _pat_diff_top_n(intent: intent_router.Intent, q: str) -> Optional[tuple[str, str]]:
    """如 "扣非净利润 与 净利润 的 差值 绝对值最大 的 Top 5 公司"。"""
    if "差值" not in q and "差异" not in q:
        return None
    if not any(w in q for w in ("最大", "最高", "top", "Top", "TOP", "前")):
        return None
    fields = _extract_fields(q, k=3)
    if len(fields) < 2:
        return None
    f1, f2 = FIELD_META[fields[0]], FIELD_META[fields[1]]
    # top N 个数：默认 5
    m = re.search(r"(?:前|top|Top|TOP)\s*(\d+)", q)
    top_n = int(m.group(1)) if m else 5

    yp = _year_period_where(intent)
    if f1.table == f2.table:
        where = f"{yp + ' AND ' if yp else ''}{f1.column} IS NOT NULL AND {f2.column} IS NOT NULL"
        sql = (
            f"SELECT stock_abbr, stock_code, report_year, report_period, "
            f"{f1.column}, {f2.column}, ABS({f1.column} - {f2.column}) AS 差值绝对值 "
            f"FROM {f1.table} WHERE {where} "
            f"ORDER BY 差值绝对值 DESC LIMIT {top_n}"
        )
    else:
        yp_t1 = yp.replace("report_year", "t1.report_year").replace("report_period", "t1.report_period")
        where = f"{'('+yp_t1+') AND ' if yp else ''}t1.{f1.column} IS NOT NULL AND t2.{f2.column} IS NOT NULL"
        sql = (
            f"SELECT t1.stock_abbr, t1.stock_code, t1.report_year, t1.report_period, "
            f"t1.{f1.column}, t2.{f2.column}, ABS(t1.{f1.column} - t2.{f2.column}) AS 差值绝对值 "
            f"FROM {f1.table} t1 JOIN {f2.table} t2 "
            f"USING (stock_code, report_year, report_period) "
            f"WHERE {where} "
            f"ORDER BY 差值绝对值 DESC LIMIT {top_n}"
        )
    return sql, "bar"


# ===== Pattern 4: A - B > N 个百分点 =====
def _pat_percent_spread(intent: intent_router.Intent, q: str) -> Optional[tuple[str, str]]:
    """如 "总资产同比增长率 超过 营业总收入同比增长率 10 个百分点"。"""
    if "个百分点" not in q:
        return None
    m = re.search(r"超过.*?(\d+(?:\.\d+)?)\s*个百分点", q)
    if not m:
        return None
    spread = float(m.group(1))
    fields = _extract_fields(q, k=3)
    if len(fields) < 2:
        return None
    f1, f2 = FIELD_META[fields[0]], FIELD_META[fields[1]]
    if f1.kind != "percent" or f2.kind != "percent":
        return None

    yp = _year_period_where(intent)
    if f1.table == f2.table:
        sql = (
            f"SELECT stock_abbr, stock_code, report_year, report_period, "
            f"{f1.column}, {f2.column}, ({f1.column} - {f2.column}) AS 差值 "
            f"FROM {f1.table} "
            f"WHERE {yp + ' AND ' if yp else ''}{f1.column} IS NOT NULL AND {f2.column} IS NOT NULL "
            f"AND ({f1.column} - {f2.column}) > {spread} "
            f"ORDER BY 差值 DESC LIMIT 50"
        )
    else:
        yp_t1 = yp.replace("report_year", "t1.report_year").replace("report_period", "t1.report_period")
        sql = (
            f"SELECT t1.stock_abbr, t1.stock_code, t1.report_year, t1.report_period, "
            f"t1.{f1.column}, t2.{f2.column}, (t1.{f1.column} - t2.{f2.column}) AS 差值 "
            f"FROM {f1.table} t1 JOIN {f2.table} t2 "
            f"USING (stock_code, report_year, report_period) "
            f"WHERE {'('+yp_t1+') AND ' if yp else ''}t1.{f1.column} IS NOT NULL AND t2.{f2.column} IS NOT NULL "
            f"AND (t1.{f1.column} - t2.{f2.column}) > {spread} "
            f"ORDER BY 差值 DESC LIMIT 50"
        )
    return sql, "bar"


# ===== Pattern 5: 比值/比例分布 直方图 =====
def _pat_ratio_hist(intent: intent_router.Intent, q: str) -> Optional[tuple[str, str]]:
    """如 "经营性现金流量净额 与 净利润 的 比值分布，用直方图展示"。"""
    if "直方图" not in q and "分布" not in q:
        return None
    if "比值" not in q and "比例" not in q and "比率" not in q:
        return None
    fields = _extract_fields(q, k=3)
    if len(fields) < 2:
        return None
    f1, f2 = FIELD_META[fields[0]], FIELD_META[fields[1]]
    yp = _year_period_where(intent)

    if f1.table == f2.table:
        sql = (
            f"SELECT stock_abbr, stock_code, ({f1.column}*1.0 / NULLIF({f2.column},0)) AS 比值 "
            f"FROM {f1.table} "
            f"WHERE {yp + ' AND ' if yp else ''}{f1.column} IS NOT NULL AND {f2.column} IS NOT NULL "
            f"AND {f2.column} != 0"
        )
    else:
        yp_t1 = yp.replace("report_year", "t1.report_year").replace("report_period", "t1.report_period")
        sql = (
            f"SELECT t1.stock_abbr, t1.stock_code, (t1.{f1.column}*1.0 / NULLIF(t2.{f2.column},0)) AS 比值 "
            f"FROM {f1.table} t1 JOIN {f2.table} t2 "
            f"USING (stock_code, report_year, report_period) "
            f"WHERE {'('+yp_t1+') AND ' if yp else ''}t1.{f1.column} IS NOT NULL AND t2.{f2.column} IS NOT NULL "
            f"AND t2.{f2.column} != 0"
        )
    return sql, "hist"


# ===== Pattern 6: 两字段相关性 散点 =====
def _pat_correlation_scatter(intent: intent_router.Intent, q: str) -> Optional[tuple[str, str]]:
    """如 "营业总收入 与 净利润 的 相关性，用散点图展示"。"""
    if "散点" not in q and "相关" not in q:
        return None
    fields = _extract_fields(q, k=3)
    if len(fields) < 2:
        return None
    f1, f2 = FIELD_META[fields[0]], FIELD_META[fields[1]]
    yp = _year_period_where(intent)

    if f1.table == f2.table:
        sql = (
            f"SELECT stock_abbr, stock_code, {f1.column}, {f2.column} "
            f"FROM {f1.table} "
            f"WHERE {yp + ' AND ' if yp else ''}{f1.column} IS NOT NULL AND {f2.column} IS NOT NULL"
        )
    else:
        yp_t1 = yp.replace("report_year", "t1.report_year").replace("report_period", "t1.report_period")
        sql = (
            f"SELECT t1.stock_abbr, t1.stock_code, t1.{f1.column}, t2.{f2.column} "
            f"FROM {f1.table} t1 JOIN {f2.table} t2 "
            f"USING (stock_code, report_year, report_period) "
            f"WHERE {'('+yp_t1+') AND ' if yp else ''}t1.{f1.column} IS NOT NULL AND t2.{f2.column} IS NOT NULL"
        )
    return sql, "scatter"


# ===== Pattern 7: CAGR 分布 =====
def _pat_cagr_hist(intent: intent_router.Intent, q: str) -> Optional[tuple[str, str]]:
    """如 "2022-2025 营业总收入 的 复合增长率 分布，用直方图"。

    SQL 一次拉取起止两年数据，Python 端再计算 CAGR。返回 (sql, chart_type="hist_cagr")
    — 让 answer_q4 post-process 计算。
    """
    if ("复合增长率" not in q and "CAGR" not in q.upper()) or ("直方图" not in q and "分布" not in q):
        return None
    fields = _extract_fields(q, k=2)
    if not fields:
        return None
    f = FIELD_META[fields[0]]
    if f.kind != "amount":
        return None
    # 从问题里直接提取年份范围 "2022-2025" 或 "2022 至 2025"
    all_years: list[int] = []
    for m in re.finditer(r"(20\d{2})", q):
        all_years.append(int(m.group(1)))
    if not all_years:
        all_years = intent.years[:]
    if not all_years:
        all_years = [2022, 2025]
    y0, y1 = min(all_years), max(all_years)
    if y0 == y1:
        y0, y1 = 2022, 2025
    period = (intent.periods or ["Q3"])[0]
    sql = (
        f"SELECT stock_abbr, stock_code, report_year, {f.column} "
        f"FROM {f.table} "
        f"WHERE report_period='{period}' AND report_year IN ({y0}, {y1}) "
        f"AND {f.column} IS NOT NULL"
    )
    return sql, "hist_cagr"


# ===== Pattern 8: 双年 Top-N 对比 =====
def _pat_dual_year_rank_compare(intent: intent_router.Intent, q: str) -> Optional[tuple[str, str]]:
    """如 "对比 2024 和 2025 第三季度 净利润同比增长率 前十 公司 的 名单变化"。"""
    if "对比" not in q and "相比" not in q:
        return None
    if not any(w in q for w in ("top", "Top", "TOP", "前十", "前5", "前五", "前三", "前10")):
        return None
    if len(intent.years) < 2:
        return None
    fields = _extract_fields(q, k=2)
    if not fields:
        return None
    f = FIELD_META[fields[0]]
    y0, y1 = sorted(intent.years)[:2]
    period = (intent.periods or ["Q3"])[0]
    m = re.search(r"前\s*(\d+)|top\s*(\d+)|Top\s*(\d+)|TOP\s*(\d+)|前十", q)
    n = 10
    if m:
        for g in m.groups():
            if g:
                n = int(g)
                break

    sql = (
        f"SELECT stock_abbr, stock_code, report_year, report_period, {f.column} FROM ( "
        f"  SELECT stock_abbr, stock_code, report_year, report_period, {f.column}, "
        f"    ROW_NUMBER() OVER (PARTITION BY report_year ORDER BY {f.column} DESC) AS rk "
        f"  FROM {f.table} "
        f"  WHERE report_year IN ({y0}, {y1}) AND report_period='{period}' AND {f.column} IS NOT NULL "
        f") WHERE rk <= {n} ORDER BY report_year, rk"
    )
    return sql, "bar"


# ===== Pattern 9: 单字段绝对值 Top-N（"波动最大 / 绝对值最大"）=====
def _pat_volatility_top(intent: intent_router.Intent, q: str) -> Optional[tuple[str, str]]:
    """如 "净利润同比增长率 波动最大 (绝对值最大) 的公司"。

    与 _pat_diff_top_n 不同：本模板针对单字段的 ABS 排序，无需两字段。
    触发：含"波动最大/最小"或"绝对值最大/最小"。
    """
    if not any(w in q for w in ("波动最大", "波动最小", "绝对值最大", "绝对值最小")):
        return None
    fields = _extract_fields(q, k=1)
    if not fields:
        return None
    f = FIELD_META[fields[0]]
    asc_desc = "ASC" if ("最小" in q) else "DESC"
    # 默认 5 家；若有 "前 N"
    m = re.search(r"(?:前|top|Top|TOP)\s*(\d+)", q)
    n = int(m.group(1)) if m else (1 if "哪家" in q else 5)

    yp = _year_period_where(intent)
    where = f"{yp + ' AND ' if yp else ''}{f.column} IS NOT NULL"
    sql = (
        f"SELECT stock_abbr, stock_code, report_year, report_period, "
        f"{f.column}, ABS({f.column}) AS abs_value "
        f"FROM {f.table} WHERE {where} "
        f"ORDER BY abs_value {asc_desc} LIMIT {n}"
    )
    return sql, "bar"


# ===== Pattern 10: 连续 N 期满足条件 =====
_CN_NUM = {"两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}


def _pat_consecutive_periods(intent: intent_router.Intent, q: str) -> Optional[tuple[str, str]]:
    """如 "连续四个报告期 扣非净利润 均超过 5000万 的公司"
       或 "连续保持正增长 的公司"。

    用 GROUP BY stock_code HAVING COUNT(...) >= N 实现。
    """
    if not re.search(r"连续\s*(?:[2-9]|两|二|三|四|五|六)?\s*(?:个)?(?:报告期|期|季度)?", q):
        return None
    if not any(w in q for w in ("连续", "保持")):
        return None
    fields = _extract_fields(q, k=1)
    if not fields:
        return None
    f = FIELD_META[fields[0]]

    # "正增长 / 负增长 / 增长率" 语境下，把金额字段切到对应的同比字段
    AMOUNT_TO_YOY = {
        "营业总收入": "营业收入同比",
        "营业收入": "营业收入同比",
        "净利润": "净利润同比",
        "总资产": "总资产同比",
        "总负债": "总负债同比",
    }
    if any(w in q for w in ("正增长", "负增长", "增长率", "同比")):
        yoy_canon = AMOUNT_TO_YOY.get(fields[0])
        if yoy_canon and yoy_canon in FIELD_META:
            f = FIELD_META[yoy_canon]

    # 解析数字
    n_match = re.search(r"连续\s*(\d+|两|二|三|四|五|六)\s*(?:个)?\s*(?:报告期|期|季度)", q)
    if n_match:
        token = n_match.group(1)
        n = int(token) if token.isdigit() else _CN_NUM.get(token, 4)
    else:
        n = 4  # 默认 4 期

    # 条件
    if "正增长" in q or "为正" in q or "保持正" in q:
        cond = f"{f.column} > 0"
    elif "为负" in q or "负增长" in q:
        cond = f"{f.column} < 0"
    elif intent.filters:
        ff = intent.filters[0]
        if ff.op in (">", "<", ">=", "<=") and f.kind in ("amount", "percent", "ratio"):
            from .answer_q4 import _filter_value_to_col_unit
            v = _filter_value_to_col_unit(ff, f)
            cond = f"{f.column} {ff.op} {v}"
        elif ff.op == "<0":
            cond = f"{f.column} < 0"
        elif ff.op == ">0":
            cond = f"{f.column} > 0"
        else:
            cond = f"{f.column} > 0"
    else:
        cond = f"{f.column} > 0"

    # 起止年份范围（连续N期场景特殊：从问题里直接抽 "2022-2025" 这类区间）
    yp_parts: list[str] = []
    range_match = re.search(r"(20\d{2})\s*[-—~至到]\s*(20\d{2})", q)
    if range_match:
        y0, y1 = sorted((int(range_match.group(1)), int(range_match.group(2))))
        yp_parts.append(f"report_year BETWEEN {y0} AND {y1}")
    elif intent.years:
        yp_parts.append(f"report_year IN ({','.join(str(y) for y in intent.years)})")
    # 连续 N 期通常跨多个 period，不限制 report_period
    yp = " AND ".join(yp_parts)

    where = f"{cond}"
    if yp:
        where = f"{yp} AND {where}"
    where = f"({where}) AND {f.column} IS NOT NULL"

    sql = (
        f"SELECT stock_abbr, stock_code, COUNT(*) AS 满足期数 "
        f"FROM {f.table} "
        f"WHERE {where} "
        f"GROUP BY stock_code, stock_abbr "
        f"HAVING COUNT(*) >= {n} "
        f"ORDER BY 满足期数 DESC LIMIT 50"
    )
    return sql, "bar"


_PATTERNS = [
    _pat_cross_table_inconsistency,  # "不一致"
    _pat_diff_top_n,                  # "差值最大 Top N"
    _pat_percent_spread,              # "A 超过 B N 个百分点"
    _pat_cagr_hist,                   # "复合增长率 分布"
    _pat_ratio_hist,                  # "比值 分布 直方图"
    _pat_correlation_scatter,         # "相关性 散点"
    _pat_dual_year_rank_compare,      # "对比 两年 Top N"
    _pat_volatility_top,              # "波动最大 / 绝对值最大"（新增）
    _pat_consecutive_periods,         # "连续 N 期" / "连续保持正增长"（新增）
    _pat_two_field_compare,           # "A 超过 B"（兜底，因此排最后）
]


def try_build(intent: intent_router.Intent, question: str) -> Optional[tuple[str, str]]:
    """依次试各 pattern，第一个命中返回 (sql, chart_type)。"""
    for p in _PATTERNS:
        try:
            out = p(intent, question)
        except Exception as e:  # noqa: BLE001
            print(f"[advanced] {p.__name__} fail: {e}")
            continue
        if out:
            return out
    return None


# ===== Post-process hooks =====
def cagr_post_process(records: list[dict], value_col: str = None) -> list[dict]:
    """把起止两年的 SELECT 结果按 stock_code 聚合，计算 CAGR。返回 [{stock_abbr, CAGR}]。"""
    if not records:
        return []
    # value column：第一个不是 stock/year 的数值列
    keys = [k for k in records[0].keys() if k not in ("stock_abbr", "stock_code", "report_year", "report_period")]
    vc = value_col or (keys[0] if keys else None)
    if not vc:
        return []
    # group by stock_code
    grouped: dict[str, dict[int, float]] = {}
    abbr: dict[str, str] = {}
    for r in records:
        code = r.get("stock_code") or r.get("stock_abbr")
        y = int(r.get("report_year") or 0)
        v = r.get(vc)
        if code is None or y == 0 or v is None:
            continue
        grouped.setdefault(code, {})[y] = float(v)
        abbr[code] = r.get("stock_abbr") or str(code)
    out = []
    for code, yv in grouped.items():
        if len(yv) < 2:
            continue
        ys = sorted(yv.keys())
        y0, y1 = ys[0], ys[-1]
        v0, v1 = yv[y0], yv[y1]
        if v0 <= 0:
            continue
        n = y1 - y0
        if n <= 0:
            continue
        cagr = ((v1 / v0) ** (1 / n) - 1) * 100  # %
        out.append({"stock_abbr": abbr.get(code, code), "stock_code": code, "CAGR": round(cagr, 2)})
    return out
