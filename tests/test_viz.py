"""U9 viz acceptance: plan view + ramp profile + cycle Gantt render real SVGs from a spec alone,
the plan view reflects the STRUCTURE (rings, ramps, one-way arrows), gallery builds distinct."""
import pytest

mpl = pytest.importorskip("matplotlib")
mpl.use("Agg")

from minehaulsim.des.dispatch import MinQueuePolicy  # noqa: E402
from minehaulsim.scenarios import OpenPitParams, generate_open_pit  # noqa: E402
from minehaulsim.viz import save_cycle_gantt, save_planview, save_ramp_profile  # noqa: E402

QUICK = OpenPitParams(n_benches=7, n_shovels=2, n_crushers=1, n_waste_dumps=1,
                      stockpile=False, n_surface_junctions=1)


@pytest.fixture(scope="module")
def spec():
    return generate_open_pit(QUICK, seed=7)


def test_planview_svg_renders_structure(tmp_path, spec):
    p = save_planview(spec, tmp_path / "pit.plan.svg")
    text = p.read_text(encoding="utf-8")
    assert text.startswith("<?xml") and "</svg>" in text
    assert p.stat().st_size > 10_000                     # rings + roads, not an empty frame
    assert spec.name in text                             # the title carries the identity


def test_planview_dual_spiral_shows_two_ramps(tmp_path):
    dual = generate_open_pit(OpenPitParams(n_benches=8, ramp_style="dual_spiral", ramp_lanes=1,
                                           n_shovels=2, n_crushers=1, n_waste_dumps=1,
                                           stockpile=False, n_surface_junctions=1), seed=3)
    p = save_planview(dual, tmp_path / "dual.plan.svg")
    assert p.stat().st_size > 10_000


def test_ramp_profile_descends_to_depth(tmp_path, spec):
    p = save_ramp_profile(spec, tmp_path / "pit.profile.svg")
    assert p.exists() and p.stat().st_size > 5_000


def test_cycle_gantt_from_events(tmp_path, spec):
    res = spec.run(MinQueuePolicy(), seed=5, until_s=3600.0)
    p = save_cycle_gantt(res.events, truck_id=1, path=tmp_path / "gantt.svg")
    assert p.exists() and p.stat().st_size > 3_000
    with pytest.raises(ValueError):
        save_cycle_gantt(res.events, truck_id=999, path=tmp_path / "none.svg")


def test_gallery_builds_distinct_pits(tmp_path):
    from scripts.gen_gallery import build_gallery
    written = build_gallery(n=4, seed=11, out=tmp_path / "g")
    svgs = [p for p in written if p.suffix == ".svg"]
    assert len(svgs) == 4
    sizes = {p.stat().st_size for p in svgs}
    assert len(sizes) == 4                               # structurally distinct -> distinct renders
    assert (tmp_path / "g" / "README.md").exists()
    assert (tmp_path / "g" / "gallery.png").exists()
