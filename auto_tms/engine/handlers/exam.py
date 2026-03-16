"""Exam handler — Claude API for answering, with retry and answer harvesting."""

import logging
import re

import anthropic
from playwright.async_api import BrowserContext

logger = logging.getLogger("auto_tms.engine.handlers.exam")

MAX_EXAM_ATTEMPTS = 5


async def handle_exam(context: BrowserContext, url: str) -> bool:
    """Solve a multiple-choice exam using Claude API.

    Flow:
    1. Navigate to exam info page
    2. Click 開始測驗 to enter the exam
    3. Wait for questions to load (networkidle on /kexam/ page)
    4. Scrape questions from div.kques-item elements
    5. Ask Claude for answers
    6. Fill in radio buttons and submit (交卷)
    7. On failure: harvest correct answers if shown, retry

    Returns True if exam passed.
    """
    if not url.startswith("http"):
        from ...config import get_base_url
        url = f"{get_base_url()}{url}"

    known_answers: dict[int, str] = {}

    for attempt in range(1, MAX_EXAM_ATTEMPTS + 1):
        logger.info("Exam %s: attempt %d/%d", url, attempt, MAX_EXAM_ATTEMPTS)

        page = await context.new_page()
        try:
            # Step 1: Navigate to exam info page
            await page.goto(url, wait_until="load")

            # Step 2: Click 開始測驗 button
            start_btn = page.locator('button:has-text("開始測驗")')
            if await start_btn.count() > 0:
                logger.debug("Clicking 開始測驗")
                await start_btn.first.click()
                # Wait for the kexam page to load
                await page.wait_for_load_state("networkidle", timeout=120000)
                await page.wait_for_timeout(3000)
            else:
                logger.debug("No 開始測驗 button, already on exam page")

            # Step 3: Scrape questions
            questions = await _scrape_questions(page)
            if not questions:
                logger.error("Exam %s: no questions found", url)
                await page.close()
                continue

            logger.info("Exam %s: found %d questions", url, len(questions))

            # Step 4: Get answers from Claude (or known answers)
            answers = await _get_answers(questions, known_answers)

            # Step 5: Fill in answers
            await _fill_answers(page, questions, answers)

            # Step 6: Submit (交卷)
            submit_btn = page.locator('button:has-text("交卷"), a:has-text("交卷")')
            if await submit_btn.count() > 0:
                await submit_btn.first.click()
                # Handle confirmation dialog if any
                confirm_btn = page.locator('button:has-text("確定"), button:has-text("確認"), .btn-primary:has-text("確")')
                await page.wait_for_timeout(2000)
                if await confirm_btn.count() > 0 and await confirm_btn.first.is_visible():
                    await confirm_btn.first.click()
                await page.wait_for_timeout(5000)
            else:
                logger.warning("Exam %s: no submit button found", url)

            # Step 7: Check result
            passed = await _check_pass(page)
            if passed:
                logger.info("Exam %s: PASSED on attempt %d", url, attempt)
                return True

            logger.info("Exam %s: did not pass, checking for correct answers", url)

            # Harvest correct answers if shown
            harvested = await _harvest_correct_answers(page)
            if harvested:
                known_answers.update(harvested)
                logger.info("Exam %s: harvested %d correct answers", url, len(harvested))

        except Exception:
            logger.error("Exam %s: attempt %d failed", url, attempt, exc_info=True)
        finally:
            await page.close()

    logger.error("Exam %s: failed after %d attempts", url, MAX_EXAM_ATTEMPTS)
    return False


async def _scrape_questions(page) -> list[dict]:
    """Scrape questions from kques-item elements.

    Returns list of dicts: {index, question, choices: [{sn, text, value, name}]}
    """
    questions = await page.evaluate("""
        () => {
            const questions = [];
            document.querySelectorAll('.kques-item').forEach((item, idx) => {
                const questionEl = item.querySelector('.question');
                const questionText = questionEl?.textContent?.trim() || '';

                const choices = [];
                item.querySelectorAll('.option-item').forEach(li => {
                    const input = li.querySelector('input');
                    const sn = li.querySelector('.optionSn')?.textContent?.trim() || '';
                    const text = li.querySelector('.option')?.textContent?.trim() || '';
                    choices.push({
                        sn: sn.replace('.', ''),
                        text: text,
                        value: input?.value || '',
                        name: input?.name || '',
                        inputId: input?.id || '',
                    });
                });

                questions.push({
                    index: idx,
                    question: questionText,
                    choices: choices,
                });
            });
            return questions;
        }
    """)
    return questions


