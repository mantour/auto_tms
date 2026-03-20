"""CAPTCHA solving — delegates to configured LLM provider."""

import logging

from ..llm import solve_captcha

logger = logging.getLogger("auto_tms.auth.captcha")

# Re-export solve_captcha for backward compatibility
__all__ = ["solve_captcha"]
