"""Check the user-facing docs: every relative link and image resolves (files and #anchors), and no personal paths.

    uv run python tools/check_docs.py          # exit 0 clean, 1 with one line per problem

Scans README.md, the other top-level *.md files, docs/*.md and examples/README.md. Markdown links and images,
and HTML src/href attributes, are checked; http(s) and mailto links are not fetched. Anchors use GitHub's heading
slugs (lowercase, punctuation dropped, spaces to hyphens, -1/-2 for repeats) plus any id="..." in the file.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
# Text a stranger should never see in the docs: private network addresses, the author's home folder, private names.
PERSONAL = [re.compile(p, re.I) for p in (r"192\.168\.", r"raysa", r"Users\\", r"tds-november")]

MD_LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML_ATTR = re.compile(r"""\b(?:src|href)\s*=\s*["']([^"']+)["']""", re.I)
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
HTML_ID = re.compile(r"""\b(?:id|name)\s*=\s*["']([^"']+)["']""", re.I)


def doc_files() -> list[Path]:
    files = sorted(ROOT.glob("*.md")) + sorted((ROOT / "docs").glob("*.md"))
    ex = ROOT / "examples" / "README.md"
    return files + ([ex] if ex.exists() else [])


def strip_code(text: str, inline: bool = True) -> list[tuple[int, str]]:
    """(line number, line) outside fenced code blocks, with inline code spans blanked (unless inline=False)."""
    out, fence = [], None
    for n, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^\s*(```+|~~~+)", line)
        if m:
            if fence is None:
                fence = m.group(1)[0] * 3
            elif line.strip().startswith(fence):
                fence = None
            continue
        if fence is None:
            out.append((n, re.sub(r"`[^`]*`", lambda c: " " * len(c.group(0)), line) if inline else line))
    return out


def slug(heading: str) -> str:
    h = re.sub(r"<[^>]+>", "", heading)                      # inline HTML
    h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", h)            # links -> their text
    h = h.replace("`", "").lower()
    h = "".join(ch for ch in h if ch in " -_" or unicodedata.category(ch)[0] in "LN")
    return h.replace(" ", "-")


_anchor_cache: dict[Path, set[str]] = {}


def anchors(path: Path) -> set[str]:
    if path not in _anchor_cache:
        seen: dict[str, int] = {}
        out: set[str] = set()
        text = path.read_text(encoding="utf-8")
        for _, line in strip_code(text, inline=False):
            m = HEADING.match(line)
            if m:
                s = slug(m.group(2))
                k = seen.get(s, 0)
                out.add(s if k == 0 else f"{s}-{k}")
                seen[s] = k + 1
        out.update(HTML_ID.findall(text))
        _anchor_cache[path] = out
    return _anchor_cache[path]


def check_file(md: Path) -> list[str]:
    problems: list[str] = []
    rel = md.relative_to(ROOT).as_posix()
    raw = md.read_text(encoding="utf-8")
    for n, line in enumerate(raw.splitlines(), 1):
        for pat in PERSONAL:
            if pat.search(line):
                problems.append(f"{rel}:{n}: personal text matches {pat.pattern!r}")
    for n, line in strip_code(raw):
        targets = MD_LINK.findall(line) + HTML_ATTR.findall(line)
        for t in targets:
            if re.match(r"^[a-z][a-z0-9+.-]*:", t, re.I):     # http:, https:, mailto:, obsidian: ...
                continue
            target, _, frag = t.partition("#")
            dest = md if not target else (md.parent / unquote(target)).resolve()
            if not dest.exists():
                problems.append(f"{rel}:{n}: missing file {t}")
                continue
            if frag and dest.suffix.lower() == ".md" and unquote(frag) not in anchors(dest):
                problems.append(f"{rel}:{n}: missing anchor {t}")
    return problems


def main() -> int:
    problems = [p for md in doc_files() for p in check_file(md)]
    for p in problems:
        print(p)
    print(f"{len(doc_files())} docs checked, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
