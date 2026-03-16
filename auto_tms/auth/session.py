"""Playwright session persistence — save/load browser storage state."""

import logging
from pathlib import Path

from playwright.async_api import BrowserContext

from ..config import SESSION_DIR

logger = logging.getLogger("auto_tms.auth.session")

STORAGE_STATE_FILE = SESSION_DIR / "storage_state.json"


async def save_session(context: BrowserContext) -> None:
    """Save browser storage state (cookies + localStorage) to disk."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(STORAGE_STATE_FILE))
    logger.info("Session saved to %s", STORAGE_STATE_FILE)


def has_saved_session() -> bool:
    """Check if a saved session file exists."""
    return STORAGE_STATE_FILE.exists()


def get_storage_state_path() -> str | None:
    """Return path to storage state file if it exists."""
    if STORAGE_STATE_FILE.exists():
        return str(STORAGE_STATE_FILE)
    return None


async def is_session_valid(context: BrowserContext, base_url: str) -> bool:
    """Test if the current session is still authenticated.

    Navigates to a protected page and checks if we get redirected to login.
    """
    page = await context.new_page()
    try:
        response = await page.goto(f"{base_url}/program/mine", wait_until="load")
        url = page.url
        # If redirected to login page, session is invalid
        if "login" in url.lower() or (response and response.status == 401):
            logger.info("Session expired or invalid")
            return False
        logger.info("Session is valid")
        return True
    except Exception:
        logger.warning("Failed to validate session", exc_info=True)
        return False
    finally:
        await page.close()
