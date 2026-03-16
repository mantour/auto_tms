"""Read/write state files to ~/.auto_tms/state/.

Progress is stored per-course to avoid race conditions during concurrent processing:
- state/run.json — run metadata (started_at, iteration)
- state/progress/<courseId>.json — per-course progress
- state/plan.json — shortfall course plan
"""

import json
import logging
from pathlib import Path

from .models import CoursePlan, CourseProgress, RunMeta

logger = logging.getLogger("auto_tms.state")

STATE_DIR = Path.home() / ".auto_tms" / "state"
RUN_META_FILE = STATE_DIR / "run.json"
PROGRESS_DIR = STATE_DIR / "progress"
PLAN_FILE = STATE_DIR / "plan.json"


# ---------------------------------------------------------------------------
# Run metadata
# ---------------------------------------------------------------------------


def load_run_meta() -> RunMeta | None:
    """Load run metadata from disk, or None if no prior run."""
    if not RUN_META_FILE.exists():
        return None
    try:
        data = json.loads(RUN_META_FILE.read_text(encoding="utf-8"))
        return RunMeta.model_validate(data)
    except Exception:
        logger.warning("Failed to load run.json, starting fresh", exc_info=True)
        return None


def save_run_meta(meta: RunMeta) -> None:
    """Persist run metadata to disk."""
    from datetime import datetime

    meta.updated_at = datetime.now()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    RUN_META_FILE.write_text(meta.model_dump_json(indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-course progress
# ---------------------------------------------------------------------------


def _course_file(course_id: str) -> Path:
    return PROGRESS_DIR / f"{course_id}.json"


def load_course_progress(course_id: str) -> CourseProgress | None:
    """Load progress for a single course, or None if not tracked yet."""
    path = _course_file(course_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return CourseProgress.model_validate(data)
    except Exception:
        logger.warning("Failed to load progress for %s", course_id, exc_info=True)
        return None


def save_course_progress(course_id: str, progress: CourseProgress) -> None:
    """Persist progress for a single course. Only touches this course's file."""
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    path = _course_file(course_id)
    path.write_text(progress.model_dump_json(indent=2), encoding="utf-8")


def load_all_courses() -> dict[str, CourseProgress]:
    """Load all course progress files. Used by status display."""
    result: dict[str, CourseProgress] = {}
    if not PROGRESS_DIR.exists():
        return result
    for path in sorted(PROGRESS_DIR.glob("*.json")):
        course_id = path.stem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result[course_id] = CourseProgress.model_validate(data)
        except Exception:
            logger.warning("Failed to load %s", path, exc_info=True)
    return result


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def load_plan() -> CoursePlan | None:
    """Load course plan from disk."""
    if not PLAN_FILE.exists():
        return None
    try:
        data = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
        return CoursePlan.model_validate(data)
    except Exception:
        logger.warning("Failed to load plan.json", exc_info=True)
        return None


def save_plan(plan: CoursePlan) -> None:
    """Persist course plan to disk."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_FILE.write_text(
        plan.model_dump_json(indent=2), encoding="utf-8"
    )
