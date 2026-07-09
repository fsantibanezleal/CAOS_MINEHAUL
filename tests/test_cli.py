"""U9 CLI acceptance: every subcommand end-to-end — generate/batch write valid specs, run exports
a contract-passing cyclelog + provenance + topo, validate gates both file kinds, demo prints KPIs."""
import json

import pytest

from minehaulsim_cli.__main__ import main as cli


def test_generate_writes_spec_and_summary(tmp_path, capsys):
    assert cli(["generate", "--seed", "5", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "openpit-5" in out and "benches" in out and "MF" in out
    spec_path = tmp_path / "openpit-5.minespec.json"
    assert spec_path.exists()
    d = json.loads(spec_path.read_text(encoding="utf-8"))
    assert d["schema"] == "minehaulsim.minespec/v1"


def test_generate_preset_and_dry_run(capsys):
    assert cli(["generate", "--preset", "starter_pit"]) == 0
    assert "starter_pit" in capsys.readouterr().out


def test_run_exports_contract_passing_artifacts(tmp_path, capsys):
    assert cli(["generate", "--seed", "5", "--out", str(tmp_path)]) == 0
    spec = str(tmp_path / "openpit-5.minespec.json")
    assert cli(["run", "--spec", spec, "--policy", "minqueue", "--shift-min", "120",
                "--seed", "7", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "t/h" in out and "contract OK" in out
    sample = tmp_path / "mhs-openpit-5-minqueue.csv"
    prov = tmp_path / "mhs-openpit-5-minqueue.provenance.json"
    topo = tmp_path / "mhs-openpit-5-minqueue.topo.json"
    assert sample.exists() and prov.exists() and topo.exists()
    tp = json.loads(topo.read_text(encoding="utf-8"))
    assert set(tp.keys()) == {"center", "rimRx", "rimRy", "nBenches", "benchHeightM",
                              "benchWidthM", "faceAngleDeg", "rampWidthM", "shovelBench", "roads"}
    assert tp["roads"]["schema"] == "minehaulsim.roads/v1" and tp["roads"]["segments"]
    # the exported csv passes the validate subcommand too (exit 0)
    assert cli(["validate", str(sample)]) == 0


def test_validate_spec_json_reports_the_named_gates(tmp_path, capsys):
    assert cli(["generate", "--seed", "5", "--out", str(tmp_path)]) == 0
    capsys.readouterr()
    assert cli(["validate", str(tmp_path / "openpit-5.minespec.json")]) == 0
    out = capsys.readouterr().out
    for gate in ("contract_ready", "connectivity", "grades", "geometry_sane",
                 "traffic_sane", "throughput_sane", "deadlock_free_smoke"):
        assert f"PASS {gate}" in out


def test_validate_rejects_bad_csv(tmp_path, capsys):
    bad = tmp_path / "bad.csv"
    bad.write_text("t,truck_id,shovel_id,event,payload_t\n0.0,1,1,fly,0.0\n", encoding="utf-8")
    assert cli(["validate", str(bad)]) == 1


def test_batch_generates_diverse_specs(tmp_path, capsys):
    assert cli(["batch", "--n", "3", "--seed", "9", "--out", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("*.minespec.json"))) == 3


def test_demo_prints_policy_kpis(capsys):
    assert cli(["demo"]) == 0
    out = capsys.readouterr().out
    assert "fixed" in out and "nearest" in out and "minqueue" in out and "t/h" in out


def test_run_fast_mode_flag(tmp_path, capsys):
    assert cli(["generate", "--seed", "5", "--out", str(tmp_path)]) == 0
    spec = str(tmp_path / "openpit-5.minespec.json")
    assert cli(["run", "--spec", spec, "--fast", "--shift-min", "60"]) == 0
    assert "free-flow" in capsys.readouterr().out


def test_info_lists_presets(capsys):
    assert cli(["info"]) == 0
    out = capsys.readouterr().out
    assert "starter_pit" in out and "deterministic" in out


def test_render_writes_both_svgs(tmp_path, capsys):
    pytest.importorskip("matplotlib")
    assert cli(["generate", "--seed", "5", "--out", str(tmp_path)]) == 0
    spec = str(tmp_path / "openpit-5.minespec.json")
    assert cli(["render", "--spec", spec, "--out", str(tmp_path)]) == 0
    assert (tmp_path / "openpit-5.plan.svg").exists()
    assert (tmp_path / "openpit-5.profile.svg").exists()
