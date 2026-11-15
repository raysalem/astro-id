"""Assert `astro-id solve examples/ --json` output against examples/expected.json.

Usage: python tests/check_examples.py <solve-output.json> [expected.json]

expected.json maps a file name to {"solved": true, "objects": [<tag or {"tag": ..}>...]} (the key may also be
"tags"); a bare list of tags is accepted too. Every listed object must be found at confidence >= MIN_CONF.
Exits 0 (skipping) when expected.json does not exist.
"""
import json
import sys
from pathlib import Path

MIN_CONF = 80


def _tags(spec):
    if isinstance(spec, list):
        items = spec
    else:
        items = spec.get("objects") or spec.get("tags") or []
    return [(i["tag"] if isinstance(i, dict) else i).strip().lower() for i in items]


def main(argv):
    out_path = Path(argv[1])
    exp_path = Path(argv[2]) if len(argv) > 2 else Path("examples/expected.json")
    if not exp_path.is_file():
        print(f"SKIP: {exp_path} not found")
        return 0
    expected = json.loads(exp_path.read_text(encoding="utf-8"))
    results = json.loads(out_path.read_text(encoding="utf-8"))["results"]
    by_name = {Path(r["file"]).name: r for r in results}
    bad = []
    for name, spec in expected.items():
        r = by_name.get(name)
        if r is None:
            bad.append(f"{name}: not in solve output")
            continue
        want_solved = spec.get("solved", True) if isinstance(spec, dict) else True
        if bool(r.get("solved")) != bool(want_solved):
            bad.append(f"{name}: solved={r.get('solved')} expected {want_solved}")
            continue
        conf = {o["tag"].lower(): o.get("confidence", 0) for o in r.get("objects", [])}
        for tag in _tags(spec):
            if conf.get(tag, -1) < MIN_CONF:
                bad.append(f"{name}: '{tag}' confidence {conf.get(tag, 'absent')} < {MIN_CONF}")
        print(f"{name}: solved={r.get('solved')} objects={len(conf)}")
    for b in bad:
        print("FAIL", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
