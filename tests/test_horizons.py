from __future__ import annotations

import urllib.error

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

    def fake_fetch(cmd, t0, t1, step, progress=None):
        n = 20
        ets = [(float(t0) + i) * 86400.0 - horizons.ET0 for i in range(n)]
        states = np.tile([1.0e8, 2.0e7, 0.0, -5.0, 29.0, 1.0], (n, 1))
        if progress:
            progress(1, 1)
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
    import time

    from orbcalc import planets

    client, tmp = eph_client
    r = client.post("/api/ephemeris/add", json={
        "naif": 2162173, "horizons": "162173;", "name": "162173 Ryugu",
        "t0": 4000.0, "t1": 4100.0, "step": 5.0})
    assert r.status_code == 202, r.get_json()
    tid = r.get_json()["task_id"]
    for _ in range(100):
        t = client.get(f"/api/ephemeris/task/{tid}").get_json()
        if t.get("status") in ("done", "error"):
            break
        time.sleep(0.05)
    assert t["status"] == "done", t
    j = t["result"]
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


def _add_and_wait(client, **body):
    import time

    r = client.post("/api/ephemeris/add", json=body)
    assert r.status_code == 202, r.get_json()
    tid = r.get_json()["task_id"]
    for _ in range(100):
        t = client.get(f"/api/ephemeris/task/{tid}").get_json()
        if t.get("status") in ("done", "error"):
            return t
        time.sleep(0.05)
    return t


def test_add_overwrite_flow(eph_client):
    client, _ = eph_client
    body = {"naif": 2162173, "horizons": "162173;", "name": "162173 Ryugu"}
    assert _add_and_wait(client, **body)["status"] == "done"

    r = client.post("/api/ephemeris/add", json=body)
    assert r.status_code == 409
    j = r.get_json()
    assert j.get("can_overwrite") and j.get("existing") == "ryugu.bsp"

    assert _add_and_wait(client, **{**body, "overwrite": True})["status"] == "done"


def test_add_rejects_table(eph_client, monkeypatch):
    from orbcalc import ephem

    client, _ = eph_client
    monkeypatch.setattr(ephem, "kernels_for_naif",
                        lambda naif: [{"file": "sb441-n16.bsp",
                                       "bodies": [{"naif": naif}, {"naif": 2}],
                                       "warning": None}])
    r = client.post("/api/ephemeris/add", json={
        "naif": 2000001, "horizons": "1;", "name": "1 Ceres"})
    assert r.status_code == 409
    assert "sb441-n16.bsp" in r.get_json()["error"]


class _Resp:
    def __init__(self, data=b"ok"):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._data


def test_get_retries_transient_then_succeeds(monkeypatch):
    from orbcalc import horizons

    calls = []

    def fake(url, timeout=None):
        calls.append(url)
        if len(calls) < 3:
            raise urllib.error.URLError("boom")
        return _Resp(b"ok")

    monkeypatch.setattr(horizons.urllib.request, "urlopen", fake)
    monkeypatch.setattr(horizons.time, "sleep", lambda _s: None)
    assert horizons._get("http://x") == "ok"
    assert len(calls) == 3


def test_get_4xx_fails_immediately(monkeypatch):
    from orbcalc import horizons

    calls = []

    def fake(url, timeout=None):
        calls.append(url)
        raise urllib.error.HTTPError(url, 400, "bad", None, None)

    monkeypatch.setattr(horizons.urllib.request, "urlopen", fake)
    with pytest.raises(urllib.error.HTTPError):
        horizons._get("http://x")
    assert len(calls) == 1


def test_get_fails_after_three_transient_errors(monkeypatch):
    from orbcalc import horizons

    calls = []

    def fake(url, timeout=None):
        calls.append(url)
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(horizons.urllib.request, "urlopen", fake)
    monkeypatch.setattr(horizons.time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError, match="已重试 3 次"):
        horizons._get("http://x")
    assert len(calls) == 3


def test_fetch_wraps_chunk_error(monkeypatch):
    from orbcalc import horizons

    def boom(*_a):
        raise RuntimeError("boom")

    monkeypatch.setattr(horizons, "_get_vectors", boom)
    with pytest.raises(RuntimeError, match="第 1/1 段请求失败"):
        horizons.fetch("1;", 0, 100, 5)
