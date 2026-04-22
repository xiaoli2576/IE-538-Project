from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path
from typing import Iterable, List

from eaft_model import generate_toy_instance, solve_instance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare linear and nonlinear charging modes on synthetic project scenarios."
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=["baseline", "partial_recharge"],
        choices=("baseline", "partial_recharge", "deep_recharge"),
        help="Scenario templates to evaluate.",
    )
    parser.add_argument("--seeds", nargs="+", type=int, default=[7], help="Random seeds to evaluate.")
    parser.add_argument("--requests", type=int, default=4, help="Number of passenger requests.")
    parser.add_argument("--buses", type=int, default=1, help="Number of buses.")
    parser.add_argument("--trips", type=int, default=2, help="Maximum trip slots per bus.")
    parser.add_argument("--stations", type=int, default=2, help="Number of charging stations.")
    parser.add_argument("--time-limit", type=float, default=30.0, help="Gurobi time limit in seconds.")
    parser.add_argument("--mip-gap", type=float, default=0.0, help="Relative MIP gap target.")
    parser.add_argument("--quiet", action="store_true", help="Suppress solver logs.")
    parser.add_argument("--csv", help="Optional CSV output path.")
    return parser


def solve_modes(
    *,
    scenario: str,
    seed: int,
    requests: int,
    buses: int,
    trips: int,
    stations: int,
    time_limit: float,
    mip_gap: float,
    quiet: bool,
) -> List[dict]:
    rows: List[dict] = []
    for mode in ("linear", "nonlinear"):
        instance = generate_toy_instance(
            num_requests=requests,
            num_buses=buses,
            num_trip_slots=trips,
            num_stations=stations,
            charging_mode=mode,
            scenario=scenario,
            seed=seed,
        )
        try:
            result = solve_instance(
                instance,
                time_limit=time_limit,
                mip_gap=mip_gap,
                verbose=not quiet,
            )
            rows.append(
                {
                    "num_requests": requests,
                    "num_buses": buses,
                    "num_trip_slots": trips,
                    "num_stations": stations,
                    "time_limit": time_limit,
                    "mip_gap": mip_gap,
                    "scenario": scenario,
                    "seed": seed,
                    "mode": mode,
                    "status": result.status,
                    "objective": result.objective_value,
                    "served_count": len(result.served_requests),
                    "served_requests": ",".join(result.served_requests),
                    "trip_count": len(result.trips),
                    "runtime_seconds": result.runtime_seconds,
                    "total_charge_time": sum(trip.charge_time_after_trip for trip in result.trips),
                    "total_charge_energy": sum(trip.charge_energy_after_trip for trip in result.trips),
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "num_requests": requests,
                    "num_buses": buses,
                    "num_trip_slots": trips,
                    "num_stations": stations,
                    "time_limit": time_limit,
                    "mip_gap": mip_gap,
                    "scenario": scenario,
                    "seed": seed,
                    "mode": mode,
                    "status": f"ERROR: {exc}",
                    "objective": None,
                    "served_count": 0,
                    "served_requests": "",
                    "trip_count": 0,
                    "runtime_seconds": 0.0,
                    "total_charge_time": 0.0,
                    "total_charge_energy": 0.0,
                }
            )
    return rows


