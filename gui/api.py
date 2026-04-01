"""FastAPI server wrapping auto_tms CLI functionality."""

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auto_tms.config import (
    DATA_DIR,
    LOCAL_ENV_FILE,
    SESSION_DIR,
    STATE_DIR,
    ensure_dirs,
    load_env,
    setup_logging,
)

logger = logging.getLogger("auto_tms.gui")

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

_pipeline_task: asyncio.Task | None = None
_ws_clients: set[WebSocket] = set()


# ---------------------------------------------------------------------------
# WebSocket log handler
# ---------------------------------------------------------------------------


class WebSocketLogHandler(logging.Handler):
    """Push log records to all connected WebSocket clients."""

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        payload = {"type": "log", "line": msg, "level": record.levelname}
        for ws in list(_ws_clients):
            try:
                asyncio.get_event_loop().create_task(ws.send_json(payload))
            except Exception:
                pass


def _install_ws_handler() -> None:
    root = logging.getLogger("auto_tms")
    handler = WebSocketLogHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
    root.addHandler(handler)


# ---------------------------------------------------------------------------
# Broadcast helpers
# ---------------------------------------------------------------------------


async def _broadcast(data: dict) -> None:
    """Send a JSON message to all connected WebSocket clients."""
    for ws in list(_ws_clients):
        try:
            await ws.send_json(data)
        except Exception:
            _ws_clients.discard(ws)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    mode: str = "all"  # all / pending / program
    course_id: str | None = None


class ConfigData(BaseModel):
    host: str = ""
    proxy: str = ""
    user: str = ""
    password: str = ""
    llm_provider: str = "none"
    llm_api_key: str = ""
    llm_base_url: str = ""
    max_pages: int = 5
    max_videos: int = 2


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env()
    ensure_dirs()
    setup_logging(verbose=True)
    _install_ws_handler()
    yield
    # Shutdown: cancel pipeline if running
    global _pipeline_task
    if _pipeline_task and not _pipeline_task.done():
        _pipeline_task.cancel()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).parent / "frontend"

app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------------------
# Config endpoints
# ---------------------------------------------------------------------------


def _read_env() -> dict[str, str]:
    """Read .env file into a dict."""
    env: dict[str, str] = {}
    if LOCAL_ENV_FILE.exists():
        for line in LOCAL_ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def _write_env(env: dict[str, str]) -> None:
    """Write dict back to .env file."""
    lines = []
    for key, val in env.items():
        if val:
            lines.append(f'{key}="{val}"')
    LOCAL_ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


@app.get("/api/config")
async def get_config() -> dict:
    env = _read_env()
    return {
        "host": env.get("TMS_HOST", ""),
        "proxy": env.get("TMS_PROXY", ""),
        "user": env.get("TMS_USER", ""),
        "password": "****" if env.get("TMS_PASSWD") else "",
        "llm_provider": env.get("TMS_LLM_PROVIDER", "none"),
        "llm_api_key": "****" if env.get("ANTHROPIC_API_KEY") or env.get("TMS_LLM_API_KEY") else "",
        "llm_base_url": env.get("TMS_LLM_BASE_URL", ""),
        "max_pages": int(env.get("TMS_MAX_PAGES", "5")),
        "max_videos": int(env.get("TMS_MAX_VIDEOS", "2")),
    }


@app.put("/api/config")
async def update_config(data: ConfigData) -> dict:
    env = _read_env()
    env["TMS_HOST"] = data.host
    env["TMS_PROXY"] = data.proxy
    env["TMS_USER"] = data.user
    if data.password and data.password != "****":
        env["TMS_PASSWD"] = data.password
    env["TMS_LLM_PROVIDER"] = data.llm_provider
    if data.llm_api_key and data.llm_api_key != "****":
        if data.llm_provider == "anthropic":
            env["ANTHROPIC_API_KEY"] = data.llm_api_key
        else:
            env["TMS_LLM_API_KEY"] = data.llm_api_key
    env["TMS_LLM_BASE_URL"] = data.llm_base_url
    env["TMS_MAX_PAGES"] = str(data.max_pages)
    env["TMS_MAX_VIDEOS"] = str(data.max_videos)
    _write_env(env)
    load_env()  # Reload
    return {"status": "saved"}


# ---------------------------------------------------------------------------
# Pipeline control
# ---------------------------------------------------------------------------


