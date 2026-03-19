"""CLI entry point for auto_tms."""

import asyncio
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path

import click

from .config import (
    DATA_DIR,
    MAX_CONCURRENT_PAGES,
    SESSION_DIR,
    ensure_dirs,
    get_current_log_file,
    setup_logging,
)


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug output")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """教育訓練系統 — 自動化測試工具"""
    ctx.ensure_object(dict)
    ensure_dirs()
    ctx.obj["logger"] = setup_logging(verbose)
    ctx.obj["verbose"] = verbose


@cli.command()
@click.argument("course_id", required=False)
@click.option("-f", "--file", "file_path", type=click.Path(exists=True), help="Course IDs file (one per line)")
@click.option("-m", "--mode", type=click.Choice(["all", "pending", "program"]), default="all",
              help="Pipeline mode: all (pending+program), pending only, program only")
@click.pass_context
def run(ctx: click.Context, course_id: str | None, file_path: str | None, mode: str) -> None:
    """Run pipeline or complete specific courses.

    \b
    auto_tms run                      Full pipeline (pending + program)
    auto_tms run --mode pending       Only complete pending courses
    auto_tms run --mode program       Only run program shortfall
    auto_tms run <courseId>            Complete one course
    auto_tms run -f courses.txt       Complete courses from file
    """
    logger = ctx.obj["logger"]

    if course_id and file_path:
        raise click.UsageError("Cannot specify both course_id and --file")

    if course_id:
        logger.info("Completing course %s", course_id)
        asyncio.run(_complete_courses([course_id]))
    elif file_path:
        with open(file_path, encoding="utf-8") as f:
            course_ids = [line.strip() for line in f if line.strip()]
        logger.info("Completing %d courses from %s", len(course_ids), file_path)
        asyncio.run(_complete_courses(course_ids))
    else:
        logger.info("Starting pipeline (mode=%s)", mode)
        asyncio.run(_run_pipeline(mode))


@cli.command()
@click.option("--refresh", is_flag=True, help="Refresh from web (default: cached)")
@click.option("--all", "show_all", is_flag=True, help="Show done courses too")
@click.pass_context
def status(ctx: click.Context, refresh: bool, show_all: bool) -> None:
    """Show program completion and course progress."""
    if not ctx.obj.get("verbose"):
        logging.getLogger("auto_tms").setLevel(logging.WARNING)
    if refresh:
        asyncio.run(_status_live(show_all))
    else:
        _status_cached(show_all)


@cli.command()
def log() -> None:
    """Tail the latest log file."""
    log_file = get_current_log_file()
    if not log_file.exists():
        click.echo("No log files found.")
        return
    click.echo(f"Tailing {log_file.name}")
    subprocess.run(["tail", "-f", str(log_file)])


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------


def _status_cached(show_all: bool = False) -> None:
    """Display status from cached state files."""
    from .state.store import load_all_courses, load_plan, load_run_meta

    plan = load_plan()
    run_meta = load_run_meta()
    courses = load_all_courses()

    if not plan and not courses:
        click.echo("No cached data. Run 'auto_tms run' first.")
        return

    if plan and plan.programs:
        programs = []
        for req in plan.programs:
            programs.append({
                "name": req.program_name,
                "total_required": req.total_required,
                "total_shortfall": max(0, req.total_required - req.total_completed),
                "mandatory_shortfall": req.mandatory_required,
            })
        _display_programs(programs, plan.created_at)

    if courses:
        _display_progress(courses, show_all)

    _display_pipeline_footer(plan, run_meta)


async def _status_live(show_all: bool = False) -> None:
    """Scrape live data and display status + progress."""
    from .auth.browser import create_browser_context
    from .auth.login import ensure_authenticated
    from .planner.scraper import build_program_requirements, scrape_programs
    from .planner.shortfall import build_shortfall_plan
    from .state.store import load_all_courses, load_run_meta, save_plan

    async with create_browser_context() as context:
        await ensure_authenticated(context)
        raw_programs = await scrape_programs(context)

    requirements = build_program_requirements(raw_programs)
    plan = build_shortfall_plan(raw_programs, requirements)
    save_plan(plan)

    run_meta = load_run_meta()
    courses = load_all_courses()

    status_data = []
    for prog in raw_programs:
        status_data.append({
            "name": prog.get("name", ""),
            "total_required": prog.get("total_required", 0),
            "total_shortfall": prog.get("total_shortfall", 0),
            "mandatory_shortfall": prog.get("mandatory_shortfall", 0),
        })
    _display_programs(status_data)

    if courses:
        _display_progress(courses, show_all)

    _display_pipeline_footer(plan, run_meta)


# ---------------------------------------------------------------------------
# Pipeline header: running state, errors, playing videos
# ---------------------------------------------------------------------------


def _is_pipeline_running() -> bool:
    """Check if the pipeline process is running."""
    result = subprocess.run(
        ["pgrep", "-f", "auto_tms.*run"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _parse_log_activity() -> dict:
    """Parse the latest log for active videos, errors, and last error message."""
    log_file = get_current_log_file()
    if not log_file.exists():
        return {"playing_videos": [], "error_count": 0, "last_error": ""}

    lines = log_file.read_text(encoding="utf-8").splitlines()

    # Track currently playing videos (started but not finished)
    video_started: dict[str, dict] = {}  # media_id -> {minutes, timestamp}
    for line in lines:
        m = re.search(
            r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+.*Video .*/media/(\d+): waiting (\d+) min",
            line,
        )
        if m:
            ts_str, media_id, minutes = m.group(1), m.group(2), int(m.group(3))
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                ts = None
            video_started[media_id] = {"minutes": minutes, "started": ts}
            continue
        m = re.search(r"Video .*/media/(\d+): playback time reached", line)
        if m:
            video_started.pop(m.group(1), None)

    # Calculate remaining time for each playing video
    now = datetime.now()
    playing = []
    for media_id, info in video_started.items():
        if info["started"]:
            elapsed = (now - info["started"]).total_seconds() / 60
            remaining = max(0, info["minutes"] - elapsed)
            playing.append({"id": media_id, "remaining_min": round(remaining)})
        else:
            playing.append({"id": media_id, "remaining_min": info["minutes"]})

    # Count errors and find last error
    error_count = 0
    last_error = ""
    for line in lines:
        if "ERROR" in line:
            error_count += 1
            # Strip timestamp prefix for display
            m = re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+\s+ERROR\s+(.*)", line)
            last_error = m.group(1).strip() if m else line.strip()

    return {
        "playing_videos": playing,
        "error_count": error_count,
        "last_error": last_error,
    }


def _format_duration(td) -> str:
    """Format a timedelta as 'Xh Ym'."""
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        return "0m"
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _check_session_valid() -> str:
    """Check if a Playwright session file exists and when it was last used."""
    state_file = SESSION_DIR / "storage_state.json"
    if not state_file.exists():
        return click.style("no session", fg="red")
    mtime = datetime.fromtimestamp(state_file.stat().st_mtime)
    age_str = _format_duration(datetime.now() - mtime)
    return click.style(f"ok ({age_str} ago)", fg="green")


def _display_pipeline_footer(plan, run_meta) -> None:
    """Display pipeline status footer: running state, errors, videos.

    Placed at the bottom so it's always visible in the terminal.
    """
    # Running state
    running = _is_pipeline_running()
    if running:
        state = click.style("Running", fg="green")
    else:
        state = click.style("Stopped", fg="white")

    parts = [f"Pipeline: {state}"]
    if run_meta:
        parts.append(f"({run_meta.iteration}/3)")
        uptime = datetime.now() - run_meta.started_at
        parts.append(f"| {_format_duration(uptime)}")

    # Errors from log
    activity = _parse_log_activity()
    if activity["error_count"]:
        parts.append("| " + click.style(f"errors: {activity['error_count']}", fg="red"))

    # Session health
    parts.append(f"| session: {_check_session_valid()}")

    click.echo(" ".join(parts))

    # Playing videos
    if activity["playing_videos"]:
        vids = ", ".join(
            f"{v['id']} (~{v['remaining_min']}m)" for v in activity["playing_videos"]
        )
        click.echo(click.style(f"Playing: {vids}", fg="cyan"))

    # Last error
    if activity["last_error"]:
        err_msg = activity["last_error"]
        if len(err_msg) > 100:
            err_msg = err_msg[:97] + "..."
        click.echo(click.style(f"Last error: {err_msg}", fg="red"))


def _truncate(text: str, max_len: int = 30) -> str:
    """Truncate text with ellipsis if too long."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


