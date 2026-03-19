"""Scrape 我的學程 pages to extract programs and course requirements."""

import logging
import re

from playwright.async_api import BrowserContext, Page

from ..config import get_base_url
from ..state.models import ProgramRequirement

logger = logging.getLogger("auto_tms.planner.scraper")


async def scrape_programs(
    context: BrowserContext, exclude_ids: set[str] | None = None,
) -> list[dict]:
    """Scrape all programs from 我的學程, handling pagination.

    Args:
        context: Playwright browser context.
        exclude_ids: Course IDs to skip when resolving categories (already done/skipped).

    Returns list of dicts with program info and sub-program course lists.
    """
    page = await context.new_page()
    try:
        programs = []
        page_num = 1

        while True:
            url = f"{get_base_url()}/program/mine"
            if page_num > 1:
                url += f"?page={page_num}"

            await page.goto(url, wait_until="load")

            # Extract program rows from the table
            rows = await _extract_program_rows(page)
            if not rows:
                break

            programs.extend(rows)

            # Check for next page: find pagination links with page > current
            has_next = await page.evaluate(r"""
                (currentPage) => {
                    const links = document.querySelectorAll('.pagination a[href*="page="]');
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

        logger.info("Found %d programs", len(programs))

        # For each program, scrape its detail page for hours + course list
        for prog in programs:
            detail = await _scrape_program_detail(page, prog["url"], exclude_ids)
            prog.update(detail)

        return programs
    finally:
        await page.close()


async def _extract_program_rows(page: Page) -> list[dict]:
    """Extract program rows from the 我的學程 table."""
    rows = await page.evaluate("""
        () => {
            const results = [];
            document.querySelectorAll('table tbody tr').forEach(row => {
                const cells = Array.from(row.querySelectorAll('td'));
                if (cells.length < 3) return;

                const link = row.querySelector('a[href*="/program/showMy/"]');
                if (!link) return;

                const href = link.href || '';
                const texts = cells.map(c => c.textContent.trim());

                results.push({
                    id: href.match(/showMy\\/(\\d+)/)?.[1] || '',
                    name: link.textContent.trim(),
                    url: href,
                    raw_texts: texts,
                });
            });
            return results;
        }
    """)
    return rows


async def _resolve_category(
    page: Page, category_url: str, required_hours: str, completed_hours: str,
    exclude_ids: set[str] | None = None,
) -> list[dict]:
    """Follow a category link to the search page and extract available online courses.

    Only returns enough courses to fill the shortfall (required - completed).
    Skips courses in exclude_ids (already done/skipped).

    Args:
        page: Playwright page to navigate.
        category_url: URL like /course/latest?category=XXX.
        required_hours: The category's required hours (from parent node col[3]).
        completed_hours: The category's completed hours (from parent node col[4]).
        exclude_ids: Course IDs to skip (already done/skipped).

    Returns list of course dicts compatible with the main course list.
    """
    shortfall = parse_hours(required_hours) - parse_hours(completed_hours)
    if shortfall <= 0:
        return []

    await page.goto(category_url, wait_until="networkidle")

    raw_courses = await page.evaluate(r"""
        () => {
            const results = [];
            document.querySelectorAll('table tbody tr').forEach(row => {
                const tds = row.querySelectorAll('td');
                if (tds.length < 6) return;
                const courseId = tds[0]?.textContent?.trim() || '';
                const rawTitle = tds[1]?.textContent?.trim() || '';
                const title = rawTitle.split('\n')[0].trim();
                const format = tds[2]?.textContent?.trim() || '';
                const hours = tds[4]?.textContent?.trim() || '1';
                const status = tds[tds.length - 1]?.textContent?.trim() || '';

                results.push({ courseId, title, format, hours, status });
            });
            return results;
        }
    """)

    is_required = required_hours not in ("-", "", "0")
    _exclude = exclude_ids or set()
    courses = []
    filled = 0.0
    for c in raw_courses:
        if filled >= shortfall:
            break
        if c["format"] != "線上":
            continue
        if c["status"] == "已通過":
            continue
        if not c["courseId"] or not c["courseId"].isdigit():
            continue
        if c["courseId"] in _exclude:
            continue
        hours = parse_hours(c["hours"]) or 1.0
        courses.append({
            "course_id": c["courseId"],
            "title": c["title"],
            "type": "課程",
            "format": "線上",
            "required_hours": c["hours"],
            "completed_hours": "0",
            "completion": "0%",
            "is_required": is_required,
            "is_online": True,
            "is_in_person": False,
        })
        filled += hours

    logger.debug("Category %s: shortfall=%.1f, selected %d courses", category_url, shortfall, len(courses))
    return courses


async def _scrape_program_detail(
    page: Page, program_url: str, exclude_ids: set[str] | None = None,
) -> dict:
    """Scrape a program detail page for hour requirements and course list.

    Returns dict with keys: total_required, total_shortfall, mandatory_shortfall, courses.
    """
    result = {
        "total_required": 0.0,
        "total_shortfall": 0.0,
        "mandatory_shortfall": 0.0,
        "courses": [],
    }

    if not program_url:
        return result

    if not program_url.startswith("http"):
        program_url = f"{get_base_url()}{program_url}"

    await page.goto(program_url, wait_until="load")

    # Extract hours from the .report divs
    reports = await page.evaluate("""
        () => {
            const data = {};
            document.querySelectorAll('.report').forEach(el => {
                const title = el.querySelector('.title')?.textContent?.trim() || '';
                const number = el.querySelector('.number')?.textContent?.trim() || '0';
                data[title] = number;
            });
            // Also get total from the description
            const dlItems = document.querySelectorAll('dt, dd');
            for (let i = 0; i < dlItems.length - 1; i++) {
                if (dlItems[i].textContent.trim() === '總稽核值') {
                    data['總稽核值_required'] = dlItems[i+1].textContent.trim();
                }
            }
            return data;
        }
    """)
    logger.debug("Program %s reports: %s", program_url, reports)

    result["total_required"] = parse_hours(reports.get("總稽核值_required", "0"))
    result["total_shortfall"] = parse_hours(reports.get("總稽核值不足", "0"))
    result["mandatory_shortfall"] = parse_hours(reports.get("必修不足", "0"))

    # Extract courses and category nodes from xtree-node elements
    nodes = await page.evaluate(r"""
        () => {
            const results = [];
            document.querySelectorAll('li.xtree-node').forEach(node => {
                const cols = node.querySelectorAll('.ext-col');
                if (cols.length < 6) return;

                // Columns: 編號, 類型, 形式, 必修要求, 完成時數, 完成度
                const courseId = cols[0]?.textContent?.trim() || '';
                const type = cols[1]?.textContent?.trim() || '';
                const format = cols[2]?.textContent?.trim() || '';
                const requiredHours = cols[3]?.textContent?.trim() || '-';
                const completedHours = cols[4]?.textContent?.trim() || '0';
                const completion = cols[5]?.textContent?.trim() || '0%';

                // Get title and courseId from the data-url attribute
                const link = node.querySelector('a[data-url*="courseId="]');
                const title = link?.textContent?.trim() || '';
                const dataUrl = link?.getAttribute('data-url') || '';
                const urlCourseId = dataUrl.match(/courseId=(\d+)/)?.[1] || courseId;

                // For category nodes, get the category link URL
                let categoryUrl = '';
                if (type === '課程類別') {
                    const catLink = node.querySelector('a[href*="category="]');
                    if (catLink) categoryUrl = catLink.href;
                }

                results.push({
                    course_id: urlCourseId,
                    title: title,
                    type: type,
                    format: format,
                    required_hours: requiredHours,
                    completed_hours: completedHours,
                    completion: completion,
                    is_required: requiredHours !== '-' && requiredHours !== '',
                    is_online: format === '線上',
                    is_in_person: format === '面授',
                    category_url: categoryUrl,
                });
            });
            return results;
        }
    """)

    # Separate courses and categories
    courses = []
    categories = []
    for node in nodes:
        if node.get("type") == "課程類別" and node.get("category_url"):
            categories.append(node)
        else:
            courses.append(node)

    # Resolve category nodes into actual courses
    for cat in categories:
        cat_url = cat["category_url"]
        cat_required = cat.get("required_hours", "-")
        cat_completed = cat.get("completed_hours", "0")
        cat_completion = cat.get("completion", "0%")
        if cat_completion == "100%":
            logger.debug("Program %s: category '%s' already complete", program_url, cat.get("title", ""))
            continue
        logger.info("Program %s: resolving category '%s'", program_url, cat.get("title", ""))
        resolved = await _resolve_category(page, cat_url, cat_required, cat_completed, exclude_ids)
        courses.extend(resolved)

    result["courses"] = courses
    logger.debug("Program %s: found %d courses", program_url, len(courses))
    return result


def parse_hours(text: str) -> float:
    """Extract numeric hours from text like '6 小時', '6.0', or '3/6'."""
    match = re.search(r"([\d.]+)\s*/\s*([\d.]+)", text)
    if match:
        return float(match.group(1))
    match = re.search(r"([\d.]+)", text)
    if match:
        return float(match.group(1))
    return 0.0


def build_program_requirements(raw_programs: list[dict]) -> list[ProgramRequirement]:
    """Convert raw scraped data into ProgramRequirement models."""
    requirements = []
    for prog in raw_programs:
        req = ProgramRequirement(
            program_id=prog.get("id", ""),
            program_name=prog.get("name", ""),
            total_required=prog.get("total_required", 0.0),
            total_completed=prog.get("total_required", 0.0) - prog.get("total_shortfall", 0.0),
            mandatory_required=prog.get("mandatory_shortfall", 0.0),  # shortfall = what's needed
            mandatory_completed=0.0,
        )
        requirements.append(req)
    return requirements
