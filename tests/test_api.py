"""API checks for the feature-parity checklist (docs/REVERSE_ENGINEERING.md), demo engine."""
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server import audio as A
from server import stems as S
from server.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait_done(client, songs, timeout=60):
    ids = [s["id"] for s in songs]
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = [client.get(f"/api/songs/{i}").json()["song"] for i in ids]
        if all(s["status"] in ("complete", "failed") for s in cur):
            return cur
        time.sleep(0.2)
    raise AssertionError(f"timed out: {[s['status'] for s in cur]}")


def create(client, **body):
    r = client.post("/api/generate", json=body)
    assert r.status_code == 200, r.text
    return wait_done(client, r.json()["songs"])


def test_status(client):
    st = client.get("/api/status").json()
    assert st["active"]["name"] == "demo"
    assert "acestep" in st


def test_simple_mode_two_variations(client):  # checklist 1, 4, 7
    songs = create(client, mode="simple", description="a happy song about summer road trips", duration=12)
    assert len(songs) == 2
    for s in songs:
        assert s["status"] == "complete", s["error"]
        assert s["audio_url"] and s["title"]
        assert "[Chorus]" in s["lyrics"]           # AI-written lyrics in simple mode
        assert 11 <= s["duration"] <= 13
    assert songs[0]["seed"] != songs[1]["seed"]       # two distinct variations
    listed = [x["id"] for x in client.get("/api/songs").json()["songs"][:2]]
    assert listed == [s["id"] for s in songs]          # take 1 listed above take 2
    assert songs[0]["batch_id"] == songs[1]["batch_id"]


def test_custom_mode_keeps_title_style_lyrics(client):  # checklist 2, 6, 15
    lyrics = "[Verse 1]\nHello neon night\n\n[Chorus]\nRun into the light"
    songs = create(client, mode="custom", title="Neon Night", style="synthwave, bright synths",
                   lyrics=lyrics, variations=1, duration=10, bpm=100, key_scale="A minor", seed=123)
    s = songs[0]
    assert s["status"] == "complete", s["error"]
    assert s["title"] == "Neon Night" and s["style"].startswith("synthwave") and s["lyrics"] == lyrics
    assert s["bpm"] == 100 and s["key_scale"].lower().startswith("a minor") and s["seed"] == 123


def test_instrumental(client):  # checklist 3
    s = create(client, mode="custom", style="lo-fi hip hop", instrumental=True, variations=1, duration=10)[0]
    assert s["status"] == "complete" and s["instrumental"] is True
    assert s["lyrics"] == "[Instrumental]"


def test_validation(client):
    assert client.post("/api/generate", json={"mode": "simple", "description": ""}).status_code == 422
    assert client.post("/api/generate", json={"mode": "custom"}).status_code == 422
    assert client.post("/api/generate", json={"mode": "custom", "style": "x", "variations": 9}).status_code == 422


def test_audio_and_download(client):  # checklist 16
    s = create(client, mode="custom", style="pop", title="Down Load", variations=1, duration=10)[0]
    r = client.get(s["audio_url"])
    assert r.status_code == 200 and len(r.content) > 10_000
    r = client.get(s["audio_url"] + "?download=1")
    disp = r.headers.get("content-disposition", "")
    assert disp.startswith("attachment") and "Down%20Load.mp3" in disp


def test_lyrics_and_idea(client):  # checklist 5
    r = client.post("/api/lyrics", json={"prompt": "missing the ocean", "style": "folk"}).json()
    assert "[Verse 1]" in r["lyrics"] and r["source"] in ("built-in", "ollama", "acestep")
    idea = client.get("/api/idea").json()
    assert idea["description"] and idea["style"]


def test_extend(client):  # checklist 9
    parent = create(client, mode="custom", style="pop rock", variations=1, duration=10)[0]
    r = client.post(f"/api/songs/{parent['id']}/extend", json={"seconds": 10})
    child = wait_done(client, r.json()["songs"])[0]
    assert child["status"] == "complete", child["error"]
    assert child["parent_id"] == parent["id"] and child["task"] == "extend"
    assert child["duration"] > parent["duration"] + 7


