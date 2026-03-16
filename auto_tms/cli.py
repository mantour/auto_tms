"""CLI entry point for auto_tms."""

import asyncio

import click

from .config import ensure_dirs, setup_logging

MAX_CONCURRENT_COURSES = 10


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
@click.argument("course_id")
@click.pass_context
def complete(ctx: click.Context, course_id: str) -> None:
    """Complete a single course by ID."""
    logger = ctx.obj["logger"]
    logger.info("Completing course %s", course_id)
    asyncio.run(_complete_courses([course_id]))


@cli.command("complete-file")
@click.argument("file", type=click.Path(exists=True))
@click.pass_context
def complete_file(ctx: click.Context, file: str) -> None:
    """Complete courses listed in a file (one course ID per line)."""
    logger = ctx.obj["logger"]
    with open(file, encoding="utf-8") as f:
        course_ids = [line.strip() for line in f if line.strip()]
    logger.info("Completing %d courses from %s", len(course_ids), file)
    asyncio.run(_complete_courses(course_ids))


@cli.command()
@click.pass_context
def plan(ctx: click.Context) -> None:
    """Scrape training programs and build a shortfall course list."""
    logger = ctx.obj["logger"]
    logger.info("Building course plan from 我的學程...")
    asyncio.run(_plan())


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Full pipeline: plan → complete → verify, up to 3 iterations."""
    logger = ctx.obj["logger"]
    logger.info("Starting full pipeline")
    asyncio.run(_run_pipeline())


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show 我的學程 completion status (from cached data)."""
    from .state.store import load_plan

    plan = load_plan()
    if not plan or not plan.programs:
        click.echo("No cached data. Run 'auto_tms plan' first.")
        return

    programs = []
    for req in plan.programs:
        programs.append({
            "name": req.program_name,
            "total_required": req.total_required,
            "total_shortfall": max(0, req.total_required - req.total_completed),
            "mandatory_shortfall": req.mandatory_required,
        })

    _display_status(programs)


def _display_status(programs: list[dict]) -> None:
    """Display program completion status with progress bars."""
    click.echo()
    click.echo("我的學程 — 完成度")
    click.echo("=" * 80)

    all_pass = True
    for prog in programs:
        name = prog.get("name", "")
        total_req = prog.get("total_required", 0)
        total_short = prog.get("total_shortfall", 0)
        mandatory_short = prog.get("mandatory_shortfall", 0)
        total_done = total_req - total_short

        if total_short <= 0 and mandatory_short <= 0:
            mark = click.style("✓ 通過", fg="green")
        else:
            mark = click.style("✗ 未通過", fg="red")
            all_pass = False

        bar_len = 20
        if total_req > 0:
            filled = int(bar_len * min(total_done, total_req) / total_req)
        else:
            filled = bar_len
        bar = "█" * filled + "░" * (bar_len - filled)

        click.echo(
            f"  {mark}  [{bar}] {total_done:.0f}/{total_req:.0f}h"
            f"  必修差:{mandatory_short:.0f}h"
            f"  {name}"
        )

    click.echo("=" * 80)
    if all_pass:
        click.echo(click.style("所有學程皆已通過！", fg="green", bold=True))
    else:
        incomplete = sum(
            1 for p in programs
            if p.get("total_shortfall", 0) > 0 or p.get("mandatory_shortfall", 0) > 0
        )
        click.echo(f"共 {len(programs)} 個學程，{incomplete} 個未完成")
    click.echo()


async def _complete_courses(course_ids: list[str]) -> None:
    """Complete a list of courses (up to 10 concurrently)."""
    import logging

    from .auth.browser import create_browser_context
    from .auth.login import ensure_authenticated
    from .engine.course import process_course
    from .state.models import CourseProgress, CourseStatus, RunProgress
    from .state.store import load_progress, save_progress

    logger = logging.getLogger("auto_tms")

    progress = load_progress() or RunProgress()
    for cid in course_ids:
        if cid not in progress.courses:
            progress.courses[cid] = CourseProgress(course_id=cid)
    save_progress(progress)

    async with create_browser_context() as context:
        await ensure_authenticated(context)

        # Filter out already-done courses
        pending = [
            cid for cid in course_ids
            if progress.courses[cid].status != CourseStatus.DONE
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
            MAX_CONCURRENT_COURSES,
        )

        sem = asyncio.Semaphore(MAX_CONCURRENT_COURSES)

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


