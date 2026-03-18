"""Calculate shortfall courses: which courses to take to meet program requirements."""

import logging

from ..state.models import CoursePlan, PlannedCourse, ProgramRequirement
from .scraper import parse_hours

logger = logging.getLogger("auto_tms.planner.shortfall")


def build_shortfall_plan(
    programs: list[dict],
    requirements: list[ProgramRequirement],
) -> CoursePlan:
    """Build a minimal course list to satisfy all program requirements.

    Strategy:
    1. For each program with shortfall, collect its incomplete online courses
    2. Add 必修 (required) courses first
    3. Add 選修 (elective) courses if 總稽核值 still short
    4. Skip 面授 (in-person) and already-completed courses

    Args:
        programs: Raw scraped program data with course lists.
        requirements: Parsed program requirements.

    Returns:
        CoursePlan with the minimal set of courses to complete.
    """
    plan = CoursePlan(programs=requirements)
    seen_course_ids: set[str] = set()

    for prog, req in zip(programs, requirements):
        total_shortfall = prog.get("total_shortfall", 0.0)
        mandatory_shortfall = prog.get("mandatory_shortfall", 0.0)

        if total_shortfall <= 0 and mandatory_shortfall <= 0:
            logger.info("Program %s: already complete", req.program_name)
            continue

        courses = prog.get("courses", [])
        if not courses:
            logger.warning("Program %s: shortfall but no courses found", req.program_name)
            continue

        logger.info(
            "Program %s: shortfall total=%.1f, mandatory=%.1f",
            req.program_name,
            total_shortfall,
            mandatory_shortfall,
        )

        # Filter: online only, not yet completed
        online_courses = [
            c for c in courses
            if c.get("is_online") and c.get("completion") != "100%"
        ]

        required_courses = [c for c in online_courses if c.get("is_required")]
        elective_courses = [c for c in online_courses if not c.get("is_required")]

        added_hours = 0.0

        # First: add required (必修) courses — only enough to fill mandatory shortfall
        if mandatory_shortfall > 0:
            mandatory_remaining = mandatory_shortfall
            for course in required_courses:
                if mandatory_remaining <= 0:
                    break
                cid = course.get("course_id", "")
                if not cid or cid in seen_course_ids:
                    continue
                hours = _get_hours(course)
                plan.courses.append(PlannedCourse(
                    course_id=cid,
                    title=course.get("title", ""),
                    url=f"/course/{cid}",
                    required=True,
                    credit_hours=hours,
                ))
                seen_course_ids.add(cid)
                added_hours += hours
                mandatory_remaining -= hours

        # Then: add electives if total still short
        remaining = total_shortfall - added_hours
        if remaining > 0:
            for course in elective_courses:
                cid = course.get("course_id", "")
                if not cid or cid in seen_course_ids:
                    continue
                hours = _get_hours(course)
                plan.courses.append(PlannedCourse(
                    course_id=cid,
                    title=course.get("title", ""),
                    url=f"/course/{cid}",
                    required=False,
                    credit_hours=hours,
                ))
                seen_course_ids.add(cid)
                remaining -= hours
                if remaining <= 0:
                    break

    logger.info("Plan: %d courses to complete", len(plan.courses))
    return plan


def _get_hours(course: dict) -> float:
    """Get credit hours for a course from scraped data."""
    rh = course.get("required_hours", "-")
    if rh and rh != "-":
        val = parse_hours(rh)
        if val > 0:
            return val
    return 1.0  # Default assumption
