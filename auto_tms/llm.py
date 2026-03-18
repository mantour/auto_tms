"""Unified LLM interface — dispatches to configured provider.

Providers:
  none      — ddddocr for CAPTCHA, random answers for exams
  anthropic — Claude Haiku (vision) + Sonnet (text)
  openai    — OpenAI API key
  gemini    — Google Gemini via OpenAI-compatible endpoint
  local     — Ollama/vLLM etc via OpenAI-compatible endpoint
"""

import base64
import logging
import os
import random
import re

logger = logging.getLogger("auto_tms.llm")

# Provider env vars
# TMS_LLM_PROVIDER: none | anthropic | openai | gemini | local
# TMS_LLM_API_KEY: API key (openai/gemini)
# TMS_LLM_BASE_URL: base URL (local models)
# TMS_LLM_MODEL: override default model
# ANTHROPIC_API_KEY: Anthropic API key (existing)

CAPTCHA_PROMPT = (
    "This is a CAPTCHA image containing exactly 4 characters. "
    "Characters are lowercase letters (a-z) and digits (0-9) only. "
    "Ignore background lines/noise. "
    "Return ONLY the 4 characters, nothing else."
)


def _get_provider() -> str:
    return os.getenv("TMS_LLM_PROVIDER", "none")


# ---------------------------------------------------------------------------
# CAPTCHA solving
# ---------------------------------------------------------------------------


async def solve_captcha(image_bytes: bytes, mime_type: str = "image/png") -> str:
    """Solve a CAPTCHA image. Dispatches to configured provider."""
    provider = _get_provider()

    if provider == "anthropic":
        return await _captcha_anthropic(image_bytes, mime_type)
    elif provider in ("openai", "gemini"):
        return await _captcha_openai(image_bytes, mime_type)
    else:
        # none, local, or unknown → ddddocr
        return _captcha_ddddocr(image_bytes)


async def _captcha_anthropic(image_bytes: bytes, mime_type: str) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic()
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    model = os.getenv("TMS_LLM_MODEL", "claude-haiku-4-5-20251001")

    message = await client.messages.create(
        model=model,
        max_tokens=64,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64_image}},
                {"type": "text", "text": CAPTCHA_PROMPT},
            ],
        }],
    )
    raw = message.content[0].text.strip()
    return _clean_captcha(raw)


async def _captcha_openai(image_bytes: bytes, mime_type: str) -> str:
    from openai import AsyncOpenAI

    client = _get_openai_client()
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    model = _get_openai_model("gpt-4o-mini")

    response = await client.chat.completions.create(
        model=model,
        max_tokens=64,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_image}"}},
                {"type": "text", "text": CAPTCHA_PROMPT},
            ],
        }],
    )
    raw = response.choices[0].message.content.strip()
    return _clean_captcha(raw)


def _captcha_ddddocr(image_bytes: bytes) -> str:
    try:
        import ddddocr
    except ImportError:
        logger.error("ddddocr not installed. Run: uv pip install ddddocr")
        return ""
    ocr = ddddocr.DdddOcr(show_ad=False)
    result = ocr.classification(image_bytes)
    cleaned = _clean_captcha(result)
    logger.debug("ddddocr result: %s → %s", result, cleaned)
    return cleaned


def _clean_captcha(raw: str) -> str:
    """Clean CAPTCHA text: keep alphanumeric, lowercase, first 4 chars."""
    return re.sub(r"[^a-zA-Z0-9]", "", raw).lower()[:4]


# ---------------------------------------------------------------------------
# Multiple choice answering
# ---------------------------------------------------------------------------


