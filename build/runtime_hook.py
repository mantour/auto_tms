"""PyInstaller runtime hook: set Playwright browser path for frozen app."""

import os
import sys

if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle
    # onedir: browsers are in <exe_dir>/ms-playwright/
    # onefile: browsers are in _MEIPASS/ms-playwright/
    bundle_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    browsers_path = os.path.join(bundle_dir, 'ms-playwright')
    if os.path.isdir(browsers_path):
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = browsers_path
