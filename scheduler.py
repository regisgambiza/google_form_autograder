# scheduler.py - APScheduler-based scheduler for auto mode
import json
from datetime import datetime, timezone, timedelta
from logger import log


class AutoGraderScheduler:
    """Scheduler for automatic form grading cycles"""
    
    def __init__(self):
        self.scheduler = None
        self.job = None
        self.running = False
        
    def start(self, interval_minutes, folders, recency_minutes):
        """Start the auto-grading scheduler
        
        Args:
            interval_minutes: How often to check for new submissions
            folders: List of folder identifiers to search
            recency_minutes: How far back to look for new submissions
        """
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.interval import IntervalTrigger
            
            log("INFO", f"Starting scheduler: interval={interval_minutes}min, folders={len(folders)}")
            
            # Stop any existing scheduler
            self.stop()
            
            self.scheduler = BackgroundScheduler(timezone=timezone.utc)
            self.scheduler.start()
            
            self.job = self.scheduler.add_job(
                self._run_cycle,
                trigger=IntervalTrigger(minutes=interval_minutes),
                kwargs={
                    'folders': folders,
                    'recency_minutes': recency_minutes
                },
                next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10)  # First run in 10 seconds
            )
            
            self.running = True
            log("INFO", f"Scheduler started - next cycle in 10 seconds, then every {interval_minutes} minutes")
            
        except Exception as e:
            log("ERROR", f"Failed to start scheduler: {e}")
            
    def stop(self):
        """Stop the scheduler"""
        log("INFO", "Stopping scheduler...")
        
        if self.job:
            try:
                self.job.remove()
                self.job = None
            except Exception as e:
                log("WARNING", f"Error removing job: {e}")
        
        if self.scheduler:
            try:
                self.scheduler.shutdown(wait=False)
                self.scheduler = None
            except Exception as e:
                log("WARNING", f"Error shutting down scheduler: {e}")
        
        self.running = False
        log("INFO", "Scheduler stopped")
        
    def _run_cycle(self, folders, recency_minutes):
        """Execute one auto-cycle"""
        log("INFO", f"[AUTO CYCLE] Searching folders: {folders}")
        
        try:
            from form_searcher import find_forms_with_submissions_in_range
            
            now_utc = datetime.now(timezone.utc)
            from_dt = now_utc - timedelta(minutes=recency_minutes)
            
            log("INFO", f"[AUTO CYCLE] Search range: {from_dt} → {now_utc}")
            
            forms = find_forms_with_submissions_in_range(
                folders,
                from_dt,
                now_utc,
                progress_callback=lambda msg: log("INFO", f"[AUTO CYCLE] {msg}")
            )
            
            log("INFO", f"[AUTO CYCLE] Found {len(forms)} form(s) with new submissions")
            
            if forms:
                # Add forms to forms_to_grade.json
                self._add_forms_to_queue(forms)
                
        except Exception as e:
            log("ERROR", f"[AUTO CYCLE] Error during cycle: {e}")
            
    def _add_forms_to_queue(self, forms):
        """Add found forms to the grading queue"""
        try:
            with open("forms_to_grade.json", "r") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"forms": []}
            
        current_urls = {item.get("url") for item in data.get("forms", [])}
        added = []
        
        for form in forms:
            url = form.get("url")
            if url and url not in current_urls:
                data["forms"].append({
                    "url": url,
                    "title": form.get("title", "Unknown Form")
                })
                current_urls.add(url)
                added.append(url)
                log("INFO", f"[AUTO CYCLE] Added to queue: {url}")
        
        if added:
            with open("forms_to_grade.json", "w") as f:
                json.dump(data, f, indent=2)
            log("INFO", f"[AUTO CYCLE] Added {len(added)} new form(s) to grading queue")
        else:
            log("INFO", "[AUTO CYCLE] No new forms to add")


# Global scheduler instance
scheduler = AutoGraderScheduler()
