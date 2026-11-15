"""--help vocabulary, exit codes (verified against the code that returns them) and the install hint."""
import argparse
import json
import re
from pathlib import Path

import pytest

from astro_id import cli
from astro_id.vocab import EXIT_CODES, VOCAB

ROOT = Path(__file__).resolve().parents[1]


def _walk(parser, prefix=""):
    yield prefix.strip(), parser
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, sp in a.choices.items():
                yield from _walk(sp, f"{prefix} {name}")


def _all_help():
    return {name: p.format_help() for name, p in _walk(cli.build_parser())}


def test_help_never_says_ordinary_user_tags():
    for name, text in _all_help().items():
        flat = " ".join(text.split()).lower()
        assert "user tags" not in flat, name
        # "ordinary tags" appears only in the one sentence about older Facets
        for m in re.finditer(r"ordinary tags", flat):
            assert "older facet stores object tags as ordinary tags" in flat[max(0, m.start() - 40):m.end() + 5], name


@pytest.mark.parametrize("cmd", ["facet sync", "facet undo", "solve", "session"])
def test_commands_define_the_three_words(cmd):
    text = " ".join(_all_help()[cmd].split())
    for word in ("object tags", "status tags", "sky labels"):
        assert word in text, (cmd, word)


def test_vocab_sentence_is_the_same_everywhere():
    flat_vocab = " ".join(VOCAB.split())
    for cmd in ("facet sync", "facet undo"):
        assert flat_vocab in " ".join(_all_help()[cmd].split())


def test_every_subcommand_documents_exit_codes():
    helps = _all_help()
    for cmd, codes in EXIT_CODES.items():
        text = helps[cmd]
        assert "exit codes:" in text, cmd
        for code, meaning in codes.items():
            assert f"  {code}  {meaning}" in text, (cmd, code)


def test_contract_md_has_the_exit_code_table():
    doc = (ROOT / "CONTRACT.md").read_text(encoding="utf-8")
    for cmd, codes in EXIT_CODES.items():
        for code, meaning in codes.items():
            assert f"| `astro-id {cmd}` | {code} | {meaning} |" in doc, (cmd, code)


# ---- the codes themselves, checked against the code paths -----------------------------------------------------

def test_exit_solve(tmp_path):
    assert cli.main(["solve", str(tmp_path / "missing")]) == 2
    assert cli.main(["solve", str(tmp_path)]) == 2   # no images
    (tmp_path / "a.jpg").write_bytes(b"")
    assert cli.main(["solve", str(tmp_path), "--offset", "garbage"]) == 2


def test_exit_setup(monkeypatch):
    from astro_id import data, solve

    monkeypatch.setattr(data, "ensure_all", lambda: {"hipparcos": "ok", "ephemeris": "ok"})
    monkeypatch.setattr(solve, "find_astap", lambda: (None, None))
    assert cli.main(["setup"]) == 3                           # ASTAP missing
    monkeypatch.setattr(solve, "find_astap", lambda: ("astap.exe", None))
    monkeypatch.setattr(solve, "available_databases", lambda db: [])
    assert cli.main(["setup"]) == 0
    monkeypatch.setattr(data, "ensure_all", lambda: {"hipparcos": "download failed"})
    assert cli.main(["setup"]) == 3                           # a dataset not ok


def test_exit_site(tmp_path):
    from astro_id.site import run_site

    assert run_site(tmp_path / "nope", say=lambda s: None) == 2
    answers = iter(["32.6,-116.3", "", "", "n"])
    assert run_site(tmp_path, ask=lambda q: next(answers), say=lambda s: None, open_browser=lambda s: None,
                    elevation=lambda a, b: 100.0) == 1      # declined at Save?
    answers = iter(["32.6,-116.3", "", "", "y"])
    assert run_site(tmp_path, ask=lambda q: next(answers), say=lambda s: None, open_browser=lambda s: None,
                    elevation=lambda a, b: 100.0) == 0


def test_exit_session_and_cull(tmp_path, capsys):
    assert cli.main(["session"]) == 2                          # no photos
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["event"] == "error"
    assert cli.main(["session", str(tmp_path / "A.JPG"), "--offset", "soon"]) == 2
    mf = tmp_path / "m.json"
    mf.write_text("{}", encoding="utf-8")
    assert cli.main(["cull", "--metrics-file", str(mf)]) == 0
    assert cli.main(["cull", "--metrics-file", str(mf), "--cull-rule", '{"order": ["x"]}']) == 2
    assert cli.main(["cull", "--metrics-file", str(tmp_path / "gone.json")]) == 2


def test_exit_facet_install_uninstall(tmp_path, capsys):
    assert cli.main(["facet", "install", "--facet-data", str(tmp_path / "nope")]) == 2
    assert cli.main(["facet", "uninstall", "--facet-data", str(tmp_path / "nope")]) == 2
    assert cli.main(["facet", "uninstall", "--facet-data", str(tmp_path)]) == 0


def test_argparse_usage_error_exits_2():
    with pytest.raises(SystemExit) as e:
        cli.main(["solve"])
    assert e.value.code == 2


# ---- install hint ----------------------------------------------------------------------------------------------

def test_install_prints_older_facet_hint(tmp_path, capsys):
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"")
    data = tmp_path / "data"
    data.mkdir()
    assert cli.main(["facet", "install", "--facet-data", str(data), "--uv", str(uv)]) == 0
    err = capsys.readouterr().err
    assert "If Facet refuses the add-on, run again with --older-facet" in err
    assert "network" in json.loads((data / "addons" / "astro-id" / "addon.json").read_text())
    assert cli.main(["facet", "install", "--facet-data", str(data), "--uv", str(uv), "--older-facet"]) == 0
    assert "If Facet refuses" not in capsys.readouterr().err
    assert "network" not in json.loads((data / "addons" / "astro-id" / "addon.json").read_text())
