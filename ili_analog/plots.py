"""Figures, following the existing national ILI research figure style.

Same as the established convention: the muted 626D71 / 839D9A / 9E8F8A / AAA39B / E5E0DA
palette, a 15x9 canvas, the CJK font probe with an English fallback, a value table under
the chart with only horizontal rules, a dotted origin line, pending horizons drawn as an
open marker over a shaded band, and 300-dpi output.

Rounding happens here only; the CSV and JSON artifacts keep raw floats. No curve
interpolation, no smoothing and no confidence or prediction interval: v1 is a deterministic
segment-scaling rule with no probabilistic model, so any band drawn here would be
uncalibrated decoration. A missing actual is left as a gap plus an explicit "pending"
marker, never drawn as 0.
"""
from __future__ import annotations

import math

from .analog import LOOKBACK_WEEKS, normalised, segment_values

CURRENT = "#626D71"     # this year's actual counts
ANALOG = "#839D9A"      # the model output
REFERENCE = "#9E8F8A"   # the matched reference-year segment, and the origin rule
PENDING_EDGE = "#AAA39B"
PENDING_FILL = "#E5E0DA"
TICK_STEP = 5
DPI = 300


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


def _table(axis, rows, row_labels, col_labels):
    """The house table: no vertical rules, a rule under the header and under the last row."""
    table = axis.table(cellText=rows, rowLabels=row_labels, colLabels=col_labels,
                       cellLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.5)
    for (row, _), cell in table.get_celld().items():
        cell.visible_edges = "TB" if row == 0 else ("B" if row == len(rows) else "")
        cell.set_edgecolor(PENDING_EDGE)
    return table


def plot_analog_comparison(out_dir, result, weekly):
    """Figure A: normalised segment overlay, then the reference segment at this year's level."""
    plt, font = _setup()
    current = list(result.current_values)
    reference = segment_values(weekly, result.selected.compare_weeks)
    u, v = normalised(current), normalised(reference)
    scale = result.x8 / reference[-1]
    rescaled = [value * scale for value in reference]
    positions = list(range(1, LOOKBACK_WEEKS + 1))
    current_span = (f"{result.current_weeks[0].yearweek}–{result.current_weeks[-1].yearweek}"
                    if font else
                    f"{result.current_weeks[0].yearweek}-{result.current_weeks[-1].yearweek}")
    reference_span = (
        f"{result.selected.compare_weeks[0].yearweek}–{result.selected.compare_weeks[-1].yearweek}"
        if font else
        f"{result.selected.compare_weeks[0].yearweek}-{result.selected.compare_weeks[-1].yearweek}")

    fig = plt.figure(figsize=(15, 9))
    grid = fig.add_gridspec(3, 1, height_ratios=[3, 3, 1.35], hspace=0.42)
    top, bottom, table_axis = (fig.add_subplot(grid[0]), fig.add_subplot(grid[1]),
                              fig.add_subplot(grid[2]))
    table_axis.axis("off")

    top.plot(positions, u, color=CURRENT, marker="o", markersize=4, linewidth=1.8,
             label=_label(font, f"今年 {current_span}（除以自身第 8 週）",
                          f"Current {current_span} (divided by its own week 8)"))
    top.plot(positions, v, color=REFERENCE, marker="s", markersize=4, linewidth=2,
             label=_label(font, f"去年 {reference_span}（除以自身第 8 週）",
                          f"Reference {reference_span} (divided by its own week 8)"))
    top.set_ylabel(_label(font, "相對於第 8 週", "relative to week 8"))
    top.set_title(_label(
        font,
        f"相似片段比較　今年 {current_span}　對　去年 {reference_span}　"
        f"distance = {result.selected.distance:.4f}",
        f"Matched segments: current {current_span} vs reference {reference_span}, "
        f"distance = {result.selected.distance:.4f}"))

    bottom.plot(positions, current, color=CURRENT, marker="o", markersize=4, linewidth=1.8,
                label=_label(font, "今年實際週人次", "Current actual weekly visits"))
    bottom.plot(positions, rescaled, color=ANALOG, marker="o", markersize=4, linewidth=2,
                label=_label(font, f"去年波形換算到今年水位（× {scale:.4f}）",
                             f"Reference shape rescaled to this year's level (x {scale:.4f})"))
    bottom.set_ylabel(_label(font, "週就診人次", "weekly visits"))
    bottom.set_xlabel(_label(font, "片段內相對位置 1–8", "position within segment 1-8"))
    bottom.set_title(_label(font, "換算到今年人次水位後的比較",
                            "Reference segment rescaled to the current level"))

    for axis in (top, bottom):
        axis.set_xticks(positions)
        axis.set_xticklabels([str(i) for i in positions])
        axis.grid(axis="y", alpha=0.2)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(frameon=False, fontsize=8, ncol=2)

    rows = [[str(w.yearweek) for w in result.current_weeks],
            [str(w.yearweek) for w in result.selected.compare_weeks],
            [f"{value:,.0f}" for value in current],
            [f"{value:,.0f}" for value in reference],
            [f"{abs(a - b):.4f}" for a, b in zip(u, v)]]
    _table(table_axis, rows,
           [_label(font, "今年 DIM 週", "Current DIM week"),
            _label(font, "去年 DIM 週", "Reference DIM week"),
            _label(font, "今年人次", "Current count"),
            _label(font, "去年人次", "Reference count"),
            _label(font, "標準化絕對差", "Normalised |diff|")],
           [str(i) for i in positions])

    path = out_dir / f"ILI_{result.origin_week.yearweek}_analog_match.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path.name, font or "DejaVu Sans (English labels)"