async def _get_answers(
    questions: list[dict], known_answers: dict[int, str]
) -> dict[int, str]:
    """Get answers for questions. Returns dict of question_index -> choice sn (A/B/C/D).

    Uses known answers first, then asks Claude for the rest.
    """
    unknown = [q for q in questions if q["index"] not in known_answers]

    claude_answers: dict[int, str] = {}
    if unknown:
        claude_answers = await _ask_claude(unknown)

    # Merge
    result: dict[int, str] = {}
    for q in questions:
        idx = q["index"]
        if idx in known_answers:
            result[idx] = known_answers[idx]
        elif idx in claude_answers:
            result[idx] = claude_answers[idx]
        elif q["choices"]:
            result[idx] = q["choices"][0]["sn"]  # fallback: first choice
    return result


async def _ask_claude(questions: list[dict]) -> dict[int, str]:
    """Ask Claude API to answer multiple-choice questions.

    Returns dict of question_index -> selected choice letter (A/B/C/D).
    """
    prompt_parts = []
    for q in questions:
        choices_str = "\n".join(
            f"  {c['sn']}. {c['text']}" for c in q["choices"]
        )
        prompt_parts.append(f"Q{q['index'] + 1}: {q['question']}\n{choices_str}")

    prompt = (
        "Answer the following multiple-choice questions from a training exam. "
        "For each question, respond with ONLY the question number and the letter, "
        "one per line, like:\nQ1: D\nQ2: A\n\n"
        + "\n\n".join(prompt_parts)
    )

    client = anthropic.AsyncAnthropic()
    message = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text
    logger.debug("Claude response: %s", response_text)

    answers: dict[int, str] = {}
    for line in response_text.strip().split("\n"):
        line = line.strip()
        match = re.match(r"Q(\d+)\s*:\s*([A-Da-d])", line)
        if match:
            idx = int(match.group(1)) - 1  # convert back to 0-indexed
            letter = match.group(2).upper()
            answers[idx] = letter

    return answers


async def _fill_answers(page, questions: list[dict], answers: dict[int, str]) -> None:
    """Click the radio button for each answer."""
    for q in questions:
        idx = q["index"]
        selected_sn = answers.get(idx)
        if not selected_sn:
            continue

        # Find the matching choice
        for choice in q["choices"]:
            if choice["sn"] == selected_sn:
                if choice["name"] and choice["value"]:
                    radio = page.locator(
                        f'input[name="{choice["name"]}"][value="{choice["value"]}"]'
                    )
                    if await radio.count() > 0:
                        await radio.first.click()
                        logger.debug("Q%d: selected %s", idx + 1, selected_sn)
                        break
                elif choice["inputId"]:
                    radio = page.locator(f'#{choice["inputId"]}')
                    if await radio.count() > 0:
                        await radio.first.click()
                        logger.debug("Q%d: selected %s", idx + 1, selected_sn)
                        break
        else:
            # Fallback: click by option-item position
            items = page.locator(f'.kques-item:nth-child({idx + 1}) .option-item')
            target_idx = ord(selected_sn) - ord('A')
            if 0 <= target_idx < await items.count():
                radio = items.nth(target_idx).locator('input')
                if await radio.count() > 0:
                    await radio.first.click()
                    logger.debug("Q%d: selected %s (by position)", idx + 1, selected_sn)


async def _check_pass(page) -> bool:
    """Check if the exam was passed after submission."""
    content = await page.content()
    # Check for score or pass indicators
    fail_indicators = ["未通過", "不及格", "未達"]
    pass_indicators = ["通過", "及格", "合格", "恭喜"]

    for indicator in fail_indicators:
        if indicator in content:
            return False
    for indicator in pass_indicators:
        if indicator in content:
            return True

    # Try to find score
    score_match = re.search(r"(\d+)\s*分", content)
    if score_match:
        score = int(score_match.group(1))
        logger.info("Exam score: %d", score)
        return score >= 100  # 測驗及格: 100分

    return False


async def _harvest_correct_answers(page) -> dict[int, str]:
    """Try to extract correct answers from the result/review page.

    Returns dict of question_index -> correct choice letter (A/B/C/D).
    """
    harvested = await page.evaluate("""
        () => {
            const answers = {};
            document.querySelectorAll('.kques-item').forEach((item, idx) => {
                // Look for correct answer markers
                const correctEl = item.querySelector(
                    '.option-item.correct, .option-item.right, ' +
                    '.option-item .correct, [class*="correct"], [class*="right-answer"]'
                );
                if (correctEl) {
                    const sn = correctEl.querySelector('.optionSn')?.textContent?.trim()?.replace('.', '') || '';
                    if (sn) answers[idx] = sn;
                }

                // Also check for checked correct answers (green highlight etc.)
                item.querySelectorAll('.option-item').forEach(li => {
                    const classes = li.className || '';
                    if (classes.includes('correct') || classes.includes('right')) {
                        const sn = li.querySelector('.optionSn')?.textContent?.trim()?.replace('.', '') || '';
                        if (sn) answers[idx] = sn;
                    }
                });
            });
            return answers;
        }
    """)
    return {int(k): v for k, v in harvested.items()} if harvested else {}
