# ADR-0005 — `Segment.single_lane_op`: operationally single-lane wide roads

- Status: Accepted · Date: 2026-07-02 (decision made at U8)

## Context

The width model says a unit with `width_class` larger than the segment's cannot use it at all
(U3). Surface trucks are `width_class=2`; underground drifts are `width_class=1`. But a narrow
OPEN-PIT ramp is a road wide enough for the largest truck that is nevertheless operated as ONE
lane of travel with direction arbitration — neither width class captures it, and DirectionZones
originally required `width_class=1` (structural validation would flag a zone on a wide road).

## Decision

Add `Segment.single_lane_op: bool` (default False): physically wide enough for the fleet, but a
single travel lane. Zone membership is structurally legal iff `width_class == 1 OR
single_lane_op`. Routing and kinematics are unaffected; only zone arbitration and validation
read it.

## Consequences

- Open-pit generators can emit single-lane spiral/switchback ramps as DirectionZone chains
  (passing at the bench crossings) while serving width-2 trucks.
- Serialization is additive (`single_lane_op` defaults False on old documents).
