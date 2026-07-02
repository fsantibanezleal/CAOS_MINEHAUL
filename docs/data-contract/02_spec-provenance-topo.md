# 02 — MineSpec, provenance and topography documents

## MineSpec (`minehaulsim.minespec/v1`)

The frozen scenario document: canonical JSON (sorted keys, fixed separators, LF) so the same
scenario is always the same bytes. Fields: `kind`, `name`, `seed`, `params` (every sampled axis
— the audit trail of WHY the mine looks like this), `network` (nodes + segments with polylines),
`zones`, `junctions`, `loaders`, `dumps`, `trucks`, `topo`, `est`, and (underground, additive)
`lhds` + `materials` (`ore_passes`, optional `shaft_bin`). `MineSpec.from_json(path).run(...)`
reproduces the exact event list of the spec it was written from.

Schema evolution rule: additions must be additive with defaults (old documents load unchanged);
any semantic change bumps the schema string.

## Provenance (`dispatchlab.cyclelog/v1` sample descriptor)

Written next to every exported sample: generator name + version, scenario seed, sim seed,
dispatcher, sim minutes, `kind: structure-real`, and HONEST caveats (synthetic; curves
class-representative; no calibration claimed). The provenance is the anti-overclaim device: a
consumer can always answer "where did this data come from?".

## PitTopoSpec (open pit)

The exact key set DispatchLab's 3D view consumes:

```
center {x,y} · rimRx · rimRy · nBenches · benchHeightM · benchWidthM · faceAngleDeg
· rampWidthM · shovelBench {shovel_id: bench}
```

`rimRx/rimRy` come from a least-squares axis fit of the REAL perturbed rim (recovers 400×300
within 5% under 3% radial noise — tested), so the consumer's ellipse approximation is honest
about the generated geometry it summarizes.

## minetopo/v1 (underground)

`{schema, levels[{index,z,drawpoints}], decline[[x,y,z]...], shafts[{bin}], ore_passes[{chute,
tips}]}` — carried inside the spec's `topo` and exported by the CLI for future underground 3D
consumers (the DispatchLab underground view is a follow-up, not a current dependency).
