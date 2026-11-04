"""Tiny background-job runner. Slow AI calls run in a thread and the dashboard polls for the result,
so a slow local model can never hit a browser or tunnel timeout (Cloudflare drops requests after about 100 seconds).
"""

import logging
import threading
import time
import uuid

log = logging.getLogger("lifeline")
_JOBS, _KEYS, _LOCK = {}, {}, threading.Lock()


def start(fn, key=None):
    """Run fn() in the background and return a job id. A second request with the same key joins the running job."""
    with _LOCK:
        if key and _JOBS.get(_KEYS.get(key), {}).get("state") == "working":
            return _KEYS[key]
        jid = uuid.uuid4().hex[:8]
        _JOBS[jid] = {"state": "working", "t": time.time()}
        if key:
            _KEYS[key] = jid
        for old in sorted(_JOBS, key=lambda j: _JOBS[j]["t"])[:-100]:
            _JOBS.pop(old, None)

    def run():
        try:
            update = {"state": "done", "result": fn()}
        except Exception as e:
            log.exception("background job failed")
            update = {"state": "error", "error": f"{type(e).__name__}: {e}"}
        with _LOCK:
            if jid in _JOBS:
                _JOBS[jid].update(update)

    threading.Thread(target=run, daemon=True).start()
    return jid


def status(jid):
    with _LOCK:
        j = _JOBS.get(jid)
        return (
            dict(j)
            if j
            else {
                "state": "error",
                "error": "unknown job (the server may have restarted; try again)",
            }
        )
