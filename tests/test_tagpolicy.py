"""Tag hierarchy: levels, parent constellation, member suppression, tag policy, tag style."""
from __future__ import annotations

import pytest

from astro_id import data
from astro_id import tagpolicy as TP
from astro_id.identify import Obj, _hierarchy


def _cat():
    cat = data.hipparcos()
    ra, dec = cat.at_epoch(2026.85)
    return ra, dec, cat.index_of()


def _star(name, hip, mag, x, y, conf=95):
    ra, dec, idx = _cat()
    i = idx[hip]
    return Obj(name, "star", conf, x, y, mag=mag, level=TP.LEVEL_STAR, ra=float(ra[i]), dec=float(dec[i]), hips=(hip,))


def run(objs, policy=None, min_conf=65):
    ra, dec, idx = _cat()
    _hierarchy(objs, ra, dec, idx, policy or TP.TagPolicy(), min_conf)
    return {o.name: o for o in objs}


def orion_scene():
    belt = [_star("Alnitak", 26727, 1.74, 1000, 1000), _star("Alnilam", 26311, 1.69, 1100, 980),
            _star("Mintaka", 25930, 2.25, 1200, 960)]
    asterism = Obj("Orion's Belt", "asterism", 90, 1100, 980, level=TP.LEVEL_ASTERISM, mag=1.69,
                   hips=(26727, 26311, 25930), ra=84.1, dec=-1.15)
    pleiades = Obj("M45 Pleiades", "cluster", 92, 3000, 500, radius_px=200, level=TP.LEVEL_CATALOG, mag=1.2,
                   ra=56.87, dec=24.11)
    members = [_star("Alcyone", 17702, 2.87, 3040, 520), _star("Atlas", 17847, 3.62, 3100, 510)]
    outside = _star("Aldebaran", 21421, 0.87, 2500, 1200)
    betel = _star("Betelgeuse", 27989, 0.45, 900, 600)
    orion = Obj("Orion", "constellation", 88, None, None, level=TP.LEVEL_CONSTELLATION)
    jup = Obj("Jupiter", "planet", 100, 500, 500, mag=-2.3, level=TP.LEVEL_BODY, ra=146.0, dec=14.0)
    faint_gal = Obj("NGC 1300", "galaxy", 90, 2000, 2000, radius_px=5, level=TP.LEVEL_CATALOG, mag=10.4,
                    ra=49.92, dec=-19.41)
    return belt + [asterism, pleiades, outside, betel, orion, jup, faint_gal] + members


def test_levels_and_parent():
    o = run(orion_scene())
    assert (o["Orion"].level, o["Jupiter"].level, o["M45 Pleiades"].level, o["Orion's Belt"].level,
            o["Betelgeuse"].level) == (1, 2, 3, 4, 5)
    assert o["Orion"].parent is None
    assert o["Betelgeuse"].parent == "orion" and o["Orion's Belt"].parent == "orion"
    assert o["M45 Pleiades"].parent == "taurus" and o["Jupiter"].parent == "leo"
    d = o["Orion"].as_dict()
    assert d["level"] == 1 and d["magnitude"] is None and d["parent"] is None and d["taggable"] is True


def test_stars_off_by_default():
    o = run(orion_scene())
    assert not any(x.taggable for x in o.values() if x.level == TP.LEVEL_STAR)
    assert all(o[n].taggable for n in ("Orion", "Jupiter", "M45 Pleiades", "Orion's Belt"))


def test_star_mag_limit_turns_stars_on():
    o = run(orion_scene(), TP.make_policy(star_mag_limit=1.0))
    assert o["Betelgeuse"].taggable and o["Aldebaran"].taggable  # 0.45 and 0.87
    o = run(orion_scene(), TP.make_policy(star_mag_limit=0.5))
    assert o["Betelgeuse"].taggable and not o["Aldebaran"].taggable


def test_pleiades_members_suppressed_under_m45():
    o = run(orion_scene(), TP.make_policy(star_mag_limit=4.0))
    for n in ("Alcyone", "Atlas"):
        assert o[n].member_of == "m45 pleiades" and not o[n].taggable
    assert o["Aldebaran"].member_of is None and o["Aldebaran"].taggable  # outside the radius


