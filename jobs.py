"""In-process async job registry for long-running operations.

Agent Forge runs as a single threaded Flask process, so jobs are tracked in a
module-level dict guarded by a lock and executed in daemon worker threads. Each
job exposes status / progress / a rolling log buffer / result / error, which the
frontend polls via GET /api/jobs/<id>. This replaces the old pattern of blocking
the request thread for up to 5 minutes while Claude ran.

Not durable across restarts (by design) — a job is a transient view of work that
also persists its real output to the database / filesystem as it goes.
"""
import threading
import traceback
import uuid
from datetime import datetime

_jobs = {}
_lock = threading.Lock()
_MAX_JOBS = 200  # cap memory; evict oldest finished jobs beyond this


def _now():
    return datetime.now().isoformat()


def _evict_locked():
    if len(_jobs) <= _MAX_JOBS:
        return
    finished = sorted(
        (j for j in _jobs.values() if j["status"] in ("done", "error")),
        key=lambda j: j["updated_at"],
    )
    for j in finished[: len(_jobs) - _MAX_JOBS]:
        _jobs.pop(j["id"], None)


def create_job(owner=None, kind=None):
    """Create a job in the 'running' state and return its id."""
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "owner": owner,
            "kind": kind,
            "status": "running",
            "progress": 0,
            "logs": [],
            "result": None,
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        _evict_locked()
    return job_id


def log(job_id, message, level="INFO"):
    """Append a line to a job's rolling log buffer (last 100 lines kept)."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job["logs"].append({
            "ts": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": str(message),
        })
        job["logs"] = job["logs"][-100:]
        job["updated_at"] = _now()


def set_progress(job_id, progress, message=None):
    """Update a job's progress percentage (0-100), optionally logging a message."""
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["progress"] = max(0, min(100, int(progress)))
            job["updated_at"] = _now()
    if message:
        log(job_id, message)


def get_job(job_id):
    """Return a shallow copy of the job dict, or None."""
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def run(job_id, fn):
    """Run fn(job_id) in a daemon thread, capturing its return value as the
    job result and any exception as the job error."""
    def _worker():
        try:
            result = fn(job_id)
            with _lock:
                job = _jobs.get(job_id)
                if job is not None:
                    job["result"] = result
                    job["status"] = "done"
                    job["progress"] = 100
                    job["updated_at"] = _now()
        except Exception as exc:  # noqa: BLE001 — capture everything for the client
            traceback.print_exc()
            with _lock:
                job = _jobs.get(job_id)
                if job is not None:
                    job["error"] = str(exc) or exc.__class__.__name__
                    job["status"] = "error"
                    job["updated_at"] = _now()

    threading.Thread(target=_worker, daemon=True).start()
