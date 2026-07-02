"""Offline end-to-end demo: generate -> simulate under three policies -> export the consumer
artifacts (cyclelog/v1 + provenance + topo) -> re-validate with the consumer's own rules.

Usage: python scripts/demo_offline.py [--seed 42] [--out out/demo]
"""
from __future__ import annotations

import argparse

from minehaulsim_cli.__main__ import main as cli


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="out/demo")
    args = ap.parse_args()

    assert cli(["generate", "--seed", str(args.seed), "--out", args.out]) == 0
    spec_path = f"{args.out}/openpit-{args.seed}.minespec.json"
    for policy in ("fixed", "nearest", "minqueue"):
        assert cli(["run", "--spec", spec_path, "--policy", policy,
                    "--shift-min", "240", "--seed", "7", "--out", args.out]) == 0
    assert cli(["validate", f"{args.out}/mhs-openpit-{args.seed}-minqueue.csv"]) == 0
    print("demo OK: generated, simulated x3 policies, exports pass the consumer contract")


if __name__ == "__main__":
    main()
