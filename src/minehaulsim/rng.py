"""Seeded random-stream manager — the ONLY source of randomness in the package.

Determinism contract (binding): a run is a pure function of (spec, policy, seed) — same inputs give
byte-identical outputs on any OS/Python. To keep independent model aspects decoupled (adding a draw
to the failure model must not shift the load-time sequence), each named PURPOSE gets its own
independent child stream derived from the master seed + the purpose name:

    rng = RngManager(seed=42)
    load_rng = rng.stream("load-times")        # stable regardless of other streams' usage
    geom_rng = rng.stream("pit-geometry")

Streams are derived with SeedSequence(master, purpose-bytes) — stable across sessions and platforms
(no Python hash randomization involved).
"""
from __future__ import annotations

import numpy as np


class RngManager:
    def __init__(self, seed: int) -> None:
        if not isinstance(seed, int) or seed < 0:
            raise ValueError(f"seed must be a non-negative int, got {seed!r}")
        self.seed = seed
        self._streams: dict[str, np.random.Generator] = {}

    def stream(self, purpose: str) -> np.random.Generator:
        """The named child stream (created on first use; the same object afterwards)."""
        if purpose not in self._streams:
            child = np.random.SeedSequence((self.seed, *purpose.encode("utf-8")))
            self._streams[purpose] = np.random.default_rng(child)
        return self._streams[purpose]

    def fresh(self, purpose: str) -> np.random.Generator:
        """A NEW generator for the purpose from the start of its sequence (replays the stream)."""
        child = np.random.SeedSequence((self.seed, *purpose.encode("utf-8")))
        return np.random.default_rng(child)
