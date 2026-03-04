import os
import json
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from config import Config


class SchedulerService:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.log_file = os.path.join(Config.DATA_DIR, "upload_log.json")

    def start(self):
        if not self.scheduler.running:
            self.scheduler.start()

    def add_job(self, job_id: str, func, hour: int, minute: int = 0):
        trigger = CronTrigger(hour=hour, minute=minute, timezone="Asia/Dhaka")
        self.scheduler.add_job(
            func, trigger=trigger, id=job_id,
            replace_existing=True, misfire_grace_time=3600
        )

    def remove_job(self, job_id: str):
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass

    def get_jobs(self) -> list:
        return [
            {"id": j.id, "next_run": str(j.next_run_time), "trigger": str(j.trigger)}
            for j in self.scheduler.get_jobs()
        ]

    def log(self, video: str, status: str, details: dict = None):
        logs = self._load_logs()
        logs.append({
            "time": datetime.now().isoformat(),
            "video": video,
            "status": status,
            "details": details or {},
        })
        with open(self.log_file, "w") as f:
            json.dump(logs[-200:], f, indent=2)

    def get_logs(self) -> list:
        return self._load_logs()

    def _load_logs(self) -> list:
        if os.path.exists(self.log_file):
            with open(self.log_file) as f:
                return json.load(f)
        return []