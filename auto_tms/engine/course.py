"""Per-course orchestrator: enroll → process materials → verify."""

import asyncio
import logging
import re

from playwright.async_api import BrowserContext, Page

from ..config import MAX_CONCURRENT_PAGES, MAX_CONCURRENT_VIDEOS, get_base_url
from ..state.models import CourseProgress, CourseStatus, MaterialType, Status
from ..state.store import load_course_progress, save_course_progress
from .handlers.document import handle_document
from .handlers.exam import handle_exam
from .handlers.survey import handle_survey
from .handlers.video import handle_video

logger = logging.getLogger("auto_tms.engine.course")

# Concurrency limits (configurable via TMS_MAX_PAGES / TMS_MAX_VIDEOS env vars)
_page_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
_video_semaphore = asyncio.Semaphore(MAX_CONCURRENT_VIDEOS)

MAX_MATERIAL_RETRIES = 3


async def enroll_in_course(page: Page, course_id: str) -> bool:
    """Navigate to course page and enroll if not already enrolled.

    Returns True if enrolled (or already was), False on failure.
    """
    await page.goto(f"{get_base_url()}/course/{course_id}", wait_until="load")
    current_url = page.url

    # If redirected to syllabus page, we need to enroll
    if "syllabus" in current_url:
        logger.info("Course %s: not enrolled, attempting signup", course_id)

        try:
            # Look for signup button/link
            signup_btn = page.locator('a[href*="signup"], button:has-text("報名"), a:has-text("報名")')
            if await signup_btn.count() > 0:
                # Check if button is disabled (e.g. role restriction)
                is_disabled = await signup_btn.first.evaluate(
                    "el => el.classList.contains('disabled') || el.disabled"
                )
                if is_disabled:
                    logger.error("Course %s: signup button is disabled (restricted)", course_id)
                    return False
                await signup_btn.first.click()
                await page.wait_for_timeout(3000)

                # Navigate back to course page to verify
                await page.goto(
                    f"{get_base_url()}/course/{course_id}", wait_until="load"
                )
                if "syllabus" not in page.url:
                    logger.info("Course %s: enrolled successfully", course_id)
                    return True

                logger.error("Course %s: enrollment failed", course_id)
                return False

            logger.error("Course %s: no signup button found", course_id)
            return False
        except Exception:
            logger.error("Course %s: enrollment exception", course_id, exc_info=True)
            return False

    logger.info("Course %s: already enrolled", course_id)
    return True


async def parse_course_materials(page: Page, course_id: str) -> list[dict]:
    """Scrape course page for material list from xtree-node elements.

    Each xtree-node contains links and text describing:
    - Videos: link to /media/<id>, "閱讀達 N 分鐘"
    - Documents: link to /media/<id> or file, "閱讀 1 次"
    - Exams: link to quiz/exam URL, "測驗" or "N 分及格"
    - Surveys: link to /course/<id>/poll/<id>, "問卷" or "須填寫"

    Returns list of dicts with keys: id, type, title, url, required_minutes.
    """
    await page.goto(f"{get_base_url()}/course/{course_id}", wait_until="load")

    nodes = await page.evaluate(r"""
        () => {
            const results = [];
            document.querySelectorAll('li.xtree-node').forEach((node, i) => {
                // Collect all links in this node
                const links = Array.from(node.querySelectorAll('a')).map(a => ({
                    href: a.href || '',
                    text: a.textContent.trim(),
                })).filter(a => a.href && !a.href.endsWith('#'));

                // Get the full text for classification
                const fullText = node.textContent || '';

                // Extract completion status from ext-col elements
                const cols = node.querySelectorAll('.ext-col');
                const colTexts = Array.from(cols).map(c => c.textContent.trim());
                const completed = cols.length >= 4 &&
                    cols[3].querySelector('.item-pass') !== null;

                results.push({
                    index: i,
                    links: links,
                    fullText: fullText.substring(0, 500),
                    colTexts: colTexts,
                    completed: completed,
                });
            });
            return results;
        }
    """)

    materials = []
    for node in nodes:
        mat = _classify_node(node, course_id)
        if mat:
            materials.append(mat)

    logger.info("Course %s: found %d materials", course_id, len(materials))
    for m in materials:
        web = "PASS" if m.get("completed") else f"rec={m.get('recorded_minutes', 0)}m"
        logger.debug(
            "  %s: %s (%s) req=%s [%s]",
            m["type"].value,
            m["title"],
            m["url"],
            m.get("required_minutes"),
            web,
        )
    return materials


