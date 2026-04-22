from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from eaft_model import cross_evaluate_modes, format_solution, generate_toy_instance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Optimize under one charging assumption and re-evaluate the fixed discrete plan under another."
    )
    parser.add_argument("--requests", type=int, default=4, help="Number of passenger requests.")
    parser.add_argument("--buses", type=int, default=1, help="Number of buses.")
    parser.add_argument("--trips", type=int, default=2, help="Maximum trip slots per bus.")
    parser.add_argument("--stations", type=int, default=2, help="Number of charging stations.")
    parser.add_argument(
        "--scenario",
        choices=("baseline", "partial_recharge", "deep_recharge"),
        default="baseline",
        help="Synthetic scenario template.",
    )
    parser.add_argument(
        "--source-mode",
        choices=("linear", "nonlinear"),
        default="linear",
        help="Charging assumption used to optimize the plan.",
    )
    parser.add_argument(
        "--target-mode",
        choices=("linear", "nonlinear"),
        default="nonlinear",
        help="Charging assumption used to re-evaluate the optimized plan.",
    )
    parser.add_argument("--seed", type=int, default=7, help="Random seed for the toy instance.")
    parser.add_argument("--time-limit", type=float, default=30.0, help="Gurobi time limit in seconds.")
    parser.add_argument("--mip-gap", type=float, default=0.0, help="Relative MIP gap target.")
    parser.add_argument("--quiet", action="store_true", help="Suppress solver log output.")
    parser.add_argument("--json", dest="json_path", default=None, help="Optional JSON output path.")
    parser.add_argument("--csv", dest="csv_path", default=None, help="Optional CSV output path.")
    return parser


def _summary_block(title: str, text: str) -> str:
    return f"{title}\n{text}".rstrip()


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _trip_to_dict(trip: Any) -> Dict[str, Any]:
    """Convert a single TripResult (or equivalent dataclass) to a plain dict."""
    return {
        "bus_id": trip.bus_id,
        "trip_index": trip.trip_index,
        "served_requests": list(trip.served_requests),
        "route": list(trip.route),
        "ready_time": trip.ready_time,
        "trip_end_time": trip.trip_end_time,
        "ready_battery": trip.ready_battery,
        "battery_end": trip.battery_end,
        "charge_station_after_trip": trip.charge_station_after_trip,
        "charge_time_after_trip": trip.charge_time_after_trip,
        "charge_energy_after_trip": trip.charge_energy_after_trip,
    }


def _solve_result_to_dict(solve_result: Any) -> Dict[str, Any]:
    """Convert a SolveResult (or equivalent dataclass) to a plain dict."""
    return {
        "status": solve_result.status,
        "objective_value": solve_result.objective_value,
        "runtime_seconds": solve_result.runtime_seconds,
        "mip_gap": solve_result.mip_gap,
        "served_requests": list(solve_result.served_requests),
        "unserved_requests": list(solve_result.unserved_requests),
        "trips": [_trip_to_dict(t) for t in solve_result.trips],
    }


def build_json_payload(
    result: Any,
    *,
    scenario: str,
    seed: int,
) -> Dict[str, Any]:
    """Build the full JSON payload from a CrossEvaluationResult."""
    return {
        "scenario": scenario,
        "source_mode": result.source_mode,
        "target_mode": result.target_mode,
        "seed": seed,
        "optimized_source": _solve_result_to_dict(result.optimized_source),
        "fixed_plan_in_target": _solve_result_to_dict(result.fixed_plan_in_target),
        "optimized_target": _solve_result_to_dict(result.optimized_target),
    }