def print_summary(rows: Iterable[dict]) -> None:
    rows = list(rows)
    grouped: dict = {}
    for row in rows:
        grouped.setdefault((row["scenario"], row["seed"]), {})[row["mode"]] = row

    # Collect unique scenarios and seeds for aggregate pass
    scenario_seeds: dict = {}
    for (scenario, seed), pair in grouped.items():
        scenario_seeds.setdefault(scenario, []).append(seed)

    for (scenario, seed), pair in grouped.items():
        linear = pair.get("linear")
        nonlinear = pair.get("nonlinear")
        print(f"\nScenario={scenario} seed={seed}")
        for row in (linear, nonlinear):
            if row is None:
                continue
            objective_text = "NA" if row["objective"] is None else f"{row['objective']:.2f}"
            print(
                f"  {row['mode']:<9} status={row['status']:<10} obj={objective_text:<8} "
                f"served={row['served_count']} trips={row['trip_count']} charge_time={row['total_charge_time']:.2f}"
            )

        if linear is not None and nonlinear is not None and linear["objective"] is not None and nonlinear["objective"] is not None:
            delta = nonlinear["objective"] - linear["objective"]
            print(f"  delta(nonlinear-linear)={delta:.2f}")

    # Aggregate block: only when more than one seed was present across any scenario
    all_seeds = set(seed for (_, seed) in grouped.keys())
    if len(all_seeds) <= 1:
        return

    print("\n=== Aggregate across seeds ===")
    for scenario in sorted(scenario_seeds.keys()):
        seeds_for_scenario = scenario_seeds[scenario]
        if len(seeds_for_scenario) <= 1:
            continue

        print(f"\nScenario={scenario}  ({len(seeds_for_scenario)} seeds)")

        for mode in ("linear", "nonlinear"):
            objectives = [
                grouped[(scenario, s)][mode]["objective"]
                for s in seeds_for_scenario
                if mode in grouped.get((scenario, s), {})
                and grouped[(scenario, s)][mode]["objective"] is not None
            ]
            served_counts = [
                grouped[(scenario, s)][mode]["served_count"]
                for s in seeds_for_scenario
                if mode in grouped.get((scenario, s), {})
            ]

            if objectives:
                obj_mean = statistics.mean(objectives)
                obj_min = min(objectives)
                obj_max = max(objectives)
                obj_std = statistics.pstdev(objectives)
                served_mean = statistics.mean(served_counts) if served_counts else float("nan")
                print(
                    f"  {mode:<9} obj: mean={obj_mean:.2f} min={obj_min:.2f} max={obj_max:.2f} "
                    f"stdev={obj_std:.2f}  served_mean={served_mean:.2f}"
                )
            else:
                print(f"  {mode:<9} no feasible solves")

        # Cross-mode feasibility and dominance comparisons
        linear_infeasible_nonlinear_feasible = 0
        nonlinear_infeasible_linear_feasible = 0
        nonlinear_better = 0
        linear_better = 0
        comparable_seeds = 0

        for s in seeds_for_scenario:
            lin = grouped.get((scenario, s), {}).get("linear")
            nl = grouped.get((scenario, s), {}).get("nonlinear")
            if lin is None or nl is None:
                continue

            lin_ok = lin["objective"] is not None
            nl_ok = nl["objective"] is not None

            if not lin_ok and nl_ok:
                linear_infeasible_nonlinear_feasible += 1
            if not nl_ok and lin_ok:
                nonlinear_infeasible_linear_feasible += 1

            if lin_ok and nl_ok:
                comparable_seeds += 1
                if nl["objective"] > lin["objective"]:
                    nonlinear_better += 1
                elif lin["objective"] > nl["objective"]:
                    linear_better += 1

        n = len(seeds_for_scenario)
        print(
            f"  linear infeasible but nonlinear feasible: "
            f"{linear_infeasible_nonlinear_feasible}/{n} "
            f"({100*linear_infeasible_nonlinear_feasible/n:.0f}%)"
        )
        print(
            f"  nonlinear infeasible but linear feasible: "
            f"{nonlinear_infeasible_linear_feasible}/{n} "
            f"({100*nonlinear_infeasible_linear_feasible/n:.0f}%)"
        )
        if comparable_seeds > 0:
            print(
                f"  nonlinear strictly better: "
                f"{nonlinear_better}/{comparable_seeds} "
                f"({100*nonlinear_better/comparable_seeds:.0f}% of comparable seeds)"
            )
            print(
                f"  linear strictly better:    "
                f"{linear_better}/{comparable_seeds} "
                f"({100*linear_better/comparable_seeds:.0f}% of comparable seeds)"
            )
        else:
            print("  no seeds with both modes feasible for objective comparison")


def write_csv(path: str, rows: Iterable[dict]) -> None:
    rows = list(rows)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "num_requests",
                "num_buses",
                "num_trip_slots",
                "num_stations",
                "time_limit",
                "mip_gap",
                "scenario",
                "seed",
                "mode",
                "status",
                "objective",
                "served_count",
                "served_requests",
                "trip_count",
                "runtime_seconds",
                "total_charge_time",
                "total_charge_energy",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    all_rows: List[dict] = []
    for scenario in args.scenarios:
        for seed in args.seeds:
            all_rows.extend(
                solve_modes(
                    scenario=scenario,
                    seed=seed,
                    requests=args.requests,
                    buses=args.buses,
                    trips=args.trips,
                    stations=args.stations,
                    time_limit=args.time_limit,
                    mip_gap=args.mip_gap,
                    quiet=args.quiet,
                )
            )

    print_summary(all_rows)
    if args.csv:
        write_csv(args.csv, all_rows)
        print(f"\nWrote CSV to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
