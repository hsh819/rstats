"""全局路径与环境变量。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _resolve_data_dir() -> Path:
    """优先：环境变量 DATA_DIR > 全部数据 > 示例数据。"""
    env = os.environ.get("DATA_DIR")
    if env:
        return Path(env)
    full = ROOT / "data" / "B题-全部数据"
    if full.exists():
        return full
    sample = ROOT / "data" / "B题-示例数据"
    if not sample.exists():
        print(f"[config] WARN: 数据目录不存在，请运行 scripts/fetch_data.py")
    return sample


DATA_DIR = _resolve_data_dir()
DB_DIR = Path(os.environ.get("DB_DIR", ROOT / "db"))
RESULT_DIR = Path(os.environ.get("RESULT_DIR", ROOT / "result"))
DB_PATH = DB_DIR / "finance.db"
SCHEMA_PATH = ROOT / "db" / "schema.sql"   # 源代码而非派生数据，固定在仓库 db/

def _glob_one(pattern: str, fallback: str) -> Path:
    """支持文件名变化（中药/医药、不同截至日期）。glob 命中第一个，否则回落到默认名。"""
    matches = sorted(DATA_DIR.glob(pattern))
    return matches[0] if matches else (DATA_DIR / fallback)


FILE_COMPANIES = _glob_one("附件1*.xlsx", "附件1：中药上市公司基本信息（截至到2025年12月22日）.xlsx")
FILE_SCHEMA_SPEC = _glob_one("附件3*.xlsx", "附件3：数据库-表名及字段说明.xlsx")
FILE_Q_TASK2 = _glob_one("附件4*.xlsx", "附件4：问题汇总.xlsx")
FILE_Q_TASK3 = _glob_one("附件6*.xlsx", "附件6：问题汇总.xlsx")
DIR_REPORTS_SH = DATA_DIR / "附件2：财务报告" / "reports-上交所"
DIR_REPORTS_SZ = DATA_DIR / "附件2：财务报告" / "reports-深交所"
DIR_RESEARCH_INDIVIDUAL = DATA_DIR / "附件5：研报数据" / "个股研报"
DIR_RESEARCH_INDUSTRY = DATA_DIR / "附件5：研报数据" / "行业研报"
def _glob_research_meta(kind: str) -> Path:
    """匹配 个股_研报信息.xlsx 或 <行业>_个股_研报信息.xlsx 等变体。"""
    base = DATA_DIR / "附件5：研报数据"
    if not base.exists():
        return base / f"{kind}_研报信息.xlsx"
    matches = sorted(base.glob(f"*{kind}_研报信息.xlsx"))
    return matches[0] if matches else (base / f"{kind}_研报信息.xlsx")


FILE_RESEARCH_INDIVIDUAL_META = _glob_research_meta("个股")
FILE_RESEARCH_INDUSTRY_META = _glob_research_meta("行业")

LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_ENABLED = bool(LLM_API_KEY)

for d in (DB_DIR, RESULT_DIR):
    d.mkdir(parents=True, exist_ok=True)
