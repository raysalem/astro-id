"""The product's words and the exit codes, in one place so every ``--help`` says the same thing (CONTRACT.md has the
same table; ``tests/test_help_text.py`` keeps them in step)."""
from __future__ import annotations

VOCAB = (
    "Words:\n"
    "  object tags  what astro-id finds in a photo (a nebula, a planet, a constellation...)\n"
    "  status tags  plate-solved, plate-solve failed, astro-review\n"
    "  sky labels   the names drawn over the photo (the labeled JPG, or Facet's annotation layer)\n"
    "An older Facet stores object tags as ordinary tags; the meaning is the same."
)

# subcommand -> {exit code: meaning}; every code was read from the code that returns it.
EXIT_CODES: dict[str, dict[int, str]] = {
    "solve": {0: "the run completed (frames that did not solve are in the report, not an error)",
              2: "usage error: target not found, a bad flag value, or no images in the folder"},
    "label": {0: "labeled (or every photo already had its output)", 1: "nothing labeled (no photo solved)",
              2: "setup or usage problem: target not found, a bad option, no photos, or ASTAP not found"},
    "check": {0: "all required checks passed", 1: "a required check failed (marked with x)"},
    "site": {0: "saved", 1: "not saved (you answered no at the Save? prompt)", 2: "the folder does not exist"},
    "setup": {0: "reference data is ready and ASTAP was found",
              3: "ASTAP was not found (set ASTAP_PATH) or a reference dataset is not ok"},
    "session": {0: "the session ran (frames may have failed; see the events)",
                2: "a bad option or no photos (after an error event on stdout)"},
    "cull": {0: "verdicts printed", 2: "bad metrics file or cull rule"},
    "facet sync": {0: "the run completed", 2: "usage, login or connection error"},
    "facet status": {0: "ledger printed", 2: "usage, login or connection error"},
    "facet undo": {0: "done (or dry run)", 2: "usage, login or connection error"},
    "facet adopt": {0: "done (or dry run)", 2: "usage, login or connection error"},
    "facet serve": {0: "stopped normally (Ctrl+C, or Facet ended)", 2: "missing or bad environment / connection error"},
    "facet install": {0: "manifest written", 2: "Facet data folder not found, uv not found, or a bad --project"},
    "facet uninstall": {0: "manifest removed (or there was none)", 2: "Facet data folder not found"},
}
ARGPARSE_EXIT = "Every subcommand also exits 2 when the command line itself cannot be parsed."


def exit_epilog(*cmds: str) -> str:
    lines = ["exit codes:"]
    for c in cmds:
        for code, text in EXIT_CODES[c].items():
            lines.append(f"  {code}  {text}")
    return "\n".join(lines)