# ---------------------------------------------------------------------------
# Program completion display
# ---------------------------------------------------------------------------


def _display_programs(
    programs: list[dict], scrape_time: datetime | None = None,
) -> None:
    """Display all program completion status with progress bars."""
    passed = sum(
        1 for p in programs
        if p.get("total_shortfall", 0) <= 0 and p.get("mandatory_shortfall", 0) <= 0
    )
    total = len(programs)

    click.echo()
    ts = f"（{scrape_time:%Y-%m-%d %H:%M}）" if scrape_time else ""
    click.echo(f"我的學程 — {passed}/{total} 通過{ts}")
    click.echo("=" * 80)

    for prog in programs:
        name = prog.get("name", "")
        total_req = prog.get("total_required", 0)
        total_short = prog.get("total_shortfall", 0)
        mandatory_short = prog.get("mandatory_shortfall", 0)
        total_done = total_req - total_short
        is_pass = total_short <= 0 and mandatory_short <= 0

        if is_pass:
            mark = click.style("✓ 通過", fg="green")
        else:
            mark = click.style("✗ 未通過", fg="red")

        bar_len = 20
        if total_req > 0:
            filled = int(bar_len * min(total_done, total_req) / total_req)
        else:
            filled = bar_len
        bar = "█" * filled + "░" * (bar_len - filled)

        click.echo(
            f"  {mark}  [{bar}] {total_done:.0f}/{total_req:.0f}h"
            f"  必修差:{mandatory_short:.0f}h"
            f"  {_truncate(name, 35)}"
        )

    click.echo("=" * 80)
    if passed == total:
        click.echo(click.style("所有學程皆已通過！", fg="green", bold=True))
    click.echo()


