"""Culling verdicts from the per-frame quality metrics. Pure functions, no I/O.

``cull_rank(metrics, rule, reference)`` -> ``{"decision": "keep" | "review" | "reject", "reasons": [...],
"checks": [...]}``. ``cull_session`` computes the session reference (best focus, typical star count) and ranks
every frame against it. Nothing here touches a photo library: a caller shows the verdicts and applies them only
after the user confirms.

Rule (``astro-id.json`` ``"cull"``, the session ``--cull-rule``, or the Facet plugin's ``astro_id.cull``)::

    {"enabled": true, "order": ["focus", "trailing", "stars"], "thresholds": {...}}

``order`` lists the checks that run, most important first (reasons come out in that order; a check left out is
off). Checks: ``focus`` (half-flux diameter vs the session's best), ``trailing`` (elongation), ``stars``
(``quality.star_count``: the larger of the detected and the plate-solve matched stars), ``solve`` (frame did not plate-solve). A defocused frame is at most ``review``, never
``reject``: defocused frames are still useful for stacking.
"""
from __future__ import annotations

import math

from .quality import star_count

CHECKS = ("focus", "trailing", "stars", "solve")
DEFAULT_THRESHOLDS = {
    "focus_ratio": 1.5,         # fwhm_px / session reference >= this -> review (defocused)
    "focus_px": None,           # absolute fwhm_px >= this -> review (null = off)
    "trailing_review": 1.6,     # elongation >= this -> review
    "trailing_reject": 3.0,     # elongation >= this -> reject (null = never)
    "stars_ratio_review": 0.4,  # star_count / session reference < this -> review
    "stars_min": 20,            # star_count < this -> reject (clouds, lens cap, foreground only)
}
DEFAULT_RULE = {"enabled": True, "order": ["focus", "trailing", "stars"], "thresholds": DEFAULT_THRESHOLDS}
DECISIONS = ("keep", "review", "reject")
_RANK = {d: i for i, d in enumerate(DECISIONS)}


def validate_rule(rule) -> list[str]:
    """Problems with a cull rule (empty list = fine). Unknown threshold keys are problems too."""
    if rule is None:
        return []
    if not isinstance(rule, dict):
        return ["cull rule must be an object"]
    errs = []
    unknown = set(rule) - {"enabled", "order", "thresholds"}
    if unknown:
        errs.append(f"unknown cull keys: {', '.join(sorted(unknown))}")
    if "enabled" in rule and not isinstance(rule["enabled"], bool):
        errs.append("cull.enabled must be true or false")
    order = rule.get("order", DEFAULT_RULE["order"])
    if not isinstance(order, list) or not all(c in CHECKS for c in order):
        errs.append(f"cull.order must be a list of {', '.join(CHECKS)}")
    th = rule.get("thresholds", {})
    if not isinstance(th, dict):
        errs.append("cull.thresholds must be an object")
    else:
        for k, v in th.items():
            if k not in DEFAULT_THRESHOLDS:
                errs.append(f"unknown cull threshold {k!r}")
            elif v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
                errs.append(f"cull threshold {k} must be a number >= 0 or null")
    return errs


def merge_rule(rule: dict | None) -> dict:
    """The rule with defaults filled in (thresholds merged key by key)."""
    rule = rule or {}
    return {"enabled": rule.get("enabled", True), "order": list(rule.get("order", DEFAULT_RULE["order"])),
            "thresholds": {**DEFAULT_THRESHOLDS, **(rule.get("thresholds") or {})}}


def _median(v: list[float]) -> float | None:
    v = sorted(v)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def _quantile(v: list[float], q: float) -> float | None:
    v = sorted(v)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def session_reference(metrics: list[dict | None]) -> dict:
    """Session baseline: ``fwhm_px`` = lower quartile (the best-focused frames), ``detected`` = median star count
    (``star_count``: detected or plate-solve matched, whichever is larger, the same number each frame is ranked on)."""
    fw = [m["fwhm_px"] for m in metrics if m and isinstance(m.get("fwhm_px"), (int, float))]
    st = [star_count(m) for m in metrics if m and isinstance(m.get("detected"), (int, float))]
    return {"fwhm_px": _quantile(fw, 0.25), "detected": _median(st), "frames": len([m for m in metrics if m])}


def cull_rank(metrics: dict | None, rule: dict | None = None, reference: dict | None = None,
              solved: bool | None = None) -> dict:
    """Verdict for one frame. ``metrics`` = the frame's ``quality`` object; ``reference`` = session_reference()."""
    r = merge_rule(rule)
    if not r["enabled"]:
        return {"decision": "keep", "reasons": [], "checks": [], "enabled": False}
    th = r["thresholds"]
    ref = reference or {}
    checks: list[dict] = []

    def add(check, level, value, threshold, text):
        checks.append({"check": check, "level": level, "value": value, "threshold": threshold, "reason": text})

    if not metrics:
        add("quality", "review", None, None, "no quality metrics for this frame")
    for c in r["order"]:
        if c == "solve":
            if solved is False:
                add("solve", "review", None, None, "did not plate-solve")
            continue
        if not metrics:
            continue
        if c == "focus":
            f = metrics.get("fwhm_px")
            if f is None:
                continue
            base = ref.get("fwhm_px")
            if th.get("focus_ratio") and base and f >= th["focus_ratio"] * base:
                add("focus", "review", f, round(th["focus_ratio"] * base, 2),
                    f"focus: star size {f:.1f} px is {f / base:.1f}x the session best {base:.1f} px "
                    "(defocused; kept for stacking)")
            elif th.get("focus_px") and f >= th["focus_px"]:
                add("focus", "review", f, th["focus_px"],
                    f"focus: star size {f:.1f} px >= {th['focus_px']} px (defocused; kept for stacking)")
        elif c == "trailing":
            e = metrics.get("elongation")
            if e is None:
                continue
            if th.get("trailing_reject") and e >= th["trailing_reject"]:
                add("trailing", "reject", e, th["trailing_reject"], f"trailing: stars elongated {e:.2f}:1")
            elif th.get("trailing_review") and e >= th["trailing_review"]:
                add("trailing", "review", e, th["trailing_review"], f"trailing: stars elongated {e:.2f}:1")
        elif c == "stars":
            if metrics.get("detected") is None:
                continue
            n = star_count(metrics)
            how = "detected" if n == metrics["detected"] else "matched by the plate solve"
            base = ref.get("detected")
            if th.get("stars_min") is not None and n < th["stars_min"]:
                add("stars", "reject", n, th["stars_min"], f"stars: only {n} {how}")
            elif th.get("stars_ratio_review") and base and n < th["stars_ratio_review"] * base:
                add("stars", "review", n, round(th["stars_ratio_review"] * base),
                    f"stars: {n} {how}, {n / base:.0%} of the session median {base:.0f} (cloud, haze, twilight?)")
    decision = "keep"
    for ch in checks:
        if _RANK[ch["level"]] > _RANK[decision]:
            decision = ch["level"]
    return {"decision": decision, "reasons": [ch["reason"] for ch in checks], "checks": checks, "enabled": True}


def cull_session(metrics_by_key: dict, rule: dict | None = None, solved_by_key: dict | None = None) -> dict:
    """{"reference": {...}, "rule": <merged rule>, "frames": {key: cull_rank(...)}} for one session."""
    ref = session_reference(list(metrics_by_key.values()))
    solved_by_key = solved_by_key or {}
    return {"reference": ref, "rule": merge_rule(rule),
            "frames": {k: cull_rank(m, rule, ref, solved_by_key.get(k)) for k, m in metrics_by_key.items()}}
