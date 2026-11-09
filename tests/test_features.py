"""Phase 1-3 features: mastering, quality rating, Max Mode, cover art, edits, exports, sounds."""
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from server import audio as A
from server.config import settings
from server.engines.acestep import AceStepEngine, pick_best
from server.engines.base import GenRequest, GenResult
from server.main import app, state

from tests.test_acestep_contract import FakeAceStep, noop, run
from tests.test_api import create, wait_done


class StubAux:
    """Stands in for the aux AI service: scores by file size, paints a flat JPEG."""

    def __init__(self):
        self.scored: list[str] = []
        self.painted: list[str] = []

    async def health(self, refresh=False):
        return {"score": True, "cover": True, "stems": False, "cuda": False}

    async def can(self, feature):
        return feature in ("score", "cover")

    async def score(self, paths, lyrics=None, language=None):
        self.scored += [Path(p).name for p in paths]
        return [{"CE": 7.0, "CU": 7.0, "PC": 5.0, "PQ": 7.5, "overall": 7.25 + i * 0.1}
                for i, _ in enumerate(paths)]

    async def cover(self, prompt, out_path, seed=None):
        from PIL import Image

        self.painted.append(prompt)
        Image.new("RGB", (64, 64), (200, 120, 40)).save(out_path, quality=90)
        return out_path

    async def stems(self, *a, **k):
        raise RuntimeError("no stems in stub")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def aux(client, monkeypatch):
    stub = StubAux()
    monkeypatch.setattr(state, "aux", stub)
    return stub


