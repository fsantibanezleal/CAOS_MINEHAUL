"""NetworkOverlay: how mining progression affects routing WITHOUT mutating the frozen base graph.

Two-tier consumption (the perf contract):
    cheap tier   every dispatch/route call feeds `routing_inputs()` straight into the EXISTING
                 Router.route(closed=..., speed_caps=...) — no rebuild, no new Router;
    structural   only when `revision` bumps (face spur re-anchored, bench activated) does the
                 consumer rebuild `effective_network(base)` + a fresh Router — never mid-traversal.

Id allocation: overlay-created nodes/segments start at 100_000 (generators stay below; asserted in
U8), from monotone counters serialized in PitState snapshots — deterministic, collision-free.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..network.graph import NodeSite, RoadNetwork, Segment

PLANNING_NODE_ID_BASE = 100_000
PLANNING_SEG_ID_BASE = 100_000


@dataclass(frozen=True)
class NetworkOverlay:
    revision: int                                                    # bumps ONLY on topology change
    moved_nodes: tuple[tuple[int, tuple[float, float, float]], ...]  # (node_id, pos), sorted by id
    added_segments: tuple[Segment, ...]                              # face spurs (ids >= SEG base)
    retired_segments: frozenset[int]                                 # old spurs (routing = closed)
    closed_segments: frozenset[int]                                  # damage closures (U-P3)
    speed_caps: tuple[tuple[int, float], ...]                        # composed caps, sorted

    def routing_inputs(self) -> tuple[frozenset[int], dict[int, float]]:
        """Feed directly into Router.route(closed=..., speed_caps=...)."""
        return (self.retired_segments | self.closed_segments, dict(self.speed_caps))

    def effective_network(self, base: RoadNetwork) -> RoadNetwork:
        """Rebuild base + overlay: moved node positions, minus retired, plus added. Deterministic."""
        net = RoadNetwork()
        moved = dict(self.moved_nodes)
        for nid in sorted(base.nodes):
            n = base.nodes[nid]
            pos = moved.get(nid, n.pos)
            net.add_node(NodeSite(n.id, n.kind, tuple(pos)))  # type: ignore[arg-type]
        # overlay-created nodes referenced by added segments but absent from base
        for seg in sorted(self.added_segments, key=lambda s: s.id):
            for nid in (seg.a, seg.b):
                if nid not in net.nodes:
                    pos = moved.get(nid)
                    if pos is None:
                        raise ValueError(f"overlay segment {seg.id} references unplaced node {nid}")
                    net.add_node(NodeSite(nid, "face", tuple(pos)))  # type: ignore[arg-type]
        for sid in sorted(base.segments):
            if sid not in self.retired_segments:
                net.add_segment(base.segments[sid])
        for seg in sorted(self.added_segments, key=lambda s: s.id):
            if seg.id not in self.retired_segments:
                net.add_segment(seg)
        return net.freeze()
