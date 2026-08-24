# gui_studio/entry.py - Application bootstrap for the Studio GUI.
# Replaces the old gui_main.py __main__ block; the runtime-environment and
# self-spawn (--grader / AUTOGRADER_GRADER=1) behaviors are preserved so
# frozen builds and GraderThread keep working unchanged.
import atexit
import ctypes
import os
import shutil
import sys

APP_ID = "regis.google_form_autograder"
APP_DATA_DIR_NAME = "GoogleFormAutograder"
RUNTIME_DEFAULT_FILES = (
    "config.json",
    "forms_to_grade.json",
    "predefined_folders.json",
    "client_secrets.json",
)
RUNTIME_DIRS = (
    "logs",
    "cache",
    os.path.join("cache", "results"),
    os.path.join("cache", "embeddings"),
    os.path.join("cache", "form_context"),
    os.path.join("cache", "vision"),
    "backups",
    os.path.join("backups", "answer_keys"),
)


def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    return os.path.join(base, *parts)


def _user_data_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DATA_DIR_NAME)


def ensure_runtime_environment():
    """Prepare writable runtime files for packaged builds."""
    if getattr(sys, "frozen", False):
        target_dir = _user_data_dir()
        os.makedirs(target_dir, exist_ok=True)
        for filename in RUNTIME_DEFAULT_FILES:
            target = os.path.join(target_dir, filename)
            source = resource_path(filename)
            if not os.path.exists(target) and os.path.exists(source):
                shutil.copy2(source, target)
        os.chdir(target_dir)
    for directory in RUNTIME_DIRS:
        os.makedirs(directory, exist_ok=True)


def _install_sleep_prevention():
    if os.name != "nt":
        return
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    def prevent_sleep():
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)

    def restore_sleep():
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

    prevent_sleep()
    atexit.register(restore_sleep)
    print("Sleep prevention active. App is running.")


def main():
    # Crash diagnostics must be live before Qt/PySide6 loads so native faults
    # inside Qt are captured (minidump + watchdog report + faulthandler dump).
    try:
        import crash_diagnostics

        crash_diagnostics.install(
            app_name="GoogleFormAutograder",
            context="grader-frozen" if os.environ.get("AUTOGRADER_GRADER") == "1" else "gui",
        )
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass

    ensure_runtime_environment()

    # Frozen grader children re-execute the packaged entry with --grader.
    if os.environ.get("AUTOGRADER_GRADER") == "1" or "--grader" in sys.argv:
        sys.argv = [arg for arg in sys.argv if arg != "--grader"]
        from main import main as run_grader_main

        sys.exit(run_grader_main() or 0)

    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication

    from gui_studio import theme as T
    from gui_studio.main_window import AutograderWindow, app_icon

    _install_sleep_prevention()

    app = QApplication(sys.argv)
    app.setApplicationName("Google Form Autograder")
    app.setOrganizationName("Regis")
    app.setWindowIcon(app_icon())
    app.setStyleSheet(T.load_stylesheet())
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#eef1f6"))
    palette.setColor(QPalette.WindowText, QColor("#1c2430"))
    app.setPalette(palette)

    window = AutograderWindow()
    app.aboutToQuit.connect(window._shutdown_owned_work)
    window.show()
    sys.exit(app.exec())
