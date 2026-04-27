from __future__ import annotations

import argparse

from eaft_model import format_solution, generate_toy_instance, solve_instance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Solve a toy static multi-trip routing and inter-trip charging instance."
    )
    parser.add_argument("--requests", type=int, default=4, help="Number of passenger requests.")
    parser.add_argument("--buses", type=int, default=1, help="Number of buses.")
    parser.add_argument("--trips", type=int, default=2, help="Maximum trip slots per bus.")
    parser.add_argument("--stations", type=int, default=2, help="Number of charging stations in the toy instance.")
    parser.add_argument(
        "--scenario",
        choices=("baseline", "partial_recharge", "deep_recharge"),
        default="baseline",
        help="Synthetic scenario template for time windows and battery tightness.",
    )
    parser.add_argument(
        "--charging-mode",
        choices=("linear", "nonlinear"),
        default="linear",
        help="Charging curve used between trips.",
    )
    parser.add_argument("--seed", type=int, default=7, help="Random seed for the toy instance.")
    parser.add_argument("--time-limit", type=float, default=60.0, help="Gurobi time limit in seconds.")
    parser.add_argument("--mip-gap", type=float, default=0.01, help="Relative MIP gap target.")
    parser.add_argument("--quiet", action="store_true", help="Suppress solver log output.")
    parser.add_argument("--write-model", help="Optional path to export the generated Gurobi model.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    instance = generate_toy_instance(
        num_requests=args.requests,
        num_buses=args.buses,
        num_trip_slots=args.trips,
        num_stations=args.stations,
        charging_mode=args.charging_mode,
        scenario=args.scenario,
        seed=args.seed,
    )

    result = solve_instance(
        instance,
        time_limit=args.time_limit,
        mip_gap=args.mip_gap,
        verbose=not args.quiet,
        write_model_path=args.write_model,
    )

    print(format_solution(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
