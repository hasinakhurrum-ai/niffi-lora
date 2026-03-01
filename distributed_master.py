"""Run a remote master for distributed workers.

Usage:
  python distributed_master.py --host 0.0.0.0 --port 7331 --token mytoken

Workers can poll this master and execute tasks.

This master simply exposes queued tasks from the DB and marks them running.
"""

from __future__ import annotations

import argparse

import db
import config
from generated.core.distributed import RemoteMaster
from generated.core import event_sourcing


def claim_task() -> dict | None:
    task = db.get_next_task()
    if not task:
        return None
    db.mark_task_running(task["id"])
    db.set_bot_status(task["bot_id"], "running")
    try:
        event_sourcing.append_event(tick=0, scope="core", type_="REMOTE_CLAIM", payload={"task_id": task["id"], "bot_id": task["bot_id"]})
    except Exception:
        pass
    return task


def report_result(payload: dict) -> None:
    task_id = int(payload.get("task_id"))
    status = payload.get("status") or "ok"
    if status == "ok":
        db.mark_task_done(task_id)
    else:
        db.mark_task_error(task_id, payload.get("error") or "remote_error")
    try:
        event_sourcing.append_event(tick=0, scope="core", type_="REMOTE_REPORT", payload=payload)
    except Exception:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=7331)
    ap.add_argument("--token", default="")
    args = ap.parse_args()

    db.init_schema()
    event_sourcing.init()

    master = RemoteMaster(host=args.host, port=args.port, token=args.token)
    master.start(claim_task, report_result)
    print(f"RemoteMaster listening on http://{args.host}:{args.port}  token={'set' if args.token else 'none'}")
    try:
        import time

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        master.stop()


if __name__ == "__main__":
    main()
