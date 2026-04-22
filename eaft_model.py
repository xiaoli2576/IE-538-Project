from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import math
import os
from pathlib import Path
import random
import sys
from typing import Dict, List, Sequence, Tuple
import warnings


def _parse_license_fields(path: Path) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    try:
        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            fields[key.strip().upper()] = value.strip()
    except OSError:
        return {}
    return fields


def _license_is_expired(fields: Dict[str, str]) -> bool:
    expiration_text = fields.get("EXPIRATION")
    if not expiration_text:
        return False
    try:
        return date.fromisoformat(expiration_text) < date.today()
    except ValueError:
        return False


def _configure_gurobi_license() -> None:
    if os.environ.get("GRB_LICENSE_FILE"):
        return

    home_license = Path.home() / "gurobi.lic"
    appdata = os.environ.get("APPDATA")
    pip_license = None
    if appdata:
        py_ver_dir = f"Python{sys.version_info.major}{sys.version_info.minor}"
        pip_candidate = Path(appdata) / "Python" / py_ver_dir / "site-packages" / "gurobipy" / "gurobi.lic"
        if pip_candidate.exists():
            pip_license = pip_candidate
        else:
            # Fall back: scan for any Python3* subdirectory that has the license
            python_root = Path(appdata) / "Python"
            if python_root.is_dir():
                for sub in sorted(python_root.iterdir()):
                    candidate = sub / "site-packages" / "gurobipy" / "gurobi.lic"
                    if candidate.exists():
                        pip_license = candidate
                        break

    if home_license.exists():
        home_fields = _parse_license_fields(home_license)
        if _license_is_expired(home_fields) and pip_license is not None:
            os.environ["GRB_LICENSE_FILE"] = str(pip_license)
        return

    if pip_license is not None:
        os.environ["GRB_LICENSE_FILE"] = str(pip_license)


_configure_gurobi_license()

import gurobipy as gp
from gurobipy import GRB


Coordinate = Tuple[float, float]


@dataclass(frozen=True)
class Request:
    request_id: str
    pickup: Coordinate
    dropoff: Coordinate
    earliest_pickup: float
    latest_pickup: float
    passengers: int
    revenue: float


@dataclass(frozen=True)
class NodeData:
    node_id: str
    request_id: str
    kind: str
    coord: Coordinate
    load_delta: int


@dataclass(frozen=True)
class ChargingCurve:
    mode: str
    time_breakpoints: Tuple[float, ...]
    energy_breakpoints: Tuple[float, ...]

    @property
    def max_time(self) -> float:
        return self.time_breakpoints[-1]

    @property
    def max_energy(self) -> float:
        return self.energy_breakpoints[-1]


@dataclass(frozen=True)
class ModelParameters:
    bus_capacity: int
    battery_capacity: float
    battery_min: float
    initial_battery: float
    trip_fixed_cost: float
    bus_fixed_cost: float
    travel_cost: float
    charge_cost: float
    unserved_penalty: float


@dataclass(frozen=True)
class EaftInstance:
    buses: Tuple[str, ...]
    trip_slots: Tuple[int, ...]
    stations: Tuple[str, ...]
    initial_station: str
    requests: Dict[str, Request]
    nodes: Dict[str, NodeData]
    pickup_node: Dict[str, str]
    dropoff_node: Dict[str, str]
    travel_time: Dict[Tuple[str, str], float]
    travel_energy: Dict[Tuple[str, str], float]
    station_to_node_time: Dict[Tuple[str, str], float]
    station_to_node_energy: Dict[Tuple[str, str], float]
    node_to_station_time: Dict[Tuple[str, str], float]
    node_to_station_energy: Dict[Tuple[str, str], float]
    direct_service_time: Dict[str, float]
    charging_curve: ChargingCurve
    station_curves: Dict[str, ChargingCurve]
    station_charge_cost: Dict[str, float]
    station_time_scale: Dict[str, float]
    params: ModelParameters
    station_coords: Dict[str, Coordinate]
    time_horizon: float


@dataclass(frozen=True)
class TripReport:
    bus_id: str
    trip_index: int
    served_requests: List[str]
    route: List[str]
    ready_time: float
    trip_end_time: float
    ready_battery: float
    battery_end: float
    charge_station_after_trip: str | None
    charge_time_after_trip: float
    charge_energy_after_trip: float


@dataclass(frozen=True)
class SolveResult:
    status: str
    objective_value: float | None
    runtime_seconds: float
    mip_gap: float | None
    served_requests: List[str]
    unserved_requests: List[str]
    trips: List[TripReport]
    fixed_plan: "FixedBinaryPlan | None" = None
    initial_station_chosen: "Dict[str, str | None] | None" = None  # C.13


@dataclass(frozen=True)
class FixedBinaryPlan:
    trip_active: Dict[Tuple[str, int], int]
    charge_station: Dict[Tuple[str, str, int], int]
    first: Dict[Tuple[str, str, int], int]
    last: Dict[Tuple[str, str, int], int]
    arc: Dict[Tuple[str, str, str, int], int]


@dataclass(frozen=True)
class CrossEvaluationResult:
    source_mode: str
    target_mode: str
    optimized_source: SolveResult
    fixed_plan_in_target: SolveResult
    optimized_target: SolveResult


def _distance(a: Coordinate, b: Coordinate) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _travel_minutes(a: Coordinate, b: Coordinate) -> float:
    return round(2.0 + 1.3 * _distance(a, b), 2)


def _travel_energy(a: Coordinate, b: Coordinate) -> float:
    return round(0.7 + 0.75 * _distance(a, b), 2)


def make_charging_curve(mode: str) -> ChargingCurve:
    normalized_mode = mode.lower()
    if normalized_mode == "linear":
        return ChargingCurve(
            mode="linear",
            time_breakpoints=(0.0, 4.0, 8.0, 12.0, 16.0),
            energy_breakpoints=(0.0, 4.8, 9.6, 14.4, 19.2),
        )
    if normalized_mode == "nonlinear":
        return ChargingCurve(
            mode="nonlinear",
            time_breakpoints=(0.0, 2.5, 5.0, 7.5, 11.5, 18.5, 32.0),
            energy_breakpoints=(0.0, 5.2, 9.6, 12.4, 14.8, 16.8, 19.2),
        )
    raise ValueError(f"Unsupported charging mode: {mode}")


def _scale_charging_curve(
    curve: ChargingCurve,
    *,
    time_scale: float = 1.0,
) -> ChargingCurve:
    return ChargingCurve(
        mode=f"{curve.mode}_scaled_{time_scale:.2f}",
        time_breakpoints=tuple(round(time_scale * value, 4) for value in curve.time_breakpoints),
        energy_breakpoints=curve.energy_breakpoints,
    )