@app.post("/api/run")
async def run_pipeline(req: RunRequest) -> dict:
    global _pipeline_task
    if _pipeline_task and not _pipeline_task.done():
        return {"status": "already_running"}

    if req.course_id:
        _pipeline_task = asyncio.create_task(_run_courses([req.course_id]))
    else:
        _pipeline_task = asyncio.create_task(_run_full_pipeline(req.mode))

    return {"status": "started"}


@app.post("/api/stop")
async def stop_pipeline() -> dict:
    global _pipeline_task
    if _pipeline_task and not _pipeline_task.done():
        _pipeline_task.cancel()
        return {"status": "stopped"}
    return {"status": "not_running"}


@app.post("/api/reset")
async def reset_progress() -> dict:
    import shutil

    progress_dir = STATE_DIR / "progress"
    if progress_dir.exists():
        shutil.rmtree(progress_dir)
        progress_dir.mkdir(parents=True, exist_ok=True)
    for f in ("run.json", "plan.json"):
        p = STATE_DIR / f
        if p.exists():
            p.unlink()
    return {"status": "reset"}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@app.get("/api/status")
async def get_status(refresh: bool = False) -> dict:
    from auto_tms.state.store import load_all_courses, load_plan, load_run_meta

    if refresh:
        try:
            await _refresh_status()
        except Exception:
            logger.warning("Status refresh failed", exc_info=True)

    plan = load_plan()
    run_meta = load_run_meta()
    courses = load_all_courses()

    # Pipeline state
    running = _pipeline_task is not None and not _pipeline_task.done()

    # Programs
    programs = []
    if plan and plan.programs:
        for req in plan.programs:
            total_done = req.total_required - max(0, req.total_required - req.total_completed)
            programs.append({
                "name": req.program_name,
                "total_required": req.total_required,
                "total_completed": total_done,
                "mandatory_required": req.mandatory_required,
                "mandatory_completed": req.mandatory_completed,
                "passed": (req.total_completed >= req.total_required
                           and req.mandatory_completed >= req.mandatory_required),
            })

    # Courses
    from auto_tms.state.models import Status
    course_list = []
    for cid, cp in courses.items():
        materials = []
        for m in cp.materials:
            materials.append({
                "id": m.material_id,
                "type": m.material_type.value,
                "status": m.status.value,
                "title": m.title,
            })
        course_list.append({
            "course_id": cid,
            "title": cp.title,
            "status": cp.status.value,
            "materials": materials,
        })

    return {
        "running": running,
        "iteration": run_meta.iteration if run_meta else 0,
        "started_at": run_meta.started_at.isoformat() if run_meta else None,
        "programs": programs,
        "courses": course_list,
    }


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@app.websocket("/ws/progress")
async def ws_progress(ws: WebSocket) -> None:
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # Keep alive
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)


# ---------------------------------------------------------------------------
# Static files (frontend)
# ---------------------------------------------------------------------------


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")


# ---------------------------------------------------------------------------
# Pipeline runners (reuse cli.py logic)
# ---------------------------------------------------------------------------


async def _run_courses(course_ids: list[str]) -> None:
    """Run specific courses — mirrors cli._complete_courses."""
    from auto_tms.auth.browser import create_browser_context
    from auto_tms.auth.login import ensure_authenticated
    from auto_tms.config import MAX_CONCURRENT_PAGES
    from auto_tms.engine.course import process_course
    from auto_tms.state.models import CourseProgress, CourseStatus
    from auto_tms.state.store import load_course_progress, save_course_progress

    for cid in course_ids:
        if not load_course_progress(cid):
            save_course_progress(cid, CourseProgress(course_id=cid))

    async with create_browser_context() as context:
        await ensure_authenticated(context)
        pending = [
            cid for cid in course_ids
            if (load_course_progress(cid) or CourseProgress(course_id=cid)).status != CourseStatus.DONE
        ]
        if not pending:
            logger.info("All courses already done!")
            return

        logger.info("Processing %d courses", len(pending))
        sem = asyncio.Semaphore(MAX_CONCURRENT_PAGES)

        async def process_one(cid: str) -> bool:
            async with sem:
                logger.info("=== Starting course %s ===", cid)
                success = await process_course(context, cid)
                await _broadcast({"type": "status", "data": {"event": "course_done", "course_id": cid, "success": success}})
                return success

        await asyncio.gather(*(process_one(cid) for cid in pending), return_exceptions=True)
    await _broadcast({"type": "status", "data": {"event": "pipeline_done"}})


