# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for auto_tms desktop app."""

import os
import sys
from pathlib import Path

block_cipher = None

def find_playwright_browsers():
    """Find ms-playwright browser installations to bundle."""
    candidates = [
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
        str(Path.home() / ".cache" / "ms-playwright"),                  # Linux/Mac
        str(Path.home() / "AppData" / "Local" / "ms-playwright"),       # Windows
    ]
    for p in candidates:
        if p and Path(p).exists() and any(Path(p).iterdir()):
            return p
    return None

project_root = os.path.abspath(os.path.join(SPECPATH, '..'))

# Collect data files
datas = [
    (os.path.join(project_root, 'gui', 'frontend'), os.path.join('gui', 'frontend')),
]

# Add Playwright driver (node package)
import playwright
pw_driver = Path(playwright.__file__).parent / "driver"
if pw_driver.exists():
    datas.append((str(pw_driver), os.path.join('playwright', 'driver')))

# Add Playwright browsers (Chromium binaries from ms-playwright)
browsers_dir = find_playwright_browsers()
if browsers_dir:
    datas.append((browsers_dir, 'ms-playwright'))
    print(f"[auto_tms.spec] Bundling browsers from: {browsers_dir}")
else:
    print("[auto_tms.spec] WARNING: No Playwright browsers found! Run 'playwright install chromium' first.")

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
    runtime_hooks=[os.path.join(project_root, 'build', 'runtime_hook.py')],
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
    [],
    exclude_binaries=True,
    name='auto_tms',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='auto_tms',
)
