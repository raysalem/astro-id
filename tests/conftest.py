import pytest


@pytest.fixture(autouse=True)
def _isolated_last_site(tmp_path_factory, monkeypatch):
    """`astro-id site` remembers the last saved site in the per-user data folder: never touch the real one."""
    p = tmp_path_factory.mktemp("userstate") / "last_site.json"
    monkeypatch.setattr("astro_id.site.last_site_path", lambda: p)
    return p