async def _run_full_pipeline(mode: str = "all") -> None:
    """Run full pipeline — mirrors cli._run_pipeline."""
    from auto_tms.auth.browser import create_browser_context
    from auto_tms.auth.login import ensure_authenticated
    from auto_tms.config import MAX_CONCURRENT_PAGES
    from auto_tms.engine.course import process_course
    from auto_tms.planner.pending import scrape_pending_courses
    from auto_tms.planner.scraper import build_program_requirements, scrape_programs
    from auto_tms.planner.shortfall import build_shortfall_plan
    from auto_tms.state.models import CourseProgress, CourseStatus, RunMeta
    from auto_tms.state.store import (
        load_all_courses,
        load_course_progress,
        load_run_meta,
        save_course_progress,
        save_plan,
        save_run_meta,
    )

    MAX_ITERATIONS = 3
    run_meta = load_run_meta() or RunMeta()

    async with create_browser_context() as context:
        await ensure_authenticated(context)

        for iteration in range(1, MAX_ITERATIONS + 1):
            logger.info("=== Pipeline iteration %d/%d ===", iteration, MAX_ITERATIONS)
            await _broadcast({"type": "status", "data": {"event": "iteration", "iteration": iteration}})

            all_progress = load_all_courses()
            exclude_ids = {
                cid for cid, cp in all_progress.items()
                if cp.status in (CourseStatus.SKIPPED, CourseStatus.DONE)
            }

            course_ids: list[str] = []
            title_map: dict[str, str] = {}
            seen: set[str] = set(exclude_ids)

            if mode in ("all", "pending"):
                pending = await scrape_pending_courses(context)
                for c in pending:
                    cid = c["course_id"]
                    title_map[cid] = c.get("title", "")
                    if cid not in seen:
                        course_ids.append(cid)
                        seen.add(cid)

            if mode in ("all", "program"):
                raw_programs = await scrape_programs(context, exclude_ids)
                requirements = build_program_requirements(raw_programs)
                plan = build_shortfall_plan(raw_programs, requirements, seen)
                save_plan(plan)
                for c in plan.courses:
                    title_map[c.course_id] = c.title
                    if c.course_id not in seen:
                        course_ids.append(c.course_id)
                        seen.add(c.course_id)

            if not course_ids:
                logger.info("All complete!")
                break

            pending_ids = []
            for cid in course_ids:
                cp = load_course_progress(cid)
                if cp and cp.status == CourseStatus.DONE:
                    continue
                if not cp:
                    save_course_progress(cid, CourseProgress(course_id=cid, title=title_map.get(cid, "")))
                elif not cp.title and title_map.get(cid):
                    cp.title = title_map[cid]
                    save_course_progress(cid, cp)
                pending_ids.append(cid)

            if not pending_ids:
                continue

            run_meta.iteration = iteration
            save_run_meta(run_meta)

            sem = asyncio.Semaphore(MAX_CONCURRENT_PAGES)

            async def process_one(cid: str) -> bool:
                async with sem:
                    try:
                        success = await process_course(context, cid)
                        await _broadcast({"type": "status", "data": {"event": "course_done", "course_id": cid, "success": success}})
                        return success
                    except Exception:
                        logger.error("Course %s failed", cid, exc_info=True)
                        return False

            await asyncio.gather(*(process_one(cid) for cid in pending_ids), return_exceptions=True)

    await _broadcast({"type": "status", "data": {"event": "pipeline_done"}})


async def _refresh_status() -> None:
    """Scrape TMS for fresh status data."""
    from auto_tms.auth.browser import create_browser_context
    from auto_tms.auth.login import ensure_authenticated
    from auto_tms.planner.scraper import build_program_requirements, scrape_programs
    from auto_tms.planner.shortfall import build_shortfall_plan
    from auto_tms.state.store import save_plan

    async with create_browser_context() as context:
        await ensure_authenticated(context)
        raw_programs = await scrape_programs(context)
    requirements = build_program_requirements(raw_programs)
    plan = build_shortfall_plan(raw_programs, requirements)
    save_plan(plan)
