#!/usr/bin/env python3
"""dev-notes check (STANDARD.md section 6). Standard library only, Python 3.9+.

    python check.py [--repo PATH] [--base REF] [--strict] [--config FILE]
                    [--scrub-file FILE] [--format text|json]

Exit 0 = clean (warnings may print), 1 = errors (or warnings with --strict),
2 = bad usage. One line per finding: ERROR|WARN <check-id> <path>:<line> <message>

The check is mechanical. It can tell that a doc exists, is filled in and links
correctly. It cannot tell whether a doc is true; that is the review step's job.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote
from typing import Dict, List, Optional, Set, Tuple

REQUIRED_DOCS = ["README.md", "AGENTS.md", "docs/DETAILS.md", "docs/ISSUES.md"]
PLANS_DIR = "docs/plans/"
LEGACY_PLAN = "docs/PLAN.md"  # 1.0.0 layout, still accepted by D6
# Folders whose files never count as code (D6). Hidden folders are always skipped.
DEFAULT_SKIP = [
    "node_modules", ".venv", "venv", "build", "dist", "__pycache__", "docs",
    "dev-notes", "fixtures", "static", "assets", "img", "archive",
]
DEFAULT_EXTS = [".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".c", ".h", ".cpp",
                ".java", ".cs", ".rb", ".sh", ".ps1"]
# Folders never walked when collecting docs.
DOC_PRUNE = {"node_modules", "venv", ".venv", "build", "dist", "__pycache__"}
SIZE_LIMITS = {"root_readme": 250, "folder_readme": 80, "agents": 120,
               "plan": 150, "issues": 300, "other": 400}
SCRUB_EXTS = {".md", ".txt", ".yml", ".yaml", ".toml", ".json", ".py", ".cfg", ".ini"}


class ConfigError(ValueError):
    pass


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
        raw = parse_toml(read_text(path))
    overrides = {}
    limits = raw.get("size_limits", {})
    if not isinstance(limits, dict):
        raise ConfigError("size_limits must be a [size_limits] table")
    for key, value in limits.items():
        if not isinstance(value, int):
            raise ConfigError("[size_limits] %s must be a whole number, got %r" % (key, value))
        overrides[str(key)] = value
    return {
        "skip_folders": list(DEFAULT_SKIP) + [str(s) for s in raw.get("skip_folders", [])],
        "code_extensions": [str(e).lower() for e in raw.get("code_extensions", DEFAULT_EXTS)],
        "extra_required_docs": [str(d) for d in raw.get("extra_required_docs", [])],
        "limits": dict(SIZE_LIMITS),
        "size_overrides": overrides,
    }


# ---------------------------------------------------------------- file helpers

def read_text(path: Path) -> str:
    """UTF-8, tolerant of a byte-order mark (Windows editors write one)."""
    return path.read_text(encoding="utf-8-sig", errors="replace")


def read_lines(path: Path) -> List[str]:
    try:
        return read_text(path).splitlines()
    except OSError:
        return []


def is_link(path: str) -> bool:
    """A symlink or a Windows junction. Never walked: it may point outside the repo."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


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


def walk(repo: Path, hidden: bool = False):
    """os.walk without links, pruned folders, the vendored dev-notes/ and (by default) hidden folders."""
    for dirpath, dirnames, filenames in os.walk(repo):
        rel_dir = Path(dirpath).relative_to(repo).as_posix()
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in DOC_PRUNE and d != ".git" and (hidden or not d.startswith("."))
            and not (rel_dir == "." and d == "dev-notes")
            and not is_link(os.path.join(dirpath, d)))
        yield rel_dir, sorted(filenames)


def doc_files(repo: Path) -> List[str]:
    """Docs checked by D3/D4/D10: root *.md, docs/**/*.md, every README.md. Relative posix paths."""
    found = []
    for rel_dir, filenames in walk(repo):
        for name in filenames:
            if not name.lower().endswith(".md"):
                continue
            rel = name if rel_dir == "." else rel_dir + "/" + name
            if rel_dir == "." or rel_dir == "docs" or rel_dir.startswith("docs/") or name == "README.md":
                found.append(rel)
    return found


