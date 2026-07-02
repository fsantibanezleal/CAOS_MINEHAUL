"""MineSpec: the frozen scenario document — everything needed to re-run a scenario identically.

`MineSpec.from_json(path).run(...)` reproduces the exact event list of the spec it was written
from: the network, traffic constraints, roster and seed are all IN the document (schema
`minehaulsim.minespec/v1`). JSON serialization is canonical (sorted keys, fixed separators, LF)
so the same spec always produces the same bytes — the determinism tests hash it.

The `params` dict records every sampled generator axis (auditability: WHY this pit looks the way
it does); `topo` carries the exact PitTopoSpec key set consumers ingest; `est` carries the
generator's cycle/match-factor estimates (also used to scale the smoke-validation horizon).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..des.dispatch import DispatchPolicy, MinQueuePolicy
from ..des.sim import (LhdSpec, LoaderSpec, OrePassSpec, ShaftBinSpec, ShiftResult, TruckSpec,
                       run_shift)
from ..network.constraints import DirectionZone, Junction
from ..network.graph import RoadNetwork

SPEC_SCHEMA = "minehaulsim.minespec/v1"


@dataclass(frozen=True)
class RuntimeBundle:
    """Deserialized, ready-to-simulate view of a MineSpec."""
    net: RoadNetwork
    zones: dict[int, DirectionZone]
    junctions: dict[int, Junction]
    loaders: list[LoaderSpec]
    dumps: list[int]
    trucks: list[TruckSpec]
    lhds: list[LhdSpec] = field(default_factory=list)
    ore_passes: list[OrePassSpec] = field(default_factory=list)
    shaft_bin: ShaftBinSpec | None = None


@dataclass(frozen=True)
class MineSpec:
    kind: str                                # "openpit" | "underground"
    name: str
    seed: int                                # the generator seed that produced this spec
    params: dict                             # every sampled axis value (JSON-safe)
    network: dict                            # RoadNetwork.to_dict()
    zones: tuple[dict, ...] = ()             # DirectionZone.to_dict() each
    junctions: tuple[dict, ...] = ()         # Junction.to_dict() each
    loaders: tuple[dict, ...] = ()           # {node_id, loader_class, n_spots}
    dumps: tuple[int, ...] = ()              # legal dump nodes; [0] is the primary crusher
    trucks: tuple[dict, ...] = ()            # {truck_id, unit_name, start_loader}
    topo: dict = field(default_factory=dict)  # PitTopoSpec key set / minetopo payload
    est: dict = field(default_factory=dict)   # {"cycle_s": .., "match_factor": .., "load_s": ..}
    lhds: tuple[dict, ...] = ()              # underground: {lhd_id, unit_name, drawpoints, tip_node, pass_id}
    materials: dict = field(default_factory=dict)  # {"ore_passes": [...], "shaft_bin": {...}|absent}
    schema: str = SPEC_SCHEMA

    # ---- serialization (canonical bytes) ----
    def to_json(self, path: str | Path | None = None) -> str:
        d = {
            "schema": self.schema, "kind": self.kind, "name": self.name, "seed": self.seed,
            "params": self.params, "network": self.network,
            "zones": list(self.zones), "junctions": list(self.junctions),
            "loaders": list(self.loaders), "dumps": list(self.dumps),
            "trucks": list(self.trucks), "topo": self.topo, "est": self.est,
            "lhds": list(self.lhds), "materials": self.materials,
        }
        text = json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        if path is not None:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text.encode("utf-8"))
        return text

    @classmethod
    def from_json(cls, src: str | Path) -> "MineSpec":
        """Load from a file path, or parse directly when given a JSON string."""
        s = str(src)
        text = s if s.lstrip().startswith("{") else Path(src).read_text(encoding="utf-8")
        d = json.loads(text)
        if d.get("schema") != SPEC_SCHEMA:
            raise ValueError(f"not a {SPEC_SCHEMA} document (schema={d.get('schema')!r})")
        return cls(
            kind=str(d["kind"]), name=str(d["name"]), seed=int(d["seed"]),
            params=dict(d["params"]), network=dict(d["network"]),
            zones=tuple(dict(z) for z in d.get("zones", [])),
            junctions=tuple(dict(j) for j in d.get("junctions", [])),
            loaders=tuple(dict(x) for x in d.get("loaders", [])),
            dumps=tuple(int(x) for x in d.get("dumps", [])),
            trucks=tuple(dict(t) for t in d.get("trucks", [])),
            topo=dict(d.get("topo", {})), est=dict(d.get("est", {})),
            lhds=tuple(dict(x) for x in d.get("lhds", [])),
            materials=dict(d.get("materials", {})),
        )

    def with_name(self, name: str) -> "MineSpec":
        return replace(self, name=name)

    # ---- runtime ----
    def to_runtime(self) -> RuntimeBundle:
        net = RoadNetwork.from_dict(self.network)
        zones = {z["id"]: DirectionZone.from_dict(z) for z in self.zones}
        junctions = {j["id"]: Junction.from_dict(j) for j in self.junctions}
        loaders = [LoaderSpec(node_id=int(x["node_id"]), loader_class=str(x["loader_class"]),
                              n_spots=int(x.get("n_spots", 1))) for x in self.loaders]
        trucks = [TruckSpec(truck_id=int(t["truck_id"]), unit_name=str(t["unit_name"]),
                            start_loader=int(t["start_loader"])) for t in self.trucks]
        lhds = [LhdSpec(lhd_id=int(x["lhd_id"]), unit_name=str(x["unit_name"]),
                        drawpoints=tuple(int(d) for d in x["drawpoints"]),
                        tip_node=int(x["tip_node"]), pass_id=int(x["pass_id"]))
                for x in self.lhds]
        ore_passes = [OrePassSpec(pass_id=int(x["pass_id"]), chute_node=int(x["chute_node"]),
                                  capacity_t=float(x["capacity_t"]))
                      for x in self.materials.get("ore_passes", [])]
        sb = self.materials.get("shaft_bin")
        shaft_bin = (ShaftBinSpec(node=int(sb["node"]), capacity_t=float(sb["capacity_t"]),
                                  hoist_tph=float(sb["hoist_tph"])) if sb else None)
        return RuntimeBundle(net=net, zones=zones, junctions=junctions, loaders=loaders,
                             dumps=list(self.dumps), trucks=trucks, lhds=lhds,
                             ore_passes=ore_passes, shaft_bin=shaft_bin)

    def run(self, policy: DispatchPolicy | None = None, seed: int = 0,
            until_s: float = 8 * 3600.0, fast_mode: bool = False,
            plan_context=None) -> ShiftResult:
        """Simulate one shift of this scenario. Deterministic in (spec, policy, seed)."""
        rt = self.to_runtime()
        return run_shift(rt.net, rt.loaders, rt.dumps, rt.trucks,
                         policy if policy is not None else MinQueuePolicy(), seed=seed,
                         plan_context=plan_context, until_s=until_s,
                         zones=rt.zones, junctions=rt.junctions, fast_mode=fast_mode,
                         lhds=rt.lhds or None, ore_passes=rt.ore_passes or None,
                         shaft_bin=rt.shaft_bin)
