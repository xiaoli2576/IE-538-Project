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

- `baseline`: the original loose toy instance
- `partial_recharge`: a tighter 4-request case where nonlinear charging helps because a moderate recharge at the fast part of the taper is enough
- `deep_recharge`: a tighter 6-request case where linear charging helps because the plan needs a much deeper recharge

## Run

```bash
python run_model.py --charging-mode linear
python run_model.py --scenario partial_recharge --charging-mode nonlinear
python compare_modes.py --quiet
python compare_modes.py --quiet --scenarios deep_recharge --requests 6
python cross_evaluate.py --scenario partial_recharge --source-mode linear --target-mode nonlinear --quiet
python cross_evaluate.py --scenario deep_recharge --source-mode linear --target-mode nonlinear --quiet --requests 6
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

- `python compare_modes.py --quiet`
  This shows `baseline` and `partial_recharge` with the default small instance.

- `python compare_modes.py --quiet --scenarios deep_recharge --requests 6`
  This shows the deeper recharge case where linear charging can outperform nonlinear charging.

- `python cross_evaluate.py --scenario partial_recharge --source-mode linear --target-mode nonlinear --quiet`
  In the current setup, the linear-optimal discrete plan becomes infeasible under nonlinear charging.

- `python cross_evaluate.py --scenario deep_recharge --source-mode linear --target-mode nonlinear --quiet --requests 6`
  In the current setup, the linear-optimal deep-recharge plan also becomes infeasible under nonlinear charging.
