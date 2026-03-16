"""Configuration loading from environment variables."""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOCAL_ENV_FILE = PROJECT_DIR / ".env"
SYSTEM_ENV_FILE = Path("/etc/auto_tms.env")
DATA_DIR = Path.home() / ".auto_tms"
SESSION_DIR = DATA_DIR / "session"
STATE_DIR = DATA_DIR / "state"
LOG_DIR = DATA_DIR / "logs"

DEFAULT_HOST = ""


def load_env() -> None:
    """Load environment variables from env file (call once at startup).

    Priority: .env in project dir > /etc/auto_tms.env > environment
    """
    if LOCAL_ENV_FILE.exists():
        load_dotenv(LOCAL_ENV_FILE)
    elif SYSTEM_ENV_FILE.exists():
        load_dotenv(SYSTEM_ENV_FILE)
    else:
        load_dotenv()


def get_base_url() -> str:
    """Return base URL from TMS_HOST env var."""
    host = os.getenv("TMS_HOST", DEFAULT_HOST)
    if not host:
        print(
            "Error: TMS_HOST must be set. Run 'make config' to set up.",
            file=sys.stderr,
        )
        sys.exit(1)
    return f"https://{host}"


def get_proxy() -> str | None:
    """Return proxy URL if TMS_PROXY is set, else None."""
    return os.getenv("TMS_PROXY") or None


def load_credentials() -> tuple[str, str]:
    """Load TMS_USER and TMS_PASSWD from env file."""
    load_env()

    user = os.getenv("TMS_USER")
    passwd = os.getenv("TMS_PASSWD")
    if not user or not passwd:
        print(
            "Error: TMS_USER and TMS_PASSWD must be set. Run 'make config' to set up.",
            file=sys.stderr,
        )
        sys.exit(1)
    return user, passwd


PROGRESS_DIR = STATE_DIR / "progress"


def ensure_dirs() -> None:
    """Create data directories if they don't exist."""
    for d in (SESSION_DIR, STATE_DIR, PROGRESS_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def get_current_log_file() -> Path:
    """Return the most recent log file, or None."""
    log_files = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.name, reverse=True)
    return log_files[0] if log_files else LOG_DIR / "no-log-yet.log"


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure logging to file and stderr.

    If run.json exists (resume), append to the latest log file.
    Otherwise, create a new timestamped log file.
    """
    ensure_dirs()
    run_meta_file = STATE_DIR / "run.json"
    existing_log = get_current_log_file()
    if run_meta_file.exists() and existing_log.exists():
        log_file = existing_log  # Resume: append to existing log
    else:
        log_file = LOG_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S}.log"

    logger = logging.getLogger("auto_tms")
    logger.setLevel(logging.DEBUG)

    # File handler — always DEBUG
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    logger.addHandler(fh)

    # Stderr handler — INFO or DEBUG
    sh = logging.StreamHandler()
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
    logger.addHandler(sh)

    return logger
