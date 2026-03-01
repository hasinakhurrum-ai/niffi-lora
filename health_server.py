"""
Health endpoint: background HTTP server that serves /health (JSON), /incoming/task, /incoming/proposal (POST).
Phase 6: webhook in for tasks and proposals. Start from main() when HEALTH_ENABLED.
"""

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import config
except ImportError:
    config = None


def _read_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length <= 0 or length > 65536:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _check_db() -> bool:
    try:
        import db
        db.count_queued_tasks()
        return True
    except Exception:
        return False


def _check_ollama() -> bool:
    try:
        import requests
        url = getattr(config, "OLLAMA_URL", "http://localhost:11434") if config else "http://localhost:11434"
        r = requests.get(f"{url}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _check_docker() -> bool:
    try:
        from tools import docker_available
        return docker_available()
    except Exception:
        return False


def _health_payload() -> dict:
    db_ok = _check_db()
    ollama_ok = _check_ollama()
    docker_ok = _check_docker()
    queued = 0
    if db_ok:
        try:
            import db
            queued = db.count_queued_tasks()
        except Exception:
            pass
    return {
        "status": "ok" if (db_ok and ollama_ok) else "degraded",
        "db": db_ok,
        "ollama": ollama_ok,
        "docker": docker_ok,
        "queued_tasks": queued,
    }


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health" or self.path == "/health/":
            payload = _health_payload()
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/metrics" or self.path == "/metrics/":
            state_dir = getattr(config, "STATE_DIR", "state") if config else "state"
            path = os.path.join(state_dir, "metrics.json")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    body = f.read().encode("utf-8")
            except Exception:
                body = json.dumps({"error": "no metrics yet"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/audit" or self.path == "/audit/":
            state_dir = getattr(config, "STATE_DIR", "state") if config else "state"
            path = os.path.join(state_dir, "audit.log")
            lines = []
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        lines.append(line.rstrip())
                lines = lines[-100:]
            except Exception:
                pass
            body = json.dumps({"lines": lines}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        webhook_enabled = config and getattr(config, "WEBHOOK_IN_ENABLED", False)
        if not webhook_enabled:
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/incoming/task" or self.path == "/incoming/task/":
            body = _read_body(self)
            bot_id = body.get("bot_id")
            prompt = body.get("prompt", "")
            priority = int(body.get("priority", 0))
            task_type = (body.get("task_type") or "code").strip()
            try:
                import db
                if not bot_id:
                    bots = db.list_bots()
                    bot_id = bots[0]["id"] if bots else None
                if bot_id:
                    tid = db.insert_task(bot_id, prompt, priority=priority, task_type=task_type)
                    out = {"ok": True, "task_id": tid}
                else:
                    out = {"ok": False, "error": "no bot_id and no bots"}
            except Exception as e:
                out = {"ok": False, "error": str(e)[:200]}
            self._send_json(out)
        elif self.path == "/incoming/proposal" or self.path == "/incoming/proposal/":
            body = _read_body(self)
            bot_id = body.get("bot_id")
            content = body.get("content", "")
            try:
                import db
                if not bot_id:
                    bots = db.list_bots()
                    bot_id = bots[0]["id"] if bots else None
                if bot_id and content:
                    db.insert_proposal(bot_id, content)
                    out = {"ok": True}
                else:
                    out = {"ok": False, "error": "bot_id and content required"}
            except Exception as e:
                out = {"ok": False, "error": str(e)[:200]}
            self._send_json(out)
        else:
            self.send_response(404)
            self.end_headers()

    def _send_json(self, obj: dict):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def start_health_server() -> None:
    """Start health server in a daemon thread. Call once at startup."""
    health_enabled = getattr(config, "HEALTH_ENABLED", True) if config else True
    if not health_enabled:
        return
    port = getattr(config, "HEALTH_PORT", 8765) if config else 8765
    try:
        server = HTTPServer(("", port), _HealthHandler)
        server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
    except Exception:
        pass
