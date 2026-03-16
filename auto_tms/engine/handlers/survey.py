"""Survey handler — auto-fill: first option for multiple choice, '無' for text."""

import logging

from playwright.async_api import BrowserContext

logger = logging.getLogger("auto_tms.engine.handlers.survey")


async def handle_survey(context: BrowserContext, url: str) -> bool:
    """Auto-fill and submit a survey.

    Strategy: select the first option for every question, fill text fields with '無'.

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

        await page.goto(url, wait_until="load")

        # Select first radio button in each question group
        radio_groups = await page.evaluate("""
            () => {
                const names = new Set();
                document.querySelectorAll('input[type="radio"]').forEach(r => {
                    if (r.name) names.add(r.name);
                });
                return [...names];
            }
        """)
        for name in radio_groups:
            first_radio = page.locator(f'input[type="radio"][name="{name}"]').first
            if await first_radio.count() > 0:
                await first_radio.click()

        # Check first checkbox in each group if needed
        checkbox_groups = await page.evaluate("""
            () => {
                const names = new Set();
                document.querySelectorAll('input[type="checkbox"]').forEach(c => {
                    if (c.name) names.add(c.name);
                });
                return [...names];
            }
        """)
        for name in checkbox_groups:
            first_cb = page.locator(f'input[type="checkbox"][name="{name}"]').first
            if await first_cb.count() > 0:
                await first_cb.click()

        # Fill text areas and text inputs with '無'
        textareas = page.locator("textarea")
        for i in range(await textareas.count()):
            await textareas.nth(i).fill("無")

        text_inputs = page.locator('input[type="text"]')
        for i in range(await text_inputs.count()):
            # Skip inputs that look like they're part of the form structure (name, search, etc.)
            input_name = await text_inputs.nth(i).get_attribute("name") or ""
            if "captcha" in input_name.lower() or "search" in input_name.lower():
                continue
            await text_inputs.nth(i).fill("無")

        # Select first option in any dropdowns
        selects = page.locator("select")
        for i in range(await selects.count()):
            options = selects.nth(i).locator("option")
            if await options.count() > 1:
                value = await options.nth(1).get_attribute("value")  # skip empty first option
                if value:
                    await selects.nth(i).select_option(value)

        # Submit
        submit_btn = page.locator(
            'button[type="submit"], input[type="submit"], '
            'button:has-text("送出"), button:has-text("提交"), a:has-text("送出")'
        )
        if await submit_btn.count() > 0:
            await submit_btn.first.click()
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            logger.info("Survey %s: submitted", url)
            return True

        logger.warning("Survey %s: no submit button found", url)
        return False
    except Exception:
        logger.error("Survey %s: failed", url, exc_info=True)
        return False
    finally:
        await page.close()
