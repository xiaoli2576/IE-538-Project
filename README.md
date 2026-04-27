# IE 538 Project Prototype

This folder now contains a first Python + Gurobi prototype for the static multi-trip routing and inter-trip charging model in the proposal.

The code also includes a small Gurobi license fallback: if `C:\Users\23857\gurobi.lic` is expired, it automatically switches to the working pip license bundled with `gurobipy`.

## Files

- `eaft_model.py`: toy-instance generator, model builder, solver, and solution formatter
- `run_model.py`: command-line entry point
- `compare_modes.py`: batch comparison script for linear versus nonlinear charging
- `cross_evaluate.py`: optimize under one charging assumption and re-evaluate the same discrete plan under another
- `plot_results.py`: generate the main report figures

## What This First Prototype Includes

- multiple buses
- multiple trip slots per bus
- pickup and dropoff routing inside each trip
- pickup time windows
- passenger load propagation
- battery propagation inside trips and across trips
- multiple charging stations with station choice between trips
- station-dependent charging speed and charging cost
- `linear` and `nonlinear` charging through a piecewise-linear tapering curve

## Current Simplifications

- buses start at a fixed initial station
- charging is modeled as charged energy versus charging time, instead of the harder bilinear form `E = p * theta`
- data is generated synthetically for quick testing
- defaults are intentionally small so they fit the current size-limited Gurobi pip license

## Built-In Scenarios

All three scenarios run on the same synthetic instance and differ only in the pickup time-window tightness imposed on a subset of the requests. Headline runs use 7 requests / 3 trip slots / 1 bus; a multi-bus robustness check at 8 requests / 2 trip slots / 2 buses is also reported.

- `baseline`: all windows loose (~55 min wide); both charging models serve every request.
- `partial_recharge`: `r3` and `r4` are squeezed to ~10-min windows. At R=7 both modes converge on the same 5-served plan; partial recharge is no longer the headline.
- `deep_recharge`: `r3`, `r4`, `r5` are tightened back-to-back. At R=7, single bus, the linear model commits to a 35.81 kWh recharge and serves 5 requests at obj 165.51; the nonlinear model refuses that recharge, falls back to 4 served and obj 109.07. The 56.44 gap is the project's headline result, and cross-evaluation shows the linear plan is **infeasible** under the nonlinear curve.

## Run

```bash
python run_model.py --charging-mode linear
python run_model.py --scenario partial_recharge --charging-mode nonlinear
python compare_modes.py --quiet --scenarios baseline partial_recharge deep_recharge --requests 5 --time-limit 120
python cross_evaluate.py --scenario partial_recharge --source-mode linear --target-mode nonlinear --quiet --requests 5
python cross_evaluate.py --scenario deep_recharge --source-mode linear --target-mode nonlinear --quiet --requests 5
python plot_results.py
```

Useful flags:

- `--time-limit 120`
- `--mip-gap 0.005`
- `--quiet`
- `--write-model toy_model.lp`

## Why The Cross-Evaluation Script Matters

`compare_modes.py` tells us how the two models behave when each one is allowed to optimize for itself.

`cross_evaluate.py` answers a different question that is closer to the project motivation:

- If we optimize under a simplified linear charging assumption, does that same route-and-trip plan remain feasible under the nonlinear charging profile?
- If it stays feasible, how much charging time does it really need?
- If it does not stay feasible, then the linear assumption is operationally misleading.

## Current Best Demo Runs

- `python compare_modes.py --quiet --scenarios baseline partial_recharge deep_recharge --requests 7 --buses 1 --trips 3 --time-limit 300 --csv results/mode_comparison_r7_t3.csv`
  Single-bus headline. Baseline serves 7/7 at 353.64. Partial recharge ties at 194.23. Deep recharge opens the headline 56.44 gap (linear 165.51 / 5 served vs nonlinear 109.07 / 4 served).

- `python compare_modes.py --quiet --scenarios baseline partial_recharge deep_recharge --requests 8 --buses 2 --trips 2 --time-limit 300 --csv results/mode_comparison_r8_b2_t2.csv`
  Multi-bus robustness check. Same pathology at smaller magnitude: deep recharge linear 300.22 vs nonlinear 293.98, gap 6.24, linear plan still infeasible under nonlinear.

- `python cross_evaluate.py --scenario deep_recharge --source-mode linear --target-mode nonlinear --quiet --requests 7 --trips 3`
  Reproduces the central pathology: linear-optimal plan returns INFEASIBLE under the nonlinear curve.

- `python plot_results.py`
  Regenerates the six headline figures: `charging_profiles`, `scenario_comparison`, the two `*_cross_evaluation` panels, and the two `*_instance_map` spatial layouts.