def _scenario_overrides(
    scenario: str,
) -> Tuple[Dict[int, Tuple[float, float]], Dict[str, float]]:
    normalized = scenario.lower()
    if normalized == "baseline":
        return {}, {}
    if normalized == "partial_recharge":
        return (
            {
                3: (43.0, 54.0),
                4: (55.0, 66.0),
            },
            {},
        )
    if normalized == "deep_recharge":
        return (
            {
                3: (50.0, 60.0),
                4: (61.0, 71.0),
                5: (72.0, 82.0),
                6: (83.0, 93.0),
            },
            {},
        )
    raise ValueError(f"Unsupported scenario: {scenario}")


def _apply_scenario(instance: EaftInstance, scenario: str) -> EaftInstance:
    time_windows, param_overrides = _scenario_overrides(scenario)
    if not time_windows and not param_overrides:
        return instance

    updated_requests: Dict[str, Request] = {}
    for idx, request_id in enumerate(instance.requests, start=1):
        request = instance.requests[request_id]
        if idx in time_windows:
            earliest, latest = time_windows[idx]
            updated_requests[request_id] = replace(
                request,
                earliest_pickup=earliest,
                latest_pickup=latest,
            )
        else:
            updated_requests[request_id] = request

    updated_params = replace(instance.params, **param_overrides) if param_overrides else instance.params
    return replace(instance, requests=updated_requests, params=updated_params)


def apply_charging_mode(instance: EaftInstance, mode: str) -> EaftInstance:
    base_curve = make_charging_curve(mode)
    updated_station_curves = {
        station: _scale_charging_curve(base_curve, time_scale=instance.station_time_scale[station])
        for station in instance.stations
    }
    return replace(
        instance,
        charging_curve=base_curve,
        station_curves=updated_station_curves,
    )


def _extract_fixed_binary_plan(
    buses: Sequence[str],
    trip_slots: Sequence[int],
    stations: Sequence[str],
    node_ids: Sequence[str],
    trip_active: gp.tupledict,
    charge_station: gp.tupledict,
    first: gp.tupledict,
    last: gp.tupledict,
    arc: gp.tupledict,
) -> FixedBinaryPlan:
    return FixedBinaryPlan(
        trip_active={
            (bus_id, trip): int(round(trip_active[bus_id, trip].X))
            for bus_id in buses
            for trip in trip_slots
        },
        charge_station={
            (bus_id, station, trip): int(round(charge_station[bus_id, station, trip].X))
            for bus_id in buses
            for station in stations
            for trip in trip_slots[:-1]
        },
        first={
            (bus_id, node_id, trip): int(round(first[bus_id, node_id, trip].X))
            for bus_id in buses
            for node_id in node_ids
            for trip in trip_slots
        },
        last={
            (bus_id, node_id, trip): int(round(last[bus_id, node_id, trip].X))
            for bus_id in buses
            for node_id in node_ids
            for trip in trip_slots
        },
        arc={
            (bus_id, origin_id, destination_id, trip): int(round(arc[bus_id, origin_id, destination_id, trip].X))
            for bus_id in buses
            for origin_id in node_ids
            for destination_id in node_ids
            for trip in trip_slots
        },
    )


def _apply_fixed_binary_plan(
    fixed_plan: FixedBinaryPlan,
    trip_active: gp.tupledict,
    charge_station: gp.tupledict,
    first: gp.tupledict,
    last: gp.tupledict,
    arc: gp.tupledict,
) -> None:
    for key, value in fixed_plan.trip_active.items():
        trip_active[key].LB = value
        trip_active[key].UB = value
    for key, value in fixed_plan.charge_station.items():
        charge_station[key].LB = value
        charge_station[key].UB = value
    for key, value in fixed_plan.first.items():
        first[key].LB = value
        first[key].UB = value
    for key, value in fixed_plan.last.items():
        last[key].LB = value
        last[key].UB = value
    for key, value in fixed_plan.arc.items():
        arc[key].LB = value
        arc[key].UB = value


