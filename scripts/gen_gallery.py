"""Render the 12-seed gallery: the artifact that PROVES generated pits are structurally distinct
(the original motivation for this package — no more same-pit-every-time synthetics).

Writes gallery/pit-<i>.plan.svg (one per scenario, diffable text) + gallery/README.md with the
per-pit structural summaries, and a contact-sheet gallery/gallery.png for quick human review.

Usage: python scripts/gen_gallery.py [--n 12] [--seed 2026] [--out gallery]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from minehaulsim.scenarios import diversity_signature, generate_batch
from minehaulsim.viz import plot_plan, save_planview


def build_gallery(n: int = 12, seed: int = 2026, out: str | Path = "gallery") -> list[Path]:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = generate_batch(n, seed=seed)

    written: list[Path] = []
    lines = [
        "# Scenario gallery", "",
        f"{n} scenarios from `generate_batch(n={n}, seed={seed})` — every one passes the seven",
        "validity gates and carries a UNIQUE structural signature (ramp style, depth, roster,",
        "network shape). Regenerate with `python scripts/gen_gallery.py`.", "",
        "| # | preview | structure |",
        "|---|---------|-----------|",
    ]
    for i, spec in enumerate(specs):
        p = save_planview(spec, out_dir / f"pit-{i:02d}.plan.svg")
        written.append(p)
        pr = spec.params
        lines.append(
            f"| {i} | ![pit {i}](pit-{i:02d}.plan.svg) | `{pr['ramp_style']}` "
            f"({pr['ramp_lanes']} lane), {pr['n_benches']} benches x {pr['bench_height_m']} m, "
            f"{pr['n_shovels']} shovels, {len(spec.dumps)} dumps, {len(spec.trucks)} trucks, "
            f"sig `{diversity_signature(spec)}` |")
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    written.append(out_dir / "README.md")

    # contact sheet for quick visual review (PNG, small)
    import matplotlib.pyplot as plt
    rows = (n + 3) // 4
    fig, axes = plt.subplots(rows, 4, figsize=(16, 4 * rows))
    for ax in axes.flat:
        ax.set_visible(False)
    for i, spec in enumerate(specs):
        ax = axes.flat[i]
        ax.set_visible(True)
        plot_plan(spec, ax=ax)
        ax.set_xlabel("")
        ax.set_ylabel("")
    fig.tight_layout()
    sheet = out_dir / "gallery.png"
    fig.savefig(sheet, dpi=70)
    plt.close(fig)
    written.append(sheet)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default="gallery")
    args = ap.parse_args()
    for p in build_gallery(args.n, args.seed, args.out):
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
