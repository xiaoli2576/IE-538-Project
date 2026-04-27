from __future__ import annotations

import json
from pathlib import Path
import textwrap
from typing import Iterable, List, Optional, Tuple
import warnings

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D
import pandas as pd

from eaft_model import cross_evaluate_modes, generate_toy_instance, CrossEvaluationResult


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
FIGURES_DIR = ROOT / "figures"

LINEAR_COLOR = "#1d3557"
NONLINEAR_COLOR = "#d97706"
ACCENT_GREEN = "#2a9d8f"
SOFT_RED = "#c1121f"
LIGHT_BG = "#f7f4ea"
GRID = "#d8d2c6"
TEXT = "#1f2933"

# Vertical layout constants for _draw_card
_CARD_TOP = 0.88
_CARD_BOTTOM = 0.10
_CARD_DY_MAX = 0.062   # normal step between lines
_CARD_DY_FLOOR = 0.035 # minimum step; below this we also shrink fontsize
_CARD_FONTSIZE_NORMAL = 10.3
_CARD_FONTSIZE_SMALL = 9.0


def _apply_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": LIGHT_BG,
            "axes.facecolor": LIGHT_BG,
            "savefig.facecolor": LIGHT_BG,
            "font.size": 11,
            "axes.edgecolor": GRID,
            "axes.labelcolor": TEXT,
            "xtick.color": TEXT,
            "ytick.color": TEXT,
            "text.color": TEXT,
            "axes.titleweight": "bold",
            "axes.titlesize": 13,
            "axes.labelsize": 11,
            "grid.color": GRID,
            "grid.linestyle": "--",
            "grid.linewidth": 0.8,
        }
    )


def _scenario_label(raw: str) -> str:
    mapping = {
        "baseline": "Baseline\n(7 requests)",
        "partial_recharge": "Partial Recharge\n(7 requests)",
        "deep_recharge": "Deep Recharge\n(7 requests)",
    }
    return mapping.get(raw, raw)


def load_comparison_data() -> pd.DataFrame:
    data = pd.read_csv(RESULTS_DIR / "mode_comparison_r7_t3.csv")
    data["scenario_label"] = data["scenario"].map(_scenario_label)
    return data


