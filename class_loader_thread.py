# class_loader_thread.py (New file for modularization)
from PyQt5.QtCore import QThread, pyqtSignal
from auth import get_classroom_service

class ClassLoaderThread(QThread):
    courses_loaded = pyqtSignal(list)
    error = pyqtSignal(str)

    def run(self):
        try:
            classroom = get_classroom_service()
            resp = classroom.courses().list(pageSize=200).execute()
            courses = resp.get('courses', [])
            out = [(c.get('name'), c.get('id')) for c in courses if c.get('name')]
            self.courses_loaded.emit(out)
        except Exception as e:
            self.error.emit(str(e))