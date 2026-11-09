"""Planner: Ollama writes one distinct plan per take; ACE-Step renders each in Custom mode."""
import asyncio
import json

import httpx
import pytest

from server.engines.acestep import AceStepEngine
from server.engines.base import GenRequest
from server.planner import OllamaPlanner, _clean_key, _validate


def run(c):
    return asyncio.run(c)


calls_unload: list = []


def fake_ollama(plans):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "glm-5.2:cloud"}, {"name": "llama3.1:latest"}]})
        if request.url.path == "/api/generate":  # unload call
            calls_unload.append(json.loads(request.content))
            return httpx.Response(200, json={"done": True})
        if request.url.path == "/api/chat":
            body = json.loads(request.content)
            calls.append(body)
            plan = plans[min(len(calls) - 1, len(plans) - 1)]
            content = plan if isinstance(plan, str) else json.dumps(plan)
            return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})
        return httpx.Response(404)

    return httpx.MockTransport(handler), calls


LYRICS = ("[Intro]\n\n[Verse 1]\na1\na2\na3\na4\n\n[Pre-Chorus]\np1\np2\n\n[Chorus]\nc1\nc2\nc3\nc4\n\n"
          "[Verse 2]\nb1\nb2\nb3\nb4\n\n[Chorus]\nc1\nc2\nc3\nc4\n\n[Outro]")
GOOD = {"title": "Neon Run", "style": "synthwave, retro 80s, upbeat, analog bass, male vocals",
        "lyrics": LYRICS, "bpm": 112, "key": "a minor", "duration": 200}


def test_plan_is_validated_and_model_resolved():
    transport, calls = fake_ollama([GOOD])
    p = run(OllamaPlanner("http://ollama", "llama3.1", transport).plan("night drive"))
    assert p.title == "Neon Run" and p.bpm == 112 and p.key == "A minor" and p.duration == 200
    assert calls[0]["model"] == "llama3.1:latest" and calls[0]["format"] == "json"
    assert calls[0]["keep_alive"] == 0  # frees VRAM for ACE-Step


def test_thin_lyrics_are_rejected():
    thin = {**GOOD, "lyrics": "[Verse 1]\na\nb\n\n[Chorus]\nc\nd"}
    with pytest.raises(ValueError, match="too thin"):
        _validate(thin, duration=None, bpm=None, key="", instrumental=False)
    transport, calls = fake_ollama([thin, GOOD])
    assert run(OllamaPlanner("http://ollama", "llama3.1", transport).plan("x", final=False)).title == "Neon Run"
    assert len(calls) == 2 and calls[0]["keep_alive"] == "2m"


def test_user_settings_override_and_bad_values_are_fixed():
    p = _validate({**GOOD, "bpm": 33, "duration": 5000, "key": "??"}, duration=None, bpm=None, key="",
                  instrumental=False)
    assert p.bpm == 110 and p.duration == 180 and p.key == "C major"   # nonsense replaced
    p = _validate(GOOD, duration=45, bpm=90, key="F# minor", instrumental=False)
    assert (p.duration, p.bpm, p.key) == (45, 90, "F# minor")         # user wins
    assert _validate(GOOD, duration=None, bpm=None, key="", instrumental=True).lyrics == "[Instrumental]"
    assert _clean_key("Bbm") == "Bb minor" and _clean_key("E") == "E major"


def test_malformed_output_retries_then_fails():
    transport, calls = fake_ollama(["not json", GOOD])
    assert run(OllamaPlanner("http://ollama", "llama3.1", transport).plan("x")).title == "Neon Run"
    transport, calls = fake_ollama(["nope"])
    with pytest.raises(RuntimeError):
        run(OllamaPlanner("http://ollama", "llama3.1", transport).plan("x"))
    assert len(calls) == 3


def test_plan_many_dedupes_titles_and_unloads():
    transport, calls = fake_ollama([GOOD, GOOD])
    plans = run(OllamaPlanner("http://ollama", "llama3.1", transport).plan_many("x", 2))
    assert [p.title for p in plans] == ["Neon Run", "Neon Run 2"]
    assert calls_unload[-1] == {"model": "llama3.1:latest", "keep_alive": 0}


def test_alternate_take_asks_for_new_title():
    transport, calls = fake_ollama([GOOD])
    run(OllamaPlanner("http://ollama", "llama3.1", transport).plan("x", take=2, avoid_titles=["Neon Run"]))
    assert "Neon Run" in calls[0]["messages"][1]["content"]