def in_fences(lines: List[str]):
    """Yield (line number, line) for lines outside fenced code blocks."""
    fence = False
    for no, line in enumerate(lines, 1):
        if re.match(r"^\s{0,3}(```|~~~)", line):
            fence = not fence
            continue
        if not fence:
            yield no, line


# ---------------------------------------------------------------- D1

def check_d1(repo: Path, cfg: dict, out: List[Finding]) -> None:
    for rel in REQUIRED_DOCS + cfg["extra_required_docs"]:
        if not (repo / rel).is_file():
            out.append(Finding("ERROR", "D1", rel, 1, "required file is missing"))


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
    for _no, line in in_fences(read_lines(path)):
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            base = slugify(m.group(1))
            n = slugs.get(base, 0)
            slugs[base] = n + 1
            result.add(base if n == 0 else "%s-%d" % (base, n))
    return result


def links_in(path: Path) -> List[Tuple[int, str]]:
    found = []
    for no, line in in_fences(read_lines(path)):
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
    if rel == "AGENTS.md":
        return lim["agents"]
    if rel == LEGACY_PLAN or rel.startswith(PLANS_DIR):
        return lim["plan"]
    if rel == "docs/ISSUES.md":
        return lim["issues"]
    return lim["other"]


def check_d4(repo: Path, docs: List[str], cfg: dict, out: List[Finding]) -> None:
    for rel in docs:
        n = len(read_lines(repo / rel))
        limit = limit_for(rel, cfg)
        if n > limit:
            out.append(Finding("WARN", "D4", rel, 1, "%d lines, limit is %d" % (n, limit)))


# ---------------------------------------------------------------- git helpers (D6, D8)

def _git(repo: Path, *args: str) -> Tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off"] + list(args),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return 127, ""
    return p.returncode, p.stdout


def changed_files(repo: Path, base: str) -> Optional[Set[str]]:
    """Files changed since the merge-base with BASE, working tree and untracked files included."""
    rc, mb = _git(repo, "merge-base", base, "HEAD")
    if rc != 0 or not mb.strip():
        return None
    rc, diff = _git(repo, "diff", "--name-status", mb.strip())
    if rc != 0:
        return None
    changed: Set[str] = set()
    for line in diff.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            changed.add(parts[-1])
    rc, untracked = _git(repo, "ls-files", "--others", "--exclude-standard")
    changed.update(p for p in untracked.splitlines() if p)
    return changed


def is_plan(path: str) -> bool:
    return path == LEGACY_PLAN or (path.startswith(PLANS_DIR) and path.lower().endswith(".md"))


def check_d6(repo: Path, base: str, cfg: dict, out: List[Finding]) -> None:
    changed = changed_files(repo, base)
    if changed is None:
        out.append(Finding("WARN", "D6", ".", 1, "cannot compare with base '%s'; D6 skipped" % base))
        return
    exts = set(cfg["code_extensions"])

    def is_code(p: str) -> bool:
        d = p.rsplit("/", 1)[0] if "/" in p else "."
        if d != "." and is_skipped(d, cfg["skip_folders"]):
            return False
        return os.path.splitext(p)[1].lower() in exts

    code = sorted(p for p in changed if is_code(p))
    if code and not any(is_plan(p) for p in changed):
        out.append(Finding("WARN", "D6", PLANS_DIR, 1,
                           "code changed (%s%s) but no plan under docs/plans/ was added or updated"
                           % (code[0], ", ..." if len(code) > 1 else "")))


# ---------------------------------------------------------------- D8

def load_scrub(path: Path) -> List["re.Pattern"]:
    pats = []
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            pats.append(re.compile(line, re.I))
        except re.error:
            pats.append(re.compile(re.escape(line), re.I))
    return pats


