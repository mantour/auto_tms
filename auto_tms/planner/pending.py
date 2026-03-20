"""Scrape 待修課程 (pending courses) from /course/notCompleteList."""

import logging

from playwright.async_api import BrowserContext

from ..config import get_base_url

logger = logging.getLogger("auto_tms.planner.pending")


async def scrape_pending_courses(context: BrowserContext) -> list[dict]:
    """Scrape all pending online courses from notCompleteList with pagination.

    Returns list of dicts: {course_id, title, hours, completion}.
    Only includes online courses (format=線上) that are not 100% complete.
    """
    page = await context.new_page()
    try:
        all_courses = []
        page_num = 1

        while True:
            url = f"{get_base_url()}/course/notCompleteList"
            if page_num > 1:
                url += f"?page={page_num}"

            await page.goto(url, wait_until="networkidle")

            rows = await page.evaluate(r"""
                () => {
                    const results = [];
                    document.querySelectorAll('table tbody tr').forEach(row => {
                        const tds = row.querySelectorAll('td');
                        if (tds.length < 7) return;
                        const courseId = tds[0]?.textContent?.trim() || '';
                        const title = tds[1]?.textContent?.trim()?.split('\n')[0]?.trim() || '';
                        const format = tds[2]?.textContent?.trim() || '';
                        const hours = tds[3]?.textContent?.trim() || '1';
                        const completion = tds[6]?.textContent?.trim() || '0%';

                        results.push({ courseId, title, format, hours, completion });
                    });
                    return results;
                }
            """)

            if not rows:
                break

            for r in rows:
                if r["format"] != "線上":
                    continue
                if r["completion"] == "100%":
                    continue
                if not r["courseId"] or not r["courseId"].isdigit():
                    continue
                all_courses.append({
                    "course_id": r["courseId"],
                    "title": r["title"],
                    "hours": r["hours"],
                    "completion": r["completion"],
                })

            # Check for next page
            has_next = await page.evaluate(r"""
                (currentPage) => {
                    const links = document.querySelectorAll('.pagination a');
                    for (const a of links) {
                        const match = a.href.match(/page=(\d+)/);
                        if (match && parseInt(match[1]) > currentPage) return true;
                    }
                    return false;
                }
            """, page_num)

            if has_next:
                page_num += 1
            else:
                break

        logger.info("Found %d pending online courses", len(all_courses))
        return all_courses
    finally:
        await page.close()