def test_engine_renders_one_custom_task_per_plan(tmp_path, monkeypatch):
    from server.config import settings
    from tests.test_acestep_contract import FakeAceStep
    from server import audio as A
    import numpy as np

    monkeypatch.setattr(settings, "acestep_poll_seconds", 0.01)
    wav = tmp_path / "t.wav"
    A.write_wav(wav, np.zeros((A.SAMPLE_RATE * 2, 2), np.float32) + 0.1)
    fake = FakeAceStep(wav.read_bytes())
    eng = AceStepEngine("http://ace.local", "", httpx.MockTransport(fake))
    p1 = _validate(GOOD, duration=None, bpm=None, key="", instrumental=False)
    p2 = _validate({**GOOD, "title": "Second Light", "bpm": 100, "key": "E major"}, duration=None,
                   bpm=None, key="", instrumental=False)

    async def noop(_):
        return None

    async def go():
        out = []
        for _ in (1,):
            fake.polls = 0
        req = GenRequest(mode="simple", description="night drive", variations=2, plans=[p1, p2],
                         out_dir=tmp_path, name_prefix="b")
        orig = eng._generate_task

        async def counted(r, prog):
            fake.polls = 0
            out.append(r)
            return await orig(r, prog)

        eng._generate_task = counted
        return out, await eng.generate(req, noop)

    subs, results = run(go())
    assert [s.mode for s in subs] == ["custom", "custom"] and all(s.variations == 1 for s in subs)
    sent = [c[1] for c in fake.calls]
    assert [f["bpm"] for f in sent] == [112, 100] and [f["key_scale"] for f in sent] == ["A minor", "E major"]
    assert all("sample_query" not in f for f in sent) and all(f["audio_duration"] == 200 for f in sent)
    assert all(f["use_cot_caption"] is False and f["use_cot_language"] is False
               and f["use_cot_metas"] is False for f in sent)
    assert all(r.file.suffix == ".mp3" for r in results)  # WAV from ACE-Step re-encoded locally
    assert [r.extra["title"] for r in results] == ["Neon Run", "Second Light"]
    assert results[0].file != results[1].file


def test_full_app_simple_mode_uses_planner_and_streams_takes(tmp_path, monkeypatch):
    """Simple mode on ACE-Step: Ollama plans 2 distinct takes; each row gets its own title."""
    import time

    import numpy as np
    from fastapi.testclient import TestClient

    from server import audio as A
    from server import main
    from server.config import settings
    from tests.test_acestep_contract import FakeAceStep

    second = {**GOOD, "title": "Chrome Horizon", "bpm": 104, "key": "E major"}
    transport, calls = fake_ollama([GOOD, second])
    wav = tmp_path / "t.wav"
    A.write_wav(wav, np.zeros((A.SAMPLE_RATE * 2, 2), np.float32) + 0.1)
    fake = FakeAceStep(wav.read_bytes())
    monkeypatch.setattr(settings, "engine", "acestep")
    monkeypatch.setattr(settings, "planner", "auto")
    monkeypatch.setattr(settings, "acestep_poll_seconds", 0.01)
    with TestClient(main.app) as client:
        main.state.engines.acestep._transport = httpx.MockTransport(fake)
        main.state.planner = OllamaPlanner("http://ollama", "llama3.1", transport)
        songs = client.post("/api/generate", json={"mode": "simple", "description": "night drive"}).json()["songs"]
        deadline = time.time() + 20
        while time.time() < deadline:
            cur = [client.get(f"/api/songs/{s['id']}").json()["song"] for s in songs]
            if all(s["status"] in ("complete", "failed") for s in cur):
                break
            time.sleep(0.1)
    assert [s["status"] for s in cur] == ["complete", "complete"], [s["error"] for s in cur]
    assert [s["title"] for s in cur] == ["Neon Run", "Chrome Horizon"]
    assert [s["bpm"] for s in cur] == [112, 104] and cur[1]["key_scale"] == "E major"
    assert all(s["params"]["planner"] == "ollama:llama3.1:latest" for s in cur)
    assert [c["keep_alive"] for c in calls] == ["2m", "2m"]  # planned in parallel...
    assert calls_unload and calls_unload[-1]["keep_alive"] == 0  # ...then GPU freed once
    assert "different title" in calls[1]["messages"][1]["content"]  # take 2 has its own angle


def test_inspire_and_art_prompt():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.1:latest"}, {"name": "llava:7b"}]})
        body = json.loads(request.content)
        if body["model"] == "llava:7b":
            assert body["messages"][1]["images"] == ["aGk="]
            out = {"title": "Sail Away", "description": "a hopeful ballad about leaving by sea",
                   "style": ["Acoustic, Folk", "Soft Rock"]}
        else:
            out = {"art": "a lone sailboat at sunset, oil painting, warm orange palette"}
        return httpx.Response(200, json={"message": {"content": json.dumps(out)}})

    pl = OllamaPlanner("http://ollama", "llama3.1", httpx.MockTransport(handler))
    idea = run(pl.inspire("aGk="))
    assert idea["style"] == "Acoustic, Folk, Soft Rock" and idea["title"] == "Sail Away" and idea["model"] == "llava:7b"
    assert "sailboat" in run(pl.art_prompt("Sail Away", "folk", "[Verse]\nwe leave"))
    # No vision model installed: a clear error that names the fix.
    def no_vision(request):
        return httpx.Response(200, json={"models": [{"name": "llama3.1:latest"}]})
    with pytest.raises(RuntimeError, match="ollama pull llava"):
        run(OllamaPlanner("http://ollama", "llama3.1", httpx.MockTransport(no_vision)).inspire("aGk="))
