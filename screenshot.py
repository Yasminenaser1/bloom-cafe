"""Save a README screenshot of the live dashboard.
Mac-only tool: needs `pip install playwright` + `python -m playwright install chromium`
(deliberately NOT in requirements.txt, so Render doesn't install a browser)."""
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://coffee-bloom.onrender.com/dashboard"
OUT = Path("figures/dashboard.png")

OUT.parent.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 1100}, device_scale_factor=2)
    page.goto(URL, timeout=120_000)                    # free tier may need to wake up
    page.wait_for_selector("#alerts .alert", timeout=90_000)
    page.wait_for_selector("#ai-insights .insight", timeout=90_000)
    page.wait_for_selector("#daily-chart", state="visible")
    page.wait_for_timeout(1500)                        # let chart animations finish
    page.screenshot(path=str(OUT))
    browser.close()

print(f"Saved {OUT}")
