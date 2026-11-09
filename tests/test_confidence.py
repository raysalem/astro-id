import pytest

from astro_id import confidence as C


@pytest.mark.parametrize("conf,tier", [(100, "high"), (80, "high"), (79, "likely"), (65, "likely"),
                                       (64, "candidate"), (0, "candidate")])
def test_tier_boundaries(conf, tier):
    assert C.tier(conf) == tier


def test_combine_is_product_and_clamped():
    assert C.combine(1, 1, 1, 1) == 100
    assert C.combine(0.9, 0.9, 1, 1) == 81
    assert C.combine(1, 1, 1, 1, bonus=1.2) == 100
    assert C.combine(1, 1, 1, 1, cap=C.EXIF_TIME_CAP) == 64
    assert C.tier(C.combine(1, 1, 1, 1, cap=C.EXIF_TIME_CAP)) == "candidate"


def test_grade_quality_order():
    assert C.solve_quality("A") > C.solve_quality("B") > C.solve_quality("C") > C.solve_quality(None) == 0


def test_position_certainty_vs_size():
    assert C.position_certainty(0, 5) == 1
    # one object-size of error costs ~40 %
    assert C.position_certainty(5, 5) == pytest.approx(0.6065, abs=1e-3)
    # the same error matters less for a big object
    assert C.position_certainty(5, 50) > C.position_certainty(5, 5)
    assert C.position_certainty(1000, 1) == 0.05


def test_detectability_logistic():
    assert C.detectability(8.0, 8.0) == pytest.approx(0.5)
    assert C.detectability(2.0, 8.0) > 0.99
    assert C.detectability(12.0, 8.0) < 0.01
    assert C.detectability(5.0, 8.0) > C.detectability(6.0, 8.0)


def test_extended_objects_need_more_light():
    assert C.extended_mag(6.0, 1.0, 3.0) == pytest.approx(6.0 + C.EXTENDED_PENALTY)
    assert C.extended_mag(6.0, 300.0, 3.0) == pytest.approx(6.0 + 2.5 * 2 + C.EXTENDED_PENALTY)


def test_margins():
    assert C.frame_margin(1.0) == 1.0 and C.frame_margin(0.0) == 0.4
    assert C.horizon_margin(None) == 1.0
    assert C.horizon_margin(10) == 1.0
    assert C.horizon_margin(3.0) == 1.0
    assert C.horizon_margin(1.5) == pytest.approx(0.8)
    assert C.horizon_margin(0.0) == pytest.approx(0.6)
    assert C.horizon_margin(-2) == 0.4


def test_constellation_confidence_is_fraction_matched():
    assert C.constellation_confidence(1.0, 7, 7) == 100
    assert C.constellation_confidence(1.0, 3, 6) == 50
    assert C.constellation_confidence(0.92, 6, 6) == 92
    assert C.constellation_confidence(1.0, 0, 0) == 0


def test_full_score_crosses_tiers_as_expected():
    # grade A, small error, bright, clear of horizon -> high
    hi = C.combine(C.solve_quality("A"), C.position_certainty(1, 4), C.detectability(3, 8), C.horizon_margin(30))
    # the same object 1 deg above the horizon in the worst case of the window -> drops a tier
    low = C.combine(C.solve_quality("A"), C.position_certainty(1, 4), C.detectability(3, 8), C.horizon_margin(1))
    assert C.tier(hi) == "high" and C.tier(low) == "likely"
