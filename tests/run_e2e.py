"""Real-browser end-to-end check (Playwright + Chrome) against a live server on the demo engine.

Run:  python tests/run_e2e.py [--shots DIR]
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def chrome_path() -> str | None:
    for name in ("google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=str(Path(tempfile.gettempdir()) / "openchorus-shots"))
    args = ap.parse_args()
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    port = free_port()
    env = {**os.environ, "OPENCHORUS_ENGINE": "demo", "OPENCHORUS_PORT": str(port),
           "OPENCHORUS_DATA": tempfile.mkdtemp(prefix="openchorus-e2e-")}
    server = subprocess.Popen([sys.executable, "-m", "server.main"], cwd=ROOT, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/api/status", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError("server did not start:\n" + (server.stdout.read() if server.stdout else ""))

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chrome_path(),
                                        args=["--autoplay-policy=no-user-gesture-required"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "fonts" not in m.text else None)
            page.goto(base)
            try:
                run_flow(page, shots)
            except Exception:
                page.screenshot(path=str(shots / "FAILED.png"))
                print("console/page errors:", errors)
                raise
            real_errors = [e for e in errors if "googleapis" not in e and "ERR_" not in e]
            assert not real_errors, f"browser errors: {real_errors}"
            browser.close()
        print(f"E2E PASSED. Screenshots in {shots}")
        return 0
    finally:
        server.terminate()
        server.wait(timeout=10)


def run_flow(page, shots: Path) -> None:
    if True:
        if True:

            # engine status + demo banner
            expect(page.locator("#engineBox")).to_contain_text("Demo synth")
            expect(page.locator("#banner")).to_be_visible()
            expect(page.locator("#songs .empty")).to_contain_text("Your library is empty")
            page.screenshot(path=str(shots / "01-empty.png"))

            # 1. simple mode -> two takes
            page.fill("#description", "an upbeat synthwave song about driving at night")
            page.click("details.adv:not(.more) summary")
            page.fill("#duration", "15")
            page.click("#createBtn")
            expect(page.locator(".song")).to_have_count(2, timeout=10_000)
            page.screenshot(path=str(shots / "02-generating.png"))
            expect(page.locator(".song .s-status")).to_have_count(0, timeout=60_000)
            expect(page.locator(".song").first).not_to_contain_text("Failed")
            page.screenshot(path=str(shots / "03-two-takes.png"))

            # play + waveform drawn + drawer open
            page.locator(".song").first.click()
            expect(page.locator("#drawer")).to_be_visible()
            expect(page.locator("#pPlay")).to_have_class("p-play playing", timeout=10_000)
            page.wait_for_function("""() => {
                const c = document.querySelector('#pWave'); const d = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                let amber = 0, teal = 0; for (let i=0;i<d.length;i+=4){ if(d[i]>200&&d[i+1]>150&&d[i+2]<100) amber++; if(d[i]<100&&d[i+1]>100&&d[i+2]>100) teal++; }
                return teal > 200;
            }""", timeout=15_000)
            page.wait_for_function("document.querySelector('#audio').currentTime > 0.5", timeout=10_000)
            expect(page.locator(".d-lyrics")).to_contain_text("[Chorus]")
            page.screenshot(path=str(shots / "04-playing-detail.png"))

            # rename via contenteditable title
            title = page.locator("#dTitle")
            title.click()
            page.keyboard.press("Control+A")
            page.keyboard.type("Neon Drive")
            page.keyboard.press("Enter")
            expect(page.locator(".song .s-title").first).to_have_text("Neon Drive", timeout=5_000)

            # like
            page.locator(".song .like").first.click()
            expect(page.locator(".song .like.liked")).to_have_count(1)

            # 2. extend from the drawer
            page.click("#dClose")
            page.locator(".song").first.click()
            page.click("[data-act=extend]")
            page.fill("#xSec", "10")
            page.click("#xGo")
            expect(page.locator(".song")).to_have_count(3, timeout=5_000)
            expect(page.locator(".song .s-status")).to_have_count(0, timeout=60_000)
            expect(page.locator(".song").first).to_contain_text("Extended")
            expect(page.locator(".song").first).to_contain_text("Neon Drive (extended)")

            # 3. replace section by dragging on the waveform
            page.locator(".song").nth(1).click()
            wave = page.locator("#dWave")
            expect(wave).to_be_visible()
            box = wave.bounding_box()
            page.mouse.move(box["x"] + box["width"] * 0.2, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] / 2, steps=8)
            page.mouse.up()
            expect(page.locator("#sRep")).to_be_visible()  # section menu: replace, keep, cut
            page.click("#sRep")
            expect(page.locator("#rGo")).to_be_visible()
            page.screenshot(path=str(shots / "05-replace-selection.png"))
            page.click("#rGo")
            expect(page.locator(".song")).to_have_count(4, timeout=5_000)
            expect(page.locator(".song .s-status")).to_have_count(0, timeout=60_000)
            expect(page.locator(".song").first).to_contain_text("Edited")

            # 4. cover in a new style
            page.locator(".song").nth(1).click()
            page.click("[data-act=cover]")
            page.fill("#cStyle", "lo-fi hip hop, mellow")
            page.click("#cGo")
            expect(page.locator(".song")).to_have_count(6, timeout=5_000)
            expect(page.locator(".song .s-status")).to_have_count(0, timeout=60_000)

            # 5. persona -> custom mode create using it
            page.click("[data-act=persona]")
            page.click("#pGo")
            expect(page.locator("#personaField")).to_be_visible(timeout=5_000)
            page.click("#dClose")
            page.click(".seg-btn[data-mode=custom]")
            page.click(".tags button[data-tag='[Verse]']")
            page.keyboard.type("Lights on the highway")
            page.click(".tags button[data-tag='[Chorus]']")
            page.keyboard.type("We run, we run")
            page.click("#styleChips button:has-text('female vocals')")
            page.select_option("#persona", index=1)
            page.fill("#title", "Highway Persona")
            page.click("#createBtn")
            expect(page.locator(".song .s-title", has_text="Highway Persona")).to_have_count(2, timeout=5_000)
            expect(page.locator(".song .s-status")).to_have_count(0, timeout=60_000)
            page.screenshot(path=str(shots / "06-custom-mode.png"))

            # 6. write lyrics button
            page.fill("#lyrics", "")
            page.click("#writeLyrics")
            expect(page.locator("#lyrics")).to_have_value(__import__("re").compile(r"\[Verse 1\]"), timeout=10_000)

            # 7. liked filter, personas view, search
            page.click(".nav-btn[data-view=liked]")
            expect(page.locator(".song")).to_have_count(1)
            page.click(".nav-btn[data-view=personas]")
            expect(page.locator(".persona")).to_have_count(1)
            page.screenshot(path=str(shots / "07-personas.png"))
            page.click(".nav-btn[data-view=all]")
            page.fill("#search", "Highway")
            expect(page.locator(".song")).to_have_count(2, timeout=5_000)
            page.fill("#search", "")
            expect(page.locator(".song")).to_have_count(8, timeout=5_000)

            # 8. delete
            page.once("dialog", lambda d: d.accept())
            page.locator(".song").first.click()
            page.click("[data-act=delete]")
            expect(page.locator(".song")).to_have_count(7, timeout=5_000)

            # 8b. instant edits, download formats, sounds and more options
            page.locator(".song").first.click()
            page.click("[data-act=edit]")
            page.fill("#eSp", "1.5")
            page.dispatch_event("#eSp", "input")
            page.click("#eSpGo")
            expect(page.locator(".song")).to_have_count(8, timeout=10_000)
            expect(page.locator(".song").first).to_contain_text("1.5x")
            page.click("[data-act=download]")
            expect(page.locator(".dl-list a")).to_have_count(3)
            page.click("#dClose")
            page.click(".kind-seg [data-kind=sound]")
            expect(page.locator("#createTitle")).to_have_text("Make a sound")
            expect(page.locator("#instrumental")).to_be_hidden()
            page.click(".seg-btn[data-mode=simple]")
            page.fill("#description", "a short glassy riser into a deep boom")
            page.click("#moreOpts summary")
            page.click("label.max-switch")
            page.click("#createBtn")
            expect(page.locator(".song")).to_have_count(10, timeout=10_000)
            expect(page.locator(".song .s-status")).to_have_count(0, timeout=60_000)
            page.screenshot(path=str(shots / "08-sounds.png"))
            page.click(".kind-seg [data-kind=song]")

            # 9. validation message
            page.click(".seg-btn[data-mode=simple]")
            page.fill("#description", "")
            page.click("#createBtn")
            expect(page.locator("#formError")).to_have_text("Describe the song you want first.")

            # 10. mobile layout renders
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(shots / "08-mobile.png"), full_page=True)
            overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            assert not overflow, "horizontal scroll on mobile"


if __name__ == "__main__":
    sys.exit(main())
