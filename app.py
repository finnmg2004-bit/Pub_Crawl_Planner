"""Tiny web server for the pub crawl planner. Run: python app.py, then open http://localhost:8000"""
import json
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import planner

JOBS = {}
INDEX = (Path(__file__).parent / "index.html").read_bytes()


def run_job(job_id, body):
    job = JOBS[job_id]
    try:
        job["result"] = planner.plan(
            body["start"], body["end"], int(body.get("n_pubs", 3)),
            body.get("requirements", ""), log=job["log"].append,
        )
    except Exception as e:
        traceback.print_exc()
        job["error"] = str(e)
    job["done"] = True


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, INDEX, "text/html; charset=utf-8")
        if self.path.startswith("/status/"):
            job = JOBS.get(self.path.split("/")[-1])
            return self._send(200, job) if job else self._send(404, {"error": "no such job"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.startswith("/monitor/"):
            job = JOBS.get(self.path.split("/")[-1])
            if not job or not job["result"]:
                return self._send(404, {"error": "no finished plan"})
            try:
                r = job["result"]
                return self._send(200, {"monitors": planner.create_monitors(r["pubs"], r["requirements"])})
            except Exception as e:
                return self._send(500, {"error": str(e)})
        if self.path.startswith("/route/"):
            job = JOBS.get(self.path.split("/")[-1])
            if not job or not job["result"]:
                return self._send(404, {"error": "no finished plan"})
            r = planner.exact_route(job["result"], log=job["log"].append)
            return self._send(200, r) if r else self._send(500, {"error": "Agent couldn't read Google Maps - try again"})
        if self.path != "/plan":
            return self._send(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        job_id = uuid.uuid4().hex[:8]
        JOBS[job_id] = {"log": [], "done": False, "result": None, "error": None}
        threading.Thread(target=run_job, args=(job_id, body), daemon=True).start()
        self._send(200, {"job_id": job_id})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("🍺 Pub Crawl Planner running at http://localhost:8000")
    ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
