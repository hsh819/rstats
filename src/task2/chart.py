"""根据 SQL 结果自动挑选图形并保存到 result/ 目录。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams
from matplotlib.ticker import FuncFormatter

# 中文字体（系统已安装任意一款）
rcParams["font.sans-serif"] = [
    "Noto Sans CJK SC", "WenQuanYi Zen Hei", "SimHei", "Microsoft YaHei", "Arial Unicode MS",
]
rcParams["axes.unicode_minus"] = False


# 百分比字段后缀模式（用于决定是否给 y 轴加 % 后缀）
_PCT_SUFFIXES = (
    "_yoy_growth", "_qoq_growth", "_margin", "_ratio_of_net_cf",
)
_PCT_FIELD_NAMES = {"roe", "asset_liability_ratio", "gross_profit_margin", "net_profit_margin"}


def _is_percent_field(name: Optional[str]) -> bool:
    if not name:
        return False
    if name in _PCT_FIELD_NAMES:
        return True
    return any(name.endswith(s) for s in _PCT_SUFFIXES)


def _clean(values: list) -> list[float]:
    return [float(v) if v is not None else 0.0 for v in values]


def _percent_formatter():
    return FuncFormatter(lambda x, _pos: f"{x:.1f}%")


def auto_plot(
    records: list[dict],
    chart_type: str,
    title: str,
    out_path: Path,
    x_field: Optional[str] = None,
    y_field: Optional[str] = None,
    series_field: Optional[str] = None,
) -> Path:
    """auto_plot：line / bar / pie / table（table 直接输出"无图"占位）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    is_pct = _is_percent_field(y_field)

    if not records:
        ax.text(0.5, 0.5, "无数据", ha="center", va="center", fontsize=14)
        ax.axis("off")
    elif chart_type == "hist":
        # 直方图：y_field 的分布
        y_use = y_field or list(records[0].keys())[-1]
        values = [float(r.get(y_use)) for r in records if r.get(y_use) is not None]
        if values:
            ax.hist(values, bins=min(20, max(5, len(values) // 3)), edgecolor="white")
            ax.set_xlabel(str(y_use))
            ax.set_ylabel("公司数")
            if is_pct:
                ax.xaxis.set_major_formatter(_percent_formatter())
        else:
            ax.text(0.5, 0.5, "无数据", ha="center", va="center", fontsize=14)
            ax.axis("off")
    elif chart_type == "scatter":
        # 散点图：优先取后两个数值列
        keys = list(records[0].keys())
        def _numeric(k):
            return any(isinstance(r.get(k), (int, float)) for r in records)
        num_keys = [k for k in keys if _numeric(k) and k not in ("stock_code", "report_year")]
        x_use = x_field if x_field in num_keys else (num_keys[-2] if len(num_keys) >= 2 else keys[0])
        y_use = y_field if y_field in num_keys else (num_keys[-1] if num_keys else keys[-1])
        xs = [float(r.get(x_use)) for r in records if r.get(x_use) is not None and r.get(y_use) is not None]
        ys = [float(r.get(y_use)) for r in records if r.get(x_use) is not None and r.get(y_use) is not None]
        if xs and ys:
            ax.scatter(xs, ys, s=20, alpha=0.7)
            ax.set_xlabel(str(x_use))
            ax.set_ylabel(str(y_use))
        else:
            ax.text(0.5, 0.5, "无数据", ha="center", va="center", fontsize=14)
            ax.axis("off")
    elif chart_type == "pie":
        x_use = x_field or list(records[0].keys())[0]
        y_use = y_field or list(records[0].keys())[-1]
        xs = [str(r.get(x_use)) for r in records]
        ys = _clean([r.get(y_use) for r in records])
        ax.pie(ys, labels=xs, autopct="%1.1f%%")
    elif chart_type == "line":
        x_use = x_field or list(records[0].keys())[0]
        y_use = y_field or list(records[0].keys())[-1]
        if series_field:
            # 按 series 分组：每组用各自的 (x, y) 序列绘制
            grouped: dict[str, list[tuple[str, float]]] = {}
            for r in records:
                s = str(r.get(series_field))
                grouped.setdefault(s, []).append((str(r.get(x_use)), float(r.get(y_use) or 0)))
            # 用最长一组作为统一 x ticks
            longest = max(grouped.values(), key=len)
            x_labels = [t[0] for t in longest]
            for s, pairs in grouped.items():
                xs = [p[0] for p in pairs]
                ys = [p[1] for p in pairs]
                # 在 x_labels 上对齐：找索引
                positions = [x_labels.index(x) if x in x_labels else i for i, x in enumerate(xs)]
                ax.plot(positions, ys, marker="o", label=s)
            ax.set_xticks(range(len(x_labels)))
            ax.set_xticklabels(x_labels, rotation=30)
            ax.legend()
        else:
            xs = [str(r.get(x_use)) for r in records]
            ys = _clean([r.get(y_use) for r in records])
            ax.plot(xs, ys, marker="o")
            ax.tick_params(axis="x", labelrotation=30)
        if is_pct:
            ax.yaxis.set_major_formatter(_percent_formatter())
    else:  # bar / table fallback
        x_use = x_field or list(records[0].keys())[0]
        y_use = y_field or list(records[0].keys())[-1]
        xs = [str(r.get(x_use)) for r in records]
        ys = _clean([r.get(y_use) for r in records])
        bars = ax.bar(xs, ys)
        ax.tick_params(axis="x", labelrotation=30)
        # 柱顶数值标注
        for b, v in zip(bars, ys):
            label = f"{v:.2f}%" if is_pct else (f"{v:,.0f}" if abs(v) >= 100 else f"{v:.2f}")
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(), label,
                    ha="center", va="bottom", fontsize=8)
        if is_pct:
            ax.yaxis.set_major_formatter(_percent_formatter())

    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
