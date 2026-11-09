"""Contract test: OpenChorus <-> ACE-Step 1.5 REST API, against a faithful fake server.

The fake mirrors ACE-Step-1.5's server code (acestep/api/http/*: query_result_service.py,
model_service_routes.py, release_task_request_parser.py): wrapper {"data","code","error"},
/release_task -> task_id, /query_result with a JSON-string `result` list carrying
progress/stage/error while running, and /v1/audio?path=... downloads.
"""
import asyncio
import json
import time

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from server import audio as A
from server.config import settings
from server.engines.acestep import AceStepEngine
from server.engines.base import GenRequest


def wrap(data, code=200):
    return httpx.Response(code, json={"data": data, "code": code, "error": None,
                                      "timestamp": int(time.time() * 1000), "extra": None})


class FakeAceStep:
    def __init__(self, wav_bytes: bytes):
        self.wav = wav_bytes
        self.calls: list[tuple[str, dict, dict]] = []  # (path, fields, files)
        self.polls = 0
        self.fail = False
        self.initialized = True
        self.init_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return wrap({"status": "ok", "service": "ACE-Step API", "version": "1.0",
                         "models_initialized": self.initialized, "llm_initialized": self.initialized,
                         "loaded_model": "acestep-v15-turbo", "loaded_lm_model": None})
        if path == "/v1/init":
            self.init_calls += 1
            self.initialized = True
            return wrap({"message": "Model initialization completed", "slot": 1})
        if path == "/v1/models":  # what the real server returns today
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": "acestep/acestep-v15-turbo", "name": "ACE-Step acestep-v15-turbo"}]})
        if path == "/v1/model_inventory":
            if not getattr(self, "inventory", False):
                return httpx.Response(404, json={"detail": "Not Found"})
            return wrap({"models": [{"name": "acestep-v15-turbo", "is_default": True, "is_loaded": True,
                                     "supported_task_types": ["text2music", "cover", "repaint"]}],
                         "default_model": "acestep-v15-turbo", "lm_models": [],
                         "loaded_lm_model": "acestep-5Hz-lm-0.6B", "llm_initialized": True})
        if path == "/release_task":
            ctype = request.headers.get("content-type", "")
            files = {}
            if ctype.startswith("application/json"):
                fields = json.loads(request.content)
            else:  # multipart
                body = request.content
                fields = {}
                boundary = ctype.split("boundary=")[1].encode()
                for part in body.split(b"--" + boundary):
                    if b"name=" not in part:
                        continue
                    head, _, val = part.partition(b"\r\n\r\n")
                    name = head.split(b'name="')[1].split(b'"')[0].decode()
                    val = val.rstrip(b"\r\n")
                    if b"filename=" in head:
                        files[name] = len(val)
                    else:
                        fields[name] = val.decode()
            self.calls.append((path, fields, files))
            return wrap({"task_id": "task-1", "status": "queued", "queue_position": 1})
        if path == "/query_result":
            self.polls += 1
            if self.polls == 1:
                running = [{"file": "", "wave": "", "status": 0, "create_time": int(time.time()), "env": "development",
                            "prompt": "", "lyrics": "", "metas": {}, "progress": 0.42, "stage": "dit",
                            "error": None}]
                return wrap([{"task_id": "task-1", "status": 0, "result": json.dumps(running),
                              "progress_text": "DiT step 3/8"}])
            if self.fail:
                failed = [{"file": "", "status": 2, "metas": {}, "progress": 0.0, "stage": "failed",
                           "error": "CUDA out of memory"}]
                return wrap([{"task_id": "task-1", "status": 2, "result": json.dumps(failed), "progress_text": ""}])
            fields = self.calls[-1][1]
            n = int(fields.get("batch_size", 2))
            items = [{"file": f"/v1/audio?path=%2Ftmp%2Fapi_audio%2Fout{i}.mp3", "wave": "", "status": 1,
                      "prompt": fields.get("prompt") or "LM written caption, indie pop",
                      "lyrics": fields.get("lyrics") or "[Verse 1]\nLM written line\n\n[Chorus]\nSing it out",
                      "metas": {"bpm": 120, "duration": 12.5, "genres": "pop", "keyscale": "C Major",
                                "timesignature": "4"},
                      "create_time": int(time.time()), "env": "development"} for i in range(n)]
            return wrap([{"task_id": "task-1", "status": 1, "result": json.dumps(items)}])
        if path == "/v1/audio":
            return httpx.Response(200, content=self.wav, headers={"content-type": "audio/mpeg"})
        if path == "/format_input":
            return wrap({"caption": "warm folk, acoustic guitar", "lyrics":
                         "[Verse 1]\nline one\nline two\n\n[Chorus]\nline three\nline four",
                         "bpm": 96, "key_scale": "G Major", "time_signature": "4", "duration": 120,
                         "vocal_language": "en"})
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture()
def fake(tmp_path):
    t = np.arange(int(3 * A.SAMPLE_RATE)) / A.SAMPLE_RATE
    tone = (0.2 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
    wav = tmp_path / "x.wav"
    A.write_wav(wav, np.stack([tone, tone], 1))
    return FakeAceStep(wav.read_bytes())


async def noop(_):
    return None


def run(coro):
    return asyncio.run(coro)


def test_health_and_models(fake):
    eng = AceStepEngine("http://ace.local", "", httpx.MockTransport(fake))
    assert run(eng.available())
    assert eng.info()["models"] == ["acestep-v15-turbo"]


def test_simple_mode_uses_sample_query(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "acestep_poll_seconds", 0.01)
    eng = AceStepEngine("http://ace.local", "secret", httpx.MockTransport(fake))
    req = GenRequest(mode="simple", description="a soft folk song about home", variations=2, out_dir=tmp_path)
    out = run(eng.generate(req, noop))
    _, fields, files = fake.calls[-1]
    assert fields["sample_query"] == "a soft folk song about home" and fields["thinking"] is True
    assert fields["batch_size"] == 2 and fields["audio_format"] in ("wav", "mp3") and not files
    assert len(out) == 2 and all(r.file.exists() and r.file.stat().st_size > 1000 for r in out)
    assert out[0].bpm == 120 and out[0].key_scale == "C Major" and out[1].seed is None
    assert "[Chorus]" in out[0].lyrics  # LM-written lyrics flow back into the library


def test_simple_mode_passes_length_as_hint(fake, tmp_path, monkeypatch):
    """ACE-Step's sample mode overwrites duration/bpm/key, so they travel as text hints."""
    eng = AceStepEngine("http://ace.local", "", httpx.MockTransport(fake))
    p = eng.build_payload(GenRequest(mode="simple", description="a synthwave song", duration=30, bpm=110,
                                     key_scale="A minor"))
    assert p["sample_query"] == "a synthwave song (about 30 seconds long, 110 BPM, in A minor)"
    assert "audio_duration" not in p and "bpm" not in p and "key_scale" not in p


def test_custom_instrumental_and_meta(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "acestep_poll_seconds", 0.01)
    eng = AceStepEngine("http://ace.local", "", httpx.MockTransport(fake))
    req = GenRequest(mode="custom", style="synthwave", instrumental=True, variations=1, duration=45,
                     bpm=108, key_scale="A minor", seed=7, out_dir=tmp_path)
    run(eng.generate(req, noop))
    f = fake.calls[-1][1]
    assert f["lyrics"] == "[Instrumental]" and f["prompt"] == "synthwave" and f["task_type"] == "text2music"
    assert f["audio_duration"] == 45 and f["bpm"] == 108 and f["key_scale"] == "A minor"
    assert f["seed"] == 7 and f["use_random_seed"] is False


def test_cover_replace_extend_send_audio(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "acestep_poll_seconds", 0.01)
    eng = AceStepEngine("http://ace.local", "", httpx.MockTransport(fake))
    src = tmp_path / "src.wav"
    src.write_bytes(fake.wav)

    run(eng.generate(GenRequest(task="cover", style="jazz", lyrics="[Verse]\nx", src_audio=src,
                                cover_strength=0.4, variations=2, out_dir=tmp_path), noop))
    _, f, files = fake.calls[-1]
    assert f["task_type"] == "cover" and f["audio_cover_strength"] == "0.4" and files["src_audio"] > 1000

    fake.polls = 0
    run(eng.generate(GenRequest(task="replace", style="rock", src_audio=src, repaint_start=1,
                                repaint_end=2.5, variations=1, out_dir=tmp_path), noop))
    _, f, files = fake.calls[-1]
    assert f["task_type"] == "repaint" and f["repainting_start"] == "1.0" and f["repainting_end"] == "2.5"
    assert f["chunk_mask_mode"] == "explicit"

    fake.polls = 0
    run(eng.generate(GenRequest(task="extend", style="rock", src_audio=src, repaint_start=3.0,
                                repaint_end=13.0, variations=1, out_dir=tmp_path), noop))
    _, f, files = fake.calls[-1]
    assert f["task_type"] == "repaint" and f["repainting_start"] == "3.0" and f["audio_duration"] == "13.0"
    # padded source (13 s of 16-bit stereo 44.1 kHz) was uploaded, not the 3 s original
    assert files["src_audio"] > 13 * A.SAMPLE_RATE * 4 * 0.95
    assert not list(tmp_path.glob("*_padded.wav"))  # temp file cleaned up


def test_progress_and_failure_are_reported(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "acestep_poll_seconds", 0.01)
    eng = AceStepEngine("http://ace.local", "", httpx.MockTransport(fake))
    seen = []

    async def record(msg):
        seen.append(msg)

    fake.fail = True
    fake.polls = 0
    with pytest.raises(Exception, match="CUDA out of memory"):
        run(eng.generate(GenRequest(style="pop", lyrics="[Verse]\nhi", variations=1, out_dir=tmp_path), record))
    assert "Generating 42%" in seen


def test_extend_limit(fake, tmp_path):
    eng = AceStepEngine("http://ace.local", "", httpx.MockTransport(fake))
    src = tmp_path / "src.wav"
    src.write_bytes(fake.wav)
    with pytest.raises(Exception, match="at most 90"):
        run(eng.generate(GenRequest(task="extend", src_audio=src, repaint_start=3, repaint_end=120,
                                    out_dir=tmp_path), noop))


def test_lyrics_via_format_input(fake):
    eng = AceStepEngine("http://ace.local", "", httpx.MockTransport(fake))
    res = run(eng.write_lyrics("coming home", "folk"))
    assert res and "[Chorus]" in res["lyrics"] and res["style"].startswith("warm folk")


def test_full_app_against_fake_acestep(fake, monkeypatch):
    """The whole web API, with ACE-Step selected as the engine."""
    from server import main

    monkeypatch.setattr(settings, "engine", "acestep")
    monkeypatch.setattr(settings, "acestep_poll_seconds", 0.01)
    with TestClient(main.app) as client:
        main.state.engines.acestep._transport = httpx.MockTransport(fake)
        assert client.get("/api/status").json()["active"]["name"] == "acestep"
        songs = client.post("/api/generate", json={"mode": "simple", "description": "rainy day blues"}).json()["songs"]
        deadline = time.time() + 20
        while time.time() < deadline:
            cur = [client.get(f"/api/songs/{s['id']}").json()["song"] for s in songs]
            if all(s["status"] != "queued" and s["status"] != "running" for s in cur):
                break
            time.sleep(0.1)
        assert [s["status"] for s in cur] == ["complete", "complete"], [s["error"] for s in cur]
        assert all(s["engine"] == "acestep" and s["bpm"] == 120 for s in cur)
        assert cur[0]["title"] == "Sing it out"  # title taken from the LM chorus line
        assert client.get(cur[0]["audio_url"]).status_code == 200


def test_auto_mode_switches_between_engines(fake, monkeypatch):
    from server.engines import EngineManager

    monkeypatch.setattr(settings, "engine", "auto")
    mgr = EngineManager()
    mgr.acestep._transport = httpx.MockTransport(fake)
    assert run(mgr.current(refresh=True)).name == "acestep"

    def down(_request):
        raise httpx.ConnectError("connection refused")

    mgr.acestep._transport = httpx.MockTransport(down)
    assert run(mgr.current(refresh=True)).name == "demo"
    assert run(mgr.status())["acestep"]["online"] is False


def test_waits_for_models_then_warms_up(fake, monkeypatch):
    """ACE-Step answers /health before its models load: stay on demo and trigger /v1/init."""
    from server.engines import EngineManager

    monkeypatch.setattr(settings, "engine", "auto")
    fake.initialized = False

    async def scenario():
        mgr = EngineManager()
        mgr.acestep._transport = httpx.MockTransport(fake)
        first = await mgr.current(refresh=True)
        st = await mgr.status()
        await asyncio.sleep(0.05)          # let the background warm-up run
        second = await mgr.current(refresh=True)
        return first.name, st["acestep"], second.name

    first, st, second = run(scenario())
    assert first == "demo" and st["online"] is True and st["ready"] is False
    assert fake.init_calls == 1           # warmed exactly once, not on every status poll
    assert second == "acestep"


def test_poll_survives_transient_network_errors(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "acestep_poll_seconds", 0.01)
    flaky = {"n": 0}

    def handler(request):
        if request.url.path == "/query_result":
            flaky["n"] += 1
            if flaky["n"] in (2, 3):
                raise httpx.ReadTimeout("server busy")
        return fake(request)

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    eng = AceStepEngine("http://ace.local", "", httpx.MockTransport(handler))
    out = run(eng.generate(GenRequest(style="pop", lyrics="[Verse]\nhi", variations=1, out_dir=tmp_path), noop))
    assert len(out) == 1 and flaky["n"] >= 4


_real_sleep = asyncio.sleep


async def _fast_sleep(_s):
    await _real_sleep(0)
