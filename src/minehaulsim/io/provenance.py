"""Provenance JSON: every exported sample carries its full generator metadata (immutable record).
Shape = the DispatchLab provenance contract (id/name/schema/kind/source/license/caveats/generator)."""
from __future__ import annotations

import json
from pathlib import Path

from .. import __version__

SCHEMA = "dispatchlab.cyclelog/v1"


def write_provenance(path: str | Path, *, sample_id: str, name: str, dispatcher: str,
                     sim_time_min: float, scenario_seed: int, sim_seed: int, kind: str,
                     spec_summary: str) -> dict:
    prov = {
        "id": sample_id,
        "name": name,
        "schema": SCHEMA,
        "kind": "structure-real",
        "source": ("Generated with minehaulsim v" + __version__ +
                   " (open-source constrained-network mine haulage DES), "
                   "github.com/fsantibanezleal/CAOS_MINEHAUL"),
        "license": "Apache-2.0 (generator); CC0 (rows)",
        "caveats": ("Synthetic physics-based simulation (rimpull kinematics on a generated road "
                    "network); not a real FMS log."),
        "generator": {"dispatcher": dispatcher, "sim_time_min": sim_time_min,
                      "minehaulsim": __version__, "scenario_seed": scenario_seed,
                      "sim_seed": sim_seed, "kind": kind, "spec_summary": spec_summary},
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prov, indent=2) + "\n", encoding="utf-8")
    return prov
