# 02 — A custom dispatch policy

A policy is any object with `name`, `next_loader(truck, mine)` and `next_dump(truck, mine)`.
It sees a READ-ONLY `MineView` and returns node ids; if it needs randomness it must receive a
Generator (take one from `RngManager(seed).stream("policy")`), never create its own.

```python
from minehaulsim.des.dispatch import MineView, TruckView

class LongestIdleLoaderPolicy:
    """Send trucks to the loader that has been idle longest (a fairness baseline)."""
    name = "longest-idle"

    def next_loader(self, truck: TruckView, mine: MineView) -> int:
        candidates = [lv for lv in mine.loaders if lv.diggable]
        # est_free_s == 0 means idle now; break ties toward the lowest node id (determinism!)
        return min(candidates, key=lambda lv: (lv.est_free_s, lv.queue_len, lv.node_id)).node_id

    def next_dump(self, truck: TruckView, mine: MineView) -> int:
        return mine.dumps[0]

res = spec.run(LongestIdleLoaderPolicy(), seed=7)
```

Rules that keep results meaningful:

1. **Deterministic tie-breaking** — always end sort keys with `node_id`; a policy that breaks
   ties on dict order destroys reproducibility.
2. **Respect `diggable`** — a False loader is plan-forbidden NOW; choosing it parks the truck.
3. **Never mutate the view** — the MineView is a snapshot; state belongs in your policy object.
4. Compare against the baselines (`fixed`, `nearest`, `minqueue`, `minsat`, `random`) on the
   SAME spec + seed; the divergence tests in `tests/test_sim.py` show the pattern.
