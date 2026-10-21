import logging
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
from sqlalchemy import select
from ashare_insights.db.models import Announcement, DownloadTask, File, utc_now
from ashare_insights.storage.local import save_pdf

logger = logging.getLogger(__name__)


class DownloadManager:
    def __init__(self, sessions, client, data_dir: Path):
        self.sessions = sessions
        self.client = client
        self.data_dir = data_dir
        self.queue: Queue[int | None] = Queue()
        self.lock = Lock()
        self.thread: Thread | None = None

    def start(self):
        with self.sessions.begin() as db:
            for task in db.scalars(select(DownloadTask).where(DownloadTask.status == "running")):
                task.status = "failed"
                task.error = "服务重启时下载中断，请重试"
                task.updated_at = utc_now()
            queued = [task.id for task in db.scalars(select(DownloadTask).where(DownloadTask.status == "queued").order_by(DownloadTask.id))]
        self.thread = Thread(target=self._run, name="pdf-download-worker", daemon=True)
        self.thread.start()
        for task_id in queued:
            self.queue.put(task_id)

    def stop(self):
        self.queue.put(None)
        if self.thread:
            self.thread.join(timeout=3)

    def request(self, announcement_id: int) -> dict:
        with self.lock:
            with self.sessions.begin() as db:
                announcement = db.get(Announcement, announcement_id)
                if announcement is None:
                    raise LookupError("公告不存在")
                task = db.scalar(select(DownloadTask).where(DownloadTask.announcement_id == announcement_id))
                file_exists = bool(announcement.file and Path(announcement.file.path).is_file())
                if task is None:
                    task = DownloadTask(announcement_id=announcement_id, status="completed" if file_exists else "queued", updated_at=utc_now())
                    db.add(task)
                    db.flush()
                    enqueue = not file_exists
                elif file_exists:
                    task.status = "completed"
                    task.error = None
                    enqueue = False
                elif task.status in ("queued", "running"):
                    enqueue = False
                else:
                    task.status = "queued"
                    task.error = None
                    task.updated_at = utc_now()
                    enqueue = True
                result = self._serialize(task)
            if enqueue:
                self.queue.put(result["id"])
            return result

    def get(self, task_id: int) -> dict:
        with self.sessions() as db:
            task = db.get(DownloadTask, task_id)
            if task is None:
                raise LookupError("下载任务不存在")
            return self._serialize(task)

    @staticmethod
    def _serialize(task):
        return {"id": task.id, "announcement_id": task.announcement_id, "status": task.status, "error": task.error, "attempts": task.attempts, "updated_at": task.updated_at}

    def _run(self):
        while True:
            task_id = self.queue.get()
            if task_id is None:
                self.queue.task_done()
                return
            try:
                self._process(task_id)
            except Exception:
                logger.exception("下载任务 %s 未预期失败", task_id)
                with self.sessions.begin() as db:
                    task = db.get(DownloadTask, task_id)
                    if task:
                        task.status = "failed"
                        task.error = "下载任务内部错误，请重试"
                        task.updated_at = utc_now()
            finally:
                self.queue.task_done()

    def _process(self, task_id: int):
        with self.sessions.begin() as db:
            task = db.get(DownloadTask, task_id)
            if task is None or task.status != "queued":
                return
            task.status = "running"
            task.attempts += 1
            task.updated_at = utc_now()
            url = task.announcement.pdf_url
            announcement_id = task.announcement_id
        try:
            sha, size, path = save_pdf(self.client, url, self.data_dir)
            with self.sessions.begin() as db:
                file = db.scalar(select(File).where(File.sha256 == sha))
                if file is None:
                    file = File(sha256=sha, size_bytes=size, path=path)
                    db.add(file)
                    db.flush()
                announcement = db.get(Announcement, announcement_id)
                announcement.file_id = file.id
                task = db.get(DownloadTask, task_id)
                task.status = "completed"
                task.error = None
                task.updated_at = utc_now()
        except Exception as exc:
            with self.sessions.begin() as db:
                task = db.get(DownloadTask, task_id)
                task.status = "failed"
                task.error = str(exc)[:1000]
                task.updated_at = utc_now()