def plot_charging_profiles() -> Path:
    from eaft_model import apply_charging_mode, generate_toy_instance

    _apply_style()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    instance = generate_toy_instance(num_requests=4, num_buses=1, num_trip_slots=2, num_stations=2, seed=7)
    linear_instance = apply_charging_mode(instance, "linear")
    nonlinear_instance = apply_charging_mode(instance, "nonlinear")

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))

    ax = axes[0]
    for curve, color, label in [
        (linear_instance.charging_curve, LINEAR_COLOR, "Linear"),
        (nonlinear_instance.charging_curve, NONLINEAR_COLOR, "Nonlinear tapering"),
    ]:
        ax.plot(curve.time_breakpoints, curve.energy_breakpoints, color=color, linewidth=3, marker="o", label=label)
    ax.set_title("Base Charging Curves")
    ax.set_xlabel("Charging Time")
    ax.set_ylabel("Charged Energy")
    ax.grid(True, axis="y")
    ax.legend(frameon=False, loc="upper left")
    ax.annotate(
        "Fast early charging\nunder the tapering profile",
        xy=(5.0, 9.6),
        xytext=(7.5, 14.5),
        arrowprops=dict(arrowstyle="->", color=TEXT, lw=1.2),
    )
    ax.annotate(
        "Slow tail",
        xy=(25.0, 18.5),
        xytext=(19.5, 12.5),
        arrowprops=dict(arrowstyle="->", color=TEXT, lw=1.2),
    )

    ax = axes[1]
    station_palette = {"depot": LINEAR_COLOR, "hub": ACCENT_GREEN, "edge": NONLINEAR_COLOR}
    for station, curve in nonlinear_instance.station_curves.items():
        ax.plot(curve.time_breakpoints, curve.energy_breakpoints, linewidth=3, marker="o", label=station.title(), color=station_palette.get(station, TEXT))
    ax.set_title("Station-Specific Nonlinear Curves")
    ax.set_xlabel("Charging Time")
    ax.set_ylabel("Charged Energy")
    ax.grid(True, axis="y")
    ax.legend(frameon=False, loc="upper left")
    ax.annotate(
        "Hub charges faster,\nso it is often selected",
        xy=(4.2, 9.1),
        xytext=(9.5, 15.5),
        arrowprops=dict(arrowstyle="->", color=TEXT, lw=1.2),
    )

    fig.suptitle("Charging Profiles Used in the Computational Study", y=1.02, fontsize=15, fontweight="bold")
    fig.tight_layout()
    output = FIGURES_DIR / "charging_profiles.png"
    fig.savefig(output, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return output


def plot_scenario_comparison(data: pd.DataFrame) -> Path:
    _apply_style()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    scenario_order = ["Baseline\n(7 requests)", "Partial Recharge\n(7 requests)", "Deep Recharge\n(7 requests)"]
    mode_order = ["linear", "nonlinear"]
    color_map = {"linear": LINEAR_COLOR, "nonlinear": NONLINEAR_COLOR}
    metric_specs = [
        ("objective", "Objective Value"),
        ("served_count", "Served Requests"),
        ("total_charge_time", "Total Charge Time"),
    ]

    fig, axes = plt.subplots(3, 1, figsize=(11, 11), sharex=True)

    x = list(range(len(scenario_order)))
    bar_width = 0.32
    offsets = {"linear": -bar_width / 2, "nonlinear": bar_width / 2}

    for ax, (metric, title) in zip(axes, metric_specs):
        for mode in mode_order:
            subset = (
                data[data["mode"] == mode]
                .set_index("scenario_label")
                .reindex(scenario_order)
                .reset_index()
            )
            positions = [value + offsets[mode] for value in x]
            bars = ax.bar(
                positions,
                subset[metric],
                width=bar_width,
                color=color_map[mode],
                alpha=0.93,
                label=mode.title() if metric == "objective" else None,
            )
            metric_max = max(subset[metric].fillna(0).max(), 1)
            label_offset = 0.012 * metric_max
            for bar, val in zip(bars, subset[metric]):
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + label_offset,
                    f"{val:.2f}" if metric != "served_count" else f"{int(val)}",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                )
        ax.set_ylabel(title)
        ax.grid(True, axis="y")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    axes[0].legend(frameon=False, ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.02))
    axes[-1].set_xticks(x, scenario_order)
    fig.suptitle("Scenario Comparison: Linear vs Nonlinear Charging", y=0.995, fontsize=15, fontweight="bold")
    fig.tight_layout()
    output = FIGURES_DIR / "scenario_comparison.png"
    fig.savefig(output, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return output


def _plan_lines(result) -> List[str]:
    # For non-optimal results: if there is an incumbent solution, show the
    # status line AND the incumbent data so readers see what was found.
    if result.status != "OPTIMAL":
        lines = [f"Status: {result.status}"]
        if result.objective_value is not None:
            # TIME_LIMIT (or similar) with an incumbent — show the data too
            lines.append(
                f"Objective: {result.objective_value:.2f}"
            )
            lines.append(
                f"Served requests: {', '.join(result.served_requests) if result.served_requests else 'none'}"
            )
            for trip in result.trips:
                station_text = trip.charge_station_after_trip if trip.charge_station_after_trip is not None else "no charge"
                lines.append(
                    f"Trip {trip.trip_index}: {', '.join(trip.served_requests) if trip.served_requests else 'none'}"
                )
                lines.append(
                    f"  recharge: {station_text}, {trip.charge_time_after_trip:.2f} time, {trip.charge_energy_after_trip:.2f} energy"
                )
        return lines
    # Fully optimal result
    lines = [
        f"Objective: {result.objective_value:.2f}" if result.objective_value is not None else "Objective: NA",
        f"Served requests: {', '.join(result.served_requests) if result.served_requests else 'none'}",
    ]
    for trip in result.trips:
        station_text = trip.charge_station_after_trip if trip.charge_station_after_trip is not None else "no charge"
        lines.append(
            f"Trip {trip.trip_index}: {', '.join(trip.served_requests) if trip.served_requests else 'none'}"
        )
        lines.append(
            f"  recharge: {station_text}, {trip.charge_time_after_trip:.2f} time, {trip.charge_energy_after_trip:.2f} energy"
        )
    return lines


def _count_rendered_rows(lines: Iterable[str]) -> int:
    """Count the total number of text rows that will be rendered for a set of
    plan-info lines (accounting for textwrap), excluding the title/subtitle
    which are handled separately in _draw_card."""
    count = 0
    for line in lines:
        if line.startswith("Trip"):
            wrapped = [line]
        elif line.startswith("  "):
            wrapped = textwrap.wrap(line.strip(), width=28, initial_indent="  ", subsequent_indent="    ")
        else:
            wrapped = textwrap.wrap(line, width=28)
        # Each logical line contributes its wrapped rows plus an inter-line gap
        # We model each wrap-row as 1 row, then add 0.4 rows for the inter-gap.
        count += len(wrapped) if wrapped else 1
    return count


def _draw_card(ax, title: str, subtitle: str, lines: Iterable[str], edge_color: str) -> None:
    """Draw a single info card onto *ax*.

    The card auto-expands its vertical step (dy) so that all content fits
    between y=_CARD_TOP and y=_CARD_BOTTOM.  If even the floor dy is not
    enough, a smaller fontsize is tried first; only as a last resort are
    trailing rows suppressed (with a warnings.warn so we notice).
    """
    lines = list(lines)  # materialise so we can iterate twice

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    box = FancyBboxPatch(
        (0.03, 0.05),
        0.94,
        0.9,
        boxstyle="round,pad=0.02,rounding_size=0.035",
        linewidth=2.0,
        edgecolor=edge_color,
        facecolor="#fffdf8",
    )
    ax.add_patch(box)
    ax.text(0.08, 0.88, title, fontsize=13, fontweight="bold", color=edge_color)

    # --- subtitle ---
    subtitle_lines = textwrap.wrap(subtitle, width=34)
    y = 0.81
    for subline in subtitle_lines:
        ax.text(0.08, y, subline, fontsize=10.5, color=TEXT)
        y -= 0.055
    y -= 0.04   # gap between subtitle and first plan line

    # --- compute how many rows the plan lines need ---
    # Each logical line contributes N wrapped rows; between logical lines we
    # add a small inter-gap that we model as 0.4 extra "rows".
    total_rows = 0
    wrapped_groups: List[List[str]] = []
    for line in lines:
        if line.startswith("Trip"):
            wrapped = [line]
        elif line.startswith("  "):
            wrapped = textwrap.wrap(line.strip(), width=28, initial_indent="  ", subsequent_indent="    ")
        else:
            wrapped = textwrap.wrap(line, width=28)
        if not wrapped:
            wrapped = [""]
        wrapped_groups.append(wrapped)
        # row-count: len(wrapped) text rows + 0.4 inter-gap rows
        total_rows += len(wrapped) + 0.4

    available = y - _CARD_BOTTOM  # vertical space remaining for plan lines

    if total_rows == 0:
        # Nothing to render
        return

    # Ideal dy so everything fits exactly
    ideal_dy = available / total_rows if total_rows > 0 else _CARD_DY_MAX
    # Clamp to [floor, ceiling]
    dy = min(_CARD_DY_MAX, max(_CARD_DY_FLOOR, ideal_dy))

    # Choose fontsize
    if ideal_dy < _CARD_DY_FLOOR:
        fontsize = _CARD_FONTSIZE_SMALL
    else:
        fontsize = _CARD_FONTSIZE_NORMAL

    # Check whether even with small fontsize + floor dy we will overflow
    rows_that_fit = int(available / _CARD_DY_FLOOR) if _CARD_DY_FLOOR > 0 else len(wrapped_groups) * 99
    total_text_rows = sum(len(g) for g in wrapped_groups)
    overflow = total_text_rows > rows_that_fit

    # --- render ---
    rendered_rows = 0
    for group_idx, wrapped in enumerate(wrapped_groups):
        for idx, wrapped_line in enumerate(wrapped):
            if overflow and rendered_rows >= rows_that_fit - 1 and (
                group_idx < len(wrapped_groups) - 1 or idx < len(wrapped) - 1
            ):
                # Last resort: suppress remaining and show ellipsis
                warnings.warn(
                    f"_draw_card: card '{title}' still overflows after shrinking fontsize. "
                    f"Suppressing {total_text_rows - rendered_rows} row(s).",
                    stacklevel=2,
                )
                ax.text(0.08, y, "…", fontsize=fontsize)
                return
            ax.text(
                0.08,
                y,
                wrapped_line,
                fontsize=fontsize,
                family="monospace" if idx == 0 and wrapped_line.startswith("Trip") else None,
            )
            y -= dy
            rendered_rows += 1
        y -= dy * 0.4   # inter-line gap proportional to dy


def _save_cross_evaluation_json(result: CrossEvaluationResult, scenario: str) -> None:
    """Persist cross-evaluation solve results as JSON for later citation."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    def _solve_dict(r) -> dict:
        return {
            "status": r.status,
            "objective_value": r.objective_value,
            "served_requests": r.served_requests,
            "trip_count": len(r.trips),
            "total_charge_time": sum(t.charge_time_after_trip for t in r.trips),
            "total_charge_energy": sum(t.charge_energy_after_trip for t in r.trips),
        }

    payload = {
        "scenario": scenario,
        "source_mode": result.source_mode,
        "target_mode": result.target_mode,
        "optimized_source": _solve_dict(result.optimized_source),
        "fixed_plan_in_target": _solve_dict(result.fixed_plan_in_target),
        "optimized_target": _solve_dict(result.optimized_target),
    }

    out_path = RESULTS_DIR / f"{scenario}_cross_evaluation.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)


_STAGE_LABELS = ("Linear\noptimum", "Linear plan\nunder nonlinear", "Nonlinear\noptimum")
_STAGE_COLORS = (LINEAR_COLOR, SOFT_RED, NONLINEAR_COLOR)
_SEGMENT_DRIVE = "#b8b0a3"
_SEGMENT_SERVE = "#1d3557"
_SEGMENT_CHARGE = "#d97706"


def _stage_metrics(result: CrossEvaluationResult):
    """Return (objective, served_count, charge_time, charge_energy) per stage."""
    solves = (result.optimized_source, result.fixed_plan_in_target, result.optimized_target)
    metrics = []
    for solve in solves:
        obj = solve.objective_value if solve.objective_value is not None else float("nan")
        served = len(solve.served_requests)
        charge_time = sum(trip.charge_time_after_trip for trip in solve.trips) if solve.trips else 0.0
        charge_energy = sum(trip.charge_energy_after_trip for trip in solve.trips) if solve.trips else 0.0
        metrics.append((obj, served, charge_time, charge_energy))
    return metrics


def _plan_segments(solve) -> List[Tuple[str, float, float, str]]:
    """Flatten a SolveResult into (label, start, width, kind) segments for a Gantt row.

    kind is one of 'drive', 'serve', 'charge'. Between-trip reposition + charge
    is shown as one charge block ending at the next trip's ready_time.
    """
    segments: List[Tuple[str, float, float, str]] = []
    for idx, trip in enumerate(solve.trips):
        # Drive-in to first pickup
        if trip.ready_time < trip.trip_end_time:
            first_pickup_time = max(
                trip.ready_time,
                trip.trip_end_time - 0.0,  # we do not know per-node times without re-deriving; use serve block
            )
        # Represent the trip as one combined service block from ready_time to trip_end
        label = ", ".join(trip.served_requests) if trip.served_requests else f"trip {trip.trip_index}"
        segments.append((label, trip.ready_time, trip.trip_end_time - trip.ready_time, "serve"))

        if (
            trip.charge_station_after_trip
            and trip.charge_time_after_trip > 0
            and idx + 1 < len(solve.trips)
        ):
            # Charging block starts at trip_end and extends through reposition
            # (so the visual block covers the full gap until the next trip is ready).
            start = trip.trip_end_time
            reposition_time = max(0.0, solve.trips[idx + 1].ready_time - start - trip.charge_time_after_trip)
            segments.append(
                (
                    f"{trip.charge_station_after_trip} +{trip.charge_energy_after_trip:.1f} kWh",
                    start,
                    trip.charge_time_after_trip + reposition_time,
                    "charge",
                )
            )
    return segments


def _draw_cross_panel(
    title: str,
    scenario: str,
    requests: int,
    precomputed_result: Optional[CrossEvaluationResult] = None,
) -> Path:
    """Render the cross-evaluation figure with quantitative panels.

    Panel layout (left-to-right):
      (a) Objective bar chart with served-request count above each bar
      (b) Grouped bars of charging time (min) and charged energy (kWh)
      (c) Schedule comparison showing the three plans as stacked Gantt rows

    If *precomputed_result* is provided it is used directly (no MIP solve);
    otherwise the cross-evaluation is run here. main() always passes the
    precomputed result so each scenario is solved only once per run.
    """
    _apply_style()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    if precomputed_result is not None:
        result = precomputed_result
    else:
        instance = generate_toy_instance(
            num_requests=requests,
            num_buses=1,
            num_trip_slots=3,
            num_stations=2,
            scenario=scenario,
            seed=7,
        )
        result = cross_evaluate_modes(
            instance,
            source_mode="linear",
            target_mode="nonlinear",
            time_limit=300.0,
            mip_gap=0.0,
            verbose=False,
        )

    metrics = _stage_metrics(result)
    objectives = [m[0] for m in metrics]
    served_counts = [m[1] for m in metrics]
    charge_times = [m[2] for m in metrics]
    charge_energies = [m[3] for m in metrics]

    fig, axes = plt.subplots(1, 3, figsize=(16.0, 5.4), gridspec_kw={"width_ratios": [1.0, 1.15, 2.0]})
    x_positions = list(range(3))

    # (a) Objective bars
    ax = axes[0]
    bars = ax.bar(x_positions, objectives, color=_STAGE_COLORS, edgecolor="none", alpha=0.92, width=0.62)
    max_obj = max([v for v in objectives if v == v] + [0.0])
    for bar, obj, served in zip(bars, objectives, served_counts):
        y = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            y + max(1.5, 0.015 * max_obj),
            f"{obj:.2f}\n({served} served)",
            ha="center",
            va="bottom",
            fontsize=10,
        )
    ax.set_ylabel(r"Objective (\$)")
    ax.set_title("(a) Operating profit")
    ax.set_xticks(x_positions, _STAGE_LABELS, fontsize=9.5)
    ax.grid(True, axis="y")
    ax.set_ylim(0, max_obj * 1.22 if max_obj > 0 else 1)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # (b) Charging time + energy grouped bars
    ax = axes[1]
    width = 0.34
    offsets = (-width / 2, width / 2)
    time_bars = ax.bar(
        [p + offsets[0] for p in x_positions],
        charge_times,
        width=width,
        color="#6b7fa0",
        edgecolor="none",
        label="Charge time (min)",
    )
    energy_bars = ax.bar(
        [p + offsets[1] for p in x_positions],
        charge_energies,
        width=width,
        color="#d97706",
        edgecolor="none",
        label="Charged energy (kWh)",
    )
    max_metric = max(charge_times + charge_energies + [1.0])
    for bar, val in zip(list(time_bars) + list(energy_bars), charge_times + charge_energies):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.03 * max_metric,
            f"{val:.2f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    ax.set_title("(b) Charging effort")
    ax.set_xticks(x_positions, _STAGE_LABELS, fontsize=9.5)
    ax.grid(True, axis="y")
    ax.set_ylim(0, max_metric * 1.25)
    ax.legend(frameon=False, loc="upper left", fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # (c) Schedule comparison as Gantt rows
    ax = axes[2]
    rows = (
        ("Linear optimum", result.optimized_source),
        ("Linear plan\nunder nonlinear", result.fixed_plan_in_target),
        ("Nonlinear optimum", result.optimized_target),
    )
    segment_colors = {"serve": _SEGMENT_SERVE, "charge": _SEGMENT_CHARGE, "drive": _SEGMENT_DRIVE}
    max_t = 0.0
    for row_idx, (row_label, solve) in enumerate(rows):
        y = len(rows) - 1 - row_idx  # top row is first
        for label, start, width_t, kind in _plan_segments(solve):
            color = segment_colors.get(kind, _SEGMENT_DRIVE)
            ax.barh(y, width_t, left=start, height=0.55, color=color, edgecolor="white", linewidth=1.0)
            if width_t > 2.0:
                ax.text(start + width_t / 2, y, label, ha="center", va="center", color="white", fontsize=8.5)
            max_t = max(max_t, start + width_t)

    ax.set_yticks([2, 1, 0], [row[0] for row in rows], fontsize=9.5)
    ax.set_xlabel("Time (min)")
    ax.set_title("(c) Schedule")
    ax.set_xlim(0, max_t * 1.04 if max_t > 0 else 1)
    ax.set_ylim(-0.6, 2.6)
    ax.grid(True, axis="x")
    # Legend
    from matplotlib.patches import Patch

    ax.legend(
        handles=[
            Patch(facecolor=_SEGMENT_SERVE, label="In-service (pickup/dropoff)"),
            Patch(facecolor=_SEGMENT_CHARGE, label="Charge (incl. reposition)"),
        ],
        frameon=False,
        loc="upper right",
        fontsize=9,
    )
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.suptitle(title, y=1.00, fontsize=14, fontweight="bold")
    fig.tight_layout()
    output = FIGURES_DIR / f"{scenario}_cross_evaluation.png"
    fig.savefig(output, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return output


def plot_partial_cross_evaluation(precomputed_result: Optional[CrossEvaluationResult] = None) -> Path:
    return _draw_cross_panel(
        "Cross-Evaluation in the Partial-Recharge Scenario",
        scenario="partial_recharge",
        requests=7,
        precomputed_result=precomputed_result,
    )


def plot_deep_cross_evaluation(precomputed_result: Optional[CrossEvaluationResult] = None) -> Path:
    return _draw_cross_panel(
        "Cross-Evaluation in the Deep-Recharge Scenario",
        scenario="deep_recharge",
        requests=7,
        precomputed_result=precomputed_result,
    )


def _run_cross_evaluation(scenario: str, requests: int):
    """Solve the cross-evaluation MIPs for one scenario.

    Returns ``(instance, result)`` so callers can also draw a spatial map.
    Results are persisted to ``results/<scenario>_cross_evaluation.json``.
    """
    instance = generate_toy_instance(
        num_requests=requests,
        num_buses=1,
        num_trip_slots=3,
        num_stations=2,
        scenario=scenario,
        seed=7,
    )
    result = cross_evaluate_modes(
        instance,
        source_mode="linear",
        target_mode="nonlinear",
        time_limit=300.0,
        mip_gap=0.0,
        verbose=False,
    )
    _save_cross_evaluation_json(result, scenario)
    return instance, result


def plot_instance_map(instance, result, name: str) -> Path:
    """Draw a 2D spatial map: stations, pickups/dropoffs, and bus routes."""
    _apply_style()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 8))

    def draw_arrow_link(x1, y1, x2, y2, color, lw=2.0, zorder=2):
        ax.plot([x1, x2], [y1, y2], linestyle="-", linewidth=lw, color=color, alpha=0.9, zorder=zorder)
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="->", color=color, lw=lw), zorder=zorder)

    for station, coord in instance.station_coords.items():
        x, y = coord
        ax.scatter(x, y, marker="s", s=180, color="gold", edgecolor="black", zorder=5)
        ax.text(x + 0.25, y + 0.25, f"{station}", fontsize=10, weight="bold", zorder=6)

    for request_id, request in instance.requests.items():
        px, py = request.pickup
        dx, dy = request.dropoff
        ax.scatter(px, py, marker="^", s=120, color="tab:blue", edgecolor="black", zorder=4)
        ax.text(px + 0.25, py + 0.25, f"{request_id}_p", fontsize=9)
        ax.scatter(dx, dy, marker="o", s=120, color="tab:red", edgecolor="black", zorder=4)
        ax.text(dx, dy + 0.4, f"{request_id}_d", fontsize=9)
        ax.plot([px, dx], [py, dy], linestyle=":", linewidth=1.0, color="gray", alpha=0.7, zorder=1)

    route_colors = ["tab:green", "tab:purple", "tab:orange", "tab:brown", "tab:pink", "tab:cyan"]

    def node_coord(node_id: str):
        return instance.nodes[node_id].coord

    initial_station_chosen = getattr(result, "initial_station_chosen", None)

    for idx, trip_report in enumerate(result.trips):
        color = route_colors[idx % len(route_colors)]
        route = trip_report.route
        bus_id = trip_report.bus_id
        trip_index = trip_report.trip_index
        if not route:
            continue

        coords = [node_coord(node_id) for node_id in route]
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        ax.plot(xs, ys, color=color, linewidth=2.5, alpha=0.95, zorder=3,
                label=f"{bus_id} trip {trip_index}")

        for i in range(len(coords) - 1):
            x1, y1 = coords[i]
            x2, y2 = coords[i + 1]
            ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                        arrowprops=dict(arrowstyle="->", color=color, lw=2), zorder=3)

        ex, ey = coords[-1]
        ax.scatter(ex, ey, s=180, facecolors=color, edgecolors="black", linewidths=1.2, zorder=6)

        if trip_report.charge_station_after_trip is not None:
            station = trip_report.charge_station_after_trip
            cx, cy = instance.station_coords[station]

            next_trip_report = next(
                (t for t in result.trips
                 if t.bus_id == bus_id and t.trip_index == trip_index + 1 and t.route),
                None,
            )
            if next_trip_report is not None:
                nx, ny = node_coord(next_trip_report.route[0])
                draw_arrow_link(cx, cy, nx, ny, color="black", lw=2.0, zorder=2)

            draw_arrow_link(ex, ey, cx, cy, color="black", lw=2.0, zorder=2)

    if initial_station_chosen:
        for trip_report in result.trips:
            if trip_report.trip_index != 1 or not trip_report.route:
                continue
            bus_id = trip_report.bus_id
            station = initial_station_chosen.get(bus_id)
            if station is None:
                continue
            sx, sy = instance.station_coords[station]
            fx, fy = node_coord(trip_report.route[0])
            draw_arrow_link(sx, sy, fx, fy, color="black", lw=2.0, zorder=2)

    legend_handles = [
        Line2D([0], [0], marker="s", color="w", label="Charging station",
               markerfacecolor="gold", markeredgecolor="black", markersize=12),
        Line2D([0], [0], marker="^", color="w", label="Pickup",
               markerfacecolor="tab:blue", markeredgecolor="black", markersize=10),
        Line2D([0], [0], marker="o", color="w", label="Dropoff",
               markerfacecolor="tab:red", markeredgecolor="black", markersize=10),
        Line2D([0], [0], color="black", lw=2, linestyle="-", label="Bus route / transfer link"),
    ]
    ax.legend(handles=legend_handles, loc="best", frameon=True)
    ax.set_xlabel("X coordinate")
    ax.set_ylabel("Y coordinate")
    ax.grid(True, alpha=0.3)
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()

    output = FIGURES_DIR / f"{name}.png"
    fig.savefig(output, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return output


def main() -> int:
    data = load_comparison_data()

    partial_instance, partial_result = _run_cross_evaluation("partial_recharge", requests=7)
    deep_instance, deep_result = _run_cross_evaluation("deep_recharge", requests=7)

    outputs = [
        plot_charging_profiles(),
        plot_scenario_comparison(data),
        plot_partial_cross_evaluation(precomputed_result=partial_result),
        plot_deep_cross_evaluation(precomputed_result=deep_result),
        plot_instance_map(partial_instance, partial_result.optimized_source, name="partial_instance_map"),
        plot_instance_map(deep_instance, deep_result.optimized_source, name="deep_instance_map"),
    ]
    print("Generated figures:")
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
