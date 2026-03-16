"""Per-course orchestrator: enroll → process materials → verify."""

import asyncio
import logging
import re

from playwright.async_api import BrowserContext, Page

from ..config import get_base_url
from ..state.models import CourseProgress, CourseStatus, MaterialType, Status
from ..state.store import load_progress, save_progress
from .handlers.document import handle_document
from .handlers.exam import handle_exam
from .handlers.survey import handle_survey
from .handlers.video import handle_video

logger = logging.getLogger("auto_tms.engine.course")

# Global semaphore: max 3 concurrent page operations
# Limited by network bandwidth through proxy tunnel
_page_semaphore = asyncio.Semaphore(3)

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

        # Look for signup button/link
        signup_btn = page.locator('a[href*="signup"], button:has-text("報名"), a:has-text("報名")')
        if await signup_btn.count() > 0:
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

                results.push({
                    index: i,
                    links: links,
                    fullText: fullText.substring(0, 500),
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
        logger.debug(
            "  %s: %s (%s) req=%s",
            m["type"].value,
            m["title"],
            m["url"],
            m.get("required_minutes"),
        )
    return materials


def _classify_node(node: dict, course_id: str) -> dict | None:
    """Classify an xtree-node into a material type."""
    text = node.get("fullText", "")
    links = node.get("links", [])
    index = node.get("index", 0)

    if not links:
        return None

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

    # Classify by pass condition and URL
    if "測驗" in text or "及格" in text or "/quiz/" in href or "/exam/" in href:
        media_id = _extract_id_from_url(href)
        return {
            "id": f"exam_{media_id or index}",
            "type": MaterialType.EXAM,
            "title": title or "測驗",
            "url": href,
            "required_minutes": None,
        }

    if "問卷" in text or "須填寫" in text or "/poll/" in href:
        media_id = _extract_id_from_url(href)
        return {
            "id": f"survey_{media_id or index}",
            "type": MaterialType.SURVEY,
            "title": title or "問卷",
            "url": href,
            "required_minutes": None,
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


def _extract_required_minutes(text: str) -> int | None:
    """Extract required minutes from text like '閱讀達 23 分鐘'."""
    match = re.search(r"閱讀達\s*(\d+)\s*分鐘", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*分鐘", text)
    if match:
        return int(match.group(1))
    return None


async def process_course(context: BrowserContext, course_id: str) -> bool:
    """Process a single course end-to-end.

    1. Enroll if needed
    2. Parse materials
    3. Complete non-exam materials (videos sequential, others can interleave)
    4. Complete exams last
    5. Update progress state

    Returns True if course completed successfully.
    """
    progress = load_progress()
    if not progress:
        from ..state.models import RunProgress

        progress = RunProgress()

    course_prog = progress.courses.get(course_id)
    if course_prog and course_prog.status == CourseStatus.DONE:
        logger.info("Course %s: already completed, skipping", course_id)
        return True

    page = None
    try:
        # Step 1 & 2: Enroll + parse (under semaphore to limit concurrent pages)
        async with _page_semaphore:
            page = await context.new_page()

            if not (course_prog and course_prog.enrolled):
                if not await enroll_in_course(page, course_id):
                    return False

            if not course_prog:
                course_prog = CourseProgress(
                    course_id=course_id, enrolled=True, status=CourseStatus.ENROLLED
                )
                progress.courses[course_id] = course_prog
            else:
                course_prog.enrolled = True
            save_progress(progress)

            raw_materials = await parse_course_materials(page, course_id)
            if not course_prog.materials:
                from ..state.models import MaterialProgress

                course_prog.materials = [
                    MaterialProgress(
                        material_id=m["id"],
                        material_type=m["type"],
                        status=Status.PENDING,
                        required_minutes=m.get("required_minutes"),
                        url=m["url"],
                        title=m.get("title", ""),
                    )
                    for m in raw_materials
                ]
            course_prog.status = CourseStatus.IN_PROGRESS
            save_progress(progress)

            await page.close()
            page = None  # Release the page and semaphore slot

        # Step 3: Process non-exam materials
        for mat in course_prog.materials:
            if mat.status == Status.DONE:
                continue
            if mat.material_type == MaterialType.EXAM:
                continue  # exams last

            mat.status = Status.IN_PROGRESS
            save_progress(progress)

            success = await _handle_material(context, mat)
            mat.status = Status.DONE if success else Status.PENDING
            save_progress(progress)

        # Step 4: Process exams
        for mat in course_prog.materials:
            if mat.status == Status.DONE:
                continue
            if mat.material_type != MaterialType.EXAM:
                continue

            mat.status = Status.IN_PROGRESS
            save_progress(progress)

            success = await _handle_material(context, mat)
            mat.status = Status.DONE if success else Status.PENDING
            save_progress(progress)

        # Step 5: Check completion
        all_done = all(m.status == Status.DONE for m in course_prog.materials)
        course_prog.status = CourseStatus.DONE if all_done else CourseStatus.IN_PROGRESS
        save_progress(progress)

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


async def _handle_material(context: BrowserContext, mat) -> bool:
    """Dispatch to the appropriate material handler with semaphore and retry."""
    for attempt in range(1, MAX_MATERIAL_RETRIES + 1):
        async with _page_semaphore:
            logger.info(
                "Processing %s: %s (%s) [attempt %d/%d]",
                mat.material_type.value,
                mat.title or mat.material_id,
                mat.url,
                attempt,
                MAX_MATERIAL_RETRIES,
            )
            try:
                if mat.material_type == MaterialType.VIDEO:
                    result = await handle_video(context, mat.url, mat.required_minutes)
                elif mat.material_type == MaterialType.DOCUMENT:
                    result = await handle_document(context, mat.url)
                elif mat.material_type == MaterialType.SURVEY:
                    result = await handle_survey(context, mat.url)
                elif mat.material_type == MaterialType.EXAM:
                    result = await handle_exam(context, mat.url)
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
