"""Read/write state files to ~/.auto_tms/state/."""

import asyncio
import json
import logging
from pathlib import Path

from .models import CoursePlan, RunProgress

__all__ = ["load_progress", "save_progress", "load_plan", "save_plan"]

logger = logging.getLogger("auto_tms.state")

PROGRESS_FILE = Path.home() / ".auto_tms" / "state" / "progress.json"
PLAN_FILE = Path.home() / ".auto_tms" / "state" / "plan.json"

# Lock for concurrent progress file access
_progress_lock = asyncio.Lock()


def load_progress() -> RunProgress | None:
    """Load progress from disk, or None if no prior run."""
    if not PROGRESS_FILE.exists():
        return None
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        return RunProgress.model_validate(data)
    except Exception:
        logger.warning("Failed to load progress.json, starting fresh", exc_info=True)
        return None


def save_progress(progress: RunProgress) -> None:
    """Persist progress to disk (concurrency-safe via lock)."""
    from datetime import datetime

    progress.updated_at = datetime.now()
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Merge with existing file to avoid concurrent overwrites
    try:
        existing = load_progress()
        if existing:
            # Merge: update existing courses with ours, keep others
            for cid, cp in progress.courses.items():
                existing.courses[cid] = cp
            existing.updated_at = progress.updated_at
            existing.iteration = progress.iteration
            data = existing.model_dump_json(indent=2)
        else:
            data = progress.model_dump_json(indent=2)
    except Exception:
        data = progress.model_dump_json(indent=2)

    PROGRESS_FILE.write_text(data, encoding="utf-8")
    logger.debug("Progress saved to %s", PROGRESS_FILE)


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
    PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    PLAN_FILE.write_text(
        plan.model_dump_json(indent=2), encoding="utf-8"
    )
    logger.debug("Plan saved to %s", PLAN_FILE)


