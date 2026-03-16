"""Login flow for TMS with CAPTCHA handling."""

import logging

from playwright.async_api import BrowserContext, Page

from ..config import get_base_url, load_credentials
from .captcha import solve_captcha
from .session import save_session

logger = logging.getLogger("auto_tms.auth.login")
MAX_CAPTCHA_RETRIES = 10


async def login(context: BrowserContext) -> Page:
    """Perform login with CAPTCHA solving. Returns an authenticated page.

    The login form submits via AJAX (stays on same page). On success the page
    redirects via JS; on failure the CAPTCHA refreshes.

    Retries CAPTCHA solving up to MAX_CAPTCHA_RETRIES times.
    Saves session state on success.

    Raises:
        RuntimeError: If login fails after all retries.
    """
    user, passwd = load_credentials()
    page = await context.new_page()

    for attempt in range(1, MAX_CAPTCHA_RETRIES + 1):
        logger.info("Login attempt %d/%d", attempt, MAX_CAPTCHA_RETRIES)

        # Full page reload each attempt to reset form state
        await page.goto(f"{get_base_url()}/index/login", wait_until="load")

        # Fill credentials
        await page.fill('input[name="account"]', user)
        await page.fill('input[name="password"]', passwd)

        # Screenshot the CAPTCHA image and solve it
        captcha_img = page.locator("img.js-captcha")
        img_bytes = await captcha_img.screenshot()
        captcha_text = await solve_captcha(img_bytes)
        logger.info("CAPTCHA answer: %s", captcha_text)
        await page.fill('input[name="captcha"]', captcha_text)

        # Submit via the 登入 button (AJAX submit, may redirect on success)
        await page.click("button.btn-primary")

        # Wait for AJAX response and possible modal/redirect
        await page.wait_for_timeout(3000)

        # Handle multi-login modal: "此帳號已在其他的位置登入"
        kick_btn = page.locator("a.kickOtherBtn")
        if await kick_btn.is_visible():
            logger.info("Multi-login detected, logging out other sessions")
            await kick_btn.click()
            await page.wait_for_timeout(3000)

        # Check if redirected away from login (success)
        if "/index/login" not in page.url:
            logger.info("Login successful — redirected to %s", page.url)
            await save_session(context)
            return page

        # Wait a bit more and check again (redirect can be slow)
        try:
            await page.wait_for_url(
                lambda url: "/index/login" not in url, timeout=5000
            )
            logger.info("Login successful — redirected to %s", page.url)
            await save_session(context)
            return page
        except Exception:
            pass

        # Still on login page — log any visible errors
        error_el = page.locator(".alert-danger, .alert-warning")
        if await error_el.count() > 0:
            for i in range(await error_el.count()):
                el = error_el.nth(i)
                if await el.is_visible():
                    error_text = await el.text_content()
                    logger.warning("Login error: %s", (error_text or "").strip())

    await page.close()
    raise RuntimeError(f"Login failed after {MAX_CAPTCHA_RETRIES} CAPTCHA attempts")


async def ensure_authenticated(context: BrowserContext) -> Page:
    """Ensure we have a valid session, logging in if necessary.

    Returns an authenticated page ready for use.
    """
    from .session import is_session_valid

    base = get_base_url()
    if await is_session_valid(context, base):
        page = await context.new_page()
        await page.goto(f"{base}/program/mine", wait_until="load")
        return page

    logger.info("No valid session, performing login")
    return await login(context)
