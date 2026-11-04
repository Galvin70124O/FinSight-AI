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

# Landing-page crops: name -> (first, last) element of one dashboard component. Each crop runs from
# the top of the first to the bottom of the last, with a thin margin of page. "overview" (the hero) is
# the whole first screen instead.
CROPS = {
    "categories": ("#categories", "#categories .bars"),
    "spending": ("#spending .section-head", "#spending .chart"),
    "health": ("#health .section-head", "#health .health"),
}
CROP_MARGIN = 12
# At 940px the app shows one 660px column beside the sidebar, about the width the landing page
# displays a crop at (~750px), so the smallest chart text (12px) still shows at 13px or more.
CROP_VIEWPORT = 940
PAGES = {"landing": "/", "upload": "/upload", "dashboard": "/dashboard"}
WIDTHS = {1440: 900, 390: 844}
THEMES = ("light", "dark")


def save_crop(page, path, first: str, last: str) -> None:
    top, bottom = page.locator(first).first.bounding_box(), page.locator(last).first.bounding_box()
    left = min(top["x"], bottom["x"]) - CROP_MARGIN
    right = max(top["x"] + top["width"], bottom["x"] + bottom["width"]) + CROP_MARGIN
    clip = {"x": max(left, 0), "y": top["y"] - CROP_MARGIN, "width": right - max(left, 0), "height": bottom["y"] + bottom["height"] - top["y"] + 2 * CROP_MARGIN}
    page.screenshot(path=path, type="jpeg", quality=82, clip=clip, full_page=True)


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
                    # The hero: the whole first screen of the app at desktop width, 1.5x for sharp text.
                    context = themed_context(browser, theme, 1440, 900, 1.5, storage)
                    page = context.new_page()
                    page.goto(f"{BASE}/dashboard")
                    settle(page)
                    page.screenshot(path=img_dir / f"overview-{theme}.jpg", type="jpeg", quality=82)
                    context.close()
                    # Tight crops of single components, at 2x (JPEG keeps them small).
                    for suffix, width in (("", CROP_VIEWPORT), ("-phone", 390)):
                        context = themed_context(browser, theme, width, 900, 2, storage)
                        page = context.new_page()
                        page.goto(f"{BASE}/dashboard")
                        settle(page)
                        if suffix == "-phone":
                            page.screenshot(path=img_dir / f"overview-phone-{theme}.jpg", type="jpeg", quality=80)
                        for name, (first, last) in CROPS.items():
                            save_crop(page, img_dir / f"{name}{suffix}-{theme}.jpg", first, last)
                        context.close()

                for theme in THEMES:
                    for width, height in WIDTHS.items():
                        for name, path in PAGES.items():
                            context = themed_context(browser, theme, width, height, 1, None if name == "landing" else storage)
                            page = context.new_page()
                            page.goto(BASE + path)
                            settle(page)
                            # A full-page capture would paint the fixed tab bar mid-page; show it at the bottom instead.
                            page.add_style_tag(content=".tabbar { position: static; }")
                            page.screenshot(path=shots_dir / f"{name}-{width}-{theme}.png", full_page=True)
                            context.close()
                browser.close()
        finally:
            server.shutdown()
    print(f"Saved {(len(CROPS) + 1) * len(THEMES) * 2} crops to {img_dir} and {len(PAGES) * len(WIDTHS) * len(THEMES)} pages to {shots_dir}")


if __name__ == "__main__":
    main()
