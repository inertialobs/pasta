from __future__ import annotations

import numpy as np
import pytest

pytestmark = pytest.mark.requires_pykep

HEADER = """\
Target body name: 162173 Ryugu (1999 JU3)         {source: JPL#270}
Asteroid physical parameters (km, seconds, rotational period in hours):
   GM= 3.E-8               RAD= .448               ROTPER= 7.63262
"""


def test_naif_id_and_slugify():
    from orbcalc import horizons

    assert horizons.naif_id(162173) == 2162173
    assert horizons.naif_id(101955) == 2101955
    assert horizons.slugify("162173 Ryugu") == "162173_ryugu"
    assert horizons.slugify("  ") == "body"


def test_parse_header_units():
    from orbcalc import horizons

    h = horizons.parse_header(HEADER)
    assert h["name"] == "162173 Ryugu (1999 JU3)"
    assert h["mu_self"] == pytest.approx(3.0e9)     # km^3/s^2 -> m^3/s^2
    assert h["radius"] == pytest.approx(448.0)      # km -> m


def test_parse_vectors_reads_states_and_et():
    from orbcalc import horizons

    text = (
        "header\n$$SOE\n"
        "2456544.500000000 = A.D. 2013-Sep-09 00:00:00.0000 TDB\n"
        " X =-8.2E+07 Y =-1.8E+08 Z =-2.1E+06\n"
        " VX= 2.0E+01 VY=-1.2E+01 VZ= 2.3E+00\n"
        "$$EOE\n"
    )
    states, ets = horizons._parse_vectors(text)
    assert states.shape == (1, 6)
    assert states[0][0] == pytest.approx(-8.2e7)
    assert ets[0] == pytest.approx((2456544.5 - horizons.J2000_JD) * 86400.0)


def test_search_prefers_small_body(monkeypatch):
    from orbcalc import horizons

    monkeypatch.setattr(horizons, "_sbdb", lambda q: {
        "des": "162173", "shortname": "162173 Ryugu",
        "fullname": "162173 Ryugu (1999 JU3)",
        "orbit_class": {"name": "Apollo"}})
    monkeypatch.setattr(horizons, "_naif_from_name",
                        lambda name: (_ for _ in ()).throw(Exception("n/a")))
    res = horizons.search("Ryugu")
    assert res and res[0]["kind"] == "small_body" and res[0]["naif"] == 2162173
    assert res[0]["horizons"] == "162173;"


def test_search_unnumbered_is_unsupported(monkeypatch):
    from orbcalc import horizons

    monkeypatch.setattr(horizons, "_sbdb", lambda q: {
        "des": "2016 HO3", "shortname": "2016 HO3", "fullname": "2016 HO3"})
    monkeypatch.setattr(horizons, "_naif_from_name",
                        lambda name: (_ for _ in ()).throw(Exception("n/a")))
    res = horizons.search("2016 HO3")
    assert res[0]["kind"] == "unnumbered" and res[0]["naif"] is None


def test_search_naif_name(monkeypatch):
    from orbcalc import horizons

    monkeypatch.setattr(horizons, "_sbdb", lambda q: None)
    monkeypatch.setattr(horizons, "_naif_from_name", lambda name: 599)
    res = horizons.search("Jupiter")
    assert res[0]["kind"] == "naif" and res[0]["naif"] == 599 and res[0]["horizons"] == "599"


# ---------------------------------------------------------------------------
# 接口 (monkeypatch 掉 fetch, 不联网)
# ---------------------------------------------------------------------------
@pytest.fixture
def eph_client(monkeypatch, tmp_path):
    import webapp.app as web

    from orbcalc import ephem, horizons

    class _StubSettings(dict):
        def save_file(self, *a, **k):        # 测试中不写 pasta.settings.json
            pass

    monkeypatch.setattr(web, "EPHEMERIS_DIR", tmp_path)
    monkeypatch.setattr(ephem, "dir_path", lambda: tmp_path)
    monkeypatch.setattr(web, "settings", _StubSettings(ephemerides=[]))

    def fake_fetch(cmd, t0, t1, step):
        n = 20
        ets = [(float(t0) + i) * 86400.0 - horizons.ET0 for i in range(n)]
        states = np.tile([1.0e8, 2.0e7, 0.0, -5.0, 29.0, 1.0], (n, 1))
        return states, ets, {"name": "162173 Ryugu", "mu_self": 3.0e9, "radius": 448.0}

    monkeypatch.setattr(horizons, "fetch", fake_fetch)
    ephem.reset_cache()
    yield web.app.test_client(), tmp_path
    ephem.reset_cache()


def test_search_endpoint(eph_client, monkeypatch):
    from orbcalc import horizons

    monkeypatch.setattr(horizons, "search", lambda q: [{"kind": "small_body", "naif": 2162173,
                                                        "label": "162173 Ryugu"}])
    client, _ = eph_client
    r = client.get("/api/horizons/search?q=Ryugu")
    assert r.status_code == 200 and r.get_json()["results"][0]["naif"] == 2162173


def test_add_and_delete_ephemeris(eph_client):
    from orbcalc import planets

    client, tmp = eph_client
    r = client.post("/api/ephemeris/add", json={
        "naif": 2162173, "horizons": "162173;", "name": "162173 Ryugu",
        "t0": 4000.0, "t1": 4100.0, "step": 5.0})
    j = r.get_json()
    assert r.status_code == 200, j
    assert (tmp / j["file"]).is_file()
    assert (tmp / (j["file"][:-4] + ".json")).is_file()
    assert "RYUGU" in planets.known_tags()

    pl = planets.get_planet("RYUGU")
    assert pl.radius == pytest.approx(448.0)
    assert pl.safe_radius == pytest.approx(470.4)

    assert j["gm_m3s2"] == pytest.approx(3.0e9)

    d = client.delete(f"/api/ephemeris/{j['file']}")
    assert d.status_code == 200
    assert not (tmp / j["file"]).exists()
    assert "RYUGU" not in planets.known_tags()
