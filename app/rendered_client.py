from __future__ import annotations

import os
from typing import Optional

from app.wis_client import wis_auth_status, get_wis_session


def _cookie_domain_ok(cookie_domain: str) -> bool:
    return "whatifsports.com" in (cookie_domain or "")


async def get_rendered_html(url: str, wait_selector: str = "table", timeout_ms: int = 45000) -> str:
    """
    Fetch final rendered DOM with Playwright.

    Why this exists:
      WhatIfSports RatingsHistory pages can show potential_* classes in the browser DOM
      that are missing from raw requests/BeautifulSoup HTML. Playwright reads the
      actual post-render DOM.
    """
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError(f"Playwright unavailable: {exc}") from exc

    # Reuse requests session login/cookies. This supports WIS_COOKIE and/or
    # WIS_USERNAME/WIS_PASSWORD from Render env vars.
    session = get_wis_session()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        )

        cookies = []
        for c in session.cookies:
            domain = c.domain or ".whatifsports.com"
            if not _cookie_domain_ok(domain):
                domain = ".whatifsports.com"
            cookies.append({
                "name": c.name,
                "value": c.value,
                "domain": domain,
                "path": c.path or "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            })

        if cookies:
            await context.add_cookies(cookies)

        page = await context.new_page()
        await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        try:
            await page.wait_for_selector(wait_selector, timeout=10000)
        except Exception:
            pass

        # Give any late WIS scripts a moment to decorate cells/classes.
        await page.wait_for_timeout(1000)

        html = await page.content()
        await browser.close()
        return html


def rendered_fetch_status() -> dict:
    try:
        import playwright  # noqa: F401
        playwright_available = True
    except Exception:
        playwright_available = False

    return {
        "playwright_available": playwright_available,
        "wis_auth": wis_auth_status(),
    }
