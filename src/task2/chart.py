"""根据 SQL 结果自动挑选图形并保存到 result/ 目录。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams

# 中文字体（系统已安装任意一款）
rcParams["font.sans-serif"] = [
    "Noto Sans CJK SC", "WenQuanYi Zen Hei", "SimHei", "Microsoft YaHei", "Arial Unicode MS",
]
rcParams["axes.unicode_minus"] = False


def _clean(values: list) -> list[float]:
    return [float(v) if v is not None else 0.0 for v in values]


def auto_plot(
    records: list[dict],
    chart_type: str,
    title: str,
    out_path: Path,
    x_field: Optional[str] = None,
    y_field: Optional[str] = None,
    series_field: Optional[str] = None,
) -> Path:
    """auto_plot：line / bar / pie / table（table 直接返回 None 图表）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7, 4.2))

    if not records:
        plt.text(0.5, 0.5, "无数据", ha="center", va="center", fontsize=14)
        plt.axis("off")
    elif chart_type == "pie":
        xs = [str(r.get(x_field or list(r.keys())[0])) for r in records]
        ys = _clean([r.get(y_field or list(r.keys())[-1]) for r in records])
        plt.pie(ys, labels=xs, autopct="%1.1f%%")
    elif chart_type == "line":
        xs = [str(r.get(x_field or list(r.keys())[0])) for r in records]
        if series_field:
            series: dict[str, list[float]] = {}
            for r in records:
                s = str(r.get(series_field))
                series.setdefault(s, []).append(float(r.get(y_field) or 0))
            for s, vs in series.items():
                plt.plot(range(len(vs)), vs, marker="o", label=s)
            plt.xticks(range(len(xs) // max(len(series), 1)), xs[: len(xs) // max(len(series), 1)], rotation=30)
            plt.legend()
        else:
            ys = _clean([r.get(y_field or list(r.keys())[-1]) for r in records])
            plt.plot(xs, ys, marker="o")
            plt.xticks(rotation=30)
    else:  # bar / table fallback
        xs = [str(r.get(x_field or list(r.keys())[0])) for r in records]
        ys = _clean([r.get(y_field or list(r.keys())[-1]) for r in records])
        plt.bar(xs, ys)
        plt.xticks(rotation=30)

    plt.title(title)
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()
    return out_path