def test_members_tagged_when_group_below_min_confidence():
    scene = orion_scene()
    for x in scene:
        if x.name == "M45 Pleiades":
            x.confidence = 40
    o = run(scene, TP.make_policy(star_mag_limit=4.0))
    assert o["Alcyone"].taggable and o["Alcyone"].member_of == "m45 pleiades"


def test_star_brighter_than_cluster_is_not_a_member():
    scene = orion_scene()
    scene.append(_star("Brightie", 17573, 0.9, 3010, 505))  # brighter than the Pleiades' integrated 1.2
    o = run(scene, TP.make_policy(star_mag_limit=4.0))
    assert o["Brightie"].member_of is None


def test_orions_belt_instead_of_its_stars():
    o = run(orion_scene(), TP.make_policy(star_mag_limit=2.5))
    for n in ("Alnitak", "Alnilam", "Mintaka"):
        assert o[n].member_of == "orion's belt" and not o[n].taggable
    assert o["Orion's Belt"].taggable and o["Betelgeuse"].taggable


def test_max_catalog_mag():
    o = run(orion_scene())
    assert o["M45 Pleiades"].taggable and not o["NGC 1300"].taggable
    o = run(orion_scene(), TP.make_policy(max_catalog_mag=11))
    assert o["NGC 1300"].taggable
    o = run(orion_scene(), TP.make_policy(max_catalog_mag=1.0))
    assert not o["M45 Pleiades"].taggable


def test_levels_flag():
    o = run(orion_scene(), TP.make_policy(levels="1,2"))
    assert o["Orion"].taggable and o["Jupiter"].taggable
    assert not o["M45 Pleiades"].taggable and not o["Orion's Belt"].taggable
    # with the belt not tagged, its stars are no longer suppressed once stars are on
    o = run(orion_scene(), TP.make_policy(levels=[1, 2, 5]))
    assert o["Alnitak"].taggable and o["Alnitak"].member_of == "orion's belt"


def test_tag_style_kind():
    o = run(orion_scene(), TP.make_policy(tag_style="kind", star_mag_limit=4.0))
    assert o["Orion"].tag == "constellation/orion"
    assert o["Jupiter"].tag == "planet/jupiter"
    assert o["M45 Pleiades"].tag == "messier/m45 pleiades"
    assert o["Orion's Belt"].tag == "asterism/orion's belt"
    assert o["Betelgeuse"].tag == "star/betelgeuse"
    assert o["NGC 1300"].tag == "ngc/ngc 1300"
    assert o["Alcyone"].member_of == "messier/m45 pleiades"
    assert o["Betelgeuse"].parent == "constellation/orion"
    assert TP.styled_tag("Moon", 2, "moon", "kind") == "moon"
    assert TP.styled_tag("Sun", 2, "star", "kind") == "sun"


def test_policy_from_config_and_flags():
    p = TP.make_policy({"levels": [1, 2], "star_mag_limit": 1.5, "max_catalog_mag": 5, "tag_style": "kind"})
    assert p.levels == (1, 2) and p.star_mag_limit == 1.5 and p.max_catalog_mag == 5.0 and p.tag_style == "kind"
    assert p.level_on(5)  # star_mag_limit turns stars on
    p = TP.make_policy({"tag_style": "kind"}, tag_style="plain", levels="1 3")
    assert p.tag_style == "plain" and p.levels == (1, 3)
    with pytest.raises(ValueError):
        TP.make_policy(levels="1,7")
    with pytest.raises(ValueError):
        TP.make_policy(tag_style="fancy")


def test_asterism_table_is_consistent():
    _, _, idx = _cat()
    for name, hips in TP.ASTERISMS.items():
        assert len(hips) >= 3 and len(set(hips)) == len(hips), name
        assert all(h in idx for h in hips), name
    assert TP.ASTERISM_CLOSED <= set(TP.ASTERISMS)
