# gui_main.py - Launcher for the Studio GUI (gui_studio package).
#
# The previous monolithic window (FormManager) was fully replaced by the
# gui_studio rebuild. This file remains the application entry point so the
# existing workflows keep working:
#   * `python gui_main.py` launches the new GUI.
#   * Frozen builds spawn `GoogleFormAutograder.exe --grader`, which runs the
#     grading pipeline in-process (handled inside gui_studio.entry.main).
#
# All backend modules (grader_thread, worker_pipeline, consensus_engine,
# ai_judges, provider_manager, ...) are untouched by the rebuild.
import sys

from gui_studio.entry import main

if __name__ == "__main__":
    main()
