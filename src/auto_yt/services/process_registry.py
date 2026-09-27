"""Process registry for tracking and force-terminating job subprocesses."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_JOB_PROCESSES: dict[str, set[subprocess.Popen[Any]]] = defaultdict(set)


def register_process(job_id: str, process: subprocess.Popen[Any]) -> None:
    """Register a running subprocess associated with a job_id or key."""
    if not job_id or process is None:
        return
    with _LOCK:
        _JOB_PROCESSES[str(job_id)].add(process)
    logger.debug("Registered process PID %s for job '%s'", getattr(process, "pid", None), job_id)


def unregister_process(job_id: str, process: subprocess.Popen[Any]) -> None:
    """Unregister a completed or failed subprocess."""
    if not job_id or process is None:
        return
    with _LOCK:
        processes = _JOB_PROCESSES.get(str(job_id))
        if processes:
            processes.discard(process)
            if not processes:
                _JOB_PROCESSES.pop(str(job_id), None)
    logger.debug("Unregistered process PID %s for job '%s'", getattr(process, "pid", None), job_id)


def kill_job_processes(job_id: str) -> int:
    """
    Forcefully terminate all subprocesses and their child process trees
    associated with the given job_id.
    """
    if not job_id:
        return 0
    with _LOCK:
        processes = list(_JOB_PROCESSES.pop(str(job_id), set()))

    killed_count = 0
    for proc in processes:
        try:
            if proc.poll() is None:
                pid = proc.pid
                logger.info("Force-killing process PID %s for job '%s'", pid, job_id)
                # On Windows, taskkill /F /T kills the entire process tree
                if os.name == "nt":
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            capture_output=True,
                            timeout=5,
                            check=False,
                        )
                    except Exception as kill_err:
                        logger.warning("taskkill failed for PID %s: %s", pid, kill_err)
                try:
                    proc.kill()
                except Exception:
                    pass
                killed_count += 1
        except Exception as exc:
            logger.warning("Error killing process %s: %s", getattr(proc, "pid", None), exc)
    return killed_count
