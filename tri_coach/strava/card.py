"""
Render a post-workout data card: a 1080x1350 PNG sized for a phone feed.

The card shows a title, up to four stat tiles, up to three stacked panels
(one measure each, no dual axes) with the target band in neutral gray (or a
single cap line), the time spent past the cap washed in the critical status
colour, and a verdict
line with a status icon. Spec (JSON):

{
  "lang": "zh",
  "title": "...", "subtitle": "...",
  "stats": [{"label": "...", "value": "..."}],
  "series": [{"kind": "heartrate" | "watts" | "pace" | "other",
              "label": "...", "unit": "...",
              "t": [elapsed seconds], "y": [values; pace in seconds per unit],
              "band": [lo, hi] or "cap": limit,
              "flag": "above" | "below" | "outside" | "none",
              "smooth_s": 10}],
  "verdict": {"status": "good" | "warning" | "serious" | "critical", "text": "..."},
  "footer": "..."
}

``series_from_streams`` builds the ``series`` list from Strava streams.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator, MultipleLocator  # noqa: E402


WIDTH_PX, HEIGHT_PX, DPI = 1080, 1350, 100
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SERIES = "#2a78d6"
BAND = "#f0efec"
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
STATUS_ICON = {"good": "✓", "warning": "!", "serious": "!", "critical": "✕"}
FLAG_WASH_ALPHA = 0.16
CJK_FONTS = (
    "PingFang SC",
    "Hiragino Sans GB",
    "Heiti TC",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Microsoft YaHei",
    "Arial Unicode MS",
)
DEFAULT_FLAG = {"heartrate": "above", "watts": "above", "pace": "below"}
DEFAULT_SMOOTH_S = {"heartrate": 10, "watts": 30, "pace": 30}
TEXT = {
    "zh": {
        "minutes": "分钟",
        "above": "超过 {limit}：{minutes} 分钟",
        "below": "快于 {limit}：{minutes} 分钟",
        "outside": "区间外：{minutes} 分钟",
        "band": "目标 {lo}–{hi}",
        "cap": "上限 {limit}",
        "heartrate": ("心率", "bpm"),
        "watts": ("功率", "W"),
        "pace": ("配速", "/km"),
    },
    "en": {
        "minutes": "min",
        "above": "Above {limit}: {minutes} min",
        "below": "Faster than {limit}: {minutes} min",
        "outside": "Outside band: {minutes} min",
        "band": "Target {lo}–{hi}",
        "cap": "Cap {limit}",
        "heartrate": ("Heart rate", "bpm"),
        "watts": ("Power", "W"),
        "pace": ("Pace", "/km"),
    },
}


def _font_family() -> list[str]:
    installed = {font.name for font in font_manager.fontManager.ttflist}
    cjk = [name for name in CJK_FONTS if name in installed][:1]
    return cjk + ["DejaVu Sans"]


def _mmss(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _fmt_value(kind: str, value: float) -> str:
    return _mmss(value) if kind == "pace" else f"{value:.0f}"


def _as_array(values: list[Any]) -> np.ndarray:
    return np.array([np.nan if value is None else float(value) for value in values], dtype=float)


def smooth(t: np.ndarray, y: np.ndarray, window_s: float) -> np.ndarray:
    """Centred moving average over ``window_s`` seconds; gaps (NaN) stay gaps."""
    if window_s <= 0 or len(y) < 3:
        return y
    steps = np.diff(t)
    step = float(np.nanmedian(steps)) if len(steps) else 1.0
    n = max(1, int(round(window_s / max(step, 1e-6))))
    if n <= 1:
        return y
    valid = ~np.isnan(y)
    kernel = np.ones(n)
    total = np.convolve(np.where(valid, y, 0.0), kernel, mode="same")
    count = np.convolve(valid.astype(float), kernel, mode="same")
    out = np.divide(total, count, out=np.full_like(total, np.nan), where=count > 0)
    out[~valid] = np.nan
    return out


def flag_mask(y: np.ndarray, band: list[float], flag: str) -> np.ndarray:
    lo, hi = band
    with np.errstate(invalid="ignore"):
        if flag == "above":
            mask = y > hi
        elif flag == "below":
            mask = y < lo
        elif flag == "outside":
            mask = (y > hi) | (y < lo)
        else:
            mask = np.zeros_like(y, dtype=bool)
    return mask & ~np.isnan(y)


def flagged_seconds(t: np.ndarray, mask: np.ndarray) -> float:
    if len(t) == 0:
        return 0.0
    steps = np.diff(t, append=t[-1] + (np.nanmedian(np.diff(t)) if len(t) > 1 else 1.0))
    return float(np.sum(steps[mask]))


def flagged_spans(
    t: np.ndarray, mask: np.ndarray, merge_gap_s: float = 60, min_span_s: float = 30
) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    start = None
    for index, flagged in enumerate(mask):
        if flagged and start is None:
            start = t[index]
        elif not flagged and start is not None:
            spans.append((start, t[index]))
            start = None
    if start is not None:
        spans.append((start, t[-1]))
    merged: list[tuple[float, float]] = []
    for span in spans:
        if merged and span[0] - merged[-1][1] <= merge_gap_s:
            merged[-1] = (merged[-1][0], span[1])
        else:
            merged.append(span)
    return [span for span in merged if span[1] - span[0] >= min_span_s]


def _pick(streams: dict[str, list[Any]], *names: str) -> list[Any]:
    for name in names:
        if streams.get(name):
            return list(streams[name])
    return []


def series_from_streams(
    streams: dict[str, list[Any]], panels: list[dict[str, Any]], lang: str = "zh"
) -> list[dict[str, Any]]:
    """Build card series from Strava streams (API or MCP key names).

    Each panel is {"kind": "heartrate" | "watts" | "pace", "band": [lo, hi], ...};
    a pace panel may set "per" (1000 for /km, 100 for /100 m) and "unit".
    """
    text = TEXT.get(lang, TEXT["zh"])
    t = _pick(streams, "time")
    series: list[dict[str, Any]] = []
    for panel in panels:
        kind = panel["kind"]
        if kind == "heartrate":
            y = _pick(streams, "heartrate", "heart_rate")
        elif kind == "watts":
            y = _pick(streams, "watts")
        elif kind == "pace":
            per = float(panel.get("per", 1000))
            y = [per / v if v is not None and v > 0.3 else None for v in _pick(streams, "velocity_smooth")]
        else:
            raise ValueError(f"unknown panel kind {kind!r}")
        if not t or not y or all(value is None for value in y):
            continue
        label, unit = text[kind]
        series.append(
            {
                **{key: value for key, value in panel.items() if key != "per"},
                "label": panel.get("label", label),
                "unit": panel.get("unit", unit),
                "t": t[: len(y)],
                "y": y[: len(t)],
            }
        )
    return series


def _draw_panel(ax: plt.Axes, item: dict[str, Any], text: dict[str, Any], x_max: float, show_x: bool) -> None:
    kind = item.get("kind", "other")
    t = _as_array(item["t"])
    y = smooth(t, _as_array(item["y"]), float(item.get("smooth_s", DEFAULT_SMOOTH_S.get(kind, 0))))
    if kind == "pace":
        typical = np.nanmedian(y)
        y = np.where(y > typical * 2.5, np.nan, y)
    minutes = t / 60.0
    band = item.get("band")
    cap = item.get("cap")
    handles: list[Any] = []

    if band:
        lo, hi = band
        ax.axhspan(lo, hi, color=BAND, zorder=0, linewidth=0)
        handles.append(Patch(color=BAND, label=text["band"].format(lo=_fmt_value(kind, lo), hi=_fmt_value(kind, hi))))
    elif cap is not None:
        lo = hi = cap
        ax.axhline(cap, color=INK_SECONDARY, linewidth=1.5, zorder=2)
        handles.append(Line2D([], [], color=INK_SECONDARY, linewidth=1.5, label=text["cap"].format(limit=_fmt_value(kind, cap))))
    if band or cap is not None:
        flag = item.get("flag", DEFAULT_FLAG.get(kind, "none"))
        mask = flag_mask(y, [lo, hi], flag)
        seconds = flagged_seconds(t, mask)
        if seconds >= 30:
            for start, end in flagged_spans(t, mask):
                ax.axvspan(start / 60, end / 60, color=STATUS["critical"], alpha=FLAG_WASH_ALPHA, linewidth=0, zorder=1)
            limit = _fmt_value(kind, hi if flag == "above" else lo)
            label = text[flag].format(limit=limit, minutes=f"{seconds / 60:.0f}")
            handles.append(Patch(color=STATUS["critical"], alpha=FLAG_WASH_ALPHA * 2, label=label))

    ax.plot(minutes, y, color=SERIES, linewidth=4.3, solid_joinstyle="round", solid_capstyle="round", zorder=3)

    finite = y[np.isfinite(y)]
    if len(finite):
        lo_v, hi_v = np.percentile(finite, [1, 99])
        if band or cap is not None:
            lo_v, hi_v = min(lo_v, lo), max(hi_v, hi)
        pad = max((hi_v - lo_v) * 0.12, 1.0)
        ax.set_ylim(lo_v - pad, hi_v + pad)
    if kind == "pace":
        ax.invert_yaxis()
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: _mmss(value)))
        span = abs(ax.get_ylim()[1] - ax.get_ylim()[0])
        step = next((base for base in (15, 30, 60, 120, 300) if span / base <= 5), 600)
        ax.yaxis.set_major_locator(MultipleLocator(step))
    else:
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
    ax.set_xlim(0, x_max)

    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.spines["bottom"].set_linewidth(1)
    ax.grid(axis="y", color=GRID, linewidth=1, linestyle="-", zorder=0)
    ax.tick_params(axis="both", length=0, colors=MUTED, labelsize=17, pad=8)
    if show_x:
        ax.set_xlabel(text["minutes"], color=MUTED, fontsize=17, labelpad=8)
    else:
        ax.tick_params(axis="x", labelbottom=False)

    title = item.get("label", "")
    if item.get("unit"):
        title = f"{title}  {item['unit']}"
    ax.set_title(title, loc="left", color=INK_SECONDARY, fontsize=20, pad=14)
    if handles:
        ax.legend(
            handles=handles,
            loc="lower right",
            bbox_to_anchor=(1.0, 1.0),
            ncol=len(handles),
            frameon=False,
            fontsize=16,
            labelcolor=INK_SECONDARY,
            handlelength=1.2,
            columnspacing=1.2,
            borderaxespad=0.2,
        )


def _fit_size(fig: plt.Figure, artists: list[Any], max_width: float, min_size: float = 18) -> None:
    """Shrink the artists to one shared font size at which each fits ``max_width`` (figure fraction)."""
    if not artists:
        return
    renderer = fig.canvas.get_renderer()
    size = artists[0].get_fontsize()
    while size > min_size:
        widest = max(artist.get_window_extent(renderer).width for artist in artists) / fig.bbox.width
        if widest <= max_width:
            break
        size -= 1
        for artist in artists:
            artist.set_fontsize(size)


def render_card(spec: dict[str, Any], out_path: Path) -> Path:
    text = TEXT.get(spec.get("lang", "zh"), TEXT["zh"])
    series = spec.get("series", [])[:3]
    stats = spec.get("stats", [])[:4]
    plt.rcParams["font.family"] = _font_family()
    plt.rcParams["axes.unicode_minus"] = False

    fig = plt.figure(figsize=(WIDTH_PX / DPI, HEIGHT_PX / DPI), dpi=DPI, facecolor=SURFACE)
    left, right = 0.074, 0.926

    title = fig.text(left, 0.952, spec.get("title", ""), color=INK, fontsize=40, fontweight="bold", va="top")
    _fit_size(fig, [title], right - left)
    if spec.get("subtitle"):
        subtitle = fig.text(left, 0.893, spec["subtitle"], color=INK_SECONDARY, fontsize=22, va="top")
        _fit_size(fig, [subtitle], right - left)

    if stats:
        column = (right - left) / len(stats)
        labels, values = [], []
        for index, stat in enumerate(stats):
            x = left + index * column
            labels.append(fig.text(x, 0.838, stat.get("label", ""), color=INK_SECONDARY, fontsize=19, va="top"))
            values.append(
                fig.text(x, 0.803, stat.get("value", ""), color=INK, fontsize=38, fontweight="semibold", va="top")
            )
        _fit_size(fig, labels, column - 0.02, min_size=14)
        _fit_size(fig, values, column - 0.025)
    fig.add_artist(Line2D([left, right], [0.738, 0.738], color=GRID, linewidth=1, transform=fig.transFigure))

    top, bottom = 0.672, 0.205
    if series:
        gap = 0.075
        height = (top - bottom - gap * (len(series) - 1)) / len(series)
        x_max = max(float(np.nanmax(_as_array(item["t"]))) / 60 for item in series)
        for index, item in enumerate(series):
            y0 = top - (index + 1) * height - index * gap
            ax = fig.add_axes((left + 0.035, y0, right - left - 0.035, height))
            _draw_panel(ax, item, text, x_max, show_x=index == len(series) - 1)
        verdict_y = 0.105
    else:
        verdict_y = 0.66

    verdict = spec.get("verdict")
    if verdict and verdict.get("text"):
        status = verdict.get("status", "good")
        fig.text(
            left + 0.022,
            verdict_y,
            STATUS_ICON.get(status, "!"),
            color="white",
            fontsize=22,
            fontweight="bold",
            ha="center",
            va="center",
            bbox={"boxstyle": "circle,pad=0.32", "facecolor": STATUS.get(status, MUTED), "edgecolor": "none"},
        )
        verdict_text = fig.text(left + 0.06, verdict_y, verdict["text"], color=INK, fontsize=26, va="center")
        _fit_size(fig, [verdict_text], right - left - 0.06)

    if spec.get("footer"):
        fig.text(left, 0.03, spec["footer"], color=MUTED, fontsize=15, va="bottom")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)
    return out_path


def load_spec(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
