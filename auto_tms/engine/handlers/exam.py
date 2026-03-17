"""Exam handler — Claude API for answering, with persistent answer memory."""

import itertools
import json
import logging
import random
import re
from pathlib import Path

import anthropic
from playwright.async_api import BrowserContext

from ...config import DATA_DIR

logger = logging.getLogger("auto_tms.engine.handlers.exam")

MAX_CLAUDE_ATTEMPTS = 3
MAX_EXAM_ATTEMPTS = 50  # Accommodate brute force
EXAM_MEMORY_DIR = DATA_DIR / "state" / "exam_memory"


# ---------------------------------------------------------------------------
# Exam memory persistence
# ---------------------------------------------------------------------------


def _memory_path(exam_id: str) -> Path:
    return EXAM_MEMORY_DIR / f"{exam_id}.json"


def _load_memory(exam_id: str) -> dict:
    """Load exam answer memory from file.

    Structure: {"attempts": [{"answers": {q_text: sn}}], "correct": {q_text: sn}}
    """
    path = _memory_path(exam_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load exam memory %s", path)
    return {"attempts": [], "correct": {}}


def _save_memory(exam_id: str, memory: dict) -> None:
    EXAM_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    _memory_path(exam_id).write_text(
        json.dumps(memory, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _extract_exam_id(url: str) -> str:
    """Extract exam ID from URL like /course/199575/exam/33128."""
    match = re.search(r"/exam/(\d+)", url)
    return match.group(1) if match else url.split("/")[-1].split("?")[0]


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


async def handle_exam(
    context: BrowserContext, url: str, course_id: str | None = None
) -> bool:
    """Solve a multiple-choice exam using Claude API with persistent answer memory.

    Flow:
    1. Navigate to exam info page
    2. Click「開始測驗」or「繼續測驗」
    3. Wait for kexam JS to render questions
    4. Use known correct answers + ask Claude for unknowns
    5. Ensure answer combination is not a repeat of previous failed attempts
    6. Submit and check pass via course page web status
    7. Update memory (correct on pass, record attempt on fail)

    Returns True if exam passed.
    """
    if not url.startswith("http"):
        from ...config import get_base_url
        url = f"{get_base_url()}{url}"

    exam_id = _extract_exam_id(url)
    memory = _load_memory(exam_id)

    for attempt in range(1, MAX_EXAM_ATTEMPTS + 1):
        logger.info("Exam %s: attempt %d/%d", url, attempt, MAX_EXAM_ATTEMPTS)

        page = await context.new_page()
        try:
            # Step 1: Navigate to exam info page
            await page.goto(url, wait_until="networkidle")

            # Step 2: Click start/continue button
            exam_btn = page.locator('button:has-text("測驗")')
            if await exam_btn.count() > 0:
                btn_text = (await exam_btn.first.text_content() or "").strip()
                logger.info("Exam %s: clicking '%s'", url, btn_text)
                async with page.expect_navigation(
                    wait_until="networkidle", timeout=30000
                ):
                    await exam_btn.first.click()
            else:
                logger.error("Exam %s: no exam button found on info page", url)
                await page.close()
                continue

            # Step 3: Wait for questions to render
            try:
                await page.wait_for_selector(
                    ".kques-item", state="attached", timeout=15000
                )
            except Exception:
                logger.error("Exam %s: questions did not load", url)
                await page.close()
                continue

            # Step 4: Scrape questions
            questions = await _scrape_questions(page)
            if not questions:
                logger.error("Exam %s: no questions found after waiting", url)
                await page.close()
                continue

            logger.info("Exam %s: found %d questions", url, len(questions))

            # Step 5: Get answers
            claude_attempts = sum(
                1 for a in memory.get("attempts", []) if a.get("method") != "brute"
            )
            if claude_attempts < MAX_CLAUDE_ATTEMPTS:
                # Use Claude with attempt history
                answers = await _get_answers(questions, memory)
                answers = _ensure_unique_combination(questions, answers, memory)
                method = "claude"
            else:
                # Switch to brute force
                answers = _brute_force_answers(questions, memory)
                if answers is None:
                    logger.error("Exam %s: all brute force combinations exhausted", url)
                    await page.close()
                    break
                method = "brute"
                logger.info("Exam %s: using brute force (attempt %d)", url, attempt)

            # Step 6: Fill in answers
            await _fill_answers(page, questions, answers)

            # Step 7: Submit (交卷)
            submit_btn = page.locator('button:has-text("交卷"), a:has-text("交卷")')
            if await submit_btn.count() > 0:
                await submit_btn.first.click()
                await page.wait_for_timeout(3000)
                # Handle confirmation dialog (button text may be「交卷」or「確定」)
                modal_confirm = page.locator(
                    '.modal button:has-text("交卷"), '
                    '.modal button:has-text("確定"), '
                    '.modal button:has-text("確認")'
                )
                if await modal_confirm.count() > 0 and await modal_confirm.first.is_visible():
                    await modal_confirm.first.click()
                    logger.debug("Exam %s: confirmed submit dialog", url)
                await page.wait_for_timeout(5000)
            else:
                logger.warning("Exam %s: no submit button found", url)

            # Build attempt_answers for harvest (keyed by question text)
            attempt_answers = {}
            for q in questions:
                q_text = q["question"].strip()
                idx = q["index"]
                if idx in answers:
                    attempt_answers[q_text] = answers[idx]

            # Harvest correct answers if shown on result page
            harvested = await _harvest_correct_answers(page, attempt_answers)
            if harvested:
                memory["correct"].update(harvested)

            await page.close()
            page = None

            # Step 8: Check pass via course page web status
            if course_id:
                passed = await _check_web_pass(context, course_id, exam_id)
            else:
                passed = bool(harvested)
                logger.warning("Exam %s: no course_id, can't verify via web", url)

            if passed:
                logger.info("Exam %s: PASSED on attempt %d", url, attempt)
                memory["correct"].update(attempt_answers)
                _save_memory(exam_id, memory)
                return True
            else:
                logger.info("Exam %s: did not pass on attempt %d", url, attempt)
                memory["attempts"].append({"answers": attempt_answers, "method": method})
                _save_memory(exam_id, memory)

        except Exception:
            logger.error("Exam %s: attempt %d failed", url, attempt, exc_info=True)
        finally:
            if page:
                await page.close()

    logger.error("Exam %s: failed after %d attempts", url, MAX_EXAM_ATTEMPTS)
    return False


# ---------------------------------------------------------------------------
# Check pass via course page
# ---------------------------------------------------------------------------


async def _check_web_pass(
    context: BrowserContext, course_id: str, exam_id: str
) -> bool:
    """Navigate to course page and check if this exam's ext-col has .item-pass."""
    from ...config import get_base_url

    page = await context.new_page()
    try:
        await page.goto(
            f"{get_base_url()}/course/{course_id}", wait_until="networkidle"
        )
        # Find the xtree-node for this exam (URL contains exam/{exam_id})
        passed = await page.evaluate(
            """(examId) => {
                const nodes = document.querySelectorAll('li.xtree-node');
                for (const node of nodes) {
                    const links = node.querySelectorAll('a');
                    for (const link of links) {
                        if (link.href && link.href.includes('/exam/' + examId)) {
                            const cols = node.querySelectorAll('.ext-col');
                            if (cols.length >= 4) {
                                return cols[3].querySelector('.item-pass') !== null;
                            }
                        }
                    }
                }
                return false;
            }""",
            exam_id,
        )
        return passed
    except Exception:
        logger.warning("Failed to check web pass for exam %s", exam_id, exc_info=True)
        return False
    finally:
        await page.close()


# ---------------------------------------------------------------------------
# Question scraping
# ---------------------------------------------------------------------------


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
    # Filter out header items with no choices (e.g. "單選題 共 5 題")
    questions = [q for q in questions if q["choices"]]
    # Re-index after filtering
    for i, q in enumerate(questions):
        q["index"] = i
    return questions


# ---------------------------------------------------------------------------
# Answer selection
# ---------------------------------------------------------------------------


async def _get_answers(
    questions: list[dict], memory: dict
) -> dict[int, str]:
    """Get answers: use known correct first, then ask Claude for the rest."""
    correct = memory.get("correct", {})

    result: dict[int, str] = {}
    unknown: list[dict] = []

    for q in questions:
        q_text = q["question"].strip()
        if q_text in correct:
            result[q["index"]] = correct[q_text]
            logger.debug("Q%d: using known correct answer %s", q["index"] + 1, correct[q_text])
        else:
            unknown.append(q)

    if unknown:
        logger.info("Asking Claude for %d/%d questions", len(unknown), len(questions))
        attempts = memory.get("attempts", [])
        claude_answers = await _ask_claude(unknown, attempts if attempts else None)
        result.update(claude_answers)

    # Fallback for any still missing
    for q in questions:
        if q["index"] not in result and q["choices"]:
            result[q["index"]] = q["choices"][0]["sn"]

    return result


def _ensure_unique_combination(
    questions: list[dict], answers: dict[int, str], memory: dict
) -> dict[int, str]:
    """Ensure this answer combination differs from all previous failed attempts."""
    previous_combos = []
    for att in memory.get("attempts", []):
        combo = {}
        for q in questions:
            q_text = q["question"].strip()
            if q_text in att["answers"]:
                combo[q["index"]] = att["answers"][q_text]
        if combo:
            previous_combos.append(combo)

    if not previous_combos:
        return answers

    # Check if current answers match any previous attempt
    current = {idx: sn for idx, sn in answers.items()}
    for prev in previous_combos:
        if all(current.get(idx) == sn for idx, sn in prev.items()):
            # Duplicate — change one non-correct answer randomly
            correct = memory.get("correct", {})
            changeable = [
                q for q in questions
                if q["question"].strip() not in correct and len(q["choices"]) > 1
            ]
            if changeable:
                q = random.choice(changeable)
                current_sn = current.get(q["index"], "")
                other_choices = [c["sn"] for c in q["choices"] if c["sn"] != current_sn]
                if other_choices:
                    new_sn = random.choice(other_choices)
                    answers[q["index"]] = new_sn
                    logger.info(
                        "Avoiding duplicate combo: changed Q%d from %s to %s",
                        q["index"] + 1, current_sn, new_sn,
                    )
            break

    return answers


def _brute_force_answers(
    questions: list[dict], memory: dict
) -> dict[int, str] | None:
    """Generate the next untried answer combination via brute force.

    Uses known correct answers for questions that have them, and
    enumerates all combinations for the rest.

    Returns None if all combinations have been tried.
    """
    correct = memory.get("correct", {})

    # Separate known vs unknown questions
    fixed: dict[int, str] = {}
    variable: list[dict] = []
    for q in questions:
        q_text = q["question"].strip()
        if q_text in correct:
            fixed[q["index"]] = correct[q_text]
        else:
            variable.append(q)

    if not variable:
        # All answers are known correct — shouldn't be here
        return {q["index"]: correct[q["question"].strip()] for q in questions}

    # Build set of already-tried variable combinations
    tried_combos: set[tuple] = set()
    for att in memory.get("attempts", []):
        combo = tuple(
            att["answers"].get(q["question"].strip(), "")
            for q in variable
        )
        tried_combos.add(combo)

    # Generate all possible combinations for variable questions
    choice_lists = [
        [c["sn"] for c in q["choices"]] for q in variable
    ]
    for combo in itertools.product(*choice_lists):
        if combo not in tried_combos:
            result = dict(fixed)
            for q, sn in zip(variable, combo):
                result[q["index"]] = sn
            return result

    return None  # All exhausted


async def _ask_claude(
    questions: list[dict], attempts: list[dict] | None = None
) -> dict[int, str]:
    """Ask Claude API to answer multiple-choice questions.

    If attempts history is provided, includes it in the prompt so Claude
    can adjust answers based on previous failures.

    Returns dict of question_index -> selected choice letter (A/B/C/D).
    """
    prompt_parts = []
    for q in questions:
        choices_str = "\n".join(
            f"  {c['sn']}. {c['text']}" for c in q["choices"]
        )
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

    prompt = (
        "Answer the following multiple-choice questions from a training exam. "
        "For each question, respond with ONLY the question number and the letter, "
        "one per line, like:\nQ1: D\nQ2: A\n"
        + history_text + "\n"
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
            idx = int(match.group(1)) - 1
            letter = match.group(2).upper()
            answers[idx] = letter

    return answers


# ---------------------------------------------------------------------------
# Answer filling
# ---------------------------------------------------------------------------


async def _fill_answers(page, questions: list[dict], answers: dict[int, str]) -> None:
    """Click the radio button for each answer."""
    for q in questions:
        idx = q["index"]
        selected_sn = answers.get(idx)
        if not selected_sn:
            continue

        for choice in q["choices"]:
            if choice["sn"] == selected_sn:
                if choice["name"] and choice["value"]:
                    radio = page.locator(
                        f'input[name="{choice["name"]}"][value="{choice["value"]}"]'
                    )
                    if await radio.count() > 0:
                        await radio.first.click(force=True)
                        logger.debug("Q%d: selected %s", idx + 1, selected_sn)
                        break
                elif choice["inputId"]:
                    radio = page.locator(f'#{choice["inputId"]}')
                    if await radio.count() > 0:
                        await radio.first.click(force=True)
                        logger.debug("Q%d: selected %s", idx + 1, selected_sn)
                        break
        else:
            # Fallback: click by option-item position
            items = page.locator(f'.kques-item:nth-child({idx + 1}) .option-item')
            target_idx = ord(selected_sn) - ord('A')
            if 0 <= target_idx < await items.count():
                radio = items.nth(target_idx).locator('input')
                if await radio.count() > 0:
                    await radio.first.click(force=True)
                    logger.debug("Q%d: selected %s (by position)", idx + 1, selected_sn)


# ---------------------------------------------------------------------------
# Answer harvesting
# ---------------------------------------------------------------------------


async def _harvest_correct_answers(
    page, attempt_answers: dict[str, str] | None = None
) -> dict[str, str]:
    """Extract correct answers from the result/review page.

    Two sources:
    1. Wrong questions show「正確答案: X」text → harvest X
    2. Correct questions (no「正確答案」) → the selected answer is correct

    Args:
        page: Result page after submission.
        attempt_answers: Dict of {question_text: selected_sn} from this attempt,
            used to record correct answers for questions answered correctly.

    Returns dict of question_text -> correct choice letter (A/B/C/D).
    """
    harvested = await page.evaluate(r"""
        () => {
            const answers = {};
            document.querySelectorAll('.kques-item').forEach(item => {
                const questionEl = item.querySelector('.question');
                const questionText = questionEl?.textContent?.trim() || '';
                if (!questionText) return;

                // Check for「正確答案: X」text (wrong questions)
                const fullText = item.textContent || '';
                const match = fullText.match(/正確答案[：:]\s*([A-D])/);
                if (match) {
                    answers[questionText] = match[1];
                } else {
                    // No 正確答案 shown — mark as correctly answered
                    answers[questionText] = '__correct__';
                }
            });
            return answers;
        }
    """)

    if not harvested:
        return {}

    result: dict[str, str] = {}
    for q_text, sn in harvested.items():
        if sn == "__correct__":
            # This question was answered correctly — use our selected answer
            if attempt_answers and q_text in attempt_answers:
                result[q_text] = attempt_answers[q_text]
        else:
            result[q_text] = sn

    if result:
        logger.info("Harvested %d correct answers from result page", len(result))

    return result
