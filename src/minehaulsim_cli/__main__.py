"""minehaulsim CLI — argparse subcommands (grow per unit: generate, run, gallery)."""
from __future__ import annotations

import argparse

import minehaulsim


def main() -> None:
    ap = argparse.ArgumentParser(prog="minehaulsim", description="Mine haulage DES toolkit")
    ap.add_argument("--version", action="version", version=f"minehaulsim {minehaulsim.__version__}")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("info", help="package + determinism info")
    args = ap.parse_args()
    if args.cmd == "info":
        print(f"minehaulsim {minehaulsim.__version__} - deterministic mine-haulage DES (numpy-only core)")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
