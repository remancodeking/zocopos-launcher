"""
ZOCO POS Launcher - Updater Module
====================================
Handles:
  1. First-time installation (download + setup)
  2. Auto-update detection and installation
  3. Launching the main ZocoPOS.exe
  4. Database backup before updates
  5. Desktop shortcut creation
  6. Background update monitoring

MODES:
  - LOCAL MODE (for testing): Copies from a local dist folder
  - GITHUB MODE (production): Downloads from GitHub Releases (PUBLIC repo)
"""

import os
import sys
import json
import hashlib
import shutil
import subprocess
import time
import threading
import requests


# ======================== CONFIGURATION ========================

# Public Repository where Releases (ZocoPOS.exe and version.json) will be hosted
GITHUB_REPO = "remancodeking/zocopos-launcher"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# ---- TESTING MODE ----
# Set to True for local testing (copies from LOCAL_SOURCE_DIR instead of GitHub)
# Set to False for production (downloads from GitHub Releases)
LOCAL_MODE = False
LOCAL_SOURCE_DIR = r"D:\business\zocopos\zocopos-desktop\dist"
LOCAL_TEST_VERSION = "1.0.0"

# ---- Standard Windows Paths ----
APP_DATA_DIR = os.path.join(
    os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
    'ZocoPOS'
)

# When LOCAL_MODE, install to AppData for easy testing (no admin needed)
# When production, install to Program Files
if LOCAL_MODE:
    INSTALL_DIR = os.path.join(APP_DATA_DIR, "install")
else:
    INSTALL_DIR = os.path.join(
        os.environ.get('PROGRAMFILES', 'C:\\Program Files'),
        'ZocoPOS'
    )

# Sub-paths
APP_DIR = os.path.join(INSTALL_DIR, "app")
APP_EXE = os.path.join(APP_DIR, "ZocoPOS.exe")
LOCAL_VERSION_FILE = os.path.join(APP_DIR, "version.json")
BACKUP_DIR = os.path.join(APP_DIR, "backup")
UPDATE_DIR = os.path.join(INSTALL_DIR, "update")
DB_PATH = os.path.join(APP_DATA_DIR, "zocopos_local.db")

# Background check interval (seconds)
BG_CHECK_INTERVAL = 30 * 60  # 30 minutes


# ======================== HELPERS ========================

def ensure_dirs():
    """Create all required directories."""
    for d in [APP_DATA_DIR, APP_DIR, BACKUP_DIR, UPDATE_DIR]:
        os.makedirs(d, exist_ok=True)


