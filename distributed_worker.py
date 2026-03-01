"""Run a remote worker that polls a RemoteMaster.

Usage:
  python distributed_worker.py --master http://HOST:7331 --token mytoken

The worker executes tasks using the same bot_runtime.run_task() used by the
local engine.
"""

from __future__ import annotations

import argparse
import traceback

import db
import config
from generated.core.distributed import RemoteWorkerClient
from bot_runtime import run_task


def execute_task(task: dict) -> dict:
    task_id = int(task["id"])
    bot_id = int(task["bot_id"])
    prompt = task.get("prompt") or ""
    task_type = task.get("task_type") or "code"
    try:
        bot = db.get_bot(bot_id=bot_id)
        if not bot:
            return {"task_id": task_id, "status": "error", "error": "bot_not_found"}
        run_task(task_id=task_id, bot_id=bot_id, prompt=prompt, task_type=task_type)
        return {"task_id": task_id, "status": "ok"}
    except Exception as e:
        return {"task_id": task_id, "status": "error", "error": str(e), "trace": traceback.format_exc()[:4000]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", required=True, help="e.g. http://192.168.1.10:7331")
    ap.add_argument("--token", default="")
    ap.add_argument("--poll", type=float, default=1.0)
    args = ap.parse_args()
    db.init_schema()
    client = RemoteWorkerClient(master_url=args.master, token=args.token, poll_s=args.poll)
    print(f"RemoteWorker polling {args.master}")
    client.run_forever(execute_task)


if __name__ == "__main__":
    main()
