# 02 — Match factor and fleet sizing

## Definition

```
MF = (N_trucks · t_load) / (N_loaders · t_cycle)
```

`t_load` = time for a loader to fill one truck; `t_cycle` = the truck's full cycle (load + haul
+ dump + return). `MF < 1`: loaders starve (truck-limited). `MF > 1`: trucks queue
(loader-limited). Neither is "wrong" — real mines run 0.8–1.3 depending on which asset is the
bottleneck cost.

## Where the package uses it

- **Generators** size fleets to a sampled target MF (0.7–1.5): estimate the representative
  cycle by routing the deepest face to the primary dump with the largest truck class (free-flow
  kinematics + load + dump service), then `N_trucks = MF · N_loaders · t_cycle / t_load`,
  clamped to sane per-kind bounds.
- **Validation** recomputes the static MF from the final integer roster and rejects scenarios
  outside [0.5, 2.2] — scenarios outside that band are degenerate (always-idle or
  hopelessly jammed) and teach a dispatch policy nothing.
- **The cyclelog validator** estimates an EMPIRICAL MF from event deltas (median load time and
  cycle time per truck) and flags files outside [0.4, 2.5] — the same sanity screen the
  consumer applies.

## Honesty note

The static MF uses free-flow cycle times: it is a LOWER bound on congestion effects by
construction. The DES exists precisely because queueing and traffic move real throughput away
from the static estimate; `plan_feasibility` documents the same caveat for planning numbers.
