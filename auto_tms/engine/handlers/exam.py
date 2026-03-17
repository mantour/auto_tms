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
MAX_EXAM_ATTEMPTS = 30  # Claude + score search + fallback brute force
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
# Exam attempt helpers
# ---------------------------------------------------------------------------


async def _enter_exam(context: BrowserContext, url: str):
    """Navigate to exam, click start, wait for questions. Returns (page, questions) or (None, None)."""
    page = await context.new_page()
    await page.goto(url, wait_until="networkidle")

    exam_btn = page.locator('button:has-text("測驗")')
    if await exam_btn.count() > 0:
        btn_text = (await exam_btn.first.text_content() or "").strip()
        logger.info("Exam %s: clicking '%s'", url, btn_text)
        async with page.expect_navigation(wait_until="networkidle", timeout=30000):
            await exam_btn.first.click()
    else:
        logger.error("Exam %s: no exam button found", url)
        await page.close()
        return None, None

    try:
        await page.wait_for_selector(".kques-item", state="attached", timeout=15000)
    except Exception:
        logger.error("Exam %s: questions did not load", url)
        await page.close()
        return None, None

    questions = await _scrape_questions(page)
    if not questions:
        logger.error("Exam %s: no questions found", url)
        await page.close()
        return None, None

    return page, questions


async def _submit_exam(page) -> int | None:
    """Fill answers should already be done. Submit exam and return score, or None."""
    submit_btn = page.locator('button:has-text("交卷"), a:has-text("交卷")')
    if await submit_btn.count() > 0:
        await submit_btn.first.click()
        await page.wait_for_timeout(3000)
        modal_confirm = page.locator(
            '.modal button:has-text("交卷"), '
            '.modal button:has-text("確定"), '
            '.modal button:has-text("確認")'
        )
        if await modal_confirm.count() > 0 and await modal_confirm.first.is_visible():
            await modal_confirm.first.click()
            logger.debug("Confirmed submit dialog")
        await page.wait_for_timeout(5000)

    return await _parse_score(page)


async def _parse_score(page) -> int | None:
    """Parse score from result page. Returns score int or None."""
    body = await page.evaluate("() => document.body.innerText")
    match = re.search(r"分數[：:]\s*(\d+)\s*/\s*(\d+)", body)
    if match:
        return int(match.group(1))
    return None


async def _do_one_attempt(
    context: BrowserContext, url: str, questions_override: list[dict] | None,
    answers: dict[int, str], course_id: str | None, exam_id: str, memory: dict,
    method: str, attempt_num: int,
) -> tuple[bool, int | None, list[dict]]:
    """Execute one exam attempt. Returns (passed, score, questions)."""
    page, questions = await _enter_exam(context, url)
    if not page:
        return False, None, questions_override or []

    if questions_override:
        # Check if questions match
        current_texts = {q["question"].strip() for q in questions}
        expected_texts = {q["question"].strip() for q in questions_override}
        if current_texts != expected_texts:
            logger.warning("Exam %s: questions changed, resetting", url)
            questions_override = None  # Fall through to use new questions

    if questions_override:
        questions = questions_override

    logger.info("Exam %s: attempt %d, %d questions, method=%s", url, attempt_num, len(questions), method)

    await _fill_answers(page, questions, answers)
    score = await _submit_exam(page)
    logger.info("Exam %s: score %s", url, score)

    # Build attempt_answers
    attempt_answers = {}
    for q in questions:
        q_text = q["question"].strip()
        idx = q["index"]
        if idx in answers:
            attempt_answers[q_text] = answers[idx]

    # Harvest
    harvested = await _harvest_correct_answers(page, attempt_answers)
    if harvested:
        memory["correct"].update(harvested)

    await page.close()

    # Check web pass
    passed = False
    if course_id:
        passed = await _check_web_pass(context, course_id, exam_id)

    # Record attempt
    memory["attempts"].append({
        "answers": attempt_answers, "method": method, "score": score,
    })
    if passed:
        memory["correct"].update(attempt_answers)
    _save_memory(exam_id, memory)

    return passed, score, questions


# ---------------------------------------------------------------------------
# Score-based per-question search (Type B exams)
# ---------------------------------------------------------------------------


