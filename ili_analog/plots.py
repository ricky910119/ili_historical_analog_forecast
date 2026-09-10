"""Figures. Rounding happens here only; the CSV and JSON artifacts keep raw floats.

No curve interpolation, no smoothing and no confidence or prediction interval: v1 is a
deterministic segment-scaling rule with no probabilistic model, so any band drawn here
would be uncalibrated decoration. Points are joined by straight segments and a missing
actual is left as a gap plus an explicit "pending" marker, never drawn as 0.
"""
from __future__ import annotations

import math

from .analog import LOOKBACK_WEEKS, normalised, segment_values

CURRENT_COLOR = "#2F4858"
REFERENCE_COLOR = "#C1666B"
PREDICTION_COLOR = "#4A7C59"
PENDING_COLOR = "#AAA39B"


def _setup():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    candidates = ("Noto Sans CJK TC", "Noto Sans CJK JP", "Microsoft JhengHei", "WenQuanYi Zen Hei")
    available = {f.name for f in font_manager.fontManager.ttflist}
    font = next((f for f in candidates if f in available), None)
    plt.rcParams["font.family"] = [font or "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt, font


def _label(font, chinese, english):
    return chinese if font else english


def plot_analog_comparison(out_dir, result, weekly):
    """Figure A: normalised segment overlay, then the reference segment at this year's level."""
    plt, font = _setup()
    current = list(result.current_values)
    reference = segment_values(weekly, result.selected.compare_weeks)
    u, v = normalised(current), normalised(reference)
    scale = result.x8 / reference[-1]
    rescaled = [value * scale for value in reference]
    positions = list(range(1, LOOKBACK_WEEKS + 1))

    fig, (top, bottom) = plt.subplots(2, 1, figsize=(13, 9))
    top.plot(positions, u, color=CURRENT_COLOR, marker="o",
             label=_label(font, f"今年 {result.current_weeks[0].yearweek}–"
                                f"{result.current_weeks[-1].yearweek}（標準化）",
                          f"Current {result.current_weeks[0].yearweek}-"
                          f"{result.current_weeks[-1].yearweek} (normalised)"))
    top.plot(positions, v, color=REFERENCE_COLOR, marker="s", linestyle="--",
             label=_label(font, f"去年 {result.selected.compare_weeks[0].yearweek}–"
                                f"{result.selected.compare_weeks[-1].yearweek}（標準化）",
                          f"Reference {result.selected.compare_weeks[0].yearweek}-"
                          f"{result.selected.compare_weeks[-1].yearweek} (normalised)"))
    top.set_title(_label(font,
                         f"相似片段比較：distance = {result.selected.distance:.4f}"
                         f"（每段除以自身最後一週）",
                         f"Matched segments: distance = {result.selected.distance:.4f} "
                         f"(each segment divided by its own last week)"))
    top.set_ylabel(_label(font, "相對於第 8 週", "relative to week 8"))

    bottom.plot(positions, current, color=CURRENT_COLOR, marker="o",
                label=_label(font, "今年實際人次", "Current actual counts"))
    bottom.plot(positions, rescaled, color=REFERENCE_COLOR, marker="s", linestyle="--",
                label=_label(font, f"去年波形 × {scale:.4f}（換算到今年水位）",
                             f"Reference shape x {scale:.4f} (rescaled to this year's level)"))
    bottom.set_title(_label(font, "換算到今年人次水位後的比較",
                            "Reference segment rescaled to the current level"))
    bottom.set_ylabel(_label(font, "週就診人次", "weekly visits"))
    bottom.set_xlabel(_label(font, "片段內相對位置（1–8）", "position within segment (1-8)"))

    for axis in (top, bottom):
        axis.set_xticks(positions)
        axis.set_xticklabels(
            [f"{i}\n{c.yearweek}\n{r.yearweek}" for i, c, r in
             zip(positions, result.current_weeks, result.selected.compare_weeks)], fontsize=8)
        axis.grid(axis="y", alpha=0.2)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    path = out_dir / f"analog_comparison_{result.origin_week.yearweek}.png"
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path.name, font or "DejaVu Sans (English labels)"


def plot_forecast(out_dir, result, weekly, actuals=None, suffix=""):
    """Figure B: recent actuals, the origin, H1-H8 predictions and a value table."""
    plt, font = _setup()
    actuals = actuals or {}
    history = [weekly[w.yearweek].ili_total for w in result.history_weeks]
    n = len(history)
    future_x = list(range(n, n + len(result.target_weeks)))
    revealed = [actuals.get(w.yearweek, math.nan) for w in result.target_weeks]

    fig = plt.figure(figsize=(15, 9))
    grid = fig.add_gridspec(2, 1, height_ratios=[3, 1.5], hspace=0.45)
    axis, table_axis = fig.add_subplot(grid[0]), fig.add_subplot(grid[1])
    table_axis.axis("off")
    axis.plot(range(n), history, color=CURRENT_COLOR, marker="o", markersize=4,
              label=_label(font, "實際週人次", "Actual weekly visits"))
    axis.plot(future_x, list(result.predictions), color=PREDICTION_COLOR, marker="D",
              linestyle="--", label=_label(font, "歷史類比預測 H1–H8",
                                           "Historical analog forecast H1-H8"))
    axis.plot([n - 1, n], [history[-1], result.predictions[0]], color=PREDICTION_COLOR,
              linestyle="--")
    if any(math.isfinite(v) for v in revealed):
        axis.plot(future_x, revealed, color=CURRENT_COLOR, marker="o", markersize=5,
                  label=_label(font, "實際值｜已揭露", "Actual | revealed"))
    pending = [x for x, v in zip(future_x, revealed) if not math.isfinite(v)]
    for x in pending:
        axis.axvspan(x - 0.45, x + 0.45, color="#E5E0DA", alpha=0.35)
    if pending:
        axis.scatter(pending, [0.03] * len(pending), transform=axis.get_xaxis_transform(),
                     facecolors="none", edgecolors=PENDING_COLOR,
                     label=_label(font, "實際值｜pending（未繪為 0）",
                                  "Actual | pending (never drawn as 0)"))
    axis.axvline(n - 0.5, color="#9E8F8A", linestyle=":",
                 label=_label(font, f"origin {result.origin_week.yearweek}",
                              f"origin {result.origin_week.yearweek}"))
    labels = [str(w.yearweek) for w in result.history_weeks]
    ticks = list(range(0, n, 2)) + future_x
    axis.set_xticks(ticks)
    axis.set_xticklabels([labels[x] if x < n else f"H{x - n + 1}" for x in ticks],
                         rotation=45, ha="right", fontsize=8)
    axis.set_title(_label(
        font,
        f"全國 ILI（nhi_opd + rods）origin {result.origin_week.yearweek} H1–H8 歷史類比預測；"
        f"類比片段 {result.selected.compare_weeks[0].yearweek}–"
        f"{result.selected.compare_weeks[-1].yearweek}，distance {result.selected.distance:.4f}",
        f"National ILI (nhi_opd + rods) origin {result.origin_week.yearweek} H1-H8 historical "
        f"analog; matched {result.selected.compare_weeks[0].yearweek}-"
        f"{result.selected.compare_weeks[-1].yearweek}, distance {result.selected.distance:.4f}"))
    axis.set_ylabel(_label(font, "週就診人次", "weekly visits"))
    axis.grid(axis="y", alpha=0.2)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(frameon=False, fontsize=9, ncol=2)

    rows = [[str(w.yearweek) for w in result.target_weeks],
            [f"{w.start:%m/%d}-{w.end:%m/%d}" for w in result.target_weeks],
            [str(w.yearweek) for w in result.selected.future_weeks],
            [f"{v:,.0f}" for v in result.predictions],
            [f"{v:,.0f}" if math.isfinite(v) else "pending" for v in revealed]]
    labels_rows = [_label(font, "目標 DIM 週", "Target DIM week"),
                   _label(font, "日期", "Dates"),
                   _label(font, "類比來源週", "Analog source week"),
                   _label(font, "預測人次", "Forecast"),
                   _label(font, "實際人次", "Actual")]
    table = table_axis.table(cellText=rows, rowLabels=labels_rows,
                             colLabels=[f"H{i}" for i in range(1, len(result.target_weeks) + 1)],
                             cellLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.5)
    for (row, _), cell in table.get_celld().items():
        cell.visible_edges = "TB" if row == 0 else ("B" if row == len(rows) else "")
        cell.set_edgecolor("#AAA39B")
    path = out_dir / f"forecast_h1_h8_{result.origin_week.yearweek}{suffix}.png"
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path.name, font or "DejaVu Sans (English labels)"