def plot_forecast(out_dir, result, weekly, actuals=None, suffix=""):
    """Figure B: recent actuals, the origin, H1-H8 predictions and a value table."""
    plt, font = _setup()
    actuals = actuals or {}
    history = [weekly[w.yearweek].ili_total for w in result.history_weeks]
    weeks = len(history)
    future_x = list(range(weeks, weeks + len(result.target_weeks)))
    revealed = [actuals.get(w.yearweek, math.nan) for w in result.target_weeks]
    reference_span = (
        f"{result.selected.compare_weeks[0].yearweek}–{result.selected.compare_weeks[-1].yearweek}"
        if font else
        f"{result.selected.compare_weeks[0].yearweek}-{result.selected.compare_weeks[-1].yearweek}")

    fig = plt.figure(figsize=(15, 9))
    grid = fig.add_gridspec(2, 1, height_ratios=[3, 1.35], hspace=0.42)
    axis, table_axis = fig.add_subplot(grid[0]), fig.add_subplot(grid[1])
    table_axis.axis("off")
    axis.plot(range(weeks), history, color=CURRENT, marker="o", markersize=3, linewidth=1.8,
              label=_label(font, "實際週人次", "Actual"))
    axis.plot(future_x, list(result.predictions), color=ANALOG, marker="o", linewidth=2,
              label=_label(font, "歷史類比預測（去年片段 × 今年水位）",
                           "Historical analog (reference segment x current level)"))
    if any(math.isfinite(value) for value in revealed):
        axis.plot(future_x, revealed, color=CURRENT, marker="o",
                  label=_label(font, "實際值｜已揭露", "Actual | revealed"))
        if math.isfinite(revealed[0]):
            axis.plot([weeks - 1, weeks], [history[-1], revealed[0]], color=CURRENT, linewidth=1.8)
    pending = [x for x, value in zip(future_x, revealed) if not math.isfinite(value)]
    if pending:
        axis.scatter(pending, [0.03] * len(pending), transform=axis.get_xaxis_transform(),
                     facecolors="none", edgecolors=PENDING_EDGE,
                     label=_label(font, "實際值｜pending（不畫為 0）",
                                  "Actual | pending (never drawn as 0)"))
        for x in pending:
            axis.axvspan(x - 0.45, x + 0.45, color=PENDING_FILL, alpha=0.4)
    axis.axvline(weeks - 0.5, color=REFERENCE, linestyle=":",
                 label=f"Origin {result.origin_week.yearweek}")
    labels = [str(w.yearweek) for w in result.history_weeks]
    ticks = list(range(0, weeks, TICK_STEP)) + future_x
    axis.set_xticks(ticks)
    axis.set_xticklabels([labels[x] if x < weeks else f"H{x - weeks + 1}" for x in ticks],
                         rotation=45, ha="right")
    axis.set_title(_label(
        font,
        f"全國 ILI（nhi_opd + rods）{result.origin_week.yearweek} H1–H8 歷史類比預測；"
        f"類比 {reference_span}，distance {result.selected.distance:.4f}",
        f"National ILI (nhi_opd + rods) {result.origin_week.yearweek} H1-H8 historical analog; "
        f"matched {reference_span}, distance {result.selected.distance:.4f}"))
    axis.set_ylabel(_label(font, "週就診人次", "count"))
    axis.grid(axis="y", alpha=0.2)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(frameon=False, fontsize=8, ncol=2)

    rows = [[str(w.yearweek) for w in result.target_weeks],
            [f"{w.start:%m/%d}–{w.end:%m/%d}" if font else f"{w.start:%m/%d}-{w.end:%m/%d}"
             for w in result.target_weeks],
            [str(w.yearweek) for w in result.selected.future_weeks],
            [f"{value:,.0f}" if math.isfinite(value) else "pending" for value in revealed],
            [f"{value:,.0f}" for value in result.predictions]]
    _table(table_axis, rows,
           [_label(font, "目標 DIM 週", "DIM week"),
            _label(font, "日期", "Date"),
            _label(font, "類比來源週", "Analog source"),
            _label(font, "實際人次", "Actual"),
            _label(font, "歷史類比", "Historical analog")],
           [f"H{i}" for i in range(1, len(result.target_weeks) + 1)])

    path = out_dir / f"ILI_{result.origin_week.yearweek}_h1_h8_national{suffix}.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path.name, font or "DejaVu Sans (English labels)"
