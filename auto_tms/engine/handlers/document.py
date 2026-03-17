"""Document handler — open document and click 結束閱讀 to register completion."""

import logging

from playwright.async_api import BrowserContext

logger = logging.getLogger("auto_tms.engine.handlers.document")


async def handle_document(context: BrowserContext, url: str) -> bool:
    """Open a document page and click 結束閱讀 to register the read.

    The TMS records a document as read only when the user clicks the
    結束閱讀 button (.fs-endReading) in the navbar, which navigates
    back to the course page and signals the server.

    Args:
        context: Playwright browser context.
        url: Document URL (e.g., /media/273522).

    Returns:
        True if 結束閱讀 was clicked successfully.
    """
    page = await context.new_page()
    try:
        if not url.startswith("http"):
            from ...config import get_base_url
            url = f"{get_base_url()}{url}"

        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(3000)

        # Click 結束閱讀 to register the read with the server
        end_btn = page.locator(".fs-endReading")
        if await end_btn.count() > 0:
            await end_btn.first.click()
            await page.wait_for_timeout(3000)
            logger.info("Document %s: clicked 結束閱讀", url)
            return True

        logger.warning("Document %s: no 結束閱讀 button found", url)
        return False
    except Exception:
        logger.error("Document %s: failed", url, exc_info=True)
        return False
    finally:
        await page.close()
