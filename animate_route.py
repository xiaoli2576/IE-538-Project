"""Render a synchronized dual-panel animation of the optimized bus route
under partial vs deep recharge (linear charging mode).

Outputs:
    figures/dual_recharge_animation.gif       - portable, plays anywhere
    figures/anim/frame_NNN.png                - frame sequence for
                                                \\animategraphics in Beamer

Uses the same data path as figures/{partial,deep}_instance_map.png:
plot_results._run_cross_evaluation(scenario, requests=7).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import to_rgba
from matplotlib.patches import Wedge

from plot_results import _run_cross_evaluation


# -- palette --
BG          = "#0a1426"
PANEL_BG    = "#0f1c33"
GRID_COLOR  = "#1a2845"
GOLD        = "#CEB888"
DARK_GOLD   = "#8E6F3E"
RED         = "#B1371B"
TEXT        = "#e8e2d0"
TEXT_DIM    = "#7a8298"
PHASE_CARRY    = "#3ddc84"
PHASE_DEADHEAD = "#5b9bff"
PHASE_CHARGE   = "#ff9326"
PICKUP_DIM  = "#284b6a"
DROPOFF_DIM = "#5a3338"
SERVED_LIT  = "#3ddc84"
DROPPED     = "#3a3f4b"


@dataclass
class Segment:
    t0: float
    t1: float
    p0: Tuple[float, float]
    p1: Tuple[float, float]
    phase: str                  # "carry" | "deadhead" | "charge"
    label: str
    arrival_node: Optional[str] # node id at end (for marker lighting)
    station: Optional[str]      # station name during this segment


# --------------------------------------------------------------------------- #
# Timeline construction
# --------------------------------------------------------------------------- #

def build_timeline(instance, result) -> List[Segment]:
    bus_id = list(instance.buses)[0]
    init_station = (
        result.initial_station_chosen.get(bus_id)
        if result.initial_station_chosen else None
    )
    trips = sorted(
        [t for t in result.trips if t.bus_id == bus_id and t.route],
        key=lambda t: t.trip_index,
    )
    segs: List[Segment] = []
    if not trips:
        return segs

    t = 0.0
    if init_station is not None:
        first = trips[0].route[0]
        sx, sy = instance.station_coords[init_station]
        nx, ny = instance.nodes[first].coord
        travel = instance.station_to_node_time.get((init_station, first), 0.0)
        segs.append(Segment(t, t + travel, (sx, sy), (nx, ny),
                            "deadhead", f"depart {init_station}",
                            arrival_node=first, station=init_station))
        t += travel

    for i, trip in enumerate(trips):
        for k in range(len(trip.route) - 1):
            a, b = trip.route[k], trip.route[k + 1]
            ax, ay = instance.nodes[a].coord
            bx, by = instance.nodes[b].coord
            travel = instance.travel_time.get((a, b), 0.0)
            na, nb = instance.nodes[a], instance.nodes[b]
            if (na.kind == "pickup" and nb.kind == "dropoff"
                    and na.request_id == nb.request_id):
                phase, label = "carry", f"carrying {na.request_id}"
            else:
                phase, label = "deadhead", "deadhead"
            segs.append(Segment(t, t + travel, (ax, ay), (bx, by),
                                phase, label, arrival_node=b, station=None))
            t += travel

        station = trip.charge_station_after_trip
        if station is not None:
            last = trip.route[-1]
            lx, ly = instance.nodes[last].coord
            sx, sy = instance.station_coords[station]
            travel = instance.node_to_station_time.get((last, station), 0.0)
            segs.append(Segment(t, t + travel, (lx, ly), (sx, sy),
                                "deadhead", f"to {station}",
                                arrival_node=None, station=station))
            t += travel

            charge = trip.charge_time_after_trip or 0.0
            if charge > 0:
                segs.append(Segment(t, t + charge, (sx, sy), (sx, sy),
                                    "charge", f"charging @ {station}",
                                    arrival_node=None, station=station))
                t += charge

            if i + 1 < len(trips):
                nxt = trips[i + 1].route[0]
                nx, ny = instance.nodes[nxt].coord
                travel = instance.station_to_node_time.get((station, nxt), 0.0)
                segs.append(Segment(t, t + travel, (sx, sy), (nx, ny),
                                    "deadhead", f"depart {station}",
                                    arrival_node=nxt, station=station))
                t += travel
    return segs


def position_at(segs: List[Segment], sim_t: float):
    if not segs:
        return (0.0, 0.0), "deadhead", "", None
    if sim_t <= segs[0].t0:
        s = segs[0]
        return s.p0, s.phase, s.label, s
    for s in segs:
        if s.t0 <= sim_t <= s.t1:
            if s.t1 == s.t0:
                return s.p1, s.phase, s.label, s
            u = (sim_t - s.t0) / (s.t1 - s.t0)
            x = s.p0[0] + u * (s.p1[0] - s.p0[0])
            y = s.p0[1] + u * (s.p1[1] - s.p0[1])
            return (x, y), s.phase, s.label, s
    s = segs[-1]
    return s.p1, "done", "complete", s


def build_arrival_events(instance, segs: List[Segment]):
    """Per-segment arrivals: list of (time, request_id, kind)."""
    events = []
    for s in segs:
        if s.arrival_node:
            nd = instance.nodes[s.arrival_node]
            events.append((s.t1, nd.request_id, nd.kind))
    return events


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #

def setup_panel(ax, instance, out_of_plan, title, accent, verdict, verdict_color):
    ax.set_facecolor(PANEL_BG)
    ax.grid(True, color=GRID_COLOR, linewidth=0.5)
    for spine in ax.spines.values():
        spine.set_color(accent)
        spine.set_linewidth(1.4)
    ax.tick_params(colors=TEXT_DIM, labelsize=8)

    xs = [c[0] for c in instance.station_coords.values()]
    ys = [c[1] for c in instance.station_coords.values()]
    for r in instance.requests.values():
        xs.extend([r.pickup[0], r.dropoff[0]])
        ys.extend([r.pickup[1], r.dropoff[1]])
    pad_x = (max(xs) - min(xs)) * 0.12 + 0.5
    pad_y = (max(ys) - min(ys)) * 0.06 + 0.5
    ax.set_xlim(min(xs) - pad_x, max(xs) + pad_x)
    # Extra headroom on top (HUD) and floor (verdict text)
    ax.set_ylim(min(ys) - pad_y * 4.0, max(ys) + pad_y * 2.2)

    ax.set_title(title, color=accent, fontsize=15, weight="bold", pad=10)

    for s, (x, y) in instance.station_coords.items():
        ax.scatter(x, y, marker="s", s=300, c=GOLD, edgecolor=DARK_GOLD,
                   linewidth=1.6, zorder=5)
        ax.text(x + 0.3, y + 0.35, s, color=GOLD, fontsize=10, weight="bold",
                zorder=6)

    rmarks = {}
    for rid, req in instance.requests.items():
        oop = rid in out_of_plan
        cp = DROPPED if oop else PICKUP_DIM
        cd = DROPPED if oop else DROPOFF_DIM
        ap = ax.scatter(*req.pickup, marker="^", s=120, c=cp,
                        edgecolor="black", linewidth=0.8, zorder=4,
                        alpha=0.40 if oop else 1.0)
        ad = ax.scatter(*req.dropoff, marker="o", s=120, c=cd,
                        edgecolor="black", linewidth=0.8, zorder=4,
                        alpha=0.40 if oop else 1.0)
        ax.plot([req.pickup[0], req.dropoff[0]],
                [req.pickup[1], req.dropoff[1]],
                ":", linewidth=0.6,
                color=DROPPED if oop else GRID_COLOR,
                alpha=0.7, zorder=1)
        ax.text(req.pickup[0] + 0.2, req.pickup[1] + 0.25, rid,
                color=DROPPED if oop else TEXT_DIM,
                fontsize=8, zorder=4,
                alpha=0.5 if oop else 1.0)
        rmarks[rid] = {"pickup": ap, "dropoff": ad,
                       "lit_p": False, "lit_d": False, "out_of_plan": oop}

    halo, = ax.plot([], [], "o", color=PHASE_DEADHEAD, markersize=34,
                    alpha=0.30, zorder=8)
    glow, = ax.plot([], [], "o", color=PHASE_DEADHEAD, markersize=24,
                    alpha=0.55, zorder=9)
    bus,  = ax.plot([], [], "o", color=PHASE_DEADHEAD, markersize=14,
                    markeredgecolor="white", markeredgewidth=1.4, zorder=10)

    trail = LineCollection([], linewidths=2.4, zorder=7, capstyle="round")
    ax.add_collection(trail)

    wedge = Wedge((0, 0), 0.9, 90, 90, width=0.22, facecolor=PHASE_CHARGE,
                  edgecolor="none", alpha=0.85, zorder=6)
    wedge.set_visible(False)
    ax.add_patch(wedge)

    hud_clk = ax.text(0.02, 0.97, "", transform=ax.transAxes, color=TEXT,
                      fontsize=11, weight="bold", va="top", family="monospace")
    hud_pha = ax.text(0.02, 0.92, "", transform=ax.transAxes,
                      color=PHASE_DEADHEAD, fontsize=10, weight="bold",
                      va="top", family="monospace")
    hud_cnt = ax.text(0.02, 0.87, "", transform=ax.transAxes, color=TEXT_DIM,
                      fontsize=10, va="top", family="monospace")

    # Bottom-of-panel verdict text (fades in near the end)
    hud_verdict = ax.text(0.5, 0.025, "", transform=ax.transAxes,
                          color=verdict_color, fontsize=12, weight="bold",
                          ha="center", va="bottom", family="monospace",
                          alpha=0.0, zorder=20,
                          bbox=dict(boxstyle="round,pad=0.45",
                                    facecolor=BG, edgecolor=verdict_color,
                                    linewidth=1.4, alpha=0.95))

    return {"bus": bus, "glow": glow, "halo": halo,
            "trail": trail, "wedge": wedge,
            "clk": hud_clk, "pha": hud_pha, "cnt": hud_cnt,
            "verdict": hud_verdict, "verdict_text": verdict,
            "rmarks": rmarks}


def _rgba(c, a):
    r = list(to_rgba(c))
    r[3] = a
    return tuple(r)


PHASE_COLOR = {"carry": PHASE_CARRY, "deadhead": PHASE_DEADHEAD,
               "charge": PHASE_CHARGE, "done": TEXT_DIM}


def update_panel(inst, segs, events, art, trail_buf, sim_t, served_target,
                 t_total):
    (x, y), phase, label, seg = position_at(segs, sim_t)
    color = PHASE_COLOR[phase]

    art["bus"].set_data([x], [y])
    art["bus"].set_color(color)
    art["glow"].set_data([x], [y])
    art["glow"].set_color(color)
    art["halo"].set_data([x], [y])
    art["halo"].set_color(color)

    # Persistent comet trail: always append, cap at large buffer
    trail_buf.append((x, y, color))
    if len(trail_buf) > 240:
        trail_buf.pop(0)

    if len(trail_buf) > 1:
        segments = []
        colors = []
        n = len(trail_buf)
        for i in range(1, n):
            x1, y1, _ = trail_buf[i - 1]
            x2, y2, c2 = trail_buf[i]
            segments.append([(x1, y1), (x2, y2)])
            # Older segments fade out but stay visible (min alpha 0.18)
            age = i / n
            colors.append(_rgba(c2, 0.20 + 0.65 * age))
        art["trail"].set_segments(segments)
        art["trail"].set_color(colors)
    else:
        art["trail"].set_segments([])

    if phase == "charge" and seg is not None and seg.station:
        sx, sy = inst.station_coords[seg.station]
        denom = max(seg.t1 - seg.t0, 1e-6)
        u = max(0.0, min(1.0, (sim_t - seg.t0) / denom))
        art["wedge"].set_center((sx, sy))
        art["wedge"].set_theta1(90 - 360 * u)
        art["wedge"].set_theta2(90)
        art["wedge"].set_visible(True)
    else:
        art["wedge"].set_visible(False)

    # Light up every arrival whose time has passed (stateless replay)
    for ev_t, rid, kind in events:
        if ev_t > sim_t:
            break
        if rid not in art["rmarks"]:
            continue
        rm = art["rmarks"][rid]
        if rm["out_of_plan"]:
            continue
        if kind == "pickup" and not rm["lit_p"]:
            rm["pickup"].set_color(SERVED_LIT)
            rm["pickup"].set_sizes([220])
            rm["lit_p"] = True
        elif kind == "dropoff" and not rm["lit_d"]:
            rm["dropoff"].set_color(SERVED_LIT)
            rm["dropoff"].set_sizes([220])
            rm["lit_d"] = True

    # Verdict fades in over the last 12% of the timeline
    completion = sim_t / max(t_total, 1e-6)
    fade_start = 0.78
    if completion > fade_start:
        a = min(1.0, (completion - fade_start) / (1.0 - fade_start))
        art["verdict"].set_alpha(a)
        art["verdict"].set_text(art["verdict_text"])

    served_now = sum(1 for rm in art["rmarks"].values() if rm["lit_d"])
    art["clk"].set_text(f"t={sim_t:5.1f}m")
    art["pha"].set_text(label.upper())
    art["pha"].set_color(color)
    cnt_color = SERVED_LIT if served_now >= served_target and served_target > 0 else TEXT_DIM
    art["cnt"].set_text(f"served {served_now}/{served_target}")
    art["cnt"].set_color(cnt_color)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    print("Solving partial scenario ...")
    p_inst, p_x = _run_cross_evaluation("partial_recharge", requests=7)
    print("Solving deep scenario ...")
    d_inst, d_x = _run_cross_evaluation("deep_recharge", requests=7)

    p_res = p_x.optimized_source
    d_res = d_x.optimized_source

    # In-plan = requests the linear optimum actually visits
    p_in_plan = set(p_res.served_requests)
    d_in_plan = set(d_res.served_requests)
    p_out = set(p_inst.requests) - p_in_plan
    d_out = set(d_inst.requests) - d_in_plan

    # Verdict (does the linear plan survive a nonlinear replay?)
    p_replay = set(p_x.fixed_plan_in_target.served_requests)
    d_replay = set(d_x.fixed_plan_in_target.served_requests)
    p_lost = p_in_plan - p_replay
    d_lost = d_in_plan - d_replay

    if not p_lost:
        p_verdict = f"NONLINEAR REPLAY: {len(p_replay)}/{len(p_in_plan)}  feasible"
        p_verdict_color = SERVED_LIT
    else:
        p_verdict = f"NONLINEAR REPLAY: {len(p_replay)}/{len(p_in_plan)}  ({len(p_lost)} dropped)"
        p_verdict_color = "#ffb84d"

    if not d_lost:
        d_verdict = f"NONLINEAR REPLAY: {len(d_replay)}/{len(d_in_plan)}  feasible"
        d_verdict_color = SERVED_LIT
    else:
        d_verdict = f"NONLINEAR REPLAY: {len(d_replay)}/{len(d_in_plan)}  INFEASIBLE"
        d_verdict_color = RED

    p_segs = build_timeline(p_inst, p_res)
    d_segs = build_timeline(d_inst, d_res)
    p_events = build_arrival_events(p_inst, p_segs)
    d_events = build_arrival_events(d_inst, d_segs)

    T_total = max(p_segs[-1].t1 if p_segs else 0.0,
                  d_segs[-1].t1 if d_segs else 0.0) + 4.0

    fps      = 24
    duration = 12.0
    n_frames = int(fps * duration)

    fig, (ax_p, ax_d) = plt.subplots(
        1, 2, figsize=(13, 8.5), facecolor=BG,
        gridspec_kw={"wspace": 0.12},
    )
    plt.subplots_adjust(left=0.05, right=0.97, top=0.92, bottom=0.06)
    fig.suptitle("Linear-mode optimum  ·  Partial vs Deep recharge",
                 color=TEXT, fontsize=16, weight="bold", y=0.985)

    p_art = setup_panel(ax_p, p_inst, p_out,
                        "PARTIAL recharge", GOLD,
                        p_verdict, p_verdict_color)
    d_art = setup_panel(ax_d, d_inst, d_out,
                        "DEEP recharge", RED,
                        d_verdict, d_verdict_color)

    served_target_p = len(p_in_plan)
    served_target_d = len(d_in_plan)

    p_trail: list = []
    d_trail: list = []

    out_dir = Path("figures")
    out_dir.mkdir(exist_ok=True)
    frames_dir = out_dir / "anim"
    if frames_dir.exists():
        for old in frames_dir.glob("frame_*.png"):
            old.unlink()
    frames_dir.mkdir(exist_ok=True)

    print(f"Rendering {n_frames} frames ({duration:.1f}s @ {fps}fps) "
          f"covering T={T_total:.1f} simulated minutes ...")

    saved_paths = []
    for i in range(n_frames):
        sim_t = (i / max(n_frames - 1, 1)) * T_total
        update_panel(p_inst, p_segs, p_events, p_art, p_trail,
                     sim_t, served_target_p, T_total)
        update_panel(d_inst, d_segs, d_events, d_art, d_trail,
                     sim_t, served_target_d, T_total)
        path = frames_dir / f"frame_{i:03d}.png"
        fig.savefig(path, dpi=110, facecolor=BG)
        saved_paths.append(path)
        if (i + 1) % 24 == 0 or i == n_frames - 1:
            print(f"  frame {i+1}/{n_frames}")

    plt.close(fig)

    # Stitch into a GIF via Pillow
    from PIL import Image
    print("Assembling GIF ...")
    frames = [Image.open(p).convert("P", palette=Image.ADAPTIVE)
              for p in saved_paths]
    gif_path = out_dir / "dual_recharge_animation.gif"
    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / fps),
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(f"Wrote: {gif_path}")
    print(f"Wrote: {len(saved_paths)} frames in {frames_dir}/")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
