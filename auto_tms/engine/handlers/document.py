"""Document handler — open PDF/PPT once to register completion."""

import logging

from playwright.async_api import BrowserContext

logger = logging.getLogger("auto_tms.engine.handlers.document")


async def handle_document(context: BrowserContext, url: str) -> bool:
    """Open a document link to register it as viewed.

    Args:
        context: Playwright browser context.
        url: Document URL.

    Returns:
        True if document was opened successfully.
    """
    page = await context.new_page()
    try:
        if not url.startswith("http"):
            from ...config import get_base_url
            url = f"{get_base_url()}{url}"

        await page.goto(url, wait_until="load")
        # Give the server time to register the view
        await page.wait_for_timeout(3000)
        logger.info("Document %s: opened successfully", url)
        return True
    except Exception:
        logger.error("Document %s: failed to open", url, exc_info=True)
        return False
    finally:
        await page.close()
