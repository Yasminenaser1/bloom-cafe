"""Save README screenshots of the live site.
Mac-only tool: needs `pip install playwright` + `python -m playwright install chromium`
(deliberately NOT in requirements.txt, so Render doesn't install a browser)."""
from pathlib import Path

from playwright.sync_api import sync_playwright

SITE = "https://coffee-bloom.onrender.com"
OUT = Path("figures")
WAIT = 90_000


def home(page):
    page.goto(f"{SITE}/", timeout=120_000)                 # free tier may need to wake up
    page.wait_for_selector("#favorites li:not(.muted)", timeout=WAIT)


def menu(page):
    page.goto(f"{SITE}/menu", timeout=WAIT)
    page.wait_for_selector(".item .buy button", timeout=WAIT)
    buttons = page.locator(".item .buy button")
    for i in (0, 2, 2, 10):                                # a few items, one twice
        buttons.nth(i).click()
    page.wait_for_selector("#cart-total:not(:empty)")


def chat(page):
    page.goto(f"{SITE}/chat", timeout=WAIT)
    page.get_by_role("button", name="How did lattes do last week?").click()
    page.wait_for_selector(".msg.bot:not(.thinking)", timeout=WAIT)
    page.fill("#question", "is the afternoon dead?")
    page.press("#question", "Enter")
    page.wait_for_selector(".msg .suggest", timeout=WAIT)
    page.click(".msg .suggest")
    page.wait_for_function("document.querySelectorAll('.msg.bot:not(.thinking)').length >= 3",
                           timeout=WAIT)


def dashboard(page):
    page.goto(f"{SITE}/dashboard", timeout=WAIT)
    page.wait_for_selector("#alerts .alert", timeout=WAIT)
    page.wait_for_selector("#ai-insights .insight", timeout=WAIT)
    page.wait_for_selector("#daily-chart", state="visible")


SHOTS = {"home": (home, 1000), "menu": (menu, 1000), "chat": (chat, 900), "dashboard": (dashboard, 1100)}

OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, (visit, height) in SHOTS.items():
        page = browser.new_page(viewport={"width": 1280, "height": height}, device_scale_factor=2)
        visit(page)
        page.wait_for_timeout(1500)                        # let animations settle
        page.screenshot(path=str(OUT / f"{name}.png"))
        page.close()
        print(f"Saved {OUT / name}.png")
    browser.close()