def generate_toy_instance(
    num_requests: int = 4,
    num_buses: int = 2,
    num_trip_slots: int = 2,
    num_stations: int = 2,
    charging_mode: str = "linear",
    scenario: str = "baseline",
    seed: int = 7,
    *,
    passenger_mix: str = "varied",
) -> EaftInstance:
    rng = random.Random(seed)
    buses = tuple(f"bus_{bus_idx + 1}" for bus_idx in range(num_buses))
    trip_slots = tuple(range(1, num_trip_slots + 1))
    station_templates = (
        ("depot", (0.0, 0.0), 1.08, 0.48),
        ("hub", (6.0, 9.0), 0.82, 0.64),
        ("edge", (9.0, 2.5), 0.93, 0.56),
    )
    if num_stations < 1:
        raise ValueError("num_stations must be at least 1")
    if num_stations > len(station_templates):
        raise ValueError(f"At most {len(station_templates)} stations are supported in the toy generator")
    stations = tuple(template[0] for template in station_templates[:num_stations])
    initial_station = stations[0]
    station_coords = {name: coord for name, coord, _, _ in station_templates[:num_stations]}

    requests: Dict[str, Request] = {}
    nodes: Dict[str, NodeData] = {}
    pickup_node: Dict[str, str] = {}
    dropoff_node: Dict[str, str] = {}

    for request_idx in range(num_requests):
        request_id = f"r{request_idx + 1}"
        lane_offset = 5.0 * request_idx
        pickup = (
            4.0 + 0.8 * (request_idx % 2) + 0.2 * rng.uniform(-1.0, 1.0),
            lane_offset + 0.4 * rng.uniform(-1.0, 1.0),
        )
        dropoff = (
            pickup[0] + 2.5 + 0.4 * rng.uniform(-1.0, 1.0),
            pickup[1] + 2.2 + 0.3 * ((request_idx + 1) % 3),
        )
        earliest = 12.0 + 20.0 * request_idx
        latest = earliest + 55.0
        # C.11: varied passenger counts make bus_capacity=3 occasionally binding;
        # seeded through rng for reproducibility. Use passenger_mix="uniform" for old behaviour.
        bus_capacity_default = 3
        if passenger_mix == "varied":
            passengers = rng.randint(1, min(3, bus_capacity_default))
        else:
            passengers = 1 + int(request_idx % 4 == 3)
        revenue = round(52.0 + 7.0 * _distance(pickup, dropoff), 2)

        requests[request_id] = Request(
            request_id=request_id,
            pickup=pickup,
            dropoff=dropoff,
            earliest_pickup=earliest,
            latest_pickup=latest,
            passengers=passengers,
            revenue=revenue,
        )

        pickup_id = f"{request_id}_p"
        dropoff_id = f"{request_id}_d"
        pickup_node[request_id] = pickup_id
        dropoff_node[request_id] = dropoff_id
        nodes[pickup_id] = NodeData(
            node_id=pickup_id,
            request_id=request_id,
            kind="pickup",
            coord=pickup,
            load_delta=passengers,
        )
        nodes[dropoff_id] = NodeData(
            node_id=dropoff_id,
            request_id=request_id,
            kind="dropoff",
            coord=dropoff,
            load_delta=-passengers,
        )

    node_ids = tuple(nodes)
    travel_time: Dict[Tuple[str, str], float] = {}
    travel_energy: Dict[Tuple[str, str], float] = {}
    station_to_node_time: Dict[Tuple[str, str], float] = {}
    station_to_node_energy: Dict[Tuple[str, str], float] = {}
    node_to_station_time: Dict[Tuple[str, str], float] = {}
    node_to_station_energy: Dict[Tuple[str, str], float] = {}

    for station, coord, _, _ in station_templates[:num_stations]:
        for node_id, node in nodes.items():
            station_to_node_time[station, node_id] = _travel_minutes(coord, node.coord)
            station_to_node_energy[station, node_id] = _travel_energy(coord, node.coord)
            node_to_station_time[node_id, station] = _travel_minutes(node.coord, coord)
            node_to_station_energy[node_id, station] = _travel_energy(node.coord, coord)

    for i in node_ids:
        for j in node_ids:
            if i == j:
                travel_time[i, j] = 0.0
                travel_energy[i, j] = 0.0
            else:
                travel_time[i, j] = _travel_minutes(nodes[i].coord, nodes[j].coord)
                travel_energy[i, j] = _travel_energy(nodes[i].coord, nodes[j].coord)

    direct_service_time = {
        request_id: travel_time[pickup_node[request_id], dropoff_node[request_id]]
        for request_id in requests
    }

    charging_curve = make_charging_curve(charging_mode)
    station_curves = {
        station: _scale_charging_curve(charging_curve, time_scale=time_scale)
        for station, _, time_scale, _ in station_templates[:num_stations]
    }
    station_charge_cost = {
        station: unit_cost
        for station, _, _, unit_cost in station_templates[:num_stations]
    }
    station_time_scale = {
        station: time_scale
        for station, _, time_scale, _ in station_templates[:num_stations]
    }
    latest_pickup = max(request.latest_pickup for request in requests.values()) if requests else 0.0
    max_leg_time = max(
        [0.0]
        + list(station_to_node_time.values())
        + list(node_to_station_time.values())
        + list(travel_time.values())
    )
    max_station_charge_time = max(curve.max_time for curve in station_curves.values())
    time_horizon = latest_pickup + num_trip_slots * (max_station_charge_time + 3.0 * max_leg_time) + 30.0

    params = ModelParameters(
        bus_capacity=3,
        battery_capacity=36.0,
        battery_min=4.0,
        initial_battery=22.0,
        trip_fixed_cost=10.0,
        bus_fixed_cost=18.0,
        travel_cost=1.1,
        charge_cost=0.55,
        unserved_penalty=18.0,
    )

    instance = EaftInstance(
        buses=buses,
        trip_slots=trip_slots,
        stations=stations,
        initial_station=initial_station,
        requests=requests,
        nodes=nodes,
        pickup_node=pickup_node,
        dropoff_node=dropoff_node,
        travel_time=travel_time,
        travel_energy=travel_energy,
        station_to_node_time=station_to_node_time,
        station_to_node_energy=station_to_node_energy,
        node_to_station_time=node_to_station_time,
        node_to_station_energy=node_to_station_energy,
        direct_service_time=direct_service_time,
        charging_curve=charging_curve,
        station_curves=station_curves,
        station_charge_cost=station_charge_cost,
        station_time_scale=station_time_scale,
        params=params,
        station_coords=station_coords,
        time_horizon=time_horizon,
    )
    return _apply_scenario(instance, scenario)