async def _plan() -> None:
    """Build shortfall plan and display status."""
    from .auth.browser import create_browser_context
    from .auth.login import ensure_authenticated
    from .planner.scraper import build_program_requirements, scrape_programs
    from .planner.shortfall import build_shortfall_plan
    from .state.store import save_plan

    async with create_browser_context() as context:
        await ensure_authenticated(context)

        raw_programs = await scrape_programs(context)
        requirements = build_program_requirements(raw_programs)

        plan = build_shortfall_plan(raw_programs, requirements)
        save_plan(plan)

    # Display status (reuse shared display logic)
    status_data = []
    for prog in raw_programs:
        status_data.append({
            "name": prog.get("name", ""),
            "total_required": prog.get("total_required", 0),
            "total_shortfall": prog.get("total_shortfall", 0),
            "mandatory_shortfall": prog.get("mandatory_shortfall", 0),
        })
    _display_status(status_data)

    # Display course plan
    if plan.courses:
        click.echo(f"待修課程（{len(plan.courses)} 門）")
        click.echo("-" * 80)
        for c in plan.courses:
            tag = click.style("必修", fg="red") if c.required else click.style("選修", fg="yellow")
            click.echo(f"  [{tag}] {c.title} ({c.course_id}) — {c.credit_hours:.0f}h")
        click.echo()
    else:
        click.echo(click.style("無需修課！", fg="green", bold=True))
        click.echo()


async def _run_pipeline() -> None:
    """Run full pipeline: plan → complete concurrently → verify, up to 3 iterations."""
    import logging

    from .auth.browser import create_browser_context
    from .auth.login import ensure_authenticated
    from .engine.course import process_course
    from .planner.scraper import build_program_requirements, scrape_programs
    from .planner.shortfall import build_shortfall_plan
    from .state.models import CourseProgress, CourseStatus, RunProgress
    from .state.store import clear_progress, load_progress, save_plan, save_progress

    logger = logging.getLogger("auto_tms")

    MAX_ITERATIONS = 3

    async with create_browser_context() as context:
        await ensure_authenticated(context)

        for iteration in range(1, MAX_ITERATIONS + 1):
            logger.info("=== Pipeline iteration %d/%d ===", iteration, MAX_ITERATIONS)

            # Build plan
            raw_programs = await scrape_programs(context)
            requirements = build_program_requirements(raw_programs)
            plan = build_shortfall_plan(raw_programs, requirements)
            save_plan(plan)

            if not plan.courses:
                logger.info("All programs complete!")
                return

            logger.info("Iteration %d: %d courses to complete", iteration, len(plan.courses))

            # Load or create progress
            progress = load_progress() or RunProgress()
            progress.iteration = iteration

            # Prepare pending courses
            pending = []
            for planned in plan.courses:
                cid = planned.course_id
                if cid not in progress.courses:
                    progress.courses[cid] = CourseProgress(
                        course_id=cid, title=planned.title
                    )
                if progress.courses[cid].status == CourseStatus.DONE:
                    logger.info("Course %s: already done", cid)
                    continue
                pending.append(cid)
            save_progress(progress)

            if not pending:
                logger.info("All planned courses already done, re-checking...")
                clear_progress()
                continue

            logger.info(
                "Processing %d courses concurrently (max %d)",
                len(pending),
                MAX_CONCURRENT_COURSES,
            )

            sem = asyncio.Semaphore(MAX_CONCURRENT_COURSES)

            async def process_one(cid: str) -> bool:
                async with sem:
                    logger.info("Starting: %s", cid)
                    try:
                        return await process_course(context, cid)
                    except Exception:
                        logger.error("Course %s failed", cid, exc_info=True)
                        return False

            results = await asyncio.gather(
                *(process_one(cid) for cid in pending),
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

            # Clear progress for next iteration's fresh check
            clear_progress()

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
