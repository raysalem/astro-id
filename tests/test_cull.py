"""cull_rank / cull_session: pure verdicts from quality metrics."""
from astro_id.cull import cull_rank, cull_session, merge_rule, session_reference, validate_rule

REF = {"fwhm_px": 5.0, "detected": 5000}


def q(fwhm=5.0, elong=1.1, det=5000):
    return {"fwhm_px": fwhm, "elongation": elong, "detected": det}


def test_good_frame_keeps():
    r = cull_rank(q(), None, REF)
    assert r["decision"] == "keep" and r["reasons"] == []


def test_defocus_is_review_never_reject():
    r = cull_rank(q(fwhm=80.0), None, REF)
    assert r["decision"] == "review" and r["reasons"][0].startswith("focus:")
    r = cull_rank(q(fwhm=80.0), {"thresholds": {"focus_ratio": 1.1, "focus_px": 2}}, REF)
    assert r["decision"] == "review"


def test_trailing_review_and_reject():
    assert cull_rank(q(elong=1.8), None, REF)["decision"] == "review"
    assert cull_rank(q(elong=3.5), None, REF)["decision"] == "reject"
    assert cull_rank(q(elong=3.5), {"thresholds": {"trailing_reject": None}}, REF)["decision"] == "review"


def test_star_count():
    assert cull_rank(q(det=10), None, REF)["decision"] == "reject"
    r = cull_rank(q(det=1000), None, REF)
    assert r["decision"] == "review" and "20%" in r["reasons"][0]


def test_order_sets_checks_and_reason_order():
    m = q(fwhm=20.0, elong=1.8, det=1000)
    r = cull_rank(m, {"order": ["stars", "focus"]}, REF)
    assert [c["check"] for c in r["checks"]] == ["stars", "focus"]
    assert cull_rank(m, {"order": []}, REF)["decision"] == "keep"


def test_disabled_and_missing_metrics_and_solve():
    assert cull_rank(q(elong=9), {"enabled": False}, REF) == {"decision": "keep", "reasons": [], "checks": [],
                                                               "enabled": False}
    assert cull_rank(None, None, REF)["decision"] == "review"
    r = cull_rank(q(), {"order": ["solve"]}, REF, solved=False)
    assert r["decision"] == "review" and r["reasons"] == ["did not plate-solve"]


def test_session_reference_and_session():
    ms = {"a": q(4.0), "b": q(5.0), "c": q(6.0), "d": q(40.0), "e": None}
    ref = session_reference(list(ms.values()))
    assert ref["fwhm_px"] == 4.75 and ref["detected"] == 5000 and ref["frames"] == 4
    out = cull_session(ms, None)
    assert out["frames"]["d"]["decision"] == "review" and out["frames"]["a"]["decision"] == "keep"
    assert out["frames"]["e"]["decision"] == "review"
    assert out["rule"]["thresholds"]["focus_ratio"] == 1.5


def test_validate_rule():
    assert validate_rule(None) == [] and validate_rule(merge_rule(None)) == []
    assert validate_rule({"order": ["focus", "bogus"]})
    assert validate_rule({"thresholds": {"nope": 1}})
    assert validate_rule({"thresholds": {"focus_ratio": -1}})
    assert validate_rule({"enabled": "yes"})
    assert validate_rule([1])
