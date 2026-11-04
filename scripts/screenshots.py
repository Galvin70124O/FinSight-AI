"""Capture real screenshots of FinSight with the bundled sample data.

    python scripts/screenshots.py

Starts the app on a throwaway database, signs up a sample user, loads the
sample transactions, then saves:

- static/img/<part>-<theme>.jpg   crops of the dashboard used on the landing page
- docs/screenshots/<page>-<width>-<theme>.png   full pages for the README

Needs the dev requirements (Playwright) and an installed Google Chrome.
Nothing from the run is kept: the database lives in a temporary folder.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 5077
BASE = f"http://127.0.0.1:{PORT}"
USER = {"username": "sample", "email": "sample@example.com", "password": "sample-password-2025"}

# Landing-page crops: name -> CSS selector of the dashboard part to capture.
# "overview" is the whole first screen of the app instead of one part.
CROPS = {"kpis": ".kpis", "categories": "#categories", "spending": "#spending", "unusual": "#unusual", "health": "#health"}
PAGES = {"landing": "/", "upload": "/upload", "dashboard": "/dashboard"}
WIDTHS = {1440: 900, 390: 844}
THEMES = ("light", "dark")


def settle(page) -> None:
    """Wait for fonts and, on the dashboard, for both charts to finish drawing."""
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready")
    # Scroll through once so lazy-loaded pictures are fetched before a full-page capture.
    page.evaluate("async () => { for (let y = 0; y < document.body.scrollHeight; y += 600) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 60)); } window.scrollTo(0, 0); }")
    if page.locator(".chart").count():
        page.wait_for_selector(".chart.is-ready >> nth=1", timeout=20000)
    page.wait_for_timeout(300)


def sign_up(context) -> None:
    page = context.new_page()
    page.goto(f"{BASE}/register")
    for field in ("username", "email", "password"):
        page.fill(f"input[name={field}]", USER[field])
    page.fill("input[name=confirm_password]", USER["password"])
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    if "/login" in page.url:
        page.fill("input[name=username]", USER["username"])
        page.fill("input[name=password]", USER["password"])
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")
    page.click("#demoForm button")
    settle(page)
    page.close()


def themed_context(browser, theme: str, width: int, height: int, scale: float, storage=None):
    context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=scale, storage_state=storage)
    context.add_init_script(f"try {{ localStorage.setItem('finsight-theme', '{theme}'); }} catch (e) {{}}")
    return context


def main() -> None:
    img_dir, shots_dir = ROOT / "static" / "img", ROOT / "docs" / "screenshots"
    img_dir.mkdir(parents=True, exist_ok=True)
    shots_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="finsight-shots-") as tmp:
        # The app builds itself on import, so point it at the throwaway database first.
        os.environ["DATABASE_PATH"] = str(Path(tmp) / "shots.db")
        sys.path.insert(0, str(ROOT))
        from werkzeug.serving import make_server
        from app import app

        server = make_server("127.0.0.1", PORT, app)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="chrome")
                signup = browser.new_context()
                sign_up(signup)
                storage = signup.storage_state()
                signup.close()

                for theme in THEMES:
                    # Crops for the landing page at desktop width, 1.5x for sharp text, JPEG to keep them small.
                    context = themed_context(browser, theme, 1440, 900, 1.5, storage)
                    page = context.new_page()
                    page.goto(f"{BASE}/dashboard")
                    settle(page)
                    page.screenshot(path=img_dir / f"overview-{theme}.jpg", type="jpeg", quality=82)
                    for name, selector in CROPS.items():
                        box = page.locator(selector).first.bounding_box()
                        # A margin of page around each part, so it reads as a crop of the app, not a cut-out.
                        clip = {"x": box["x"] - 24, "y": box["y"] - 24, "width": box["width"] + 48, "height": box["height"] + 48}
                        page.screenshot(path=img_dir / f"{name}-{theme}.jpg", type="jpeg", quality=82, clip=clip, full_page=True)
                    context.close()
                    # The same parts at phone width, so the landing page stays readable on a phone.
                    context = themed_context(browser, theme, 390, 844, 2, storage)
                    page = context.new_page()
                    page.goto(f"{BASE}/dashboard")
                    settle(page)
                    page.screenshot(path=img_dir / f"overview-phone-{theme}.jpg", type="jpeg", quality=80)
                    for name, selector in CROPS.items():
                        box = page.locator(selector).first.bounding_box()
                        clip = {"x": 0, "y": box["y"] - 16, "width": 390, "height": box["height"] + 32}
                        page.screenshot(path=img_dir / f"{name}-phone-{theme}.jpg", type="jpeg", quality=80, clip=clip, full_page=True)
                    context.close()

                for theme in THEMES:
                    for width, height in WIDTHS.items():
                        for name, path in PAGES.items():
                            context = themed_context(browser, theme, width, height, 1, None if name == "landing" else storage)
                            page = context.new_page()
                            page.goto(BASE + path)
                            settle(page)
                            page.screenshot(path=shots_dir / f"{name}-{width}-{theme}.png", full_page=True)
                            context.close()
                browser.close()
        finally:
            server.shutdown()
    print(f"Saved {(len(CROPS) + 1) * len(THEMES) * 2} crops to {img_dir} and {len(PAGES) * len(WIDTHS) * len(THEMES)} pages to {shots_dir}")


if __name__ == "__main__":
    main()