def wait_polished(client, song_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/songs/{song_id}").json()["song"]
        p = s["params"]
        if not p.get("polishing") and not p.get("art_status"):
            return s
        time.sleep(0.2)
    raise AssertionError("polish timed out")


def test_mastering_hits_streaming_loudness(client):
    s = create(client, mode="custom", style="pop", title="Loud", variations=1, duration=10)[0]
    s = wait_polished(client, s["id"])
    p = s["params"]
    assert s["audio_file"].endswith("_mx.mp3") and p["wav_file"].endswith("_mx.wav") and s["has_wav"]
    assert abs(p["loudness"]["after"] - settings.master_lufs) < 1.5
    assert (settings.audio_dir / p["raw_file"]).exists()  # the unmastered take is kept


def test_new_options_are_recorded(client):
    s = create(client, mode="simple", description="a dark techno track", variations=1, duration=10,
               vocal_gender="female", weirdness=80, style_influence=20, variety="high",
               exclude_styles="guitar", max_mode=True)[0]
    assert s["status"] == "complete", s["error"]
    p = s["params"]
    assert p["weirdness"] == 80 and p["vocal_gender"] == "female" and p["exclude"] == "guitar"
    assert p["max_mode"] is True and p["variety"] == "high"
    bad = client.post("/api/generate", json={"mode": "simple", "description": "x", "weirdness": 101})
    assert bad.status_code == 422


def test_sounds_are_short_instrumentals(client):
    s = create(client, mode="simple", description="a deep cinematic boom", kind="sound", variations=1)[0]
    assert s["status"] == "complete" and s["instrumental"] is True
    assert s["duration"] <= 13 and s["params"]["kind"] == "sound"


def test_quality_rating_and_cover_art(client, aux):
    s = create(client, mode="custom", style="dreamy synth pop", title="Night Drive",
               lyrics="[Verse]\nneon\n\n[Chorus]\ndrive", variations=1, duration=10)[0]
    s = wait_polished(client, s["id"])
    assert s["params"]["rating"]["overall"] > 7 and s["params"]["quality"] == "high"
    assert s["art_url"] and s["cover_file"]
    r = client.get(s["art_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert aux.painted and "Night Drive" in aux.painted[0]  # prompt written from the song
    # Repaint with a prompt, then upload one.
    old = s["cover_file"]
    client.post(f"/api/songs/{s['id']}/art", json={"prompt": "a red moon over the sea"})
    s2 = wait_polished(client, s["id"])
    assert s2["cover_file"] != old and s2["params"]["art_prompt"] == "a red moon over the sea"
    assert not (settings.cover_dir / old).exists()
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (10, 200, 10)).save(buf, "PNG")
    up = client.post(f"/api/songs/{s['id']}/art/upload", files={"file": ("a.png", buf.getvalue(), "image/png")})
    assert up.status_code == 200 and up.json()["song"]["cover_file"]
    img = Image.open(settings.cover_dir / up.json()["song"]["cover_file"])
    assert img.size == (1024, 1024)
    bad = client.post(f"/api/songs/{s['id']}/art/upload", files={"file": ("a.gif", b"GIF8", "image/gif")})
    assert bad.status_code == 415
    # Children inherit the artwork.
    child = client.post(f"/api/songs/{s['id']}/edit", json={"op": "reverse"}).json()["song"]
    assert child["cover_file"] and child["cover_file"] != s2["cover_file"]


def test_art_needs_aux(client):
    s = create(client, mode="custom", style="pop", variations=1, duration=10)[0]
    assert client.post(f"/api/songs/{s['id']}/art", json={}).status_code == 501
    assert client.get(f"/api/songs/{s['id']}/art").status_code == 404


def test_edits(client):
    s = wait_polished(client, create(client, mode="custom", style="rock", title="Cut Me",
                                     variations=1, duration=12)[0]["id"])
    crop = client.post(f"/api/songs/{s['id']}/edit", json={"op": "crop", "start": 2, "end": 7}).json()["song"]
    assert crop["task"] == "edit" and crop["parent_id"] == s["id"] and abs(crop["duration"] - 5) < 0.4
    cut = client.post(f"/api/songs/{s['id']}/edit", json={"op": "remove", "start": 2, "end": 6}).json()["song"]
    assert abs(cut["duration"] - (s["duration"] - 4)) < 0.6
    fast = client.post(f"/api/songs/{s['id']}/edit", json={"op": "speed", "factor": 1.5}).json()["song"]
    assert abs(fast["duration"] - s["duration"] / 1.5) < 0.5 and "1.5x" in fast["title"]
    fade = client.post(f"/api/songs/{s['id']}/edit", json={"op": "fade_out", "seconds": 4}).json()["song"]
    assert abs(fade["duration"] - s["duration"]) < 0.3
    assert client.post(f"/api/songs/{s['id']}/edit", json={"op": "crop", "start": 5, "end": 3}).status_code == 422
    assert client.post(f"/api/songs/{s['id']}/edit", json={"op": "speed", "factor": 3}).status_code == 422
    assert client.post(f"/api/songs/{s['id']}/edit", json={"op": "nope"}).status_code == 422


def test_remaster(client):
    s = create(client, mode="custom", style="soul", title="Old Tape", variations=1, duration=10)[0]
    r = wait_done(client, client.post(f"/api/songs/{s['id']}/remaster").json()["songs"])[0]
    assert r["status"] == "complete" and r["task"] == "remaster" and r["title"] == "Old Tape (remaster)"


def test_downloads_mp3_wav_mp4(client):
    s = wait_polished(client, create(client, mode="custom", style="pop", title="Formats",
                                     variations=1, duration=10)[0]["id"])
    wav = client.get(s["audio_url"] + "?download=1&format=wav")
    assert wav.status_code == 200 and wav.content[:4] == b"RIFF" and "Formats.wav" in wav.headers["content-disposition"]
    mp4 = client.get(s["audio_url"] + "?download=1&format=mp4")
    assert mp4.status_code == 200 and mp4.headers["content-type"] == "video/mp4" and b"ftyp" in mp4.content[:16]
    assert client.get(s["audio_url"] + "?format=flac").status_code == 422
    # Deleting removes every file that belongs to the song.
    files = [settings.audio_dir / s["audio_file"], settings.audio_dir / s["params"]["wav_file"],
             settings.audio_dir / s["params"]["raw_file"]]
    assert client.delete(f"/api/songs/{s['id']}").status_code == 200
    assert not any(f.exists() for f in files) and not list(settings.audio_dir.glob(f"{s['id']}_*.mp4"))


def test_dislike(client):
    s = create(client, mode="custom", style="pop", variations=1, duration=10)[0]
    out = client.patch(f"/api/songs/{s['id']}", json={"disliked": True}).json()["song"]
    assert out["disliked"] is True and out["liked"] is False


def test_status_reports_new_services(client):
    st = client.get("/api/status").json()
    assert "aux" in st and "mastering" in st and "vision" in st


def test_inspire_without_vision_model(client):
    r = client.post("/api/inspire", files={"file": ("a.jpg", b"\xff\xd8\xff", "image/jpeg")})
    assert r.status_code in (501, 502)


# ----------------------------------------------------------- Max Mode (engine)
def test_pick_best_keeps_top_scores(tmp_path):
    cands = []
    for i in range(4):
        f = tmp_path / f"c{i}.mp3"
        f.write_bytes(b"x" * 10)
        cands.append(GenResult(file=f, duration=10, style="", lyrics="", seed=i))

    async def judge(paths, lyrics=None):
        return [{"overall": s} for s in (5.0, 8.0, 6.0, 7.5)]

    best = run(pick_best(cands, 2, judge))
    assert [b.seed for b in best] == [1, 3] and best[0].extra["candidates"] == 4
    assert not (tmp_path / "c0.mp3").exists() and not (tmp_path / "c2.mp3").exists()


def test_max_mode_renders_extra_candidates(tmp_path, monkeypatch):
    t = np.arange(int(3 * A.SAMPLE_RATE)) / A.SAMPLE_RATE
    tone = (0.2 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
    wav = tmp_path / "x.wav"
    A.write_wav(wav, np.stack([tone, tone], 1))
    fake = FakeAceStep(wav.read_bytes())
    monkeypatch.setattr(settings, "acestep_poll_seconds", 0.01)
    eng = AceStepEngine("http://ace.local", "", httpx.MockTransport(fake))
    judged = []

    async def judge(paths, lyrics=None):
        judged.append(len(paths))
        assert lyrics and all(l.startswith("[Verse]") for l in lyrics)
        return [{"overall": 9.0 - i, "rank": float(i)} for i in range(len(paths))]  # rank wins over overall

    req = GenRequest(mode="custom", style="pop", lyrics="[Verse]\nla", variations=2, max_mode=True,
                     judge=judge, out_dir=tmp_path, weirdness=100, style_influence=0)
    out = run(eng.generate(req, noop))
    f = fake.calls[-1][1]
    assert f["batch_size"] == 4 and judged == [4] and len(out) == 2
    assert all(r.extra["candidates"] == 4 and "rating" in r.extra for r in out)
    assert f["lm_temperature"] == pytest.approx(1.1) and f["lm_cfg_scale"] == pytest.approx(1.5)


def test_restart_resumes_unfinished_finishing(client, aux):
    """A song caught mid-mastering or mid-painting by a restart gets finished, not stuck."""
    import server.main as M

    s = create(client, mode="custom", style="ambient", title="Half Done", variations=1, duration=10)[0]
    s = wait_polished(client, s["id"])
    M.merge_params(s["id"], polishing="rating", art_status="painting", rating=None)

    async def restart():
        M.resume_finishing()

    client.portal.call(restart)
    s = wait_polished(client, s["id"])
    assert s["params"]["rating"]["overall"] > 7 and s["cover_file"]
    assert s["audio_file"].endswith("_mx.mp3") and "_mx_mx" not in s["audio_file"]  # not mastered twice
