"""Compare benchmark runs: python tools/bench_report.py bench"""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "bench")
rows = []
for d in sorted(p for p in root.iterdir() if (p / "results.json").exists()):
    res = [r for r in json.loads((d / "results.json").read_text()) if "error" not in r]
    if not res:
        continue
    avg = lambda k: sum(r["quality"].get(k, 0) for r in res) / len(res)
    w = [r["wer"] for r in res if "wer" in r]
    rows.append((d.name, avg("overall"), avg("CE"), avg("PQ"), avg("PC"),
                 sum(w) / len(w) if w else float("nan"), sum(r["seconds"] for r in res) / len(res)))
print(f"{'config':16s} {'overall':>7s} {'CE':>5s} {'PQ':>5s} {'PC':>5s} {'WER':>5s} {'sec':>6s}")
for r in sorted(rows, key=lambda x: -x[1]):
    print(f"{r[0]:16s} {r[1]:7.2f} {r[2]:5.2f} {r[3]:5.2f} {r[4]:5.2f} {r[5]:5.2f} {r[6]:6.0f}")