# ---------------------------------------------------------------------------
# Course progress display
# ---------------------------------------------------------------------------


def _display_progress(courses: dict, show_all: bool = False) -> None:
    """Display course execution progress from per-course files.

    Default: only show active (in_progress) and failed courses.
    --all: show everything including done and pending.
    """
    from .state.models import CourseStatus, MaterialType, Status

    if not courses:
        return

    counts = {"done": 0, "in_progress": 0, "pending": 0, "failed": 0}
    failed_details: list[str] = []
    total_remaining_minutes = 0
    display_rows: list[str] = []

    for cid, cp in courses.items():
        total_materials = len(cp.materials)
        done_materials = sum(1 for m in cp.materials if m.status == Status.DONE)

        if cp.status == CourseStatus.DONE:
            icon = click.style("✓", fg="green")
            label = "done"
            counts["done"] += 1
            visible = show_all  # Hidden by default
        elif cp.status == CourseStatus.IN_PROGRESS:
            has_failed = any(m.status == Status.SKIPPED for m in cp.materials)
            if has_failed:
                icon = click.style("✗", fg="red")
                label = "incomplete"
                counts["failed"] += 1
                for m in cp.materials:
                    if m.status == Status.SKIPPED:
                        failed_details.append(f"{cid}/{m.material_id}")
            else:
                icon = click.style("◎", fg="yellow")
                label = "in_progress"
                counts["in_progress"] += 1
            visible = True
        else:
            icon = click.style("·", fg="white")
            label = "pending"
            counts["pending"] += 1
            visible = True  # Show pending by default

        # Estimate remaining video minutes
        if cp.status != CourseStatus.DONE:
            for m in cp.materials:
                if m.status != Status.DONE and m.material_type == MaterialType.VIDEO:
                    total_remaining_minutes += m.required_minutes or 0

        if visible:
            title = cp.title or cid
            # Group by material type: [video:1/2 survey:0/1 exam:0/1]
            if total_materials:
                type_counts: dict[str, list[int]] = {}
                for m in cp.materials:
                    t = m.material_type.value
                    type_counts.setdefault(t, [0, 0])
                    type_counts[t][1] += 1
                    if m.status == Status.DONE:
                        type_counts[t][0] += 1
                mat_info = "[" + " ".join(
                    f"{t}:{d}/{n}" for t, (d, n) in type_counts.items()
                ) + "]"
            else:
                mat_info = ""
            display_rows.append(f"  {icon} {cid}  {_truncate(title, 35):<35s} {label:<14s} {mat_info}")

    # Summary line
    parts = []
    for key in ("done", "in_progress", "pending", "failed"):
        if counts[key]:
            parts.append(f"{counts[key]} {key}")
    summary = f"{len(courses)} courses: {', '.join(parts)}"
    if total_remaining_minutes > 0:
        hours = total_remaining_minutes // 60
        mins = total_remaining_minutes % 60
        est = f"{hours}h {mins}m" if hours else f"{mins}m"
        summary += f" | ~{est} remaining"

    click.echo(f"課程: {summary}")
    click.echo("-" * 80)

    for row in display_rows:
        click.echo(row)

    if not show_all and counts["done"]:
        click.echo(f"  + {counts['done']} done（--all 顯示）")

    click.echo("-" * 80)

    if failed_details:
        click.echo(click.style(f"Failed: {', '.join(failed_details)}", fg="red"))

    click.echo()


