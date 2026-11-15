"""Minimum versions (astro_id/requirements.py): version parsing, the run-time gate, `astro-id check`, and the
Facet feature mapping against the fake Facet MCP."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from astro_id import cli
from astro_id import requirements as R
from astro_id.facet.client import connect
from fake_facet import FakeFacet
from test_facet_addon import TOKEN, photos
from test_facet_model_d import NEW_TOOLS, OLD_TOOLS
from fake_facet_mcp import ASTRO_TOOLS


@pytest.mark.parametrize("text,want", [
    ("ASTAP astrometric solver version CLI-2026.10.30, multi-threaded\n(C) 2018", (2026, 10, 30)),
    ("ASTAP version GUI-2026.11.12", (2026, 11, 12)),
    ("v2026.11.01", (2026, 11, 1)),
    ("astap 2024.05.01b", (2024, 5, 1)),
    ("version 2026.1.5", (2026, 1, 5)),
    ("no version here, build 12345.67", None),
    ("2026.13.40", None),
    ("", None),
])
def test_parse_astap_version(text, want):
    assert R.parse_astap_version(text) == want


def fake_astap(version, exe="/x/astap_cli", dbs=("w08",)):
    return lambda: R.AstapStatus(Path(exe), Path("/x"), version, set(dbs)) if exe else R.AstapStatus()


@pytest.mark.real_gate
def test_gate_below_minimum_is_one_clear_sentence(monkeypatch):
    monkeypatch.setattr(R, "astap_status", fake_astap((2024, 5, 1)))
    with pytest.raises(R.RequirementError) as e:
        R.require_astap()
    msg = str(e.value)
    assert "2024.5.1" in msg and "2026.10.30" in msg and "astro-id check" in msg and "\n" not in msg


@pytest.mark.real_gate
def test_gate_missing_and_fallback_and_unknown_version(monkeypatch):
    monkeypatch.setattr(R, "astap_status", fake_astap(None, exe=None))
    with pytest.raises(R.RequirementError, match="not installed"):
        R.require_astap()
    R.require_astap(fallback_ok=True)          # a configured astrometry.net key may stand in
    monkeypatch.setattr(R, "astap_status", fake_astap(None))
    R.require_astap()                           # unreadable version: warned by `check`, not a stop


@pytest.mark.real_gate
def test_solve_and_session_stop_early(monkeypatch, tmp_path, capsys):
    (tmp_path / "a.jpg").write_bytes(b"x")
    monkeypatch.delenv("ASTROMETRY_API_KEY", raising=False)
    monkeypatch.setattr(R, "astap_status", fake_astap((2020, 1, 1)))
    assert cli.main(["solve", str(tmp_path)]) == 2
    assert cli.main(["session", str(tmp_path / "a.jpg")]) == 2
    err = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert err["event"] == "error" and "older than the minimum" in err["error"]


@pytest.mark.real_gate
def test_missing_database(monkeypatch):
    monkeypatch.setattr(R, "astap_status", fake_astap((2026, 10, 30), dbs=("d50",)))
    rows = {r["key"]: r for r in R.run_checks(offline=True)}
    assert rows["database"]["state"] == R.FAIL and "W08" in rows["database"]["text"] and "D50" in rows["database"]["text"]
    assert R.requirements_block()["ok"] is False
    monkeypatch.setattr(R, "astap_status", fake_astap((2026, 10, 30), dbs=("w08",)))
    rows = {r["key"]: r for r in R.run_checks(offline=True)}
    assert rows["database"]["state"] == R.OK and rows["database_narrow"]["state"] == R.WARN


@pytest.mark.real_gate
def test_check_exit_codes_and_json(monkeypatch, capsys):
    monkeypatch.setattr(R, "astap_status", fake_astap((2026, 10, 30)))
    assert cli.main(["check", "--offline", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is True and doc["minimums"]["astap"] == "2026.10.30"
    assert {"key", "state", "text", "fix"} <= set(doc["checks"][0])
    monkeypatch.setattr(R, "astap_status", fake_astap((2020, 1, 1)))
    assert cli.main(["doctor", "--offline"]) == 1
    out = capsys.readouterr().out
    assert "\u2717 ASTAP 2020.1.1" in out and "->" in out and "Some required checks failed" in out
    monkeypatch.setattr(R, "astap_status", fake_astap(None, exe=None))
    assert cli.main(["check", "--offline"]) == 1


@pytest.mark.real_gate
def test_check_facet_rows_with_injected_probe(monkeypatch):
    monkeypatch.setattr(R, "astap_status", fake_astap((2026, 10, 30)))
    ok = lambda u: ("1.2", {"facet_api": 1}, {"model_d": True, "plugin_tags": True, "annotations_put": True, "tags": True}, True)
    rows = {r["key"]: r for r in R.run_checks(offline=True, facet_url="http://f", facet_probe=ok)}
    assert rows["facet_api"]["state"] == R.OK and "object tags" in rows["facet_mode"]["text"]
    old = lambda u: ("0.9", {}, {"annotations_put": True, "tags": True}, True)
    rows = {r["key"]: r for r in R.run_checks(offline=True, facet_url="http://f", facet_probe=old)}
    assert rows["facet_api"]["state"] == R.FAIL

    def down(u):
        raise ConnectionError("refused")
    rows = {r["key"]: r for r in R.run_checks(offline=True, facet_url="http://f", facet_probe=down)}
    assert rows["facet"]["state"] == R.FAIL and "refused" in rows["facet"]["text"]


def _fake(tools, **kw):
    return FakeFacet(photos(), addon_token=TOKEN, mcp_tools=tools, **kw)


def test_feature_mapping_against_fake_facet():
    with _fake(NEW_TOOLS, model_d=True) as f:
        c = connect(f.url, token=TOKEN)
        a = R.facet_assessment(c.status(), c.features())
        assert a["facet_api_ok"] and a["tags"] == "object tags" and a["sky_labels"]
        assert R.requirements_block(c)["ok"] is True or all("Facet" not in p for p in R.requirements_block(c)["problems"])
    with _fake(OLD_TOOLS) as f:      # annotations but no model D: plain tags, and no facet_api reported
        c = connect(f.url, token=TOKEN)
        a = R.facet_assessment(c.status(), c.features())
        assert a["tags"] == "plain tags" and a["sky_labels"] and a["facet_api_ok"] is False
        assert "older than Astro needs" in R.facet_problems(c.status(), c.features())[0]
    with _fake(ASTRO_TOOLS) as f:    # no annotation tools: plain tags, no sky labels
        c = connect(f.url, token=TOKEN)
        a = R.facet_assessment(c.status(), c.features())
        assert a["tags"] == "plain tags" and not a["sky_labels"] and "no sky labels" in a["what"]


def test_state_has_requirements_block():
    from astro_id.facet import serve as SV

    assert "requirements_block(self.client)" in Path(SV.__file__).read_text(encoding="utf-8")
