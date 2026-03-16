"""Show pipeline progress summary from log and state files."""

import re
import sys
from pathlib import Path


def main() -> None:
    log = Path.home() / ".auto_tms" / "logs" / "run_output.log"
    if not log.exists():
        print("No log found. Run 'make run' first.")
        sys.exit(0)

    lines = log.read_text().splitlines()

    videos_done = sum(1 for l in lines if "playback time reached" in l)
    surveys_done = sum(1 for l in lines if ": submitted" in l)
    courses_done = [
        m.group(1) for l in lines if (m := re.search(r"Course (\d+): completed", l))
    ]
    courses_incomplete = [
        m.group(1)
        for l in lines
        if (m := re.search(r"Course (\d+): incomplete", l))
    ]
    retries = sum(1 for l in lines if "attempt 2/" in l or "attempt 3/" in l)
    errors = sum(1 for l in lines if "ERROR" in l)

    # Find current iteration
    iteration = 0
    for l in lines:
        m = re.search(r"Iteration (\d+)", l)
        if m:
            iteration = int(m.group(1))

    # Currently waiting videos
    waiting = {}
    for l in lines:
        m = re.search(r"Video .*/media/(\d+): waiting (\d+) minutes", l)
        if m:
            waiting[m.group(1)] = int(m.group(2))
    for l in lines:
        m = re.search(r"Video .*/media/(\d+): playback time reached", l)
        if m:
            waiting.pop(m.group(1), None)

    # Check if process is running
    import subprocess

    ps = subprocess.run(
        ["pgrep", "-f", "auto_tms.*run"], capture_output=True, text=True
    )
    running = ps.returncode == 0

    print()
    print("Pipeline Progress")
    print("=" * 50)
    print(f"  Status:           {'🟢 Running' if running else '⚪ Stopped'}")
    print(f"  Iteration:        {iteration}/3")
    print(f"  Courses done:     {len(courses_done)}")
    if courses_incomplete:
        print(f"  Courses pending:  {len(set(courses_incomplete) - set(courses_done))}")
    print(f"  Videos done:      {videos_done}")
    print(f"  Surveys done:     {surveys_done}")
    if retries:
        print(f"  Retries:          {retries}")
    if errors:
        print(f"  Errors:           {errors}")
    if waiting:
        print(f"  Videos playing:   {len(waiting)}")
        max_wait = max(waiting.values())
        print(f"  Longest wait:     {max_wait} min")
    print("=" * 50)
    if courses_done:
        print(f"  Completed: {', '.join(courses_done)}")
    print()


if __name__ == "__main__":
    main()
