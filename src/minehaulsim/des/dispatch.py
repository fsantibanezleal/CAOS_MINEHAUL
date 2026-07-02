"""Dispatch policies: who goes where next. The policy sees a READ-ONLY MineView and returns node
ids; a stochastic policy draws ONLY from its own named RNG stream (never perturbing the physics).

Baselines mirror the DispatchLab/OpenMines set so cross-tool comparisons are possible:
    FixedPolicy         static truck->loader assignment (round-robin at build; the do-nothing floor)
    NearestPolicy       min free-flow ETA to the loader
    MinQueuePolicy      min (queue + inbound) then ETA
    MinSaturationPolicy min expected wait including in-service remaining + travel (SPTF-like)
    RandomPolicy        uniform over serviceable loaders (seeded stream "policy")
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class LoaderView:
    node_id: int
    queue_len: int
    inbound: int
    in_service: bool
    est_free_s: float          # seconds until the current service completes (0 if idle)
    load_mean_s: float
    diggable: bool             # False when the plan forbids serving this face NOW


@dataclass(frozen=True)
class TruckView:
    truck_id: int
    unit_name: str
    home_loader: int


@dataclass(frozen=True)
class MineView:
    now: float
    loaders: tuple[LoaderView, ...]
    dumps: tuple[int, ...]
    eta_s: dict[tuple[int, int], float]      # (from-ish key, loader node) -> free-flow ETA; see sim.py


class DispatchPolicy(Protocol):
    name: str

    def next_loader(self, truck: TruckView, mine: MineView) -> int: ...

    def next_dump(self, truck: TruckView, mine: MineView) -> int: ...


def _serviceable(mine: MineView) -> list[LoaderView]:
    out = [lv for lv in mine.loaders if lv.diggable]
    if not out:
        raise RuntimeError("no diggable loader available (plan exhausted or all faces blocked)")
    return out


def _eta(mine: MineView, truck: TruckView, loader: int) -> float:
    return mine.eta_s.get((truck.truck_id, loader), float("inf"))


def _backlog_wait(lv: LoaderView) -> float:
    return lv.est_free_s + (lv.queue_len + lv.inbound) * lv.load_mean_s


class FixedPolicy:
    name = "fixed"

    def next_loader(self, truck: TruckView, mine: MineView) -> int:
        for lv in _serviceable(mine):
            if lv.node_id == truck.home_loader:
                return lv.node_id
        return min(_serviceable(mine), key=lambda lv: lv.node_id).node_id

    def next_dump(self, truck: TruckView, mine: MineView) -> int:
        return mine.dumps[0]


class NearestPolicy:
    name = "nearest"

    def next_loader(self, truck: TruckView, mine: MineView) -> int:
        return min(_serviceable(mine), key=lambda lv: (_eta(mine, truck, lv.node_id), lv.node_id)).node_id

    def next_dump(self, truck: TruckView, mine: MineView) -> int:
        return mine.dumps[0]


class MinQueuePolicy:
    name = "minqueue"

    def next_loader(self, truck: TruckView, mine: MineView) -> int:
        return min(_serviceable(mine),
                   key=lambda lv: (lv.queue_len + lv.inbound, _eta(mine, truck, lv.node_id), lv.node_id)).node_id

    def next_dump(self, truck: TruckView, mine: MineView) -> int:
        return mine.dumps[0]


class MinSaturationPolicy:
    name = "minsat"

    def next_loader(self, truck: TruckView, mine: MineView) -> int:
        def cost(lv: LoaderView) -> float:
            t = _eta(mine, truck, lv.node_id)
            return t + max(0.0, _backlog_wait(lv) - t)
        return min(_serviceable(mine), key=lambda lv: (cost(lv), lv.node_id)).node_id

    def next_dump(self, truck: TruckView, mine: MineView) -> int:
        return mine.dumps[0]


class RandomPolicy:
    name = "random"

    def __init__(self, rng: np.random.Generator) -> None:
        self._rng = rng

    def next_loader(self, truck: TruckView, mine: MineView) -> int:
        opts = sorted(lv.node_id for lv in _serviceable(mine))
        return int(opts[int(self._rng.integers(0, len(opts)))])

    def next_dump(self, truck: TruckView, mine: MineView) -> int:
        return mine.dumps[0]


BASELINES = ("fixed", "nearest", "minqueue", "minsat", "random")