def get_local_version():
    """Read the installed version from version.json."""
    try:
        if os.path.exists(LOCAL_VERSION_FILE):
            with open(LOCAL_VERSION_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get("version", "0.0.0")
    except Exception as e:
        print(f"Error reading local version: {e}")
    return "0.0.0"


def is_app_installed():
    """Check if ZocoPOS.exe exists."""
    return os.path.exists(APP_EXE)


def calculate_sha256(filepath):
    """Calculate SHA256 hash of a file."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest().upper()


def create_desktop_shortcut():
    """Create a desktop shortcut for ZOCO POS."""
    try:
        # In production (frozen): shortcut → Launcher EXE (handles updates)
        # In dev/testing: shortcut → installed ZocoPOS.exe (direct launch)
        if getattr(sys, 'frozen', False):
            target_exe = os.path.abspath(sys.executable)
            working_dir = os.path.dirname(target_exe)
        else:
            target_exe = APP_EXE
            working_dir = APP_DIR

        if not os.path.exists(target_exe):
            print(f"[Launcher] Cannot create shortcut: {target_exe} not found")
            return False

        desktop = os.path.join(os.environ.get('USERPROFILE', ''), 'Desktop')
        shortcut_path = os.path.join(desktop, 'ZOCO POS.lnk')
        
        # Use the Launcher's own icon for the shortcut (reliable)
        icon_path = target_exe

        # Write a temp .ps1 script to avoid escaping nightmares
        ps1_path = os.path.join(UPDATE_DIR, '_create_shortcut.ps1')
        os.makedirs(UPDATE_DIR, exist_ok=True)

        with open(ps1_path, 'w', encoding='utf-8') as f:
            lines = []
            lines.append('$ws = New-Object -ComObject WScript.Shell')
            lines.append('$s = $ws.CreateShortcut("' + shortcut_path + '")')
            lines.append('$s.TargetPath = "' + target_exe + '"')
            lines.append('$s.WorkingDirectory = "' + working_dir + '"')
            lines.append('$s.IconLocation = "' + icon_path + ', 0"')
            lines.append('$s.Description = "ZOCO POS - Point of Sale System"')
            lines.append('$s.Save()')
            f.write(os.linesep.join(lines))

        # Use full path to PowerShell (subprocess may not find it by name alone)
        _ps_exe = os.path.join(
            os.environ.get('SYSTEMROOT', r'C:\WINDOWS'),
            'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'
        )
        if not os.path.exists(_ps_exe):
            _ps_exe = 'powershell'

        result = subprocess.run(
            [_ps_exe, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ps1_path],
            capture_output=True, text=True, timeout=10
        )

        # Clean up temp script
        try:
            os.remove(ps1_path)
        except Exception:
            pass

        if result.returncode == 0:
            print(f"[Launcher] Desktop shortcut created: {shortcut_path}")
            return True
        else:
            print(f"[Launcher] Shortcut error: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"[Launcher] Shortcut creation failed: {e}")
        return False


def is_process_running(exe_name):
    """Check if a process with the given name is running."""
    try:
        result = subprocess.run(
            ['tasklist', '/FI', f'IMAGENAME eq {exe_name}', '/NH'],
            capture_output=True, text=True, timeout=5
        )
        return exe_name.lower() in result.stdout.lower()
    except Exception:
        return False


# ======================== UPDATER CLASS ========================

class Updater:
    """
    PyWebView JS API for the launcher UI.
    All public methods are callable from JavaScript.
    """

    def __init__(self):
        self.window = None
        self.remote_info = None
        self._retry_action = None
        self._bg_running = False  # Background update monitor flag

    def set_window(self, window):
        self.window = window

    # ---- JS Bridge: UI Control ----

    def _js(self, code):
        """Execute JavaScript in the UI."""
        if self.window:
            try:
                self.window.evaluate_js(code)
            except Exception:
                pass

    def _set_status(self, text, sub=""):
        safe_text = text.replace('"', '\\"').replace("'", "\\'")
        safe_sub = sub.replace('"', '\\"').replace("'", "\\'")
        self._js(f'setStatus("{safe_text}", "{safe_sub}")')

    def _set_version(self, ver):
        self._js(f'setVersion("{ver}")')

    def _set_progress(self, pct):
        self._js(f'setProgress({pct})')

    def _set_progress_indeterminate(self):
        self._js('setProgressIndeterminate()')

    def _set_progress_label(self, left, right=""):
        self._js(f'setProgressLabel("{left}", "{right}")')

    def _show_install_btn(self):
        self._js('showInstallButton()')

    def _hide_install_btn(self):
        self._js('hideInstallButton()')

    def _show_retry_btn(self):
        self._js('showRetryButton()')

    def _hide_retry_btn(self):
        self._js('hideRetryButton()')

    def _show_error(self, msg):
        safe = msg.replace('"', '\\"').replace("'", "\\'")
        self._js(f'setError("{safe}")')

    # ---- JS Bridge: Actions ----

    def on_ui_ready(self):
        """Called when the HTML UI is loaded and pywebview is ready."""
        threading.Thread(target=self._startup_flow, daemon=True).start()

    def do_install(self):
        """Called when user clicks the Install button."""
        threading.Thread(target=self._run_install, daemon=True).start()

    def do_retry(self):
        """Called when user clicks Retry."""
        if self._retry_action:
            threading.Thread(target=self._retry_action, daemon=True).start()

    # ---- Core Logic ----

    def _startup_flow(self):
        """Main startup logic - decides what to do."""
        time.sleep(0.5)
        ensure_dirs()

        mode_label = "LOCAL TEST" if LOCAL_MODE else "ONLINE"
        print(f"[Launcher] Mode: {mode_label}")
        print(f"[Launcher] Install Dir: {INSTALL_DIR}")
        print(f"[Launcher] Data Dir: {APP_DATA_DIR}")

        local_ver = get_local_version()
        self._set_version(local_ver)
        installed = is_app_installed()

        if not installed:
            self._first_time_flow()
        else:
            self._update_flow(local_ver)

    def _first_time_flow(self):
        """First time installation: show install screen."""
        self._set_status("Welcome to ZOCO POS", "First time setup required")
        self._set_progress(0)
        self._set_progress_label("")

        self._set_status("Checking source...")
        self.remote_info = self._fetch_release_info()

        if not self.remote_info:
            if LOCAL_MODE:
                self._show_error("Source not found")
                self._js(f'document.getElementById("sub-status").innerText = "EXE not found in {LOCAL_SOURCE_DIR.replace(chr(92), "/")}"')
            else:
                self._show_error("Server unavailable")
                self._js('document.getElementById("sub-status").innerText = "Check internet connection or GitHub"')
            self._retry_action = self._first_time_flow
            self._show_retry_btn()
            return

        remote_ver = self.remote_info.get("version", "?")
        size_mb = self.remote_info.get("size_mb", "?")
        source = "Local" if LOCAL_MODE else "GitHub"

        self._set_status(
            f"Ready to install v{remote_ver}",
            f"Source: {source} | Size: {size_mb} MB"
        )
        self._set_progress(0)
        self._set_progress_label("")
        self._show_install_btn()

    def _run_install(self):
        """Download/copy and install the app (first time)."""
        self._hide_install_btn()
        self._hide_retry_btn()

        success = self._download_and_install(self.remote_info, is_first_time=True)

        if success:
            # Create desktop shortcut
            self._set_status("Creating shortcut...", "Adding to desktop")
            create_desktop_shortcut()
            time.sleep(0.5)

            self._set_status("Installation complete!", "Launching app...")
            self._set_progress(100)
            time.sleep(1.5)
            self._launch_app_and_go_background()
        else:
            self._show_error("Installation failed")
            self._js('document.getElementById("sub-status").innerText = "Check source and try again"')
            self._retry_action = self._run_install
            self._show_retry_btn()

    def _update_flow(self, local_ver):
        """Check for updates and auto-apply if available."""
        self._set_status("Checking for updates...", f"Current version: {local_ver}")
        self._set_progress_indeterminate()

        remote = self._fetch_release_info()

        if remote:
            remote_ver = remote.get("version", "0.0.0")

            if remote_ver != local_ver:
                self._set_status(
                    f"Updating to v{remote_ver}...",
                    f"From v{local_ver}"
                )

                success = self._download_and_install(remote, is_first_time=False)

                if success:
                    self._set_status(f"Updated to v{remote_ver}")
                    self._set_version(remote_ver)
                    self._set_progress(100)
                    time.sleep(1)
                    self._launch_app_and_go_background()
                    return
                else:
                    self._set_status("Update failed, using current version")
                    time.sleep(1.5)
                    self._launch_app_and_go_background()
                    return
            else:
                self._set_status("App is up to date", f"v{local_ver}")
        else:
            self._set_status("Offline mode", f"Using v{local_ver}")

        self._set_progress(100)
        self._set_progress_label("")
        time.sleep(1)
        self._launch_app_and_go_background()

    # ---- Download & Install ----

    def _download_and_install(self, remote_info, is_first_time=False):
        """Download/copy the EXE and install it."""
        try:
            new_ver = remote_info.get("version", "unknown")

            # Step 1: Backup
            if not is_first_time and is_app_installed():
                self._set_status("Backing up current version...")
                self._backup_current()
                self._backup_database()

            if LOCAL_MODE:
                return self._install_from_local(remote_info, is_first_time)
            else:
                return self._install_from_github(remote_info, is_first_time)

        except Exception as e:
            print(f"Install error: {e}")
            if not os.path.exists(APP_EXE):
                self._restore_backup()
            return False

    def _install_from_local(self, remote_info, is_first_time):
        """Copy EXE from local dist folder (testing mode)."""
        new_ver = remote_info.get("version", "unknown")
        source_exe = os.path.join(LOCAL_SOURCE_DIR, "ZocoPOS.exe")

        if not os.path.exists(source_exe):
            print(f"Local source not found: {source_exe}")
            return False

        action = "Installing" if is_first_time else "Updating"
        self._set_status(f"{action} v{new_ver}...", "Copying from local build...")

        # Simulate progress for visual feedback
        file_size = os.path.getsize(source_exe)
        mb_total = file_size / (1024 * 1024)

        self._set_progress(10)
        self._set_progress_label(f"0.0 / {mb_total:.1f} MB", "10%")
        time.sleep(0.3)

        # Remove old exe
        if os.path.exists(APP_EXE):
            try:
                os.remove(APP_EXE)
            except Exception:
                time.sleep(1)
                os.remove(APP_EXE)

        self._set_progress(30)
        self._set_progress_label(f"{mb_total*0.3:.1f} / {mb_total:.1f} MB", "30%")
        time.sleep(0.2)

        # Copy
        shutil.copy2(source_exe, APP_EXE)

        self._set_progress(80)
        self._set_progress_label(f"{mb_total*0.8:.1f} / {mb_total:.1f} MB", "80%")
        time.sleep(0.2)

        # Verify
        sha = calculate_sha256(APP_EXE)
        self._set_status("Verifying integrity...", "Checking SHA256")
        self._set_progress(90)
        time.sleep(0.3)

        # Save version info
        version_data = {
            "version": new_ver,
            "sha256": sha,
            "source": "local",
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        with open(LOCAL_VERSION_FILE, 'w', encoding='utf-8') as f:
            json.dump(version_data, f, indent=2)

        self._set_progress(100)
        self._set_progress_label("Complete", "100%")
        return True

    def _install_from_github(self, remote_info, is_first_time):
        """Download EXE from GitHub release (PUBLIC repo - no token)."""
        download_url = remote_info.get("download_url")
        expected_sha = remote_info.get("sha256")
        new_ver = remote_info.get("version", "unknown")

        if not download_url:
            return False

        temp_path = os.path.join(UPDATE_DIR, "ZocoPOS_new.exe")
        action = "Installing" if is_first_time else "Updating"

        self._set_status(f"{action} v{new_ver}...", "Downloading...")

        # For PUBLIC repos: use standard download URL (browser_download_url)
        # Authentication headers NOT needed for public releases
        headers = {
            "User-Agent": "ZocoPOS-Launcher/1.0",
            "Accept": "application/octet-stream"
        }
        
        response = requests.get(download_url, headers=headers, stream=True, timeout=120)
        response.raise_for_status()

        total_size = int(response.headers.get('Content-Length', 0))
        downloaded = 0

        with open(temp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

                    if total_size > 0:
                        pct = int((downloaded / total_size) * 100)
                        self._set_progress(pct)
                        mb_done = downloaded / (1024 * 1024)
                        mb_total = total_size / (1024 * 1024)
                        self._set_progress_label(
                            f"{mb_done:.1f} / {mb_total:.1f} MB",
                            f"{pct}%"
                        )

        # Verify SHA256
        if expected_sha:
            self._set_status("Verifying integrity...", "Checking SHA256 hash")
            actual_sha = calculate_sha256(temp_path)
            if actual_sha != expected_sha.upper():
                self._show_error("Integrity check failed!")
                self._js('document.getElementById("sub-status").innerText = "Downloaded file is corrupted"')
                os.remove(temp_path)
                return False
            self._set_progress_label("Integrity verified", "")

        # Install
        self._set_status("Installing...", "Almost done")
        self._set_progress(95)

        if os.path.exists(APP_EXE):
            try:
                os.remove(APP_EXE)
            except PermissionError:
                time.sleep(2)
                try:
                    os.remove(APP_EXE)
                except Exception:
                    os.rename(APP_EXE, APP_EXE + ".old")

        shutil.move(temp_path, APP_EXE)

        # Save version info
        version_data = {
            "version": new_ver,
            "sha256": expected_sha or calculate_sha256(APP_EXE),
            "source": "github",
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        with open(LOCAL_VERSION_FILE, 'w', encoding='utf-8') as f:
            json.dump(version_data, f, indent=2)

        self._set_progress(100)
        return True

    # ---- Fetch Release Info ----

    def _fetch_release_info(self):
        """Get available release info (local or GitHub)."""
        if LOCAL_MODE:
            return self._fetch_local_release()
        else:
            return self._fetch_github_release()

    def _fetch_local_release(self):
        """Check local dist folder for a built EXE."""
        source_exe = os.path.join(LOCAL_SOURCE_DIR, "ZocoPOS.exe")

        if not os.path.exists(source_exe):
            print(f"[Local Mode] EXE not found: {source_exe}")
            return None

        file_size = os.path.getsize(source_exe)

        # Check version.json in dist if exists
        version_file = os.path.join(LOCAL_SOURCE_DIR, "version.json")
        version = LOCAL_TEST_VERSION
        sha256 = None

        if os.path.exists(version_file):
            try:
                with open(version_file, 'r', encoding='utf-8') as f:
                    vdata = json.load(f)
                version = vdata.get("version", LOCAL_TEST_VERSION)
                sha256 = vdata.get("sha256")
            except Exception:
                pass

        return {
            "version": version,
            "download_url": source_exe,
            "sha256": sha256,
            "size_mb": f"{file_size / (1024*1024):.1f}",
            "release_notes": "Local build"
        }

    def _fetch_github_release(self):
        """Fetch latest release info from GitHub (PUBLIC repo - supports Pre-releases)."""
        try:
            # No Auth Headers needed for Public Repos
            headers = {
                "User-Agent": "ZocoPOS-Launcher/1.0",
                "Accept": "application/vnd.github.v3+json"
            }

            # Use /releases to get list (including pre-releases), take the first one
            api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=1"
            resp = requests.get(api_url, headers=headers, timeout=10)

            if resp.status_code == 404:
                print("[GitHub] Repo not found")
                return None

            resp.raise_for_status()
            releases = resp.json()

            if not releases:
                print("[GitHub] No releases found")
                return None

            release = releases[0]  # Get the most recent one

            tag = release.get("tag_name", "v0.0.0").lstrip("v")

            exe_url = None
            version_json_url = None
            exe_size = 0

            for asset in release.get("assets", []):
                name = asset.get("name", "").lower()
                if name == "zocopos.exe":
                    # For PUBLIC repos, we can use browser_download_url directly
                    exe_url = asset.get("browser_download_url")
                    exe_size = asset.get("size", 0)
                elif name == "version.json":
                    version_json_url = asset.get("browser_download_url")

            sha256 = None
            if version_json_url:
                try:
                    # Download version.json to get SHA256
                    vr = requests.get(version_json_url, headers=headers, timeout=10)
                    vdata = vr.json()
                    sha256 = vdata.get("sha256")
                except Exception:
                    pass

            return {
                "version": tag,
                "download_url": exe_url,
                "sha256": sha256,
                "size_mb": f"{exe_size / (1024*1024):.1f}" if exe_size else "~22",
                "release_notes": release.get("body", "")
            }

        except requests.exceptions.ConnectionError:
            print("[GitHub] No internet connection")
            return None
        except Exception as e:
            print(f"[GitHub] Error: {e}")
            return None

    # ---- Backup & Restore ----

    def _backup_current(self):
        """Backup the current ZocoPOS.exe."""
        if os.path.exists(APP_EXE):
            try:
                backup_name = f"ZocoPOS_backup_{int(time.time())}.exe"
                shutil.copy2(APP_EXE, os.path.join(BACKUP_DIR, backup_name))
                backups = sorted([
                    f for f in os.listdir(BACKUP_DIR)
                    if f.startswith("ZocoPOS_backup_") and f.endswith(".exe")
                ])
                while len(backups) > 3:
                    os.remove(os.path.join(BACKUP_DIR, backups.pop(0)))
            except Exception as e:
                print(f"Backup warning: {e}")

    def _backup_database(self):
        """Safety backup of the SQLite database."""
        if os.path.exists(DB_PATH):
            try:
                backup_db = os.path.join(
                    APP_DATA_DIR,
                    f"zocopos_local_backup_{int(time.time())}.db"
                )
                shutil.copy2(DB_PATH, backup_db)
                db_backups = sorted([
                    f for f in os.listdir(APP_DATA_DIR)
                    if f.startswith("zocopos_local_backup_") and f.endswith(".db")
                ])
                while len(db_backups) > 5:
                    os.remove(os.path.join(APP_DATA_DIR, db_backups.pop(0)))
            except Exception as e:
                print(f"DB backup warning: {e}")

    def _restore_backup(self):
        """Restore the latest backup if update failed."""
        try:
            backups = sorted([
                f for f in os.listdir(BACKUP_DIR)
                if f.startswith("ZocoPOS_backup_") and f.endswith(".exe")
            ], reverse=True)
            if backups:
                shutil.copy2(os.path.join(BACKUP_DIR, backups[0]), APP_EXE)
                print("Restored previous version from backup")
        except Exception as e:
            print(f"Restore failed: {e}")

    # ---- Launch App ----

    def _launch_app_and_go_background(self):
        """Launch the main app, hide the launcher window, and run background update checks."""
        try:
            if os.path.exists(APP_EXE):
                print(f"[Launcher] Starting: {APP_EXE}")
                subprocess.Popen(
                    [APP_EXE],
                    cwd=APP_DIR,
                    creationflags=subprocess.DETACHED_PROCESS
                )
                time.sleep(1)

                # Hide the launcher window (don't destroy — stay in background)
                if self.window:
                    self.window.hide()
                    print("[Launcher] Window hidden, entering background mode")

                # Start background update monitor
                self._bg_running = True
                self._background_update_loop()
            else:
                self._show_error("ZocoPOS.exe not found!")
                self._retry_action = self._startup_flow
                self._show_retry_btn()
        except Exception as e:
            self._show_error(f"Launch failed: {str(e)[:40]}")
            self._retry_action = self._launch_app_and_go_background
            self._show_retry_btn()

    def _background_update_loop(self):
        """Run in background: periodically check for updates.
        When an update is found, wait for ZocoPOS.exe to close, then apply it silently."""
        print(f"[Background] Update monitor started (interval: {BG_CHECK_INTERVAL}s)")

        while self._bg_running:
            time.sleep(BG_CHECK_INTERVAL)

            if not self._bg_running:
                break

            print("[Background] Checking for updates...")
            try:
                local_ver = get_local_version()
                remote = self._fetch_release_info()

                if not remote:
                    print("[Background] No release info (offline or error)")
                    continue

                remote_ver = remote.get("version", "0.0.0")

                if remote_ver == local_ver:
                    print(f"[Background] Up to date: v{local_ver}")
                    continue

                print(f"[Background] Update available: v{local_ver} -> v{remote_ver}")

                # Wait for ZocoPOS.exe to close before updating
                print("[Background] Waiting for ZocoPOS.exe to close...")
                wait_count = 0
                while is_process_running("ZocoPOS.exe"):
                    time.sleep(10)
                    wait_count += 1
                    if wait_count > 360:  # Max 1 hour wait
                        print("[Background] Waited too long, skipping update")
                        break

                if is_process_running("ZocoPOS.exe"):
                    continue

                # App is closed — apply update silently
                print(f"[Background] Applying update v{remote_ver}...")
                self._backup_current()
                self._backup_database()

                if LOCAL_MODE:
                    success = self._install_from_local(remote, is_first_time=False)
                else:
                    success = self._install_from_github(remote, is_first_time=False)

                if success:
                    print(f"[Background] Update to v{remote_ver} complete!")
                else:
                    print("[Background] Update failed")

            except Exception as e:
                print(f"[Background] Error: {e}")

        print("[Background] Update monitor stopped")

    def close_launcher(self):
        """Called when user clicks the X button — fully exits."""
        self._bg_running = False
        if self.window:
            self.window.destroy()
