#!/usr/bin/env python3
"""dev-notes drift check (STANDARD.md section 6). Standard library only, Python 3.9+.

    python check.py [--repo PATH] [--base REF] [--strict] [--config FILE]
                    [--scrub-file FILE] [--format text|json]

Exit 0 = clean (warnings may print), 1 = errors (or warnings with --strict),
2 = bad usage. One line per finding: ERROR|WARN <check-id> <path>:<line> <message>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote
from typing import Dict, List, Optional, Set, Tuple

REQUIRED_DOCS = [
    "README.md", "AGENTS.md",
    "docs/BRIEF.md", "docs/DETAILS.md", "docs/FEATURES.md", "docs/USAGE.md",
    "docs/SETUP.md", "docs/REQUIREMENTS.md", "docs/TESTING.md", "docs/PLAN.md",
    "docs/TODO.md", "docs/ISSUES.md",
]
# Folders that are never "code folders" (D2, D5, D7). Hidden folders are always skipped.
DEFAULT_SKIP = [
    "node_modules", ".venv", "venv", "build", "dist", "__pycache__", "docs",
    "dev-notes", "fixtures", "static", "assets", "img", "archive",
]
DEFAULT_EXTS = [".py", ".ts", ".js", ".go", ".rs"]
PACKAGE_MARKERS = {"__init__.py", "package.json", "go.mod", "Cargo.toml"}
TOP_LEVEL_CODE_DIRS = {"tests", "tools", "examples", "plugins"}
# Folders never walked when collecting docs.
DOC_PRUNE = {"node_modules", "venv", ".venv", "build", "dist", "__pycache__"}
SIZE_LIMITS = {"root_readme": 250, "folder_readme": 80, "BRIEF.md": 60,
               "PLAN.md": 150, "ISSUES.md": 300, "other": 400}


class Finding:
    def __init__(self, level: str, check: str, path: str, line: int, message: str):
        self.level, self.check, self.path, self.line, self.message = level, check, path, line, message

    def as_dict(self) -> dict:
        return {"level": self.level, "check": self.check, "path": self.path,
                "line": self.line, "message": self.message}

    def text(self) -> str:
        return "%s %s %s:%d %s" % (self.level, self.check, self.path, self.line, self.message)


# ---------------------------------------------------------------- config

def _strip_comment(line: str) -> str:
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).strip()


def _toml_value(raw: str):
    raw = raw.strip()
    if raw.startswith("["):
        return [a or b for a, b in re.findall(r'"([^"]*)"|\'([^\']*)\'', raw)]
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw.strip("\"'")


def parse_toml(text: str) -> dict:
    """Minimal TOML subset: top-level keys, one-level [tables], strings, ints, string lists."""
    data: dict = {}
    table = data
    pending = ""
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if pending:
            pending += " " + line
            if pending.count("[") <= pending.count("]"):
                line, pending = pending, ""
            else:
                continue
        if not line:
            continue
        m = re.fullmatch(r"\[([^\[\]]+)\]", line)
        if m:
            table = data.setdefault(m.group(1).strip(), {})
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().strip("\"'")
        value = value.strip()
        if value.startswith("[") and value.count("[") > value.count("]"):
            pending = key + " = " + value
            continue
        table[key] = _toml_value(value)
    return data


def load_config(path: Optional[Path]) -> dict:
    raw: dict = {}
    if path is not None:
        raw = parse_toml(path.read_text(encoding="utf-8"))
    skip = list(DEFAULT_SKIP) + [str(s) for s in raw.get("skip_folders", [])]
    limits = dict(SIZE_LIMITS)
    return {
        "skip_folders": skip,
        "code_extensions": [str(e).lower() for e in raw.get("code_extensions", DEFAULT_EXTS)],
        "extra_required_docs": [str(d) for d in raw.get("extra_required_docs", [])],
        "limits": limits,
        "size_overrides": {str(k): int(v) for k, v in raw.get("size_limits", {}).items()},
    }


# ---------------------------------------------------------------- file helpers

def is_skipped(rel: str, skip: List[str]) -> bool:
    parts = rel.split("/")
    if any(p.startswith(".") and p not in (".", "..") for p in parts):
        return True
    for entry in skip:
        entry = entry.strip("/")
        if "/" in entry:
            if rel == entry or rel.startswith(entry + "/"):
                return True
        elif entry in parts:
            return True
    return False


def read_lines(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def doc_files(repo: Path) -> List[str]:
    """Docs checked by D3/D4: root *.md, docs/**/*.md, every README.md. Relative posix paths."""
    found = []
    for dirpath, dirnames, filenames in os.walk(repo):
        rel_dir = Path(dirpath).relative_to(repo).as_posix()
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and d not in DOC_PRUNE
                             and not (rel_dir == "." and d == "dev-notes"))
        for name in sorted(filenames):
            if not name.lower().endswith(".md"):
                continue
            rel = name if rel_dir == "." else rel_dir + "/" + name
            if rel_dir == "." or rel_dir == "docs" or rel_dir.startswith("docs/") or name == "README.md":
                found.append(rel)
    return found


# ---------------------------------------------------------------- D1

def check_d1(repo: Path, cfg: dict, out: List[Finding]) -> None:
    for rel in REQUIRED_DOCS + cfg["extra_required_docs"]:
        if not (repo / rel).is_file():
            out.append(Finding("ERROR", "D1", rel, 1, "required file is missing"))


# ---------------------------------------------------------------- D2

def find_code_folders(repo: Path, cfg: dict) -> List[str]:
    exts = set(cfg["code_extensions"])
    res = []
    for dirpath, dirnames, filenames in os.walk(repo):
        rel = Path(dirpath).relative_to(repo).as_posix()
        dirnames[:] = sorted(d for d in dirnames
                             if not is_skipped((d if rel == "." else rel + "/" + d), cfg["skip_folders"]))
        if rel == ".":
            continue
        top = "/" not in rel
        has_code = any(f in PACKAGE_MARKERS or os.path.splitext(f)[1].lower() in exts for f in filenames)
        if has_code or (top and rel in TOP_LEVEL_CODE_DIRS):
            res.append(rel)
    return res


def check_d2(repo: Path, cfg: dict, out: List[Finding]) -> None:
    for folder in find_code_folders(repo, cfg):
        readme = repo / folder / "README.md"
        if not readme.is_file():
            out.append(Finding("ERROR", "D2", folder + "/", 1, "code folder has no README.md"))
            continue
        text = readme.read_text(encoding="utf-8", errors="replace")
        for heading in ("Brief", "Tests"):
            if not re.search(r"^##\s+%s\b" % heading, text, re.M):
                out.append(Finding("ERROR", "D2", folder + "/README.md", 1,
                                   "missing '## %s' section" % heading))


# ---------------------------------------------------------------- D3

_INLINE = re.compile(r"!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^)\s]+))(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
_REFDEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(\S+)")
_HTMLATTR = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.I)
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def slugify(heading: str) -> str:
    h = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    h = re.sub(r"[`*~]", "", h)
    h = h.strip().lower()
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def anchors_of(path: Path) -> Set[str]:
    slugs: Dict[str, int] = {}
    result: Set[str] = set()
    fence = False
    for line in read_lines(path):
        if re.match(r"^\s{0,3}(```|~~~)", line):
            fence = not fence
            continue
        if fence:
            continue
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            base = slugify(m.group(1))
            n = slugs.get(base, 0)
            slugs[base] = n + 1
            result.add(base if n == 0 else "%s-%d" % (base, n))
    return result


def links_in(path: Path) -> List[Tuple[int, str]]:
    found = []
    fence = False
    for no, line in enumerate(read_lines(path), 1):
        if re.match(r"^\s{0,3}(```|~~~)", line):
            fence = not fence
            continue
        if fence:
            continue
        clean = re.sub(r"`[^`]*`", "", line)
        for m in _INLINE.finditer(clean):
            found.append((no, m.group(1) or m.group(2)))
        m = _REFDEF.match(clean)
        if m:
            found.append((no, m.group(1)))
        for m in _HTMLATTR.finditer(clean):
            found.append((no, m.group(1)))
    return found


def check_d3(repo: Path, docs: List[str], out: List[Finding]) -> None:
    anchor_cache: Dict[Path, Set[str]] = {}
    for rel in docs:
        src = repo / rel
        for line, target in links_in(src):
            if _SCHEME.match(target) or target.startswith("//"):
                continue
            path_part, _, anchor = target.partition("#")
            path_part, anchor = unquote(path_part), unquote(anchor)
            if path_part == "":
                dest = src
            elif path_part.startswith("/"):
                dest = repo / path_part.lstrip("/")
            else:
                dest = src.parent / path_part
            if not dest.exists():
                out.append(Finding("ERROR", "D3", rel, line, "broken link: " + target))
                continue
            if anchor and dest.is_file() and dest.suffix.lower() == ".md":
                if dest not in anchor_cache:
                    anchor_cache[dest] = anchors_of(dest)
                if anchor.lower() not in anchor_cache[dest]:
                    out.append(Finding("ERROR", "D3", rel, line, "broken anchor: " + target))


# ---------------------------------------------------------------- D4

def limit_for(rel: str, cfg: dict) -> int:
    if rel in cfg["size_overrides"]:
        return cfg["size_overrides"][rel]
    lim = cfg["limits"]
    name = rel.rsplit("/", 1)[-1]
    if rel == "README.md":
        return lim["root_readme"]
    if name == "README.md":
        return lim["folder_readme"]
    if rel in ("docs/BRIEF.md", "docs/PLAN.md", "docs/ISSUES.md"):
        return lim[name]
    return lim["other"]


def check_d4(repo: Path, docs: List[str], cfg: dict, out: List[Finding]) -> None:
    for rel in docs:
        n = len(read_lines(repo / rel))
        limit = limit_for(rel, cfg)
        if n > limit:
            out.append(Finding("WARN", "D4", rel, 1, "%d lines, limit is %d" % (n, limit)))


# ---------------------------------------------------------------- git helpers (D5, D6)

def _git(repo: Path, *args: str) -> Tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off"] + list(args),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return 127, ""
    return p.returncode, p.stdout


def changed_files(repo: Path, base: str) -> Optional[Tuple[Set[str], Set[str]]]:
    """Files changed since the merge-base with BASE (working tree included): (changed, added)."""
    rc, mb = _git(repo, "merge-base", base, "HEAD")
    if rc != 0 or not mb.strip():
        return None
    rc, diff = _git(repo, "diff", "--name-status", mb.strip())
    if rc != 0:
        return None
    changed: Set[str] = set()
    added: Set[str] = set()
    for line in diff.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        changed.add(path)
        if status.startswith("A"):
            added.add(path)
    rc, untracked = _git(repo, "ls-files", "--others", "--exclude-standard")
    for path in untracked.splitlines():
        if path:
            changed.add(path)
            added.add(path)
    return changed, added


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    return bool(re.match(r"test_.*\.\w+$", name) or re.search(r"_test\.\w+$|\.(test|spec)\.\w+$", name))


def check_d5_d6(repo: Path, base: str, docs: List[str], cfg: dict, out: List[Finding]) -> None:
    result = changed_files(repo, base)
    if result is None:
        out.append(Finding("WARN", "D5", ".", 1, "cannot compare with base '%s'; D5/D6 skipped" % base))
        return
    changed, added = result
    exts = set(cfg["code_extensions"])

    def is_code(p: str) -> bool:
        d = p.rsplit("/", 1)[0] if "/" in p else "."
        if d != "." and is_skipped(d, cfg["skip_folders"]):
            return False
        return os.path.splitext(p)[1].lower() in exts

    code = sorted(p for p in changed if is_code(p))
    docs_touched = any(p.startswith("docs/") for p in changed)
    flagged: Set[str] = set()
    for p in code:
        folder = p.rsplit("/", 1)[0] if "/" in p else ""
        while folder and not (repo / folder / "README.md").is_file():
            folder = folder.rsplit("/", 1)[0] if "/" in folder else ""
        if not folder or folder in flagged:
            continue
        readme = folder + "/README.md"
        if readme not in changed and not docs_touched:
            flagged.add(folder)
            out.append(Finding("WARN", "D5", readme, 1,
                               "code changed under %s/ but its README.md and docs/ did not" % folder))
    if code and "docs/PLAN.md" not in changed:
        out.append(Finding("WARN", "D6", "docs/PLAN.md", 1, "code changed but docs/PLAN.md was not touched"))
    named_in = ""
    for rel in docs:
        if rel == "docs/TESTING.md" or rel.endswith("README.md"):
            named_in += "\n".join(read_lines(repo / rel)) + "\n"
    for p in sorted(added):
        if is_test_file(p) and p.rsplit("/", 1)[-1] not in named_in:
            out.append(Finding("WARN", "D6", p, 1,
                               "new test file is not named in docs/TESTING.md or a folder README.md"))


# ---------------------------------------------------------------- D7

def _code_text(folder: Path, cfg: dict, repo: Path) -> str:
    exts = set(cfg["code_extensions"])
    chunks = []
    for dirpath, dirnames, filenames in os.walk(folder):
        rel = Path(dirpath).relative_to(repo).as_posix()
        dirnames[:] = [d for d in dirnames if not is_skipped(rel + "/" + d, cfg["skip_folders"])]
        for f in filenames:
            if os.path.splitext(f)[1].lower() in exts:
                try:
                    chunks.append((Path(dirpath) / f).read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
    return "\n".join(chunks)


def surface_rows(lines: List[str]) -> List[Tuple[int, str]]:
    """First cells of the table that follows 'Public surface' in a folder README."""
    rows: List[Tuple[int, str]] = []
    start = None
    for i, line in enumerate(lines):
        if "public surface" in line.lower():
            start = i + 1
            break
    if start is None:
        return rows
    seen_table = False
    for i in range(start, len(lines)):
        line = lines[i].strip()
        if line.startswith("|"):
            seen_table = True
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells or re.fullmatch(r"[:\- ]*", cells[0]) or cells[0].lower() == "name":
                continue
            rows.append((i + 1, cells[0]))
        elif seen_table:
            break
        elif line.startswith("#"):
            break
    return rows


def check_d7(repo: Path, docs: List[str], cfg: dict, out: List[Finding]) -> None:
    for rel in docs:
        if not rel.endswith("README.md") or rel == "README.md":
            continue
        folder = repo / rel.rsplit("/", 1)[0]
        lines = read_lines(repo / rel)
        text = None
        for line_no, cell in surface_rows(lines):
            tokens = re.findall(r"`([^`]+)`", cell) or [cell]
            for tok in tokens:
                tok = tok.strip()
                if not tok or tok.startswith("<") or "*" in tok or tok in ("…", "..."):
                    continue
                word = tok.split()[0].rstrip(":,")
                if text is None:
                    text = _code_text(folder, cfg, repo)
                if word.startswith("-"):
                    ok = word in text
                else:
                    word = re.sub(r"\(.*\)$", "", word)
                    ident = re.split(r"[.:/]+", word)[-1]
                    ok = (folder / word).exists() or ident == "" or \
                        re.search(r"\b%s\b" % re.escape(ident), text) is not None
                if not ok:
                    out.append(Finding("WARN", "D7", rel, line_no,
                                       "'%s' is in Public surface but not found in the folder's code" % word))


# ---------------------------------------------------------------- D8

def load_scrub(path: Path) -> List["re.Pattern"]:
    pats = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            pats.append(re.compile(line, re.I))
        except re.error:
            pats.append(re.compile(re.escape(line), re.I))
    return pats


def check_d8(repo: Path, patterns: List["re.Pattern"], out: List[Finding]) -> None:
    exts = {".md", ".txt", ".yml", ".yaml", ".toml", ".json", ".py", ".cfg", ".ini"}
    for dirpath, dirnames, filenames in os.walk(repo):
        rel_dir = Path(dirpath).relative_to(repo).as_posix()
        dirnames[:] = sorted(d for d in dirnames if d not in DOC_PRUNE and d != ".git"
                             and not (rel_dir == "." and d == "dev-notes"))
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() not in exts and name not in ("LICENSE", "VERSION"):
                continue
            rel = name if rel_dir == "." else rel_dir + "/" + name
            for no, line in enumerate(read_lines(Path(dirpath) / name), 1):
                for pat in patterns:
                    if pat.search(line):
                        out.append(Finding("ERROR", "D8", rel, no, "scrub pattern matched: " + pat.pattern))
                        break


# ---------------------------------------------------------------- D9

def check_d9(repo: Path, out: List[Finding]) -> None:
    agents = repo / "AGENTS.md"
    if not agents.is_file():
        return  # D1 reports it
    text = agents.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"dev-notes:begin v(\S+)", text)
    if not m:
        out.append(Finding("WARN", "D9", "AGENTS.md", 1, "no dev-notes marked block"))
        return
    stated = m.group(1)
    vfile = repo / "dev-notes" / "VERSION"
    if not vfile.is_file() and (repo / "STANDARD.md").is_file():
        vfile = repo / "VERSION"  # the standard's own repo
    if not vfile.is_file():
        out.append(Finding("WARN", "D9", "dev-notes/VERSION", 1, "VERSION file is missing"))
        return
    have = vfile.read_text(encoding="utf-8").strip()
    if have != stated:
        out.append(Finding("WARN", "D9", "AGENTS.md", 1,
                           "AGENTS.md states v%s but VERSION is %s" % (stated, have)))


# ---------------------------------------------------------------- driver

def run(repo: Path, base: Optional[str] = None, config: Optional[Path] = None,
        scrub: Optional[Path] = None) -> List[Finding]:
    if config is None and (repo / ".dev-notes.toml").is_file():
        config = repo / ".dev-notes.toml"
    cfg = load_config(config)
    docs = doc_files(repo)
    out: List[Finding] = []
    check_d1(repo, cfg, out)
    check_d2(repo, cfg, out)
    check_d3(repo, docs, out)
    check_d4(repo, docs, cfg, out)
    if base:
        check_d5_d6(repo, base, docs, cfg, out)
    check_d7(repo, docs, cfg, out)
    if scrub:
        check_d8(repo, load_scrub(scrub), out)
    check_d9(repo, out)
    out.sort(key=lambda f: (f.check, f.path, f.line))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="check.py", description="dev-notes drift check")
    ap.add_argument("--repo", default=".", help="repo root (default: current folder)")
    ap.add_argument("--base", help="git ref to compare with (enables D5, D6)")
    ap.add_argument("--strict", action="store_true", help="warnings fail the run")
    ap.add_argument("--config", help="config file (default: <repo>/.dev-notes.toml)")
    ap.add_argument("--scrub-file", help="file of regex patterns, one per line (enables D8)")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args(argv)  # bad usage exits 2

    repo = Path(args.repo)
    if not repo.is_dir():
        print("check.py: --repo is not a folder: %s" % args.repo, file=sys.stderr)
        return 2
    repo = repo.resolve()
    config = Path(args.config) if args.config else None
    scrub = Path(args.scrub_file) if args.scrub_file else None
    for label, p in (("--config", config), ("--scrub-file", scrub)):
        if p is not None and not p.is_file():
            print("check.py: %s file not found: %s" % (label, p), file=sys.stderr)
            return 2
    findings = run(repo, args.base, config, scrub)
    errors = sum(1 for f in findings if f.level == "ERROR")
    warns = len(findings) - errors
    if args.format == "json":
        print(json.dumps({"errors": errors, "warnings": warns,
                          "findings": [f.as_dict() for f in findings]}, indent=2))
    else:
        for f in findings:
            print(f.text())
        print("dev-notes check: %d error(s), %d warning(s)" % (errors, warns), file=sys.stderr)
    return 1 if errors or (args.strict and warns) else 0


if __name__ == "__main__":
    sys.exit(main())