def solve_instance(
    instance: EaftInstance,
    *,
    time_limit: float | None = 60.0,
    mip_gap: float | None = 0.01,
    verbose: bool = True,
    write_model_path: str | None = None,
    fixed_plan: FixedBinaryPlan | None = None,
) -> SolveResult:
    model = gp.Model("eaft_static_multitrip")
    if not verbose:
        model.Params.OutputFlag = 0
    if time_limit is not None:
        model.Params.TimeLimit = time_limit
    if mip_gap is not None:
        model.Params.MIPGap = mip_gap

    buses = instance.buses
    trip_slots = instance.trip_slots
    stations = instance.stations
    requests = tuple(instance.requests)
    node_ids = tuple(instance.nodes)
    final_trip = trip_slots[-1]
    charging_trips = trip_slots[:-1]
    next_trips = trip_slots[1:]

    max_leg_time = max(
        [0.0]
        + list(instance.station_to_node_time.values())
        + list(instance.node_to_station_time.values())
        + list(instance.travel_time.values())
    )
    max_charge_time = max(curve.max_time for curve in instance.station_curves.values())
    max_leg_energy = max(
        [0.0]
        + list(instance.station_to_node_energy.values())
        + list(instance.node_to_station_energy.values())
        + list(instance.travel_energy.values())
    )
    max_charge_energy = max(curve.max_energy for curve in instance.station_curves.values())
    max_passengers = max([0] + [request.passengers for request in instance.requests.values()])
    # A.2: constants sized to the tightest valid bound per constraint family.
    # Time bound covers chained ready+first-leg (or trip_end + return + charge).
    big_m_time = instance.time_horizon + max_charge_time + max_leg_time
    # Energy bound must cover battery difference + one travel/leg leg.
    big_m_energy = instance.params.battery_capacity + max_leg_energy
    # Load bound must cover capacity + a single request's passenger jump.
    big_m_load = instance.params.bus_capacity + max_passengers

    serve = model.addVars(requests, vtype=GRB.BINARY, name="serve")
    trip_active = model.addVars(buses, trip_slots, vtype=GRB.BINARY, name="trip_active")
    bus_used = model.addVars(buses, vtype=GRB.BINARY, name="bus_used")
    assign = model.addVars(buses, requests, trip_slots, vtype=GRB.BINARY, name="assign")
    # visit[b,n,t] removed (A.1): fully determined by assign via _node_assign_expr
    first = model.addVars(buses, node_ids, trip_slots, vtype=GRB.BINARY, name="first")
    last = model.addVars(buses, node_ids, trip_slots, vtype=GRB.BINARY, name="last")
    arc = model.addVars(buses, node_ids, node_ids, trip_slots, vtype=GRB.BINARY, name="arc")

    arrival = model.addVars(buses, node_ids, trip_slots, lb=0.0, name="arrival")
    load = model.addVars(
        buses,
        node_ids,
        trip_slots,
        lb=0.0,
        ub=instance.params.bus_capacity,
        name="load",
    )
    ready_time = model.addVars(buses, trip_slots, lb=0.0, ub=instance.time_horizon, name="ready_time")
    trip_end = model.addVars(buses, trip_slots, lb=0.0, ub=instance.time_horizon, name="trip_end")
    first_leg_time = model.addVars(buses, trip_slots, lb=0.0, ub=instance.time_horizon, name="first_leg_time")
    first_leg_energy = model.addVars(
        buses,
        trip_slots,
        lb=0.0,
        ub=instance.params.battery_capacity,
        name="first_leg_energy",
    )

    battery = model.addVars(
        buses,
        node_ids,
        trip_slots,
        lb=0.0,
        ub=instance.params.battery_capacity,
        name="battery",
    )
    ready_battery = model.addVars(
        buses,
        trip_slots,
        lb=0.0,
        ub=instance.params.battery_capacity,
        name="ready_battery",
    )
    battery_end = model.addVars(
        buses,
        trip_slots,
        lb=0.0,
        ub=instance.params.battery_capacity,
        name="battery_end",
    )
    return_time = model.addVars(buses, charging_trips, lb=0.0, ub=instance.time_horizon, name="return_time")
    return_energy = model.addVars(
        buses,
        charging_trips,
        lb=0.0,
        ub=instance.params.battery_capacity,
        name="return_energy",
    )
    station_arrival_battery = model.addVars(
        buses,
        charging_trips,
        lb=0.0,
        ub=instance.params.battery_capacity,
        name="station_arrival_battery",
    )
    charge_station = model.addVars(buses, stations, charging_trips, vtype=GRB.BINARY, name="charge_station")
    charge_time = model.addVars(
        buses,
        charging_trips,
        lb=0.0,
        ub=max_charge_time,
        name="charge_time",
    )
    charge_energy = model.addVars(
        buses,
        charging_trips,
        lb=0.0,
        ub=max_charge_energy,
        name="charge_energy",
    )
    charge_time_station = model.addVars(
        buses,
        stations,
        charging_trips,
        lb=0.0,
        ub=max_charge_time,
        name="charge_time_station",
    )
    charge_energy_station = model.addVars(
        buses,
        stations,
        charging_trips,
        lb=0.0,
        ub=max_charge_energy,
        name="charge_energy_station",
    )
    last_station_link = model.addVars(
        buses,
        node_ids,
        stations,
        charging_trips,
        vtype=GRB.BINARY,
        name="last_station_link",
    )
    start_station_link = model.addVars(
        buses,
        stations,
        node_ids,
        next_trips,
        vtype=GRB.BINARY,
        name="start_station_link",
    )

    # C.13: initial starting station is a decision variable (instance.initial_station
    # is kept for backward compatibility but the model no longer uses it directly).
    start_station_initial = model.addVars(buses, stations, vtype=GRB.BINARY, name="start_station_initial")
    start_station_link_initial = model.addVars(buses, stations, node_ids, vtype=GRB.BINARY, name="start_station_link_initial")

    # A.1: helper that returns the assign expression for a node (pickup or dropoff)
    def _node_assign_expr(node_id: str, bus_id: str, trip: int) -> gp.Var:
        node = instance.nodes[node_id]
        request_id = node.request_id
        return assign[bus_id, request_id, trip]

    if fixed_plan is not None:
        _apply_fixed_binary_plan(
            fixed_plan,
            trip_active,
            charge_station,
            first,
            last,
            arc,
        )

    for request_id in requests:
        model.addConstr(
            serve[request_id] == gp.quicksum(assign[bus_id, request_id, trip] for bus_id in buses for trip in trip_slots),
            name=f"serve_once[{request_id}]",
        )

    for bus_id in buses:
        model.addConstr(
            bus_used[bus_id] <= gp.quicksum(trip_active[bus_id, trip] for trip in trip_slots),
            name=f"bus_used_upper[{bus_id}]",
        )
        for trip in trip_slots:
            model.addConstr(bus_used[bus_id] >= trip_active[bus_id, trip], name=f"bus_used_lower[{bus_id},{trip}]")
            served_in_trip = gp.quicksum(assign[bus_id, request_id, trip] for request_id in requests)
            model.addConstr(served_in_trip >= trip_active[bus_id, trip], name=f"trip_nonempty[{bus_id},{trip}]")
            model.addConstr(
                served_in_trip <= len(requests) * trip_active[bus_id, trip],
                name=f"trip_active_upper[{bus_id},{trip}]",
            )

            for request_id in requests:
                model.addConstr(
                    assign[bus_id, request_id, trip] <= trip_active[bus_id, trip],
                    name=f"assign_active[{bus_id},{request_id},{trip}]",
                )

                pickup_id = instance.pickup_node[request_id]
                dropoff_id = instance.dropoff_node[request_id]
                # A.1: visit_pickup / visit_dropoff removed (tautologies after removing visit)

                request = instance.requests[request_id]
                # A.2: tighter big-M per constraint
                model.addConstr(
                    arrival[bus_id, pickup_id, trip]
                    >= request.earliest_pickup - request.latest_pickup * (1 - assign[bus_id, request_id, trip]),
                    name=f"pickup_earliest[{bus_id},{request_id},{trip}]",
                )
                model.addConstr(
                    arrival[bus_id, pickup_id, trip]
                    <= request.latest_pickup + instance.time_horizon * (1 - assign[bus_id, request_id, trip]),
                    name=f"pickup_latest[{bus_id},{request_id},{trip}]",
                )
                model.addConstr(
                    arrival[bus_id, dropoff_id, trip]
                    >= arrival[bus_id, pickup_id, trip]
                    + instance.direct_service_time[request_id]
                    - instance.time_horizon * (1 - assign[bus_id, request_id, trip]),
                    name=f"pickup_before_dropoff[{bus_id},{request_id},{trip}]",
                )

            model.addConstr(
                gp.quicksum(first[bus_id, node_id, trip] for node_id in node_ids) == trip_active[bus_id, trip],
                name=f"one_first[{bus_id},{trip}]",
            )
            model.addConstr(
                gp.quicksum(last[bus_id, node_id, trip] for node_id in node_ids) == trip_active[bus_id, trip],
                name=f"one_last[{bus_id},{trip}]",
            )

            model.addConstr(
                trip_end[bus_id, trip] <= instance.time_horizon * trip_active[bus_id, trip],
                name=f"trip_end_zero[{bus_id},{trip}]",
            )
            model.addConstr(
                first_leg_time[bus_id, trip] <= instance.time_horizon * trip_active[bus_id, trip],
                name=f"first_leg_time_zero[{bus_id},{trip}]",
            )
            model.addConstr(
                first_leg_energy[bus_id, trip] <= instance.params.battery_capacity * trip_active[bus_id, trip],
                name=f"first_leg_energy_zero[{bus_id},{trip}]",
            )
            model.addConstr(
                battery_end[bus_id, trip] >= instance.params.battery_min * trip_active[bus_id, trip],
                name=f"battery_end_min[{bus_id},{trip}]",
            )
            model.addConstr(
                ready_battery[bus_id, trip] >= instance.params.battery_min * trip_active[bus_id, trip],
                name=f"ready_battery_min[{bus_id},{trip}]",
            )

            for node_id in node_ids:
                node = instance.nodes[node_id]
                # A.1: replace visit[b,n,t] with _node_assign_expr(n, b, t)
                visit_expr = _node_assign_expr(node_id, bus_id, trip)

                # A.2: tightest constants per constraint
                model.addConstr(
                    arrival[bus_id, node_id, trip] <= instance.time_horizon * visit_expr,
                    name=f"arrival_zero[{bus_id},{node_id},{trip}]",
                )
                model.addConstr(
                    load[bus_id, node_id, trip] <= instance.params.bus_capacity * visit_expr,
                    name=f"load_zero[{bus_id},{node_id},{trip}]",
                )
                model.addConstr(
                    battery[bus_id, node_id, trip] >= instance.params.battery_min * visit_expr,
                    name=f"battery_min[{bus_id},{node_id},{trip}]",
                )
                model.addConstr(
                    battery[bus_id, node_id, trip] <= instance.params.battery_capacity * visit_expr,
                    name=f"battery_max[{bus_id},{node_id},{trip}]",
                )

                model.addConstr(
                    gp.quicksum(arc[bus_id, node_id, next_node, trip] for next_node in node_ids)
                    + last[bus_id, node_id, trip]
                    == visit_expr,
                    name=f"out_degree[{bus_id},{node_id},{trip}]",
                )
                model.addConstr(
                    gp.quicksum(arc[bus_id, prev_node, node_id, trip] for prev_node in node_ids)
                    + first[bus_id, node_id, trip]
                    == visit_expr,
                    name=f"in_degree[{bus_id},{node_id},{trip}]",
                )
                model.addConstr(arc[bus_id, node_id, node_id, trip] == 0, name=f"no_self_loop[{bus_id},{node_id},{trip}]")

                # A.2: first_node_time_lb must cover ready_time + first_leg_time.
                model.addConstr(
                    arrival[bus_id, node_id, trip]
                    >= ready_time[bus_id, trip]
                    + first_leg_time[bus_id, trip]
                    - big_m_time * (1 - first[bus_id, node_id, trip]),
                    name=f"first_node_time_lb[{bus_id},{node_id},{trip}]",
                )

                # A.2: first_node load big-M = capacity + max_passengers (covers worst gap).
                model.addConstr(
                    load[bus_id, node_id, trip] >= node.load_delta - big_m_load * (1 - first[bus_id, node_id, trip]),
                    name=f"first_node_load_lb[{bus_id},{node_id},{trip}]",
                )
                model.addConstr(
                    load[bus_id, node_id, trip] <= node.load_delta + big_m_load * (1 - first[bus_id, node_id, trip]),
                    name=f"first_node_load_ub[{bus_id},{node_id},{trip}]",
                )

                # A.2: first_node_battery bound must cover battery_capacity + one leg energy.
                model.addConstr(
                    battery[bus_id, node_id, trip]
                    >= ready_battery[bus_id, trip]
                    - first_leg_energy[bus_id, trip]
                    - big_m_energy * (1 - first[bus_id, node_id, trip]),
                    name=f"first_node_battery_lb[{bus_id},{node_id},{trip}]",
                )
                model.addConstr(
                    battery[bus_id, node_id, trip]
                    <= ready_battery[bus_id, trip]
                    - first_leg_energy[bus_id, trip]
                    + big_m_energy * (1 - first[bus_id, node_id, trip]),
                    name=f"first_node_battery_ub[{bus_id},{node_id},{trip}]",
                )

                # A.2: trip_end / battery_end big-M match their variable ranges.
                model.addConstr(
                    trip_end[bus_id, trip]
                    >= arrival[bus_id, node_id, trip] - big_m_time * (1 - last[bus_id, node_id, trip]),
                    name=f"trip_end_lb[{bus_id},{node_id},{trip}]",
                )
                model.addConstr(
                    trip_end[bus_id, trip]
                    <= arrival[bus_id, node_id, trip] + big_m_time * (1 - last[bus_id, node_id, trip]),
                    name=f"trip_end_ub[{bus_id},{node_id},{trip}]",
                )
                model.addConstr(
                    battery_end[bus_id, trip]
                    >= battery[bus_id, node_id, trip] - big_m_energy * (1 - last[bus_id, node_id, trip]),
                    name=f"battery_end_lb[{bus_id},{node_id},{trip}]",
                )
                model.addConstr(
                    battery_end[bus_id, trip]
                    <= battery[bus_id, node_id, trip] + big_m_energy * (1 - last[bus_id, node_id, trip]),
                    name=f"battery_end_ub[{bus_id},{node_id},{trip}]",
                )

            for origin_id in node_ids:
                for destination_id in node_ids:
                    if origin_id == destination_id:
                        continue
                    # A.2: arc flow big-Ms: time covers horizon + one leg; load/battery cover capacity + worst jump.
                    model.addConstr(
                        arrival[bus_id, destination_id, trip]
                        >= arrival[bus_id, origin_id, trip]
                        + instance.travel_time[origin_id, destination_id]
                        - big_m_time * (1 - arc[bus_id, origin_id, destination_id, trip]),
                        name=f"time_flow[{bus_id},{origin_id},{destination_id},{trip}]",
                    )
                    model.addConstr(
                        load[bus_id, destination_id, trip]
                        >= load[bus_id, origin_id, trip]
                        + instance.nodes[destination_id].load_delta
                        - big_m_load * (1 - arc[bus_id, origin_id, destination_id, trip]),
                        name=f"load_flow_lb[{bus_id},{origin_id},{destination_id},{trip}]",
                    )
                    model.addConstr(
                        load[bus_id, destination_id, trip]
                        <= load[bus_id, origin_id, trip]
                        + instance.nodes[destination_id].load_delta
                        + big_m_load * (1 - arc[bus_id, origin_id, destination_id, trip]),
                        name=f"load_flow_ub[{bus_id},{origin_id},{destination_id},{trip}]",
                    )
                    model.addConstr(
                        battery[bus_id, destination_id, trip]
                        >= battery[bus_id, origin_id, trip]
                        - instance.travel_energy[origin_id, destination_id]
                        - big_m_energy * (1 - arc[bus_id, origin_id, destination_id, trip]),
                        name=f"battery_flow_lb[{bus_id},{origin_id},{destination_id},{trip}]",
                    )
                    model.addConstr(
                        battery[bus_id, destination_id, trip]
                        <= battery[bus_id, origin_id, trip]
                        - instance.travel_energy[origin_id, destination_id]
                        + big_m_energy * (1 - arc[bus_id, origin_id, destination_id, trip]),
                        name=f"battery_flow_ub[{bus_id},{origin_id},{destination_id},{trip}]",
                    )

        model.addConstr(ready_time[bus_id, trip_slots[0]] == 0.0, name=f"ready_time_initial[{bus_id}]")
        model.addConstr(
            ready_battery[bus_id, trip_slots[0]] == instance.params.initial_battery,
            name=f"ready_battery_initial[{bus_id}]",
        )

        # C.13: initial starting station is a decision variable;
        # instance.initial_station is retained for backward compatibility but not used here.
        model.addConstr(
            gp.quicksum(start_station_initial[bus_id, station] for station in stations)
            == trip_active[bus_id, trip_slots[0]],
            name=f"choose_initial_station[{bus_id}]",
        )
        for station in stations:
            for node_id in node_ids:
                model.addConstr(
                    start_station_link_initial[bus_id, station, node_id]
                    <= first[bus_id, node_id, trip_slots[0]],
                    name=f"ssi_lb1[{bus_id},{station},{node_id}]",
                )
                model.addConstr(
                    start_station_link_initial[bus_id, station, node_id]
                    <= start_station_initial[bus_id, station],
                    name=f"ssi_lb2[{bus_id},{station},{node_id}]",
                )
                model.addConstr(
                    start_station_link_initial[bus_id, station, node_id]
                    >= first[bus_id, node_id, trip_slots[0]] + start_station_initial[bus_id, station] - 1,
                    name=f"ssi_lb3[{bus_id},{station},{node_id}]",
                )
            # Aggregate: sum_n link == start_station_initial[b,s]
            model.addConstr(
                gp.quicksum(start_station_link_initial[bus_id, station, node_id] for node_id in node_ids)
                == start_station_initial[bus_id, station],
                name=f"ssi_agg_station[{bus_id},{station}]",
            )
        for node_id in node_ids:
            # Aggregate: sum_s link == first[b,n,trip_slots[0]]
            model.addConstr(
                gp.quicksum(start_station_link_initial[bus_id, station, node_id] for station in stations)
                == first[bus_id, node_id, trip_slots[0]],
                name=f"ssi_agg_node[{bus_id},{node_id}]",
            )
        model.addConstr(
            first_leg_time[bus_id, trip_slots[0]]
            == gp.quicksum(
                instance.station_to_node_time[station, node_id] * start_station_link_initial[bus_id, station, node_id]
                for station in stations
                for node_id in node_ids
            ),
            name=f"first_leg_time_initial[{bus_id}]",
        )
        model.addConstr(
            first_leg_energy[bus_id, trip_slots[0]]
            == gp.quicksum(
                instance.station_to_node_energy[station, node_id] * start_station_link_initial[bus_id, station, node_id]
                for station in stations
                for node_id in node_ids
            ),
            name=f"first_leg_energy_initial[{bus_id}]",
        )

        for previous_trip, next_trip in zip(trip_slots[:-1], trip_slots[1:]):
            model.addConstr(
                trip_active[bus_id, next_trip] <= trip_active[bus_id, previous_trip],
                name=f"trip_order[{bus_id},{previous_trip}]",
            )
            model.addConstr(
                gp.quicksum(charge_station[bus_id, station, previous_trip] for station in stations) == trip_active[bus_id, next_trip],
                name=f"choose_station[{bus_id},{previous_trip}]",
            )
            model.addConstr(
                charge_time[bus_id, previous_trip]
                == gp.quicksum(charge_time_station[bus_id, station, previous_trip] for station in stations),
                name=f"charge_time_sum[{bus_id},{previous_trip}]",
            )
            model.addConstr(
                charge_energy[bus_id, previous_trip]
                == gp.quicksum(charge_energy_station[bus_id, station, previous_trip] for station in stations),
                name=f"charge_energy_sum[{bus_id},{previous_trip}]",
            )

            for station in stations:
                station_curve = instance.station_curves[station]
                model.addConstr(
                    charge_time_station[bus_id, station, previous_trip]
                    <= station_curve.max_time * charge_station[bus_id, station, previous_trip],
                    name=f"charge_time_active[{bus_id},{station},{previous_trip}]",
                )
                model.addConstr(
                    charge_energy_station[bus_id, station, previous_trip]
                    <= station_curve.max_energy * charge_station[bus_id, station, previous_trip],
                    name=f"charge_energy_active[{bus_id},{station},{previous_trip}]",
                )
                model.addGenConstrPWL(
                    charge_time_station[bus_id, station, previous_trip],
                    charge_energy_station[bus_id, station, previous_trip],
                    list(station_curve.time_breakpoints),
                    list(station_curve.energy_breakpoints),
                    name=f"charge_curve[{bus_id},{station},{previous_trip}]",
                )
                # A.4: direct propagation when station not chosen
                model.addGenConstrIndicator(
                    charge_station[bus_id, station, previous_trip],
                    False,
                    charge_time_station[bus_id, station, previous_trip] == 0,
                    name=f"charge_time_zero_ind[{bus_id},{station},{previous_trip}]",
                )
                model.addGenConstrIndicator(
                    charge_station[bus_id, station, previous_trip],
                    False,
                    charge_energy_station[bus_id, station, previous_trip] == 0,
                    name=f"charge_energy_zero_ind[{bus_id},{station},{previous_trip}]",
                )

                for node_id in node_ids:
                    model.addConstr(
                        last_station_link[bus_id, node_id, station, previous_trip] <= last[bus_id, node_id, previous_trip],
                        name=f"last_station_lb1[{bus_id},{node_id},{station},{previous_trip}]",
                    )
                    model.addConstr(
                        last_station_link[bus_id, node_id, station, previous_trip]
                        <= charge_station[bus_id, station, previous_trip],
                        name=f"last_station_lb2[{bus_id},{node_id},{station},{previous_trip}]",
                    )
                    model.addConstr(
                        last_station_link[bus_id, node_id, station, previous_trip]
                        >= last[bus_id, node_id, previous_trip] + charge_station[bus_id, station, previous_trip] - 1,
                        name=f"last_station_lb3[{bus_id},{node_id},{station},{previous_trip}]",
                    )
                    model.addConstr(
                        start_station_link[bus_id, station, node_id, next_trip] <= first[bus_id, node_id, next_trip],
                        name=f"start_station_lb1[{bus_id},{station},{node_id},{next_trip}]",
                    )
                    model.addConstr(
                        start_station_link[bus_id, station, node_id, next_trip]
                        <= charge_station[bus_id, station, previous_trip],
                        name=f"start_station_lb2[{bus_id},{station},{node_id},{next_trip}]",
                    )
                    model.addConstr(
                        start_station_link[bus_id, station, node_id, next_trip]
                        >= first[bus_id, node_id, next_trip] + charge_station[bus_id, station, previous_trip] - 1,
                        name=f"start_station_lb3[{bus_id},{station},{node_id},{next_trip}]",
                    )

                # A.3: aggregate equalities for last_station_link and start_station_link
                model.addConstr(
                    gp.quicksum(last_station_link[bus_id, node_id, station, previous_trip] for node_id in node_ids)
                    == charge_station[bus_id, station, previous_trip],
                    name=f"last_link_agg_station[{bus_id},{station},{previous_trip}]",
                )
                model.addConstr(
                    gp.quicksum(start_station_link[bus_id, station, node_id, next_trip] for node_id in node_ids)
                    == charge_station[bus_id, station, previous_trip],
                    name=f"start_link_agg_station[{bus_id},{station},{previous_trip}]",
                )

            # A.3: aggregate equalities per node
            for node_id in node_ids:
                model.addConstr(
                    gp.quicksum(last_station_link[bus_id, node_id, station, previous_trip] for station in stations)
                    == last[bus_id, node_id, previous_trip],
                    name=f"last_link_agg_node[{bus_id},{node_id},{previous_trip}]",
                )
                model.addConstr(
                    gp.quicksum(start_station_link[bus_id, station, node_id, next_trip] for station in stations)
                    == first[bus_id, node_id, next_trip],
                    name=f"start_link_agg_node[{bus_id},{node_id},{previous_trip}]",
                )

            model.addConstr(
                return_time[bus_id, previous_trip]
                == gp.quicksum(
                    instance.node_to_station_time[node_id, station] * last_station_link[bus_id, node_id, station, previous_trip]
                    for node_id in node_ids
                    for station in stations
                ),
                name=f"return_time_def[{bus_id},{previous_trip}]",
            )
            model.addConstr(
                return_energy[bus_id, previous_trip]
                == gp.quicksum(
                    instance.node_to_station_energy[node_id, station] * last_station_link[bus_id, node_id, station, previous_trip]
                    for node_id in node_ids
                    for station in stations
                ),
                name=f"return_energy_def[{bus_id},{previous_trip}]",
            )
            model.addConstr(
                first_leg_time[bus_id, next_trip]
                == gp.quicksum(
                    instance.station_to_node_time[station, node_id] * start_station_link[bus_id, station, node_id, next_trip]
                    for station in stations
                    for node_id in node_ids
                ),
                name=f"first_leg_time_def[{bus_id},{next_trip}]",
            )
            model.addConstr(
                first_leg_energy[bus_id, next_trip]
                == gp.quicksum(
                    instance.station_to_node_energy[station, node_id] * start_station_link[bus_id, station, node_id, next_trip]
                    for station in stations
                    for node_id in node_ids
                ),
                name=f"first_leg_energy_def[{bus_id},{next_trip}]",
            )
            model.addConstr(
                station_arrival_battery[bus_id, previous_trip]
                == battery_end[bus_id, previous_trip] - return_energy[bus_id, previous_trip],
                name=f"station_arrival_battery[{bus_id},{previous_trip}]",
            )
            model.addConstr(
                station_arrival_battery[bus_id, previous_trip] >= instance.params.battery_min * trip_active[bus_id, next_trip],
                name=f"station_arrival_min[{bus_id},{previous_trip}]",
            )
            # A.2: big_m_time = time_horizon + max_charge + max_leg covers trip_end + return + charge sum.
            model.addConstr(
                ready_time[bus_id, next_trip]
                >= trip_end[bus_id, previous_trip]
                + return_time[bus_id, previous_trip]
                + charge_time[bus_id, previous_trip]
                - big_m_time * (1 - trip_active[bus_id, next_trip]),
                name=f"ready_time_transition[{bus_id},{previous_trip}]",
            )
            model.addConstr(
                ready_battery[bus_id, next_trip]
                == station_arrival_battery[bus_id, previous_trip]
                + charge_energy[bus_id, previous_trip],
                name=f"ready_battery_transition[{bus_id},{previous_trip}]",
            )

    # A.5: symmetry breaking for identical (homogeneous) buses
    if len(buses) >= 2:
        for bus_i, bus_j in zip(buses[:-1], buses[1:]):
            model.addConstr(
                bus_used[bus_i] >= bus_used[bus_j],
                name=f"symmetry_bus_used[{bus_i},{bus_j}]",
            )
            model.addConstr(
                trip_active[bus_i, trip_slots[0]] >= trip_active[bus_j, trip_slots[0]],
                name=f"symmetry_trip_active_first[{bus_i},{bus_j}]",
            )

    revenue_expr = gp.quicksum(
        instance.requests[request_id].revenue * serve[request_id] - instance.params.unserved_penalty * (1 - serve[request_id])
        for request_id in requests
    )
    bus_cost_expr = gp.quicksum(instance.params.bus_fixed_cost * bus_used[bus_id] for bus_id in buses)
    trip_cost_expr = gp.quicksum(
        instance.params.trip_fixed_cost * trip_active[bus_id, trip] for bus_id in buses for trip in trip_slots
    )
    travel_cost_expr = instance.params.travel_cost * (
        gp.quicksum(
            instance.travel_time[origin_id, destination_id] * arc[bus_id, origin_id, destination_id, trip]
            for bus_id in buses
            for trip in trip_slots
            for origin_id in node_ids
            for destination_id in node_ids
        )
        + gp.quicksum(first_leg_time[bus_id, trip] for bus_id in buses for trip in trip_slots)
        + gp.quicksum(return_time[bus_id, trip] for bus_id in buses for trip in charging_trips)
    )
    charge_cost_expr = gp.quicksum(
        instance.station_charge_cost[station] * charge_energy_station[bus_id, station, trip]
        for bus_id in buses
        for station in stations
        for trip in charging_trips
    )
    primary_profit_expr = revenue_expr - bus_cost_expr - trip_cost_expr - travel_cost_expr - charge_cost_expr
    time_tiebreak_expr = gp.quicksum(trip_end[bus_id, trip] + ready_time[bus_id, trip] for bus_id in buses for trip in trip_slots)

    # A.6: hierarchical multi-objective. All sub-objectives share ModelSense.
    # Set sense to MAXIMIZE so the primary (profit) is maximized; negate the
    # time tie-breaker so "smaller total time" is preferred among profit-ties.
    model.ModelSense = GRB.MAXIMIZE
    model.setObjectiveN(primary_profit_expr, index=0, priority=2, weight=1.0, name="profit")
    model.setObjectiveN(-time_tiebreak_expr, index=1, priority=1, weight=1.0, name="time_tiebreak")

    if write_model_path:
        model.write(write_model_path)

    model.optimize()

    status_name = _status_name(model.Status)
    objective_value = primary_profit_expr.getValue() if model.SolCount > 0 else None
    # A.6: multi-objective mode does not expose MIPGap; guard with try/except
    try:
        mip_gap_value = model.MIPGap if model.SolCount > 0 and model.IsMIP else None
    except AttributeError:
        mip_gap_value = None

    if model.SolCount == 0:
        return SolveResult(
            status=status_name,
            objective_value=objective_value,
            runtime_seconds=model.Runtime,
            mip_gap=mip_gap_value,
            served_requests=[],
            unserved_requests=list(requests),
            trips=[],
            fixed_plan=fixed_plan,
        )

    served_requests = [request_id for request_id in requests if serve[request_id].X > 0.5]
    unserved_requests = [request_id for request_id in requests if serve[request_id].X <= 0.5]
    trip_reports: List[TripReport] = []
    extracted_plan = _extract_fixed_binary_plan(
        buses,
        trip_slots,
        stations,
        node_ids,
        trip_active,
        charge_station,
        first,
        last,
        arc,
    )

    # C.13: record which initial station each bus chose
    initial_station_chosen: Dict[str, str | None] = {}
    for bus_id in buses:
        if trip_active[bus_id, trip_slots[0]].X > 0.5:
            chosen_init = [s for s in stations if start_station_initial[bus_id, s].X > 0.5]
            initial_station_chosen[bus_id] = chosen_init[0] if chosen_init else None
        else:
            initial_station_chosen[bus_id] = None

    for bus_id in buses:
        for trip in trip_slots:
            if trip_active[bus_id, trip].X <= 0.5:
                continue
            route = _extract_route(node_ids, first, arc, last, bus_id, trip)
            served_in_trip = [request_id for request_id in requests if assign[bus_id, request_id, trip].X > 0.5]
            chosen_station = None
            if trip != final_trip:
                selected = [station for station in stations if charge_station[bus_id, station, trip].X > 0.5]
                chosen_station = selected[0] if selected else None
            charge_minutes = charge_time[bus_id, trip].X if trip != final_trip else 0.0
            charge_kwh = charge_energy[bus_id, trip].X if trip != final_trip else 0.0
            trip_reports.append(
                TripReport(
                    bus_id=bus_id,
                    trip_index=trip,
                    served_requests=served_in_trip,
                    route=route,
                    ready_time=ready_time[bus_id, trip].X,
                    trip_end_time=trip_end[bus_id, trip].X,
                    ready_battery=ready_battery[bus_id, trip].X,
                    battery_end=battery_end[bus_id, trip].X,
                    charge_station_after_trip=chosen_station,
                    charge_time_after_trip=charge_minutes,
                    charge_energy_after_trip=charge_kwh,
                )
            )

    return SolveResult(
        status=status_name,
        objective_value=objective_value,
        runtime_seconds=model.Runtime,
        mip_gap=mip_gap_value,
        served_requests=served_requests,
        unserved_requests=unserved_requests,
        trips=trip_reports,
        fixed_plan=extracted_plan,
        initial_station_chosen=initial_station_chosen,
    )


