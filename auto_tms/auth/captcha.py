"""CAPTCHA solving using Claude Vision API."""

import base64
import logging

import anthropic

logger = logging.getLogger("auto_tms.auth.captcha")


async def solve_captcha(image_bytes: bytes, mime_type: str = "image/png") -> str:
    """Send a CAPTCHA image to Claude Vision API and return the text.

    Args:
        image_bytes: Raw bytes of the CAPTCHA image.
        mime_type: MIME type of the image (e.g., "image/png", "image/jpeg").

    Returns:
        The CAPTCHA text extracted by Claude.
    """
    client = anthropic.AsyncAnthropic()
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    message = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=64,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": b64_image,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a CAPTCHA image containing exactly 4 characters. "
                            "Characters are lowercase letters (a-z) and digits (0-9) only. "
                            "Ignore background lines/noise. "
                            "Return ONLY the 4 characters, nothing else."
                        ),
                    },
                ],
            }
        ],
    )

    raw = message.content[0].text.strip()
    # Post-process: keep only alphanumeric, force lowercase, take first 4
    import re
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", raw).lower()[:4]
    logger.debug("CAPTCHA raw=%s cleaned=%s", raw, cleaned)
    return cleaned
