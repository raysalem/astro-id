import pytest

from astro_id import requirements as R


@pytest.fixture(autouse=True)
def _astap_requirement_met(request, monkeypatch):
    """Tests stub the solver, so the run-time ASTAP gate must not depend on this machine (tests/test_requirements.py
    exercises the real gate and opts out with the ``real_gate`` marker)."""
    if request.node.get_closest_marker("real_gate"):
        return
    from pathlib import Path

    monkeypatch.setattr(R, "astap_status", lambda: R.AstapStatus(Path("astap_cli"), Path("."), R.MIN_ASTAP, {"w08"}))
