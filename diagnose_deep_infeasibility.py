"""Saved diagnostic for the deep-recharge INFEASIBLE finding.

Takes the linear-optimal plan (per-trip charge time at each station) and
evaluates the nonlinear charging curve at those same charge times on the
same stations. Reports the per-trip energy shortfall and the total deficit.

This grounds the cross-evaluation INFEASIBLE flag in a concrete
energy-budget gap, instead of leaving it as an inference in the prose.
"""

from __future__ import annotations

import json
from pathlib import Path

from eaft_model import (
    apply_charging_mode,
    cross_evaluate_modes,
    generate_toy_instance,
)


RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _interp_pwl(time: float, time_breakpoints, energy_breakpoints) -> float:
    """Linear interpolation on a PWL given as parallel breakpoint sequences."""
    if time <= time_breakpoints[0]:
        return energy_breakpoints[0]
    if time >= time_breakpoints[-1]:
        return energy_breakpoints[-1]
    for i in range(len(time_breakpoints) - 1):
        t0, t1 = time_breakpoints[i], time_breakpoints[i + 1]
        if t0 <= time <= t1:
            e0, e1 = energy_breakpoints[i], energy_breakpoints[i + 1]
            if t1 == t0:
                return e0
            return e0 + (e1 - e0) * (time - t0) / (t1 - t0)
    return energy_breakpoints[-1]


def main() -> int:
    instance = generate_toy_instance(
        num_requests=7,
        num_buses=1,
        num_trip_slots=3,
        num_stations=2,
        scenario="deep_recharge",
        charging_mode="linear",
        seed=7,
    )

    cross = cross_evaluate_modes(
        instance,
        source_mode="linear",
        target_mode="nonlinear",
        time_limit=120.0,
        mip_gap=0.0,
        verbose=False,
    )

    nonlinear_instance = apply_charging_mode(instance, "nonlinear")
    nonlinear_per_station = nonlinear_instance.station_curves

    per_trip = []
    total_linear_energy = 0.0
    total_nonlinear_deliverable = 0.0
    for trip in cross.optimized_source.trips:
        station = trip.charge_station_after_trip
        t = trip.charge_time_after_trip
        e_linear = trip.charge_energy_after_trip
        if station is None or t <= 0:
            per_trip.append({
                "bus_id": trip.bus_id,
                "trip_index": trip.trip_index,
                "station": None,
                "charge_time_min": t,
                "linear_energy_kWh": e_linear,
                "nonlinear_deliverable_kWh": 0.0,
                "shortfall_kWh": 0.0,
                "note": "no inter-trip charge",
            })
            continue
        curve = nonlinear_per_station[station]
        e_nonlinear = _interp_pwl(t, curve.time_breakpoints, curve.energy_breakpoints)
        per_trip.append({
            "bus_id": trip.bus_id,
            "trip_index": trip.trip_index,
            "station": station,
            "charge_time_min": round(t, 4),
            "linear_energy_kWh": round(e_linear, 4),
            "nonlinear_deliverable_kWh": round(e_nonlinear, 4),
            "shortfall_kWh": round(e_linear - e_nonlinear, 4),
        })
        total_linear_energy += e_linear
        total_nonlinear_deliverable += e_nonlinear

    diagnostic = {
        "scenario": "deep_recharge",
        "configuration": {
            "num_requests": 7,
            "num_buses": 1,
            "num_trip_slots": 3,
            "num_stations": 2,
            "seed": 7,
        },
        "linear_source_objective": cross.optimized_source.objective_value,
        "nonlinear_target_status": cross.fixed_plan_in_target.status,
        "per_trip_energy_check": per_trip,
        "totals": {
            "linear_total_kWh": round(total_linear_energy, 4),
            "nonlinear_deliverable_total_kWh": round(total_nonlinear_deliverable, 4),
            "total_shortfall_kWh": round(total_linear_energy - total_nonlinear_deliverable, 4),
        },
        "interpretation": (
            "The linear plan books inter-trip recharge times that, on the same "
            "stations, would deliver less energy under the tapering nonlinear "
            "curve. The shortfall propagates to insufficient battery for the "
            "next trip, which Gurobi reports as INFEASIBLE for the fixed plan."
        ),
    }

    output_path = RESULTS_DIR / "deep_recharge_infeasibility_diagnostic.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(diagnostic, handle, indent=2)

    print(f"Wrote {output_path}")
    print()
    print("Per-trip energy check (linear plan times -> nonlinear curve):")
    for row in per_trip:
        if row["station"] is None:
            continue
        print(
            f"  trip {row['trip_index']} @ {row['station']}: "
            f"{row['charge_time_min']:>5.2f} min linear={row['linear_energy_kWh']:>5.2f} kWh "
            f"nonlinear={row['nonlinear_deliverable_kWh']:>5.2f} kWh "
            f"shortfall={row['shortfall_kWh']:>5.2f} kWh"
        )
    print(
        f"  total: linear {diagnostic['totals']['linear_total_kWh']} kWh "
        f"vs nonlinear deliverable {diagnostic['totals']['nonlinear_deliverable_total_kWh']} kWh "
        f"(shortfall {diagnostic['totals']['total_shortfall_kWh']} kWh)"
    )
    print(f"Fixed plan status under nonlinear: {diagnostic['nonlinear_target_status']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