async def _score_search(
    context: BrowserContext, url: str, course_id: str | None,
    exam_id: str, memory: dict, baseline_questions: list[dict],
    baseline_answers: dict[int, str], baseline_score: int,
) -> bool:
    """Search for correct answers one question at a time using score differences.

    For each unconfirmed question, swap to each alternative answer and compare
    the resulting score with baseline. Score increase = found correct answer.
    """
    confirmed: dict[int, str] = dict(baseline_answers)  # Will be updated as we confirm
    points_per_q = baseline_score  # Will be recalculated
    total_questions = len(baseline_questions)
    if total_questions > 0:
        # Infer points per question from total (e.g. 100/5=20 or 100/10=10)
        points_per_q = 100 // total_questions

    current_score = baseline_score
    questions = baseline_questions

    # Track which questions are confirmed correct
    confirmed_indices: set[int] = set()
    # If score is already 100, we're done (shouldn't be here but safety check)
    if current_score >= 100:
        return True

    for q in questions:
        idx = q["index"]
        if idx in confirmed_indices:
            continue

        baseline_sn = confirmed[idx]
        alternatives = [c["sn"] for c in q["choices"] if c["sn"] != baseline_sn]

        found = False
        for alt_sn in alternatives:
            # Swap this one question
            test_answers = dict(confirmed)
            test_answers[idx] = alt_sn

            passed, score, new_questions = await _do_one_attempt(
                context, url, questions, test_answers, course_id, exam_id,
                memory, "score_search",
                len(memory.get("attempts", [])) + 1,
            )

            if passed:
                logger.info("Exam %s: PASSED during score search!", url)
                return True

            if score is None:
                logger.warning("Exam %s: could not parse score, skipping question", url)
                break

            # Check if questions changed (question bank)
            current_texts = {q["question"].strip() for q in new_questions}
            expected_texts = {q["question"].strip() for q in questions}
            if current_texts != expected_texts:
                logger.warning("Exam %s: questions changed during score search, aborting", url)
                return False

            if score > current_score:
                # Found correct answer for this question
                logger.info("Exam %s: Q%d correct=%s (score %d→%d)",
                            url, idx + 1, alt_sn, current_score, score)
                confirmed[idx] = alt_sn
                confirmed_indices.add(idx)
                current_score = score
                found = True
                break
            elif score < current_score:
                # Baseline was correct for this question
                logger.info("Exam %s: Q%d baseline=%s confirmed correct (score dropped %d→%d)",
                            url, idx + 1, baseline_sn, current_score, score)
                confirmed_indices.add(idx)
                found = True
                break
            # else: same score → both wrong, try next alternative

        if not found and not alternatives:
            # Only one choice (shouldn't happen)
            confirmed_indices.add(idx)

        if not found:
            # All alternatives tried, score never changed → baseline was correct
            logger.info("Exam %s: Q%d baseline=%s correct by elimination", url, idx + 1, baseline_sn)
            confirmed_indices.add(idx)

    # Final attempt with all confirmed answers
    if current_score < 100:
        logger.info("Exam %s: final attempt with confirmed answers", url)
        passed, score, _ = await _do_one_attempt(
            context, url, questions, confirmed, course_id, exam_id,
            memory, "score_search_final",
            len(memory.get("attempts", [])) + 1,
        )
        if passed:
            logger.info("Exam %s: PASSED on final attempt!", url)
            return True
        logger.warning("Exam %s: score search completed but still not passed (score=%s)", url, score)

    return current_score >= 100


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


async def handle_exam(
    context: BrowserContext, url: str, course_id: str | None = None
) -> bool:
    """Solve a multiple-choice exam using Claude API with persistent answer memory.

    Strategy:
    1. Claude attempts (up to MAX_CLAUDE_ATTEMPTS) — Type A exams harvest correct answers
    2. If not passed and Type A: use harvested correct answers directly
    3. If not passed and Type B: score-based per-question search
    4. Fallback: brute force enumeration

    Returns True if exam passed.
    """
    if not url.startswith("http"):
        from ...config import get_base_url
        url = f"{get_base_url()}{url}"

    exam_id = _extract_exam_id(url)
    memory = _load_memory(exam_id)

    best_score = 0
    best_answers: dict[int, str] = {}
    best_questions: list[dict] = []

    # Phase 1: Claude attempts
    for attempt in range(1, MAX_CLAUDE_ATTEMPTS + 1):
        page, questions = await _enter_exam(context, url)
        if not page:
            continue

        answers = await _get_answers(questions, memory)
        answers = _ensure_unique_combination(questions, answers, memory)

        passed, score, questions = await _do_one_attempt(
            context, url, None, answers, course_id, exam_id,
            memory, "claude", attempt,
        )

        if passed:
            return True

        if score is not None and score > best_score:
            best_score = score
            best_answers = answers
            best_questions = questions

    # Phase 2: If Type A (has harvested correct answers), try them
    if memory.get("correct"):
        logger.info("Exam %s: trying %d harvested correct answers", url, len(memory["correct"]))
        page, questions = await _enter_exam(context, url)
        if page:
            answers = await _get_answers(questions, memory)  # Uses correct from memory
            passed, score, questions = await _do_one_attempt(
                context, url, None, answers, course_id, exam_id,
                memory, "harvest_retry", MAX_CLAUDE_ATTEMPTS + 1,
            )
            if passed:
                return True
            if score is not None and score > best_score:
                best_score = score
                best_answers = answers
                best_questions = questions

    # Phase 3: Score-based search (Type B — no harvest)
    if best_questions and best_score > 0 and best_score < 100:
        logger.info("Exam %s: starting score search (baseline=%d)", url, best_score)
        passed = await _score_search(
            context, url, course_id, exam_id, memory,
            best_questions, best_answers, best_score,
        )
        if passed:
            return True

    # Phase 4: Brute force fallback
    remaining = MAX_EXAM_ATTEMPTS - len(memory.get("attempts", []))
    if remaining > 0:
        logger.info("Exam %s: falling back to brute force (%d attempts left)", url, remaining)
        for _ in range(remaining):
            page, questions = await _enter_exam(context, url)
            if not page:
                continue
            answers = _brute_force_answers(questions, memory)
            if answers is None:
                logger.error("Exam %s: all brute force combinations exhausted", url)
                break
            passed, score, _ = await _do_one_attempt(
                context, url, None, answers, course_id, exam_id,
                memory, "brute", len(memory.get("attempts", [])) + 1,
            )
            if passed:
                return True

    logger.error("Exam %s: failed after %d attempts", url, len(memory.get("attempts", [])))
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

    # Check if this is Type A (has feedback) or Type B (no feedback)
    has_any_feedback = any(sn != "__correct__" for sn in harvested.values())
    if not has_any_feedback:
        # Type B: no question shows 正確答案 → can't infer anything
        return {}

    result: dict[str, str] = {}
    for q_text, sn in harvested.items():
        if sn == "__correct__":
            # Type A: no 正確答案 shown = answered correctly → use selected answer
            if attempt_answers and q_text in attempt_answers:
                result[q_text] = attempt_answers[q_text]
        else:
            result[q_text] = sn

    if result:
        logger.info("Harvested %d correct answers from result page", len(result))

    return result
