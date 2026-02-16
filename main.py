"""
ZOCO POS Launcher - Main Entry Point
=====================================
A standalone launcher that handles first-time installation and auto-updates
for the ZOCO POS Desktop application.

This is a SEPARATE project from zocopos-desktop.
Build: pyinstaller --noconsole --onefile --name "ZocoPOS_Launcher" --icon="assets/icon.png" --add-data "assets;assets" --add-data "ui;ui" main.py
"""

import os
import sys
import webview
from src.updater import Updater


def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and PyInstaller."""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


def main():
    updater = Updater()

    # ui_path = get_resource_path(...)
    ui_path = get_resource_path(os.path.join("ui", "launcher.html"))

    window = webview.create_window(
        title="ZOCO POS",
        url=ui_path,
        js_api=updater,
        width=480,
        height=380,
        resizable=False,
        frameless=True,
        easy_drag=True,
    )

    updater.set_window(window)

    # Set debug=False for production
    webview.start(debug=False)


if __name__ == "__main__":
    main()
