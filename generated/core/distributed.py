"""Distributed execution.

Default mode: local threads/processes via WorkerPool.

Optional mode: remote workers via a thin stdlib HTTP RPC.

Remote protocol:
  - Master exposes:
      GET  /claim?token=...  -> JSON {"task": {...}} or {"task": null}
      POST /report           -> JSON {"task_id":..., "status":"ok"|"error", "error":..., "meta":...}
  - Workers poll /claim and execute tasks locally.

This is intentionally simple (polling). It enables distributed execution
without adding heavy dependencies.
"""

from __future__ import annotations

import concurrent.futures as cf
import os
import socket
import multiprocessing as mp
from dataclasses import dataclass
from typing import Callable, Any

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs


@dataclass
class WorkerConfig:
    mode: str = "thread"  # thread|process
    max_workers: int = 2


class WorkerPool:
    def __init__(self, cfg: WorkerConfig) -> None:
        self.cfg = cfg
        self._executor: cf.Executor | None = None

    def __enter__(self) -> "WorkerPool":
        if self.cfg.mode == "process":
            ctx = mp.get_context("spawn")
            self._executor = cf.ProcessPoolExecutor(max_workers=self.cfg.max_workers, mp_context=ctx)
        else:
            self._executor = cf.ThreadPoolExecutor(max_workers=self.cfg.max_workers)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._executor:
                self._executor.shutdown(wait=True, cancel_futures=False)
        finally:
            self._executor = None

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> cf.Future:
        if not self._executor:
            raise RuntimeError("WorkerPool not started")
        return self._executor.submit(fn, *args, **kwargs)


class RemoteMaster:
    """Minimal HTTP master for distributed workers."""

    def __init__(self, *, host: str = "0.0.0.0", port: int = 7331, token: str = "") -> None:
        self.host = host
        self.port = int(port)
        self.token = token or ""
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self, claim_fn: Callable[[], dict | None], report_fn: Callable[[dict], None]) -> None:
        token = self.token

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, payload: dict) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urlparse(self.path)
                if u.path != "/claim":
                    self._send(404, {"error": "not_found"})
                    return
                qs = parse_qs(u.query)
                if token and (qs.get("token", [""])[0] != token):
                    self._send(403, {"error": "forbidden"})
                    return
                task = claim_fn()
                self._send(200, {"task": task})

            def do_POST(self):
                u = urlparse(self.path)
                if u.path != "/report":
                    self._send(404, {"error": "not_found"})
                    return
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._send(400, {"error": "bad_json"})
                    return
                try:
                    report_fn(payload)
                except Exception as e:
                    self._send(500, {"error": str(e)})
                    return
                self._send(200, {"ok": True})

            def log_message(self, format: str, *args) -> None:
                return

        self._server = HTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
        self._server = None


class RemoteWorkerClient:
    """Poll a RemoteMaster and execute tasks locally."""

    def __init__(self, *, master_url: str, token: str = "", poll_s: float = 1.0) -> None:
        self.master_url = master_url.rstrip("/")
        self.token = token
        self.poll_s = float(poll_s)

    def run_forever(self, execute_fn: Callable[[dict], dict]) -> None:
        import time
        import urllib.request

        while True:
            try:
                url = f"{self.master_url}/claim"
                if self.token:
                    url += f"?token={self.token}"
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                task = data.get("task")
                if not task:
                    time.sleep(self.poll_s)
                    continue
                result = execute_fn(task)
                req = urllib.request.Request(
                    f"{self.master_url}/report",
                    data=json.dumps(result).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30):
                    pass
            except KeyboardInterrupt:
                return
            except Exception:
                time.sleep(max(1.0, self.poll_s))
