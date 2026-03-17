"""Video handler — headless playback with network request logging."""

import json
import logging
from datetime import datetime
from pathlib import Path

from playwright.async_api import BrowserContext, Request

from ...config import DATA_DIR

logger = logging.getLogger("auto_tms.engine.handlers.video")

NETWORK_LOG_DIR = DATA_DIR / "network_logs"


async def handle_video(
    context: BrowserContext,
    url: str,
    required_minutes: int | None,
    recorded_minutes: int = 0,
) -> bool:
    """Play a video in headless browser and wait for the required duration.

    If recorded_minutes is provided, only plays the remaining difference.

    Args:
        context: Playwright browser context.
        url: Video page URL (e.g., /media/273090).
        required_minutes: Minimum playback minutes needed, or None.
        recorded_minutes: Minutes already recorded on the web.

    Returns:
        True if playback duration was satisfied.
    """
    if required_minutes is None:
        required_minutes = 1  # Default: just open it briefly

    effective_minutes = max(0, required_minutes - recorded_minutes)
    if effective_minutes <= 0:
        logger.info("Video %s: already recorded %dm >= required %dm, skipping",
                     url, recorded_minutes, required_minutes)
        return True

    wait_ms = effective_minutes * 60 * 1000 + 30_000  # Add 30s buffer

    if recorded_minutes > 0:
        logger.info("Video %s: need %d min, recorded %dm, playing %dm (+30s buffer)",
                     url, required_minutes, recorded_minutes, effective_minutes)
    else:
        logger.info("Video %s: need %d min, waiting %d ms", url, required_minutes, wait_ms)

    page = await context.new_page()
    network_requests: list[dict] = []

    # Log network requests for future optimization analysis
    async def on_request(request: Request) -> None:
        if request.resource_type in ("xhr", "fetch"):
            network_requests.append({
                "timestamp": datetime.now().isoformat(),
                "method": request.method,
                "url": request.url,
                "resource_type": request.resource_type,
            })

    page.on("request", on_request)

    try:
        if not url.startswith("http"):
            from ...config import get_base_url
            url = f"{get_base_url()}{url}"

        await page.goto(url, wait_until="load")

        # The site uses a custom player with a .fs-playBtn overlay
        # Try clicking the overlay first, then fallback to JS play
        play_overlay = page.locator(".fs-playBtn")
        if await play_overlay.count() > 0 and await play_overlay.first.is_visible():
            await play_overlay.first.click()
            logger.debug("Clicked .fs-playBtn overlay")
            await page.wait_for_timeout(2000)

        # Also try to start video via JS (works even if click was intercepted)
        await page.evaluate("""
            () => {
                const video = document.querySelector('video');
                if (video) {
                    video.muted = true;
                    video.play().catch(() => {});
                }
            }
        """)
        logger.debug("Triggered video.play() via JS")

        # Wait for the required duration
        logger.info("Video %s: waiting %d minutes...", url, required_minutes)
        await page.wait_for_timeout(wait_ms)

        logger.info("Video %s: playback time reached", url)

        # Save network log for future analysis
        _save_network_log(url, network_requests)

        return True
    except Exception:
        logger.error("Video %s: playback failed", url, exc_info=True)
        return False
    finally:
        await page.close()


def _save_network_log(video_url: str, requests: list[dict]) -> None:
    """Save captured network requests for future optimization analysis."""
    if not requests:
        return
    NETWORK_LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Use a safe filename from the URL
    safe_name = video_url.split("/")[-1].split("?")[0]
    log_file = NETWORK_LOG_DIR / f"video_{safe_name}_{datetime.now():%Y%m%d_%H%M%S}.json"
    log_file.write_text(json.dumps(requests, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.debug("Saved %d network requests to %s", len(requests), log_file)