def cross_evaluate_modes(
    instance: EaftInstance,
    *,
    source_mode: str = "linear",
    target_mode: str = "nonlinear",
    time_limit: float | None = 60.0,
    mip_gap: float | None = 0.01,
    verbose: bool = True,
) -> CrossEvaluationResult:
    source_instance = apply_charging_mode(instance, source_mode)
    optimized_source = solve_instance(
        source_instance,
        time_limit=time_limit,
        mip_gap=mip_gap,
        verbose=verbose,
    )

    target_instance = apply_charging_mode(instance, target_mode)
    fixed_plan_in_target = solve_instance(
        target_instance,
        time_limit=time_limit,
        mip_gap=mip_gap,
        verbose=verbose,
        fixed_plan=optimized_source.fixed_plan,
    )
    optimized_target = solve_instance(
        target_instance,
        time_limit=time_limit,
        mip_gap=mip_gap,
        verbose=verbose,
    )

    return CrossEvaluationResult(
        source_mode=source_mode,
        target_mode=target_mode,
        optimized_source=optimized_source,
        fixed_plan_in_target=fixed_plan_in_target,
        optimized_target=optimized_target,
    )


def format_solution(result: SolveResult) -> str:
    lines = [
        f"Solver status: {result.status}",
        f"Objective value: {result.objective_value:.2f}" if result.objective_value is not None else "Objective value: unavailable",
        f"Runtime (s): {result.runtime_seconds:.2f}",
        f"MIP gap: {result.mip_gap:.4f}" if result.mip_gap is not None else "MIP gap: unavailable",
        f"Served requests: {', '.join(result.served_requests) if result.served_requests else 'none'}",
        f"Unserved requests: {', '.join(result.unserved_requests) if result.unserved_requests else 'none'}",
    ]
    # C.13: print chosen initial station per bus if available
    if result.initial_station_chosen:
        for bus_id, station in result.initial_station_chosen.items():
            lines.append(f"  initial station {bus_id}: {station or 'none (idle)'}")

    for trip in result.trips:
        route_text = " -> ".join(trip.route) if trip.route else "empty"
        lines.extend(
            [
                "",
                f"{trip.bus_id} / trip {trip.trip_index}",
                f"  requests: {', '.join(trip.served_requests) if trip.served_requests else 'none'}",
                f"  route: {route_text}",
                f"  ready time: {trip.ready_time:.2f}",
                f"  trip end: {trip.trip_end_time:.2f}",
                f"  battery at trip start: {trip.ready_battery:.2f}",
                f"  battery at trip end: {trip.battery_end:.2f}",
                f"  charging station after trip: {trip.charge_station_after_trip or 'none'}",
                f"  charge after trip (time): {trip.charge_time_after_trip:.2f}",
                f"  charge after trip (energy): {trip.charge_energy_after_trip:.2f}",
            ]
        )
    return "\n".join(lines)


