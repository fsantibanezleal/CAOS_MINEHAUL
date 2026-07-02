# 03 — A custom truck class / your own network

## Your own truck class

```python
from minehaulsim.equipment.catalog import TRUCKS, _truck

TRUCKS["MY_150T"] = _truck("MY_150T", "rigid", payload=150.0, sd=7.0, empty=110.0,
                           width=2, power_kw=1300.0, vmax=55.0)
```

`_truck` builds the class-representative envelopes (`F(v) = min(traction, ηP/v)`) from the
magnitudes you give. If you have a REAL rimpull table, construct `TruckClass` directly and pass
your `((v_kmh, F_kN), ...)` points (monotone decreasing in v) — everything downstream (solver,
routing, traffic) consumes the table, not the generator.

Honesty rule: label runs made with hand-tuned classes accordingly in your provenance; the
bundled catalog is class-representative, not OEM data.

## Your own network (bring-your-own-mine)

The generators are conveniences — the simulator runs on ANY frozen `RoadNetwork`:

```python
import numpy as np
from minehaulsim.des.dispatch import MinQueuePolicy
from minehaulsim.des.sim import LoaderSpec, TruckSpec, run_shift
from minehaulsim.network.graph import NodeSite, RoadNetwork, Segment

net = RoadNetwork()
net.add_node(NodeSite(1, "face", (0.0, 0.0, -30.0)))
net.add_node(NodeSite(101, "crusher", (1800.0, 0.0, 0.0)))
net.add_segment(Segment(id=1, a=1, b=101,
                        polyline=np.array([[0, 0, -30.0], [1800, 0, 0.0]], dtype=float),
                        length_m=1800.2, grade_pct=1.7, width_class=2, one_way=False,
                        speed_limit_kmh=45.0))
net.freeze()
assert net.validate() == []          # ALWAYS check before simulating

res = run_shift(net, [LoaderSpec(1)], [101],
                [TruckSpec(i, "CAT_785D", 1) for i in range(1, 7)],
                MinQueuePolicy(), seed=7)
```

Conventions your network must honor: loader node ids 1..N and dump ids ≥ 101 if you plan to
export cyclelog/v1; signed `grade_pct` in the a→b direction; split long ramps into
piecewise-constant-grade segments; put `zone_id` (+ `single_lane_op` on wide roads) wherever
opposing traffic must arbitrate.