def _classify_node(node: dict, course_id: str) -> dict | None:
    """Classify an xtree-node into a material type.

    Also extracts web completion status from ext-col:
    - completed: True if col[3] has .item-pass
    - recorded_minutes: parsed from col[2] time string (HH:MM:SS)
    """
    text = node.get("fullText", "")
    links = node.get("links", [])
    index = node.get("index", 0)
    completed = node.get("completed", False)
    col_texts = node.get("colTexts", [])

    if not links:
        return None

    # Parse recorded time from col[2] (e.g. "01:27:14" or "00:00")
    recorded_minutes = 0
    if len(col_texts) >= 3:
        recorded_minutes = _parse_time_to_minutes(col_texts[2])

    # Find the main content link (not the mobile extension link)
    content_link = None
    for link in links:
        href = link["href"]
        if "/media/" in href or "/poll/" in href or "/quiz/" in href or "/exam/" in href:
            content_link = link
            break
        # Skip fragment-only and readTime AJAX links
        if "#" not in href.split("?")[0].split("/")[-1]:
            content_link = link

    if not content_link:
        return None

    href = content_link["href"]
    title = content_link["text"] or ""

    # Try to get a better title from the node text
    title_match = re.search(r"\d+\.\s+(.+?)(?:\n|$)", text)
    if title_match:
        title = title_match.group(1).strip() or title

    # Common fields for all types
    web_status = {"completed": completed, "recorded_minutes": recorded_minutes}

    # Classify by pass condition and URL
    if "測驗" in text or "及格" in text or "/quiz/" in href or "/exam/" in href:
        media_id = _extract_id_from_url(href)
        return {
            "id": f"exam_{media_id or index}",
            "type": MaterialType.EXAM,
            "title": title or "測驗",
            "url": href,
            "required_minutes": None,
            **web_status,
        }

    if "問卷" in text or "須填寫" in text or "/poll/" in href:
        media_id = _extract_id_from_url(href)
        return {
            "id": f"survey_{media_id or index}",
            "type": MaterialType.SURVEY,
            "title": title or "問卷",
            "url": href,
            "required_minutes": None,
            **web_status,
        }

    if "閱讀達" in text and "分鐘" in text:
        # Video with time requirement
        minutes = _extract_required_minutes(text)
        media_id = _extract_id_from_url(href)
        return {
            "id": f"video_{media_id or index}",
            "type": MaterialType.VIDEO,
            "title": title,
            "url": href,
            "required_minutes": minutes,
            **web_status,
        }

    if "閱讀" in text and "次" in text:
        # Document — open once
        media_id = _extract_id_from_url(href)
        return {
            "id": f"doc_{media_id or index}",
            "type": MaterialType.DOCUMENT,
            "title": title,
            "url": href,
            "required_minutes": None,
            **web_status,
        }

    # Default: if URL has /media/, treat as video without time requirement
    if "/media/" in href:
        media_id = _extract_id_from_url(href)
        return {
            "id": f"video_{media_id or index}",
            "type": MaterialType.VIDEO,
            "title": title,
            "url": href,
            "required_minutes": None,
            **web_status,
        }

    return None


def _extract_id_from_url(url: str) -> str:
    """Extract the last numeric segment from a URL."""
    parts = url.rstrip("/").split("/")
    for part in reversed(parts):
        clean = part.split("?")[0]
        if clean.isdigit():
            return clean
    return ""


def _parse_time_to_minutes(text: str) -> int:
    """Parse time string like '01:27:14' or '00:30' to total minutes."""
    text = text.strip()
    if not text or text == "-":
        return 0
    # HH:MM:SS
    match = re.match(r"(\d+):(\d+):(\d+)", text)
    if match:
        h, m, s = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return h * 60 + m + (1 if s > 0 else 0)
    # MM:SS
    match = re.match(r"(\d+):(\d+)", text)
    if match:
        m, s = int(match.group(1)), int(match.group(2))
        return m + (1 if s > 0 else 0)
    return 0