def write_json(path: str, payload: Dict[str, Any]) -> None:
    """Write the JSON payload to *path*, creating parent directories as needed."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)


def build_csv_rows(
    result: Any,
    *,
    scenario: str,
    seed: int,
) -> List[Dict[str, Any]]:
    """
    Build flat CSV rows — one row per (which_solve, trip_index).

    *which_solve* is one of ``optimized_source``, ``fixed_plan_in_target``,
    ``optimized_target``.  Solves with no trips emit a single blank-trip row.
    """
    rows: List[Dict[str, Any]] = []

    solve_labels = [
        ("optimized_source", result.optimized_source),
        ("fixed_plan_in_target", result.fixed_plan_in_target),
        ("optimized_target", result.optimized_target),
    ]

    for which_solve, solve_result in solve_labels:
        top_level = {
            "scenario": scenario,
            "source_mode": result.source_mode,
            "target_mode": result.target_mode,
            "seed": seed,
            "which_solve": which_solve,
            "status": solve_result.status,
            "objective_value": solve_result.objective_value,
        }

        trips = list(solve_result.trips)
        if not trips:
            # Emit a single row with trip fields blank
            rows.append(
                {
                    **top_level,
                    "bus_id": "",
                    "trip_index": "",
                    "served_requests": "",
                    "route": "",
                    "ready_time": "",
                    "trip_end_time": "",
                    "ready_battery": "",
                    "battery_end": "",
                    "charge_station_after_trip": "",
                    "charge_time_after_trip": "",
                    "charge_energy_after_trip": "",
                }
            )
        else:
            for trip in trips:
                rows.append(
                    {
                        **top_level,
                        "bus_id": trip.bus_id,
                        "trip_index": trip.trip_index,
                        "served_requests": ",".join(str(r) for r in trip.served_requests),
                        "route": ",".join(str(n) for n in trip.route),
                        "ready_time": trip.ready_time,
                        "trip_end_time": trip.trip_end_time,
                        "ready_battery": trip.ready_battery,
                        "battery_end": trip.battery_end,
                        "charge_station_after_trip": trip.charge_station_after_trip,
                        "charge_time_after_trip": trip.charge_time_after_trip,
                        "charge_energy_after_trip": trip.charge_energy_after_trip,
                    }
                )

    return rows


_CSV_FIELDNAMES = [
    "scenario",
    "source_mode",
    "target_mode",
    "seed",
    "which_solve",
    "status",
    "objective_value",
    "bus_id",
    "trip_index",
    "served_requests",
    "route",
    "ready_time",
    "trip_end_time",
    "ready_battery",
    "battery_end",
    "charge_station_after_trip",
    "charge_time_after_trip",
    "charge_energy_after_trip",
]


def write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    """Write flat trip-level CSV rows to *path*, creating parent directories."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    instance = generate_toy_instance(
        num_requests=args.requests,
        num_buses=args.buses,
        num_trip_slots=args.trips,
        num_stations=args.stations,
        scenario=args.scenario,
        seed=args.seed,
    )

    result = cross_evaluate_modes(
        instance,
        source_mode=args.source_mode,
        target_mode=args.target_mode,
        time_limit=args.time_limit,
        mip_gap=args.mip_gap,
        verbose=not args.quiet,
    )

    # Existing text output — preserved exactly
    print(f"Scenario: {args.scenario}")
    print(f"Source mode: {result.source_mode}")
    print(f"Target mode: {result.target_mode}")
    print("")
    print(_summary_block("Optimized under source mode", format_solution(result.optimized_source)))
    print("")
    print(_summary_block(f"Same discrete plan re-evaluated under {result.target_mode}", format_solution(result.fixed_plan_in_target)))
    print("")
    print(_summary_block(f"Fully re-optimized under {result.target_mode}", format_solution(result.optimized_target)))

    # Optional JSON output
    if args.json_path:
        payload = build_json_payload(result, scenario=args.scenario, seed=args.seed)
        write_json(args.json_path, payload)
        print(f"\nWrote JSON to {args.json_path}")

    # Optional CSV output
    if args.csv_path:
        csv_rows = build_csv_rows(result, scenario=args.scenario, seed=args.seed)
        write_csv(args.csv_path, csv_rows)
        print(f"\nWrote CSV to {args.csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
