import pytest


def test_root_redirect(client):
    r = client.get("/", follow_redirects=True)
    assert r.status_code == 200
    # should serve HTML
    assert "text/html" in r.headers.get("content-type", "")


def test_ui_served(client):
    r = client.get("/ui/")
    assert r.status_code == 200
    # should serve HTML
    assert "text/html" in r.headers.get("content-type", "")


def test_get_config(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg.get("inputs") == 6
    assert cfg.get("outputs") == 2


def test_state_shape(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    st = r.json()
    n = 6
    for k in ("gains_linear", "gains_db", "mutes", "vu_peak", "vu_rms", "rec_dropped_buffers"):
        assert isinstance(st[k], list)
        assert len(st[k]) == n
    assert st["selected_channel"] == 1


def test_select_channel(client):
    r = client.post("/api/select/3")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # verify state reflects selection
    st = client.get("/api/state").json()
    assert st["selected_channel"] == 3


def test_set_gain_db(client):
    r = client.post("/api/gain/2", json={"gain_db": -6.0})
    assert r.status_code == 200
    st = client.get("/api/state").json()
    # gains_db index 1 should be near -6 dB
    assert -6.5 <= st["gains_db"][1] <= -5.5


def test_set_gain_linear(client):
    r = client.post("/api/gain/4", json={"gain_linear": 0.5})
    assert r.status_code == 200
    st = client.get("/api/state").json()
    assert 0.49 <= st["gains_linear"][3] <= 0.51


def test_set_gain_bad_payload(client):
    r = client.post("/api/gain/1", json={})
    assert r.status_code == 400


def test_set_mute(client):
    r = client.post("/api/mute/1", json={"mute": True})
    assert r.status_code == 200
    st = client.get("/api/state").json()
    assert st["mutes"][0] is True


def test_out_of_range_channel(client):
    r = client.post("/api/select/99")
    assert r.status_code == 400


@pytest.mark.timeout(5)
def test_websocket_vu_stream(client):
    with client.websocket_connect("/ws/vu") as ws:
        # receive a few payloads
        payload = ws.receive_json()
        assert set(["vu_peak", "vu_rms", "selected_channel", "mutes", "gains_db"]) <= set(payload.keys())
        # Expected list lengths
        assert len(payload["vu_peak"]) == 6
        assert len(payload["vu_rms"]) == 6
