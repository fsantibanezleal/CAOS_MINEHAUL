# 01 — Quickstart

```bash
pip install minehaulsim            # numpy-only core
pip install "minehaulsim[viz]"     # + matplotlib renders
minehaulsim demo                   # offline end-to-end, prints per-policy KPIs
```

## Generate, simulate, export

```python
from minehaulsim import generate_open_pit, generate_underground
from minehaulsim.des.dispatch import MinQueuePolicy
from minehaulsim.io import write_cyclelog, validate_cyclelog

spec = generate_open_pit(seed=42)            # validated, structurally unique scenario
res = spec.run(MinQueuePolicy(), seed=7)     # deterministic in (spec, policy, seed)
print(res.tonnes, res.cycles, res.truck_wait_s)

write_cyclelog(res.events, "shift.csv")
assert validate_cyclelog("shift.csv").ok     # the consumer's own ingest rules

ug = generate_underground(seed=1)            # LHD/ore-pass coupled underground mine
print(ug.run(seed=7).materials)              # conservation summaries per pass
```

Same via the CLI: `minehaulsim generate --seed 42 --out out/`, then
`minehaulsim run --spec out/openpit-42.minespec.json --policy minqueue --out out/`.

## Reproducibility contract

A `MineSpec` JSON re-runs identically anywhere (`MineSpec.from_json(p).run(...)`); the same
generation call produces byte-identical spec files. If you need variance, vary the SEED —
never patch the document by hand (regenerate instead, so `params` stays a truthful audit
trail).

## Failures and free-flow

```python
from minehaulsim.des.failures import FailureConfig
res = spec.run(seed=7, failures=FailureConfig(truck_mtbf_h=20.0))   # opt-in disturbances
res = spec.run(seed=7, fast_mode=True)                              # free-flow (no traffic)
```
