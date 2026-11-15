from pathlib import Path

import pytest

from astro_id import requirements as R


@pytest.fixture(autouse=True)
def _astap_requirement_met(request, monkeypatch):
    """Tests stub the solver, so the run-time ASTAP gate must not depend on this machine (tests/test_requirements.py
    exercises the real gate and opts out with the ``real_gate`` marker)."""
    if request.node.get_closest_marker("real_gate"):
        return
    monkeypatch.setattr(R, "astap_status", lambda: R.AstapStatus(Path("astap_cli"), Path("."), R.MIN_ASTAP, {"w08"}))


@pytest.fixture(autouse=True)
def _isolated_last_site(tmp_path_factory, monkeypatch):
    """`astro-id site` remembers the last saved site in the per-user data folder: never touch the real one."""
    p = tmp_path_factory.mktemp("userstate") / "last_site.json"
    monkeypatch.setattr("astro_id.site.last_site_path", lambda: p)
    return p