# ---------------------------------------------------------------------------
# Course completion
# ---------------------------------------------------------------------------


async def _complete_courses(course_ids: list[str]) -> None:
    """Complete a list of courses (up to 10 concurrently)."""
    import logging

    from .auth.browser import create_browser_context
    from .auth.login import ensure_authenticated
    from .engine.course import process_course
    from .state.models import CourseProgress, CourseStatus
    from .state.store import load_course_progress, save_course_progress

    logger = logging.getLogger("auto_tms")

    # Ensure each course has a progress file
    for cid in course_ids:
        if not load_course_progress(cid):
            save_course_progress(cid, CourseProgress(course_id=cid))

    async with create_browser_context() as context:
        await ensure_authenticated(context)

        pending = [
            cid for cid in course_ids
            if (load_course_progress(cid) or CourseProgress(course_id=cid)).status != CourseStatus.DONE
        ]
        done_count = len(course_ids) - len(pending)
        if done_count:
            logger.info("Skipping %d already-completed courses", done_count)

        if not pending:
            logger.info("All courses already done!")
            return

        logger.info(
            "Processing %d courses (up to %d concurrent)",
            len(pending),
            MAX_CONCURRENT_PAGES,
        )

        sem = asyncio.Semaphore(MAX_CONCURRENT_PAGES)

        async def process_one(cid: str) -> bool:
            async with sem:
                logger.info("=== Starting course %s ===", cid)
                success = await process_course(context, cid)
                if success:
                    logger.info("=== Course %s: DONE ===", cid)
                else:
                    logger.warning("=== Course %s: INCOMPLETE ===", cid)
                return success

        results = await asyncio.gather(
            *(process_one(cid) for cid in pending),
            return_exceptions=True,
        )

        completed = sum(1 for r in results if r is True)
        failed = len(results) - completed
        logger.info("Batch done: %d completed, %d incomplete", completed, failed)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


