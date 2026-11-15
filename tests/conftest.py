from pathlib import Path

import pytest

from astro_id import requirements as R

_REAL_ASTAP_STATUS = R.astap_status


@pytest.fixture(autouse=True, scope="session")
def _astap_requirement_met():
    """Tests stub the solver, so the run-time ASTAP gate must not depend on this machine. Session scope, so that
    module-scoped fixtures that run a solve (tests/test_annotations.py) see the stub too."""
    mp = pytest.MonkeyPatch()
    mp.setattr(R, "astap_status", lambda: R.AstapStatus(Path("astap_cli"), Path("."), R.MIN_ASTAP, {"w08"}))
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def _real_gate(request, monkeypatch):
    """tests/test_requirements.py exercises the real gate and opts out of the stub with the ``real_gate`` marker."""
    if request.node.get_closest_marker("real_gate"):
        monkeypatch.setattr(R, "astap_status", _REAL_ASTAP_STATUS)


@pytest.fixture(autouse=True)
def _isolated_last_site(tmp_path_factory, monkeypatch):
    """`astro-id site` remembers the last saved site in the per-user data folder: never touch the real one."""
    p = tmp_path_factory.mktemp("userstate") / "last_site.json"
    monkeypatch.setattr("astro_id.site.last_site_path", lambda: p)
    return p
