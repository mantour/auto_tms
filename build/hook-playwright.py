"""PyInstaller hook for Playwright — include driver and browser binaries."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules("playwright")
datas = collect_data_files("playwright")

# Include the Playwright driver package
try:
    import playwright
    driver_dir = Path(playwright.__file__).parent / "driver"
    if driver_dir.exists():
        for root, dirs, files in os.walk(driver_dir):
            for f in files:
                src = os.path.join(root, f)
                dst = os.path.join("playwright", "driver", os.path.relpath(root, driver_dir))
                datas.append((src, dst))
except Exception:
    pass
