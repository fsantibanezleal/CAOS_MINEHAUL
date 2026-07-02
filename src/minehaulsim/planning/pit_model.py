"""The immutable pit model: benches tiled by dig blocks, partitioned into phases (pushbacks).

Domain grounding (docs/frameworks + the planning research): real plans mine a pushback bench-by-bench
top-down; each bench face is decomposed into ordered DIG BLOCKS (mining cuts) with tonnes + material;
a pushback's lateral expansion is expressed as its OWN benches (so reserve sums partition exactly).
`grade` here is METAL grade (%), never road grade_pct (types.py owns the road convention).

Construction is validated rule-by-rule (every rule named + unit-tested); a model that constructs is
internally consistent by definition. Network existence of anchors is deliberately a SEPARATE check
(`bind_check(net)`) so models can be built and serialized before a network exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np

from ..network.graph import RoadNetwork
from .phase import Phase

MATERIALS = ("ore", "waste")


@dataclass(frozen=True)
class DigBlock:
    id: int
    bench_id: int
    seq: int                 # mining order along the bench (0-based, contiguous per bench)
    tonnes: float
    material: str            # "ore" | "waste"
    ore_grade: float         # metal grade (e.g. %Cu); 0.0 for waste
    arc_s0: float            # arc-length start [m] along the bench polyline
    arc_s1: float            # arc-length end; blocks tile the bench without overlap

    def to_dict(self) -> dict:
        return {"id": self.id, "bench_id": self.bench_id, "seq": self.seq, "tonnes": self.tonnes,
                "material": self.material, "ore_grade": self.ore_grade,
                "arc_s0": self.arc_s0, "arc_s1": self.arc_s1}

    @classmethod
    def from_dict(cls, d: dict) -> "DigBlock":
        return cls(int(d["id"]), int(d["bench_id"]), int(d["seq"]), float(d["tonnes"]),
                   str(d["material"]), float(d["ore_grade"]), float(d["arc_s0"]), float(d["arc_s1"]))


@dataclass(frozen=True)
class Bench:
    id: int
    z: float                       # floor elevation [m] (0 rim, negative down)
    height_m: float
    polyline: np.ndarray           # (k,3) float64 bench face line
    anchor_node: int               # network node where the bench meets the ramp
    block_ids: tuple[int, ...]     # in seq order

    @cached_property
    def arc_length_m(self) -> float:
        d = np.diff(self.polyline, axis=0)
        return float(np.sqrt((d * d).sum(axis=1)).sum())

    def to_dict(self) -> dict:
        return {"id": self.id, "z": self.z, "height_m": self.height_m,
                "polyline": self.polyline.tolist(), "anchor_node": self.anchor_node,
                "block_ids": list(self.block_ids)}

    @classmethod
    def from_dict(cls, d: dict) -> "Bench":
        return cls(int(d["id"]), float(d["z"]), float(d["height_m"]),
                   np.asarray(d["polyline"], dtype=np.float64), int(d["anchor_node"]),
                   tuple(int(x) for x in d["block_ids"]))


@dataclass(frozen=True)
class PitModel:
    id: str
    benches: tuple[Bench, ...]
    blocks: tuple[DigBlock, ...]
    phases: tuple[Phase, ...]

    # ---- construction validation (rule-named; see module docstring) ----
    def __post_init__(self) -> None:
        bl_ids = [b.id for b in self.blocks]
        be_ids = [b.id for b in self.benches]
        ph_ids = [p.id for p in self.phases]
        if len(set(bl_ids)) != len(bl_ids) or len(set(be_ids)) != len(be_ids) or len(set(ph_ids)) != len(ph_ids):
            raise ValueError("unique_ids: duplicate block/bench/phase id")
        benches = {b.id: b for b in self.benches}
        for blk in self.blocks:
            if blk.bench_id not in benches:
                raise ValueError(f"block_bench_exists: block {blk.id} references unknown bench {blk.bench_id}")
            if blk.tonnes <= 0:
                raise ValueError(f"tonnes_positive: block {blk.id}")
            if blk.material not in MATERIALS:
                raise ValueError(f"material_enum: block {blk.id} material {blk.material!r}")
        # blocks_tile_bench: per bench, seq contiguous from 0, arc ranges ordered/non-overlapping, in range
        by_bench: dict[int, list[DigBlock]] = {}
        blocks = {b.id: b for b in self.blocks}
        for blk in self.blocks:
            by_bench.setdefault(blk.bench_id, []).append(blk)
        for be in self.benches:
            listed = [blocks[i] for i in be.block_ids if i in blocks]
            if len(listed) != len(be.block_ids) or sorted(b.id for b in listed) != sorted(b.id for b in by_bench.get(be.id, [])):
                raise ValueError(f"blocks_tile_bench: bench {be.id} block_ids mismatch its blocks")
            seqs = [b.seq for b in listed]
            if seqs != list(range(len(listed))):
                raise ValueError(f"blocks_tile_bench: bench {be.id} seq must be contiguous from 0, got {seqs}")
            prev_end = 0.0
            for b in listed:
                if b.arc_s1 <= b.arc_s0 or b.arc_s0 < prev_end - 1e-9:
                    raise ValueError(f"blocks_tile_bench: bench {be.id} arc ranges overlap/regress at block {b.id}")
                prev_end = b.arc_s1
            if listed and prev_end > be.arc_length_m + 1e-6:
                raise ValueError(f"blocks_tile_bench: bench {be.id} blocks exceed polyline arc length")
        # bench_in_exactly_one_phase: an exact partition (reserve sums partition total_tonnes)
        seen: dict[int, int] = {}
        for ph in self.phases:
            for bid in ph.bench_ids:
                if bid not in benches:
                    raise ValueError(f"bench_in_exactly_one_phase: phase {ph.id} references unknown bench {bid}")
                if bid in seen:
                    raise ValueError(f"bench_in_exactly_one_phase: bench {bid} in phases {seen[bid]} and {ph.id}")
                seen[bid] = ph.id
        missing = set(benches) - set(seen)
        if missing:
            raise ValueError(f"bench_in_exactly_one_phase: benches {sorted(missing)} not owned by any phase")
        for be in self.benches:
            if be.anchor_node < 0:
                raise ValueError(f"anchor_node_nonnegative: bench {be.id}")

    # ---- lookups + reserves (pure, derived) ----
    def block(self, block_id: int) -> DigBlock:
        return self._blocks[block_id]

    def bench(self, bench_id: int) -> Bench:
        return self._benches[bench_id]

    def phase(self, phase_id: int) -> Phase:
        return self._phases[phase_id]

    @cached_property
    def _blocks(self) -> dict[int, DigBlock]:
        return {b.id: b for b in self.blocks}

    @cached_property
    def _benches(self) -> dict[int, Bench]:
        return {b.id: b for b in self.benches}

    @cached_property
    def _phases(self) -> dict[int, Phase]:
        return {p.id: p for p in self.phases}

    @cached_property
    def total_tonnes(self) -> float:
        return float(sum(b.tonnes for b in self.blocks))

    @cached_property
    def tonnes_by_bench(self) -> dict[int, float]:
        out: dict[int, float] = {b.id: 0.0 for b in self.benches}
        for blk in self.blocks:
            out[blk.bench_id] += blk.tonnes
        return out

    @cached_property
    def tonnes_by_phase(self) -> dict[int, float]:
        return {p.id: float(sum(self.tonnes_by_bench[b] for b in p.bench_ids)) for p in self.phases}

    @cached_property
    def ore_tonnes(self) -> float:
        return float(sum(b.tonnes for b in self.blocks if b.material == "ore"))

    @cached_property
    def waste_tonnes(self) -> float:
        return float(sum(b.tonnes for b in self.blocks if b.material == "waste"))

    @property
    def strip_ratio(self) -> float:
        return self.waste_tonnes / self.ore_tonnes if self.ore_tonnes > 0 else float("inf")

    def bind_check(self, net: RoadNetwork) -> list[str]:
        """Anchor nodes must exist in the network (separate from construction; see docstring)."""
        return [f"bench {b.id}: anchor node {b.anchor_node} not in network"
                for b in self.benches if b.anchor_node not in net.nodes]

    def to_dict(self) -> dict:
        return {"id": self.id, "benches": [b.to_dict() for b in self.benches],
                "blocks": [b.to_dict() for b in self.blocks],
                "phases": [p.to_dict() for p in self.phases]}

    @classmethod
    def from_dict(cls, d: dict) -> "PitModel":
        return cls(id=str(d["id"]),
                   benches=tuple(Bench.from_dict(x) for x in d["benches"]),
                   blocks=tuple(DigBlock.from_dict(x) for x in d["blocks"]),
                   phases=tuple(Phase.from_dict(x) for x in d["phases"]))