async def _run_pipeline(mode: str = "all") -> None:
    """Run pipeline: plan from pending + program sources, then complete courses.

    Modes:
        all: merge pending courses + program shortfall, then complete (default)
        pending: only complete pending courses from notCompleteList
        program: only run program shortfall planning

    Progress is preserved across iterations — completed materials (videos,
    surveys, etc.) are never re-processed.
    """
    import logging

    from .auth.browser import create_browser_context
    from .auth.login import ensure_authenticated
    from .engine.course import process_course
    from .planner.pending import scrape_pending_courses
    from .planner.scraper import build_program_requirements, scrape_programs
    from .planner.shortfall import build_shortfall_plan
    from .state.models import CourseProgress, CourseStatus, RunMeta
    from .state.store import (
        load_all_courses,
        load_course_progress,
        load_run_meta,
        save_course_progress,
        save_plan,
        save_run_meta,
    )

    logger = logging.getLogger("auto_tms")

    MAX_ITERATIONS = 3

    # Load or create run metadata
    run_meta = load_run_meta() or RunMeta()

    async with create_browser_context() as context:
        await ensure_authenticated(context)

        for iteration in range(1, MAX_ITERATIONS + 1):
            logger.info("=== Pipeline iteration %d/%d ===", iteration, MAX_ITERATIONS)

            # Collect skipped course IDs (enroll failed) to exclude
            all_progress = load_all_courses()
            exclude_ids = {
                cid for cid, cp in all_progress.items()
                if cp.status in (CourseStatus.SKIPPED, CourseStatus.DONE)
            }
            if exclude_ids:
                logger.info("Excluding %d done/skipped courses", len(exclude_ids))

            # Step 1: Gather course IDs from both sources
            course_ids: list[str] = []
            seen: set[str] = set(exclude_ids)

            # Source A: Pending courses (notCompleteList)
            if mode in ("all", "pending"):
                pending = await scrape_pending_courses(context)
                for c in pending:
                    cid = c["course_id"]
                    if cid not in seen:
                        course_ids.append(cid)
                        seen.add(cid)
                logger.info("Pending: %d courses", len(pending))

            # Source B: Program shortfall
            if mode in ("all", "program"):
                raw_programs = await scrape_programs(context, exclude_ids)
                requirements = build_program_requirements(raw_programs)
                plan = build_shortfall_plan(raw_programs, requirements, seen)
                save_plan(plan)
                for c in plan.courses:
                    if c.course_id not in seen:
                        course_ids.append(c.course_id)
                        seen.add(c.course_id)
                logger.info("Program shortfall: %d courses", len(plan.courses))

            if not course_ids:
                logger.info("All complete!")
                return

            # Step 2: Filter out already-done courses
            pending_ids = []
            for cid in course_ids:
                cp = load_course_progress(cid)
                if cp and cp.status == CourseStatus.DONE:
                    continue
                if not cp:
                    save_course_progress(cid, CourseProgress(course_id=cid))
                pending_ids.append(cid)

            if not pending_ids:
                logger.info("All courses already done, verifying...")
                continue

            logger.info("Iteration %d: %d courses to complete", iteration, len(pending_ids))
            run_meta.iteration = iteration
            save_run_meta(run_meta)

            # Step 3: Complete all courses concurrently
            sem = asyncio.Semaphore(MAX_CONCURRENT_PAGES)

            async def process_one(cid: str) -> bool:
                async with sem:
                    logger.info("Starting: %s", cid)
                    try:
                        return await process_course(context, cid)
                    except Exception:
                        logger.error("Course %s failed", cid, exc_info=True)
                        return False

            results = await asyncio.gather(
                *(process_one(cid) for cid in pending_ids),
                return_exceptions=True,
            )

            completed = sum(1 for r in results if r is True)
            failed = len(results) - completed
            logger.info(
                "Iteration %d results: %d completed, %d incomplete",
                iteration,
                completed,
                failed,
            )

        # Final check
        raw_programs = await scrape_programs(context)
        requirements = build_program_requirements(raw_programs)
        plan = build_shortfall_plan(raw_programs, requirements)

        if plan.courses:
            logger.warning(
                "Still %d courses remaining after %d iterations",
                len(plan.courses),
                MAX_ITERATIONS,
            )
        else:
            logger.info("All programs complete!")
