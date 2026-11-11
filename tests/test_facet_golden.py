"""Golden annotation payloads, validated by Facet's own ``db/annotations.validate_payload``.

The validator is loaded by file path from the Facet checkout of the annotations branch (``FACET_ANNOT_DIR``,
default ``~/facet-annot``; else ``FACET_DIR``, default ``~/facet-wintest``); skipped when neither has it.
``tests/fixtures/annotation_golden.json`` is the payload the sidecar sends for the synthetic test frame (engine
annotations + the top-level ``quality`` object). Regenerate it with ``ASTRO_ID_WRITE_GOLDEN=1``.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from astro_id.facet.sync import annotation_payload
from test_annotations import doc  # noqa: F401  (module fixture: a synthetic solve)
from test_synthetic import frame_dir  # noqa: F401

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "fixtures" / "annotation_golden.json"


def _validator():
    for d in (os.environ.get("FACET_ANNOT_DIR"), str(Path.home() / "facet-annot"), os.environ.get("FACET_DIR"),
              str(Path.home() / "facet-wintest")):
        if d and (Path(d) / "db" / "annotations.py").is_file():
            spec = importlib.util.spec_from_file_location("facet_annotations_golden", Path(d) / "db" / "annotations.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    pytest.skip("no Facet checkout with db/annotations.py (FACET_ANNOT_DIR / FACET_DIR)")


def test_golden_file_validates():
    mod = _validator()
    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    text = mod.validate_payload(payload)
    assert json.loads(text)["quality"]["grade"] == payload["quality"]["grade"]
    mod.validate_source(payload["source"])
    assert len(text.encode("utf-8")) < mod.MAX_PAYLOAD_BYTES


def test_live_payload_validates_and_matches_golden_shape(doc):  # noqa: F811
    mod = _validator()
    payload = annotation_payload(doc)
    assert isinstance(payload, dict) and "quality" in payload
    mod.validate_payload(payload)
    if os.environ.get("ASTRO_ID_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(payload) == sorted(golden)
    assert [g["id"] for g in payload["groups"]] == [g["id"] for g in golden["groups"]]
    assert {i["id"] for i in payload["items"]} == {i["id"] for i in golden["items"]}
    assert sorted(payload["quality"]) == sorted(golden["quality"])


@pytest.mark.parametrize("mutate, message", [
    (lambda p: p.update(version=2), "version"),
    (lambda p: p.pop("image"), "image"),
    (lambda p: p["items"][0].update(shape="hexagon"), "shape"),
])
def test_validator_rejects_broken_payloads(mutate, message):
    mod = _validator()
    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    mutate(payload)
    with pytest.raises(mod.AnnotationError, match=message):
        mod.validate_payload(payload)
