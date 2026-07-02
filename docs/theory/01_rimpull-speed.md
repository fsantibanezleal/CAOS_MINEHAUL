# 01 — Rimpull, retarder and speed-by-grade

## Effective resistance

For a truck of gross vehicle weight `GVW` [t] on a segment of signed grade `g` [%] with rolling
resistance `rr` [%], the force required to hold constant speed is

```
F_req [kN] = GVW · 9.80665 · (g + rr) / 100
```

Uphill (or any positive effective resistance), the machine must SUPPLY that force at the wheels;
downhill, the retarder must ABSORB it to hold the descent.

## The force envelopes

The catalog generates class-representative envelopes from first principles:

```
F(v) = min( F_traction,  η · P / v )          η ≈ 0.85 driveline
F_traction ≈ μ · w_drive · GVW · 9.80665      μ ≈ 0.6 maintained roads,
                                              w_drive ≈ 0.55 (rear-drive rigid)
```

sampled at a fixed speed ladder (dense at low speeds where grades bite) into a monotone table.
The retarder envelope has the same `1/v` shape at ~0.85 of engine power. These are documented as
**class-representative, not OEM data** (see what-it-is-and-isnt).

## Attainable speed

The solver walks the envelope from fast to slow and returns the largest `v` with
`F(v) ≥ F_req`, interpolating between sampled points; below the slowest point the machine
stalls (`v = 0`, an inadmissible edge for routing). Final segment speed =
`min(attainable, segment limit, zone caps, class max)`.

Anchor (tested by hand): a CAT-793F-class truck, loaded, on 10% grade + 2% rolling ⇒
`F_req ≈ 461 kN` ⇒ power-limited at ≈ 13.1 km/h — the magnitude the Performance Handbook charts
give for this class.

## Traversal time

`t = L / v_seg + max(0, v_seg − v_entry) / a` with `a` = 0.35 m/s² loaded, 0.5 empty — a bounded
trapezoidal acceleration penalty, so climbs out of junction stops are honestly slower than
free-flow.
