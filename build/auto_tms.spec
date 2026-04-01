# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for auto_tms desktop app."""

import os
import sys
from pathlib import Path

block_cipher = None

# Find Playwright's Chromium browser path
def get_playwright_browser_path():
    """Locate the Playwright Chromium installation."""
    import playwright
    pw_dir = Path(playwright.__file__).parent
    driver_dir = pw_dir / "driver"
    if driver_dir.exists():
        return str(driver_dir)
    # Fallback: check PLAYWRIGHT_BROWSERS_PATH
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers_path and Path(browsers_path).exists():
        return browsers_path
    # Default location
    home = Path.home()
    default = home / ".cache" / "ms-playwright"
    if default.exists():
        return str(default)
    return None

project_root = os.path.abspath(os.path.join(SPECPATH, '..'))

# Collect data files
datas = [
    (os.path.join(project_root, 'gui', 'frontend'), os.path.join('gui', 'frontend')),
]

# Add Playwright driver
pw_path = get_playwright_browser_path()
if pw_path:
    datas.append((pw_path, os.path.join('playwright', 'driver')))

# Add Playwright browsers (Chromium)
import subprocess
result = subprocess.run(
    [sys.executable, '-c', 'import playwright; print(playwright.__file__)'],
    capture_output=True, text=True,
)
if result.returncode == 0:
    pw_init = Path(result.stdout.strip())
    browsers_json = pw_init.parent / "driver" / "package" / "browsers.json"
    if browsers_json.exists():
        datas.append((str(browsers_json.parent), os.path.join('playwright', 'driver', 'package')))

a = Analysis(
    [os.path.join(project_root, 'gui', 'app.py')],
    pathex=[project_root],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'auto_tms',
        'auto_tms.cli',
        'auto_tms.config',
        'auto_tms.llm',
        'auto_tms.auth',
        'auto_tms.auth.browser',
        'auto_tms.auth.captcha',
        'auto_tms.auth.login',
        'auto_tms.auth.session',
        'auto_tms.engine',
        'auto_tms.engine.course',
        'auto_tms.engine.handlers',
        'auto_tms.engine.handlers.video',
        'auto_tms.engine.handlers.document',
        'auto_tms.engine.handlers.survey',
        'auto_tms.engine.handlers.exam',
        'auto_tms.planner',
        'auto_tms.planner.pending',
        'auto_tms.planner.scraper',
        'auto_tms.planner.shortfall',
        'auto_tms.state',
        'auto_tms.state.models',
        'auto_tms.state.store',
        'gui',
        'gui.api',
        'gui.app',
        'fastapi',
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'websockets',
        'pydantic',
        'dotenv',
        'click',
        'playwright',
        'playwright.async_api',
    ],
    hookspath=[os.path.join(project_root, 'build')],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='auto_tms',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
