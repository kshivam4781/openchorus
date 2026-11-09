"""A/B benchmark for engine settings: render the same fixed songs through OpenChorus
and collect the AI judge scores, render time and loudness.

    python tools/bench_quality.py <label> [--url http://127.0.0.1:7777] [--out bench]

Run it once per ACE-Step configuration (restart ACE-Step with different env vars in
between), then compare with tools/bench_report.py.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

SONGS = [
    {"name": "pop", "title": "Paper Planes", "seed": 1234, "duration": 120,
     "style": "bright modern pop, female lead vocal, punchy drums, warm synth bass, airy pads, "
              "catchy hook, polished radio mix, 112 BPM feel",
     "lyrics": "[Verse 1]\nWe folded every promise into paper planes\nThrew them from the rooftop in the summer rain\n"
               "You said the sky was something we could rearrange\nAnd I believed you, I believed you\n\n"
               "[Pre-Chorus]\nHold on, hold on, the wind is turning now\n\n"
               "[Chorus]\nFly, fly, over the city lights\nWe are the paper planes tonight\n"
               "Fly, fly, nothing can hold us down\nWe are the paper planes, we never touch the ground\n\n"
               "[Verse 2]\nStreetlights spelling out the letters of your name\nEvery corner of this town still feels the same\n"
               "We were never scared of falling, only of the change\nBut I believed you, I believed you\n\n"
               "[Chorus]\nFly, fly, over the city lights\nWe are the paper planes tonight\n"
               "Fly, fly, nothing can hold us down\nWe are the paper planes, we never touch the ground\n\n[Outro]\nPaper planes tonight"},
    {"name": "rock", "title": "Iron Coast", "seed": 777, "duration": 120,
     "style": "anthemic alternative rock, gritty male vocal, distorted electric guitars, driving bass, "
              "big live drums, stadium chorus, raw energetic mix",
     "lyrics": "[Verse 1]\nSalt on the engine and rust on the gate\nWe drove all night and we never were late\n"
               "The radio crackled a song about home\nBut home was a feeling we never had known\n\n"
               "[Chorus]\nRaise it up, raise it up on the iron coast\nWe are the ones that the morning chose\n"
               "Raise it up, raise it up till the thunder goes\nWe will be standing on the iron coast\n\n"
               "[Verse 2]\nBroken guitars and a borrowed guitar case\nScars on our hands and the wind on our face\n"
               "Nobody told us the road would be long\nSo we kept on singing the same old song\n\n"
               "[Guitar Solo]\n\n[Chorus]\nRaise it up, raise it up on the iron coast\nWe are the ones that the morning chose\n"
               "Raise it up, raise it up till the thunder goes\nWe will be standing on the iron coast"},
    {"name": "folk", "title": "Kitchen Light", "seed": 4242, "duration": 120,
     "style": "intimate acoustic folk ballad, soft female vocal, fingerpicked acoustic guitar, warm upright bass, "
              "gentle brushed drums, cello, close-mic natural recording",
     "lyrics": "[Verse 1]\nThe kitchen light is on again\nThe kettle sings the way it did back then\n"
               "Your coat still hangs beside the door\nLike you will need it one time more\n\n"
               "[Chorus]\nAnd I will leave the light on, I will leave the light on\nFor the road that brings you home\n"
               "And I will keep the song on, I will keep the song on\nSo you never walk alone\n\n"
               "[Verse 2]\nThe garden grew without your hands\nThe river moved the way the river plans\n"
               "I write your name on frosted glass\nAnd watch it fade the way the seasons pass\n\n"
               "[Bridge]\nOh, the winter cannot stay\nOh, the winter cannot stay\n\n"
               "[Chorus]\nAnd I will leave the light on, I will leave the light on\nFor the road that brings you home"},
    {"name": "edm", "title": "Neon Tide", "seed": 99, "duration": 90, "instrumental": True,
     "style": "melodic progressive house instrumental, euphoric supersaw lead, deep sidechained bass, "
              "four on the floor kick, big build and drop, crisp festival mix, 126 BPM", "lyrics": ""},
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("--url", default="http://127.0.0.1:7777")
    ap.add_argument("--out", default="bench")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    out = Path(args.out) / args.label
    out.mkdir(parents=True, exist_ok=True)
    c = httpx.Client(base_url=args.url, timeout=60)
    results = []
    for song in SONGS:
        if args.only and song["name"] not in args.only.split(","):
            continue
        body = {"mode": "custom", "title": f"[bench {args.label}] {song['title']}", "style": song["style"],
                "lyrics": song["lyrics"], "instrumental": bool(song.get("instrumental")), "variations": 1,
                "duration": song["duration"], "seed": song["seed"], "quality": "high"}
        t = time.time()
        sid = c.post("/api/generate", json=body).raise_for_status().json()["songs"][0]["id"]
        while True:
            s = c.get(f"/api/songs/{sid}").json()["song"]
            if s["status"] == "failed":
                print(song["name"], "FAILED", s["error"], flush=True)
                break
            if s["status"] == "complete" and not s["params"].get("polishing"):
                break
            time.sleep(3)
        if s["status"] != "complete":
            results.append({"name": song["name"], "error": s["error"]})
            continue
        audio = c.get(f"/api/songs/{sid}/audio").content
        (out / f"{song['name']}.mp3").write_bytes(audio)
        q = s["params"].get("rating") or {}
        r = {"name": song["name"], "id": sid, "seconds": round(time.time() - t, 1), "quality": q,
             "loudness": s["params"].get("loudness"), "duration": s["duration"], "lyrics": song["lyrics"]}
        results.append(r)
        print(f"{song['name']:5s} overall={q.get('overall')} CE={q.get('CE')} PQ={q.get('PQ')} "
              f"time={r['seconds']}s", flush=True)
    (out / "results.json").write_text(json.dumps(results, indent=2))
    print("saved", out / "results.json")


if __name__ == "__main__":
    main()
