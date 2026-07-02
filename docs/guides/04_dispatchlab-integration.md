# 04 — DispatchLab integration

DispatchLab (the dispatch-analytics web app this package was built to feed) consumes three
artifacts per sample, all produced by `minehaulsim run --out`:

```
mhs-<scenario>-<policy>.csv               # cyclelog/v1 (validated with the ported ingest rules)
mhs-<scenario>-<policy>.provenance.json   # generator record, kind: structure-real, honest caveats
mhs-<scenario>-<policy>.topo.json         # PitTopoSpec (open pit) — the 3D view renders the
                                          # REAL generated geometry
```

Pipeline shape (lives in the DispatchLab repo, not here):

```python
from minehaulsim.scenarios import generate_batch
specs = generate_batch(6, seed=2026)                  # varied, validated, diverse
for spec in specs:
    ...spec.run(policy, seed)...                       # + write_cyclelog/provenance/topo
```

Contract anchors a consumer must not re-derive differently:

- **Event anchoring** is ADR-0003 (deltas = loading / loaded travel / dumping / empty+queue).
- **Ids**: shovels 1..N, dumps 101..; underground shovels are chutes or LHD-loading stubs.
- **Provenance kind** is `structure-real` — the structure (network, distances, grades) is real
  simulation; the mine itself is synthetic. Never present these samples as mine data.
- Every shipped sample must pass `validate_cyclelog` — the same function the app's ingest
  implements. If the two ever disagree, THAT is the bug to fix, not the sample.
