# 02 — Underground axes and flow modes

| axis | range / choices | what it changes |
|---|---|---|
| levels | 3..10, spacing 25..60 m, first at 60..120 m | mine depth, decline length |
| decline | spiral (r 25..40 m) / zigzag · grade 1:8..1:6.5 | access topology · truck speeds |
| passing bays | every 150..400 m | DirectionZone span sizes ⇒ decline capacity |
| zone policy | lockout / loaded_priority / group_batching | arbitration dynamics |
| drifts | 1..4 per level (1 in truck_direct), 80..350 m, 1..3 drawpoints | production capillarity |
| ore passes | 1..3, upper-level spans, capacity 200..600 t | inventory buffers between fleets |
| shaft | present ~35% (forced by truck_shaft) | short-cycle dumping at the bin |
| LHDs | 1..2 per producing level, LHD_14/18 | feed rate to the passes |
| trucks | UG_TRUCK_50/63, sized to MF 0.7..1.5, clamp 2..20 | decline congestion |

## Flow modes (the structural material-path axis)

- **lhd_orepass_truck** (sublevel-caving-like): LHDs dig drawpoints and tip into passes; trucks
  load at the haulage-level CHUTES and climb the decline to the surface dump. Chutes are the
  cyclelog shovels.
- **truck_direct**: trucks drive to the level drift STUBS where an LHD shuttle-loads them
  (abstracted into `LHD_*_LOADING` classes, ~2 min/pass including the tram). One active heading
  per level — more would demand a fleet the single decline cannot feed (the MF gate enforces
  the same physics).
- **truck_shaft**: like the LHD flow, but trucks dump at the shaft BIN near the haulage level —
  short cycles bounded by hoist drain, not by the decline.

## The coupling (ADR-0006)

Fleets interact only through inventories: full pass parks the LHD, empty pass parks the truck
under the chute, full bin holds the dumper for the exact hoist time. Conservation is asserted on
every simulated run.