def test_cover_and_replace(client):  # checklist 10, 11
    parent = create(client, mode="custom", style="piano ballad", variations=1, duration=12)[0]
    cov = wait_done(client, client.post(f"/api/songs/{parent['id']}/cover",
                                        json={"style": "synthwave", "variations": 2}).json()["songs"])
    assert len(cov) == 2 and all(c["status"] == "complete" and c["style"] == "synthwave" for c in cov)
    rep = client.post(f"/api/songs/{parent['id']}/replace", json={"start": 3, "end": 8})
    rep = wait_done(client, rep.json()["songs"])[0]
    assert rep["status"] == "complete", rep["error"]
    assert abs(rep["duration"] - parent["duration"]) < 0.6
    bad = client.post(f"/api/songs/{parent['id']}/replace", json={"start": 5, "end": 6})
    assert bad.status_code == 422


def test_persona(client):  # checklist 12
    src = create(client, mode="custom", style="dreamy indie pop, female vocals", variations=1, duration=10)[0]
    p = client.post("/api/personas", json={"song_id": src["id"], "name": "Dreamy"}).json()["persona"]
    assert p["style"].startswith("dreamy") and p["reference_file"]
    assert any(x["id"] == p["id"] for x in client.get("/api/personas").json()["personas"])
    s = create(client, mode="custom", lyrics="[Verse]\nla la", persona_id=p["id"], variations=1, duration=10)[0]
    assert s["status"] == "complete" and s["style"].startswith("dreamy") and s["persona_id"] == p["id"]
    assert client.delete(f"/api/personas/{p['id']}").status_code == 200


def test_upload_then_extend(client, tmp_path):  # checklist 13
    tone = (0.3 * np.sin(2 * np.pi * 220 * np.arange(6 * A.SAMPLE_RATE) / A.SAMPLE_RATE)).astype(np.float32)
    wav = tmp_path / "my take.wav"
    A.write_wav(wav, np.stack([tone, tone], 1))
    r = client.post("/api/upload", files={"file": ("my take.wav", wav.read_bytes(), "audio/wav")},
                    data={"title": ""})
    assert r.status_code == 200, r.text
    up = r.json()["song"]
    assert up["title"] == "my take" and abs(up["duration"] - 6) < 0.2
    child = wait_done(client, client.post(f"/api/songs/{up['id']}/extend", json={"seconds": 8}).json()["songs"])[0]
    assert child["status"] == "complete", child["error"]
    bad = client.post("/api/upload", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 415


def test_stems_reports_missing_demucs(client):  # checklist 14
    s = create(client, mode="custom", style="pop", variations=1, duration=10)[0]
    r = client.post(f"/api/songs/{s['id']}/stems")
    if S.demucs_available():
        assert r.status_code == 200
    else:
        assert r.status_code == 501 and "demucs" in r.json()["detail"].lower()


def test_library_like_search_rename_delete(client):  # checklist 7
    s = create(client, mode="custom", style="jazz", title="Blue Room", variations=1, duration=10)[0]
    assert client.patch(f"/api/songs/{s['id']}", json={"liked": True}).json()["song"]["liked"]
    assert any(x["id"] == s["id"] for x in client.get("/api/songs?liked=true").json()["songs"])
    assert client.get("/api/songs?q=Blue%20Room").json()["songs"][0]["id"] == s["id"]
    assert client.patch(f"/api/songs/{s['id']}", json={"title": "Green Room"}).json()["song"]["title"] == "Green Room"
    assert client.delete(f"/api/songs/{s['id']}").status_code == 200
    assert client.get(f"/api/songs/{s['id']}").status_code == 404


def test_web_app_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "OpenChorus" in r.text
