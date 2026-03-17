"""Survey handler — navigate to questionnaire, fill first option, submit."""

import logging

from playwright.async_api import BrowserContext

logger = logging.getLogger("auto_tms.engine.handlers.survey")


async def handle_survey(context: BrowserContext, url: str) -> bool:
    """Auto-fill and submit a survey.

    Flow:
    1. Open poll page → click「開始填寫」/「繼續填寫」
    2. Wait for kquestionnaire JS to render questions (networkidle)
    3. Select first radio in each group, fill textareas with「無」
    4. Submit and verify

    Args:
        context: Playwright browser context.
        url: Survey page URL.

    Returns:
        True if survey was submitted successfully.
    """
    page = await context.new_page()
    try:
        if not url.startswith("http"):
            from ...config import get_base_url
            url = f"{get_base_url()}{url}"

        await page.goto(url, wait_until="networkidle")

        # Step 1: Click「開始填寫」or「繼續填寫」if present
        start_btn = page.locator('button:has-text("填寫")')
        if await start_btn.count() > 0:
            btn_text = (await start_btn.first.text_content() or "").strip()
            logger.info("Survey %s: clicking '%s'", url, btn_text)
            async with page.expect_navigation(wait_until="networkidle", timeout=30000):
                await start_btn.first.click()
        elif "/kquestionnaire/" not in page.url:
            logger.warning("Survey %s: no start button and not on questionnaire page", url)
            return False

        # Step 2: Wait for questions to render
        try:
            await page.wait_for_selector(
                "input[type=radio]", state="attached", timeout=15000
            )
        except Exception:
            logger.error("Survey %s: questions did not load (no radio buttons)", url)
            return False

        # Step 3: Fill answers — first radio in each group
        radio_groups = await page.evaluate("""
            () => {
                const names = new Set();
                document.querySelectorAll('input[type="radio"]').forEach(r => {
                    if (r.name && !r.name.startsWith('mobile_')) names.add(r.name);
                });
                return [...names];
            }
        """)
        logger.info("Survey %s: %d question groups", url, len(radio_groups))

        for name in radio_groups:
            first_radio = page.locator(f'input[type="radio"][name="{name}"]').first
            if await first_radio.count() > 0:
                await first_radio.click(force=True)

        # Fill visible text areas with「無」
        textareas = page.locator("textarea:visible")
        for i in range(await textareas.count()):
            await textareas.nth(i).fill("無")

        # Fill rich text editor (iframe-based RTE) with「無」
        rte_body = page.frame_locator(".richtexteditor iframe").locator("body")
        if await rte_body.count() > 0:
            await rte_body.fill("無")
            logger.debug("Survey %s: filled RTE iframe", url)

        # Step 4: Submit
        submit_btn = page.locator(
            'button:has-text("送出"), input[type="submit"], '
            'button[type="submit"]:has-text("送出")'
        )
        if await submit_btn.count() == 0:
            logger.warning("Survey %s: no submit button found", url)
            return False

        url_before = page.url
        await submit_btn.first.click()
        await page.wait_for_timeout(3000)

        # Verify: check if navigated away or shows completion
        url_after = page.url
        if url_after != url_before:
            logger.info("Survey %s: submitted (navigated to %s)", url, url_after[:80])
            return True

        # Still on same page — check for success indicators or remaining questions
        body_text = await page.evaluate("() => document.body.innerText")
        if "已完成" in body_text or "感謝" in body_text:
            logger.info("Survey %s: submitted (completion message found)", url)
            return True

        # Check if there's a confirm dialog (bootbox uses "OK", others use 確定/送出)
        confirm_btn = page.locator(
            '.modal button:has-text("OK"), '
            '.bootbox button:has-text("OK"), '
            '.modal button:has-text("送出"), '
            'button:has-text("確定"), '
            'button:has-text("確認")'
        )
        if await confirm_btn.count() > 0 and await confirm_btn.first.is_visible():
            await confirm_btn.first.click()
            await page.wait_for_timeout(5000)
            logger.info("Survey %s: submitted (confirmed dialog)", url)
            return True

        logger.warning("Survey %s: submit clicked but unclear if successful", url)
        return True  # Optimistic — we filled and clicked submit

    except Exception:
        logger.error("Survey %s: failed", url, exc_info=True)
        return False
    finally:
        await page.close()