def scrub_files(repo: Path) -> List[str]:
    """Files D8 reads: what git tracks (so ignored private notes are left alone); every file if not a git repo."""
    rc, listed = _git(repo, "ls-files")
    if rc == 0:
        files = [p for p in listed.splitlines() if p]
    else:
        files = [name if rel_dir == "." else rel_dir + "/" + name
                 for rel_dir, names in walk(repo, hidden=True) for name in names]
    return sorted(p for p in files
                  if not p.startswith("dev-notes/") and not is_link(str(repo / p))
                  and (os.path.splitext(p)[1].lower() in SCRUB_EXTS
                       or p.rsplit("/", 1)[-1] in ("LICENSE", "VERSION")))


def check_d8(repo: Path, patterns: List["re.Pattern"], out: List[Finding]) -> None:
    for rel in scrub_files(repo):
        for no, line in enumerate(read_lines(repo / rel), 1):
            for pat in patterns:
                if pat.search(line):
                    out.append(Finding("ERROR", "D8", rel, no, "scrub pattern matched: " + pat.pattern))
                    break


# ---------------------------------------------------------------- D9

def check_d9(repo: Path, out: List[Finding]) -> None:
    agents = repo / "AGENTS.md"
    if not agents.is_file():
        return  # D1 reports it
    m = re.search(r"dev-notes:begin v(\S+)", read_text(agents))
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
    have = read_text(vfile).strip()
    if have != stated:
        out.append(Finding("WARN", "D9", "AGENTS.md", 1,
                           "AGENTS.md states v%s but VERSION is %s" % (stated, have)))


# ---------------------------------------------------------------- D10

_PLACEHOLDER = re.compile(r"<[^<>]*[A-Za-z][^<>]*>")


def template_dir(repo: Path) -> Optional[Path]:
    vendored = repo / "dev-notes" / "templates"
    if vendored.is_dir():
        return vendored
    if (repo / "STANDARD.md").is_file() and (repo / "templates").is_dir():
        return repo / "templates"  # the standard's own repo
    return None


def placeholder_lines(templates: Path) -> Set[str]:
    """Template lines that still hold an <angle bracket> placeholder, stripped."""
    found: Set[str] = set()
    for path in sorted(templates.glob("*.md")):
        for _no, line in in_fences(read_lines(path)):
            s = line.strip()
            if len(s) >= 8 and not s.startswith("<!--") and _PLACEHOLDER.search(s):
                found.add(s)
    return found


def check_d10(repo: Path, docs: List[str], out: List[Finding]) -> None:
    """A doc line identical to a template placeholder line was never filled in."""
    templates = template_dir(repo)
    if templates is None:
        return
    marks = placeholder_lines(templates)
    own = templates.relative_to(repo).as_posix() + "/"
    for rel in docs:
        if rel.startswith(own):
            continue
        hits = [(no, line.strip()) for no, line in in_fences(read_lines(repo / rel)) if line.strip() in marks]
        if hits:
            out.append(Finding("ERROR", "D10", rel, hits[0][0],
                               "%d unfilled template line(s), first: %s" % (len(hits), hits[0][1][:60])))


# ---------------------------------------------------------------- driver

def run(repo: Path, base: Optional[str] = None, config: Optional[Path] = None,
        scrub: Optional[Path] = None) -> List[Finding]:
    if config is None and (repo / ".dev-notes.toml").is_file():
        config = repo / ".dev-notes.toml"
    cfg = load_config(config)
    docs = doc_files(repo)
    out: List[Finding] = []
    check_d1(repo, cfg, out)
    check_d3(repo, docs, out)
    check_d4(repo, docs, cfg, out)
    if base:
        check_d6(repo, base, cfg, out)
    if scrub:
        check_d8(repo, load_scrub(scrub), out)
    check_d9(repo, out)
    check_d10(repo, docs, out)
    out.sort(key=lambda f: (int(f.check[1:]), f.path, f.line))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="check.py", description="dev-notes check")
    ap.add_argument("--repo", default=".", help="repo root (default: current folder)")
    ap.add_argument("--base", help="git ref to compare with (enables D6)")
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
    try:
        findings = run(repo, args.base, config, scrub)
    except ConfigError as e:
        print("check.py: bad config: %s" % e, file=sys.stderr)
        return 2
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