async def ask_multiple_choice(
    questions: list[dict], attempts: list[dict] | None = None
) -> dict[int, str]:
    """Answer multiple choice questions. Dispatches to configured provider.

    Args:
        questions: List of {index, question, choices: [{sn, text}]}
        attempts: Previous failed attempts for context

    Returns dict of question_index -> selected choice letter (A/B/C/D).
    """
    provider = _get_provider()

    if provider == "none":
        return _random_answers(questions)
    elif provider == "anthropic":
        return await _mc_anthropic(questions, attempts)
    else:
        # openai, gemini, local
        return await _mc_openai(questions, attempts)


def _random_answers(questions: list[dict]) -> dict[int, str]:
    """Return random answers for each question."""
    result = {}
    for q in questions:
        if q["choices"]:
            result[q["index"]] = random.choice(q["choices"])["sn"]
    return result


async def _mc_anthropic(
    questions: list[dict], attempts: list[dict] | None
) -> dict[int, str]:
    import anthropic

    client = anthropic.AsyncAnthropic()
    model = os.getenv("TMS_LLM_MODEL", "claude-sonnet-4-20250514")
    prompt = _build_mc_prompt(questions, attempts)

    message = await client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_mc_response(message.content[0].text)


async def _mc_openai(
    questions: list[dict], attempts: list[dict] | None
) -> dict[int, str]:
    client = _get_openai_client()
    model = _get_openai_model("gpt-4o-mini")
    prompt = _build_mc_prompt(questions, attempts)

    response = await client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_mc_response(response.choices[0].message.content)


def _build_mc_prompt(questions: list[dict], attempts: list[dict] | None) -> str:
    """Build the prompt for multiple choice questions with optional history."""
    prompt_parts = []
    for q in questions:
        choices_str = "\n".join(f"  {c['sn']}. {c['text']}" for c in q["choices"])
        prompt_parts.append(f"Q{q['index'] + 1}: {q['question']}\n{choices_str}")

    history_text = ""
    if attempts:
        history_lines = []
        for i, att in enumerate(attempts, 1):
            ans_str = ", ".join(
                f"{q_text}: {sn}" for q_text, sn in att["answers"].items()
            )
            history_lines.append(f"  Attempt {i} (failed): {ans_str}")
        history_text = (
            "\n\nPrevious failed attempts on this exam (these answer combinations were wrong):\n"
            + "\n".join(history_lines)
            + "\n\nTry different answers, especially for questions where previous answers may have been wrong.\n"
        )

    return (
        "Answer the following multiple-choice questions from a training exam. "
        "For each question, respond with ONLY the question number and the letter, "
        "one per line, like:\nQ1: D\nQ2: A\n"
        + history_text + "\n"
        + "\n\n".join(prompt_parts)
    )


def _parse_mc_response(text: str) -> dict[int, str]:
    """Parse LLM response into {question_index: answer_letter}."""
    answers: dict[int, str] = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        match = re.match(r"Q(\d+)\s*:\s*([A-Da-d])", line)
        if match:
            idx = int(match.group(1)) - 1
            letter = match.group(2).upper()
            answers[idx] = letter
    return answers


# ---------------------------------------------------------------------------
# OpenAI-compatible client helper
# ---------------------------------------------------------------------------


def _get_openai_client():
    """Create AsyncOpenAI client configured for the current provider."""
    from openai import AsyncOpenAI

    provider = _get_provider()
    if provider == "gemini":
        return AsyncOpenAI(
            api_key=os.getenv("TMS_LLM_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    elif provider == "local":
        return AsyncOpenAI(
            api_key=os.getenv("TMS_LLM_API_KEY", "dummy"),
            base_url=os.getenv("TMS_LLM_BASE_URL", "http://localhost:11434/v1"),
        )
    else:  # openai
        return AsyncOpenAI(api_key=os.getenv("TMS_LLM_API_KEY"))


def _get_openai_model(default: str) -> str:
    """Get model name, with provider-specific defaults."""
    model = os.getenv("TMS_LLM_MODEL")
    if model:
        return model
    provider = _get_provider()
    if provider == "gemini":
        return "gemini-2.0-flash"
    return default
