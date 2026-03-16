"""Playwright browser context management."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from playwright.async_api import BrowserContext, async_playwright

from ..config import get_proxy, load_env
from .session import get_storage_state_path

logger = logging.getLogger("auto_tms.auth.browser")


@asynccontextmanager
async def create_browser_context(headless: bool = True) -> AsyncIterator[BrowserContext]:
    """Create a Playwright browser context, loading saved session if available.

    If TMS_PROXY is set (e.g. socks5://127.0.0.1:1080), routes browser
    traffic through that proxy (useful for SSH tunnel via nas).

    Usage:
        async with create_browser_context() as context:
            page = await context.new_page()
            ...
    """
    load_env()
    proxy = get_proxy()

    async with async_playwright() as p:
        launch_args: dict = {"headless": headless}
        if proxy:
            launch_args["proxy"] = {"server": proxy}
            logger.info("Using proxy: %s", proxy)
        browser = await p.chromium.launch(**launch_args)
        storage_state = get_storage_state_path()
        if storage_state:
            logger.debug("Loading saved session from %s", storage_state)
        context = await browser.new_context(
            storage_state=storage_state,
            locale="zh-TW",
        )
        context.set_default_timeout(120_000)
        context.set_default_navigation_timeout(300_000)
        try:
            yield context
        finally:
            await context.close()
            await browser.close()