def _extract_required_minutes(text: str) -> int | None:
    """Extract required minutes from text like '閱讀達 23 分鐘'."""
    match = re.search(r"閱讀達\s*(\d+)\s*分鐘", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*分鐘", text)
    if match:
        return int(match.group(1))
    return None


async def _check_material_web_pass(
    context: BrowserContext, course_id: str, material_id: str
) -> bool:
    """Navigate to course page and check if a specific material has .item-pass.

    Matches by numeric ID extracted from material_id (e.g. video_273090 → 273090).
    """
    numeric_id = re.sub(r"^(video|doc|survey|exam)_", "", material_id)

    page = await context.new_page()
    try:
        await page.goto(
            f"{get_base_url()}/course/{course_id}", wait_until="networkidle"
        )
        passed = await page.evaluate(
            """(numericId) => {
                const nodes = document.querySelectorAll('li.xtree-node');
                for (const node of nodes) {
                    const links = node.querySelectorAll('a');
                    for (const link of links) {
                        if (link.href && link.href.includes('/' + numericId)) {
                            const cols = node.querySelectorAll('.ext-col');
                            if (cols.length >= 4) {
                                return cols[3].querySelector('.item-pass') !== null;
                            }
                        }
                    }
                }
                return false;
            }""",
            numeric_id,
        )
        return passed
    except Exception:
        logger.warning("Failed to check web pass for %s", material_id, exc_info=True)
        return False
    finally:
        await page.close()


async def _check_all_materials_web(
    context: BrowserContext, course_id: str
) -> dict[str, bool]:
    """Navigate to course page and check .item-pass for all materials.

    Returns dict of material_id_suffix → bool (pass status).
    Matches xtree-node links by numeric ID in the URL.
    """
    page = await context.new_page()
    try:
        await page.goto(
            f"{get_base_url()}/course/{course_id}", wait_until="networkidle"
        )
        results = await page.evaluate("""
            () => {
                const statuses = {};
                document.querySelectorAll('li.xtree-node').forEach(node => {
                    const cols = node.querySelectorAll('.ext-col');
                    const passed = cols.length >= 4 &&
                        cols[3].querySelector('.item-pass') !== null;

                    // Extract numeric ID from links
                    const links = node.querySelectorAll('a');
                    for (const link of links) {
                        const href = link.href || '';
                        const match = href.match(/\\/(media|poll|exam|quiz)\\/(\\d+)/);
                        if (match) {
                            statuses[match[2]] = passed;
                            break;
                        }
                    }
                });
                return statuses;
            }
        """)
        return results
    except Exception:
        logger.warning("Failed to check web statuses for course %s", course_id, exc_info=True)
        return {}
    finally:
        await page.close()


async def process_course(context: BrowserContext, course_id: str) -> bool:
    """Process a single course end-to-end.

    1. Enroll if needed
    2. Parse materials
    3. Complete non-exam materials (videos sequential, others can interleave)
    4. Complete exams last
    5. Update progress state

    Returns True if course completed successfully.
    """
    course_prog = load_course_progress(course_id)
    if course_prog and course_prog.status == CourseStatus.DONE:
        logger.info("Course %s: already completed, skipping", course_id)
        return True
    if course_prog and course_prog.status == CourseStatus.SKIPPED:
        logger.info("Course %s: previously skipped (enroll failed), skipping", course_id)
        return False

    page = None
    try:
        # Step 1 & 2: Enroll + parse (under semaphore to limit concurrent pages)
        async with _page_semaphore:
            page = await context.new_page()

            if not (course_prog and course_prog.enrolled):
                if not await enroll_in_course(page, course_id):
                    # Mark as skipped so plan can exclude and pick alternative
                    skip_prog = course_prog or CourseProgress(course_id=course_id)
                    skip_prog.status = CourseStatus.SKIPPED
                    save_course_progress(course_id, skip_prog)
                    logger.warning("Course %s: enroll failed, marked as skipped", course_id)
                    return False

            if not course_prog:
                course_prog = CourseProgress(
                    course_id=course_id, enrolled=True, status=CourseStatus.ENROLLED
                )
            else:
                course_prog.enrolled = True
            save_course_progress(course_id, course_prog)

            raw_materials = await parse_course_materials(page, course_id)
            if not course_prog.materials:
                from ..state.models import MaterialProgress

                course_prog.materials = [
                    MaterialProgress(
                        material_id=m["id"],
                        material_type=m["type"],
                        status=Status.DONE if m.get("completed") else Status.PENDING,
                        required_minutes=m.get("required_minutes"),
                        recorded_minutes=m.get("recorded_minutes", 0),
                        url=m["url"],
                        title=m.get("title", ""),
                    )
                    for m in raw_materials
                ]
            else:
                # Update existing materials with latest web status
                web_by_id = {m["id"]: m for m in raw_materials}
                for mat in course_prog.materials:
                    web = web_by_id.get(mat.material_id)
                    if web:
                        mat.recorded_minutes = web.get("recorded_minutes", 0)
                        if web.get("completed") and mat.status != Status.DONE:
                            logger.info("  %s: web says PASS, marking done", mat.material_id)
                            mat.status = Status.DONE

            # Also mark done if recorded >= required (pass icon may lag)
            for mat in course_prog.materials:
                if (
                    mat.status != Status.DONE
                    and mat.material_type == MaterialType.VIDEO
                    and mat.required_minutes
                    and mat.recorded_minutes >= mat.required_minutes
                ):
                    logger.info("  %s: recorded %dm >= required %dm, marking done",
                                mat.material_id, mat.recorded_minutes, mat.required_minutes)
                    mat.status = Status.DONE

            skipped = sum(1 for m in course_prog.materials if m.status == Status.DONE)
            if skipped:
                logger.info("Course %s: %d/%d materials already done on web",
                            course_id, skipped, len(course_prog.materials))
            course_prog.status = CourseStatus.IN_PROGRESS
            save_course_progress(course_id, course_prog)

            await page.close()
            page = None  # Release the page and semaphore slot

        # Step 3: Process non-exam materials
        for mat in course_prog.materials:
            if mat.status == Status.DONE:
                continue
            if mat.material_type == MaterialType.EXAM:
                continue  # exams last

            mat.status = Status.IN_PROGRESS
            save_course_progress(course_id, course_prog)

            success = await _handle_material(context, mat, course_id)
            if success:
                # Verify via web
                async with _page_semaphore:
                    web_pass = await _check_material_web_pass(
                        context, course_id, mat.material_id
                    )
                if web_pass:
                    mat.status = Status.DONE
                else:
                    logger.warning(
                        "  %s: handler succeeded but web not passed yet",
                        mat.material_id,
                    )
                    mat.status = Status.PENDING
            else:
                mat.status = Status.PENDING
            save_course_progress(course_id, course_prog)

        # Step 4: Process exams
        for mat in course_prog.materials:
            if mat.status == Status.DONE:
                continue
            if mat.material_type != MaterialType.EXAM:
                continue

            mat.status = Status.IN_PROGRESS
            save_course_progress(course_id, course_prog)

            success = await _handle_material(context, mat, course_id)
            if success:
                async with _page_semaphore:
                    web_pass = await _check_material_web_pass(
                        context, course_id, mat.material_id
                    )
                if web_pass:
                    mat.status = Status.DONE
                else:
                    logger.warning(
                        "  %s: handler succeeded but web not passed yet",
                        mat.material_id,
                    )
                    mat.status = Status.PENDING
            else:
                mat.status = Status.PENDING
            save_course_progress(course_id, course_prog)

        # Step 5: Final web verification — check all materials at once
        async with _page_semaphore:
            web_statuses = await _check_all_materials_web(context, course_id)
        if web_statuses:
            for mat in course_prog.materials:
                numeric_id = re.sub(r"^(video|doc|survey|exam)_", "", mat.material_id)
                if web_statuses.get(numeric_id) and mat.status != Status.DONE:
                    logger.info("  %s: web confirms PASS on final check", mat.material_id)
                    mat.status = Status.DONE

        all_done = all(m.status == Status.DONE for m in course_prog.materials)
        course_prog.status = CourseStatus.DONE if all_done else CourseStatus.IN_PROGRESS
        save_course_progress(course_id, course_prog)

        if all_done:
            logger.info("Course %s: completed!", course_id)
        else:
            pending = [
                m.material_id
                for m in course_prog.materials
                if m.status != Status.DONE
            ]
            logger.warning("Course %s: incomplete, pending: %s", course_id, pending)

        return all_done
    finally:
        if page:
            await page.close()


async def _handle_material(
    context: BrowserContext, mat, course_id: str | None = None
) -> bool:
    """Dispatch to the appropriate material handler with semaphore and retry.

    Video acquires both _video_semaphore and _page_semaphore.
    Other types only acquire _page_semaphore.
    """
    is_video = mat.material_type == MaterialType.VIDEO

    for attempt in range(1, MAX_MATERIAL_RETRIES + 1):
        logger.info(
            "Processing %s: %s (%s) [attempt %d/%d]",
            mat.material_type.value,
            mat.title or mat.material_id,
            mat.url,
            attempt,
            MAX_MATERIAL_RETRIES,
        )
        try:
            if is_video:
                async with _video_semaphore:
                    async with _page_semaphore:
                        result = await handle_video(
                            context, mat.url, mat.required_minutes, mat.recorded_minutes
                        )
            else:
                async with _page_semaphore:
                    if mat.material_type == MaterialType.DOCUMENT:
                        result = await handle_document(context, mat.url)
                    elif mat.material_type == MaterialType.SURVEY:
                        result = await handle_survey(context, mat.url)
                    elif mat.material_type == MaterialType.EXAM:
                        result = await handle_exam(context, mat.url, course_id)
                    else:
                        result = False

            if result:
                return True

        except Exception:
            logger.error(
                "Failed to handle %s %s (attempt %d)",
                mat.material_type.value,
                mat.material_id,
                attempt,
                exc_info=True,
            )

        # Exponential backoff before retry
        if attempt < MAX_MATERIAL_RETRIES:
            backoff = [10, 30, 60][min(attempt - 1, 2)]
            logger.info("Retrying %s %s in %ds...", mat.material_type.value, mat.material_id, backoff)
            await asyncio.sleep(backoff)

    logger.error(
        "Giving up on %s %s after %d attempts",
        mat.material_type.value,
        mat.material_id,
        MAX_MATERIAL_RETRIES,
    )
    return False
