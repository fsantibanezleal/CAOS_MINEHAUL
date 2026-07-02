"""minehaulsim CLI — the blueprint's console surface (section 4.5).

    minehaulsim generate --seed 42 --out out/            spec JSON (+ plan SVG with [viz])
    minehaulsim generate --preset deep_spiral --out out/
    minehaulsim batch --n 10 --seed 2026 --out samples/  diverse specs (+ SVGs)
    minehaulsim run --spec out/openpit-42.minespec.json --policy minqueue --shift-min 480 \
                    --seed 7 --out out/                  cyclelog CSV + provenance + topo
    minehaulsim render --spec X.minespec.json --out out/ plan view + ramp profile (needs [viz])
    minehaulsim validate PATH                            .csv -> consumer ingest rules;
                                                         .json -> the 7 named spec gates
    minehaulsim demo                                     offline end-to-end, prints KPIs
    minehaulsim info

Generation/run dry-print a summary when --out is omitted. Every command is deterministic in its
(seed, arguments); exit code 0 = OK, 1 = validation failed / error named on stderr.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import minehaulsim
from minehaulsim.des.dispatch import (FixedPolicy, MinQueuePolicy, MinSaturationPolicy,
                                      NearestPolicy, RandomPolicy)
from minehaulsim.io import validate_cyclelog, write_cyclelog, write_provenance
from minehaulsim.rng import RngManager
from minehaulsim.scenarios import (MineSpec, generate_batch, generate_open_pit, load_preset,
                                   preset_names, validate_spec)

POLICIES = ("fixed", "nearest", "minqueue", "minsat", "random")


def _policy(name: str, seed: int):
    if name == "fixed":
        return FixedPolicy()
    if name == "nearest":
        return NearestPolicy()
    if name == "minqueue":
        return MinQueuePolicy()
    if name == "minsat":
        return MinSaturationPolicy()
    return RandomPolicy(RngManager(seed).stream("policy"))


def _spec_summary(spec: MineSpec) -> str:
    p = spec.params
    return (f"{spec.name}: {spec.kind}, {p.get('ramp_style')} ramp ({p.get('ramp_lanes')} lane), "
            f"{p.get('n_benches')} benches x {p.get('bench_height_m')} m, "
            f"{len(spec.loaders)} shovels, {len(spec.dumps)} dumps, "
            f"{len(spec.trucks)} trucks ({', '.join(p.get('truck_classes', []))}), "
            f"est cycle {float(spec.est.get('cycle_s', 0)) / 60:.1f} min, "
            f"MF {spec.est.get('match_factor')}")


def _maybe_render(spec: MineSpec, out: Path) -> list[Path]:
    try:
        from minehaulsim.viz import save_planview
    except ImportError:
        return []
    return [save_planview(spec, out / f"{spec.name}.plan.svg")]


def cmd_generate(args: argparse.Namespace) -> int:
    spec = load_preset(args.preset) if args.preset else generate_open_pit(seed=args.seed)
    print(_spec_summary(spec))
    if args.out:
        out = Path(args.out)
        spec.to_json(out / f"{spec.name}.minespec.json")
        for w in [out / f"{spec.name}.minespec.json"] + _maybe_render(spec, out):
            print(f"  wrote {w}")
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    specs = generate_batch(args.n, seed=args.seed)
    for spec in specs:
        print(_spec_summary(spec))
        if args.out:
            spec.to_json(Path(args.out) / f"{spec.name}.minespec.json")
            _maybe_render(spec, Path(args.out))
    if args.out:
        print(f"  wrote {len(specs)} specs to {args.out}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    spec = MineSpec.from_json(args.spec)
    res = spec.run(_policy(args.policy, args.seed), seed=args.seed,
                   until_s=args.shift_min * 60.0, fast_mode=args.fast)
    tph = res.tonnes / (args.shift_min / 60.0)
    print(f"{spec.name} + {args.policy} (seed {args.seed}, {args.shift_min:.0f} min"
          f"{', free-flow' if args.fast else ''}): "
          f"{res.tonnes:.1f} t, {res.cycles} cycles, {tph:.0f} t/h, "
          f"truck wait {res.truck_wait_s / 60:.1f} min total, {res.events_executed} events")
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        sample = f"mhs-{spec.name}-{args.policy}"
        n = write_cyclelog(res.events, out / f"{sample}.csv")
        write_provenance(out / f"{sample}.provenance.json", sample_id=sample,
                         name=f"{spec.name} ({args.policy})", dispatcher=args.policy,
                         sim_time_min=int(args.shift_min), scenario_seed=spec.seed,
                         sim_seed=args.seed, kind=spec.kind, spec_summary=_spec_summary(spec))
        if spec.topo:
            (out / f"{sample}.topo.json").write_text(
                json.dumps(spec.topo, indent=1) + "\n", encoding="utf-8")
        rep = validate_cyclelog(out / f"{sample}.csv")
        if not rep.ok:
            print(f"  EXPORT FAILED the consumer contract: {rep.rejected}", file=sys.stderr)
            return 1
        print(f"  wrote {sample}.csv ({n} rows, contract OK) + provenance + topo to {args.out}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    try:
        from minehaulsim.viz import save_planview, save_ramp_profile
    except ImportError:
        print("render needs matplotlib: pip install 'minehaulsim[viz]'", file=sys.stderr)
        return 1
    spec = MineSpec.from_json(args.spec)
    out = Path(args.out or ".")
    for p in (save_planview(spec, out / f"{spec.name}.plan.svg"),
              save_ramp_profile(spec, out / f"{spec.name}.profile.svg")):
        print(f"  wrote {p}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.suffix == ".csv":
        rep = validate_cyclelog(path)
        if rep.ok:
            print(f"OK: {rep.n_rows} rows, trucks {rep.trucks}, shovels {rep.shovels}, "
                  f"dumps {rep.dumps}" + (f", flags: {rep.flags}" if rep.flags else ""))
            return 0
        print(f"REJECTED: {rep.rejected}", file=sys.stderr)
        return 1
    report = validate_spec(MineSpec.from_json(path))
    for c in report.checks:
        print(f"  {'PASS' if c.ok else 'FAIL'} {c.name}" + (f" — {c.detail}" if c.detail else ""))
    return 0 if report.ok else 1


def cmd_demo(_args: argparse.Namespace) -> int:
    spec = load_preset("starter_pit")
    print(_spec_summary(spec))
    for policy in ("fixed", "nearest", "minqueue"):
        res = spec.run(_policy(policy, 7), seed=7, until_s=240 * 60.0)
        print(f"  {policy:>9}: {res.tonnes:8.1f} t  {res.cycles:3d} cycles  "
              f"{res.tonnes / 4.0:6.0f} t/h  wait {res.truck_wait_s / 60:6.1f} min")
    return 0


def cmd_info(_args: argparse.Namespace) -> int:
    print(f"minehaulsim {minehaulsim.__version__} — deterministic mine-haulage DES "
          f"(numpy-only core)")
    print(f"  presets: {', '.join(preset_names())}")
    try:
        from minehaulsim.viz import HAS_MPL
        viz = "available" if HAS_MPL else "NOT installed"
    except ImportError:
        viz = "NOT installed"
    print(f"  viz extra: {viz}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="minehaulsim", description="Mine haulage DES toolkit")
    ap.add_argument("--version", action="version",
                    version=f"minehaulsim {minehaulsim.__version__}")
    sub = ap.add_subparsers(dest="cmd")

    g = sub.add_parser("generate", help="generate one validated open-pit scenario")
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--preset", choices=preset_names())
    g.add_argument("--out", help="directory for the spec JSON (+ plan SVG with [viz])")

    b = sub.add_parser("batch", help="generate n structurally diverse scenarios")
    b.add_argument("--n", type=int, default=10)
    b.add_argument("--seed", type=int, default=2026)
    b.add_argument("--out")

    r = sub.add_parser("run", help="simulate a shift from a spec; export cyclelog/v1")
    r.add_argument("--spec", required=True)
    r.add_argument("--policy", choices=POLICIES, default="minqueue")
    r.add_argument("--shift-min", type=float, default=480.0)
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--fast", action="store_true", help="free-flow (skip traffic)")
    r.add_argument("--out")

    rd = sub.add_parser("render", help="plan view + ramp profile SVGs (needs [viz])")
    rd.add_argument("--spec", required=True)
    rd.add_argument("--out")

    v = sub.add_parser("validate", help=".csv -> consumer ingest rules; .json -> spec gates")
    v.add_argument("path")

    sub.add_parser("demo", help="offline end-to-end on the starter pit, prints KPIs")
    sub.add_parser("info", help="package + extras info")

    args = ap.parse_args(argv)
    handlers = {"generate": cmd_generate, "batch": cmd_batch, "run": cmd_run,
                "render": cmd_render, "validate": cmd_validate, "demo": cmd_demo,
                "info": cmd_info}
    if args.cmd is None:
        ap.print_help()
        return 0
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