def _extract_route(
    node_ids: Sequence[str],
    first: gp.tupledict,
    arc: gp.tupledict,
    last: gp.tupledict,
    bus_id: str,
    trip: int,
) -> List[str]:
    first_nodes = [node_id for node_id in node_ids if first[bus_id, node_id, trip].X > 0.5]
    if not first_nodes:
        return []

    route = [first_nodes[0]]
    current = first_nodes[0]
    visited = {current}

    while last[bus_id, current, trip].X <= 0.5:
        next_nodes = [candidate for candidate in node_ids if arc[bus_id, current, candidate, trip].X > 0.5]
        if not next_nodes:
            break
        current = next_nodes[0]
        if current in visited:
            route.append(current)
            break
        route.append(current)
        visited.add(current)

    # D.15: warn if we exited the loop before reaching a last node (subtour or broken arc chain)
    if last[bus_id, current, trip].X <= 0.5:
        warnings.warn(
            f"_extract_route: bus={bus_id} trip={trip} — loop exited without reaching a 'last' node. "
            "This may indicate a subtour or broken arc chain in the solution.",
            stacklevel=2,
        )
    return route


def _status_name(status_code: int) -> str:
    if status_code == GRB.OPTIMAL:
        return "OPTIMAL"
    if status_code == GRB.TIME_LIMIT:
        return "TIME_LIMIT"
    if status_code == GRB.INFEASIBLE:
        return "INFEASIBLE"
    if status_code == GRB.INF_OR_UNBD:
        return "INF_OR_UNBD"
    if status_code == GRB.UNBOUNDED:
        return "UNBOUNDED"
    return f"STATUS_{status_code}"
