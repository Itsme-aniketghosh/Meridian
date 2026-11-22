"""meridian-risk: the champion behind POST /score (S2, contract D1). Standard library only.

    python -m serve.app                       # from Model-Deployment/: http://localhost:8080
    MERIDIAN_BLOCK=../out/<run_id>/meridian.sqlite python -m serve.app     # also score new pushes

    GET  /health  -> 200 {"ok": true, "model": {"version", "md5"}}
    POST /score   -> 200 {"risk", "percentile_val", "p9": {"rule", "alert"}, "model": {"version", "md5"},
                          "features_version"}
                     400 {"error"} on a bad body · 413 over the size cap · 422 a repo the model wasn't trained on

Settings by environment, so Cloud Run sets them and nothing is hard-coded: PORT (8080); MERIDIAN_CHAMPION,
MERIDIAN_FEATURES, MERIDIAN_VAL_SCORES; MERIDIAN_HISTORY, the image's precomputed history state (serve.precompute),
or MERIDIAN_BLOCK and MERIDIAN_RUN_ID to replay the block at start (local runs; neither: known SHAs only); and
MERIDIAN_CORS_ORIGINS, the comma-separated origins a browser may call /score from (the website's, and only it;
unset: no CORS headers at all). Every call writes one JSON line to stdout (contract D5) that Cloud Logging parses:
the event, status, latency, model version and, on a scored call, its kind, feature values and risk. Never the body,
an email, a name or a person id.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from serve.scorer import BadRequest, Scorer, WrongRepo, describe

MAX_BODY = 256 * 1024     # bytes: a push of a few thousand files fits; anything bigger is refused before parsing


def log(**fields):
    print(json.dumps({"severity": "INFO" if fields.get("status", 200) < 500 else "ERROR", **fields},
                     separators=(",", ":")), flush=True)


def make_handler(scorer, cors_origins=()):
    cors = frozenset(o.strip().rstrip("/") for o in cors_origins if o.strip())

    class Handler(BaseHTTPRequestHandler):
        server_version = "meridian-risk"

        def _send(self, status, payload):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):          # the D5 line replaces http.server's access log
            pass

        def end_headers(self):                  # CORS on every reply, for the allowed origins only
            origin = self.headers.get("Origin")
            if origin and origin in cors:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            super().end_headers()

        def do_OPTIONS(self):                  # a browser's preflight for POST /score from the website
            t0 = time.perf_counter()
            allowed = self.path == "/score" and self.headers.get("Origin") in cors
            self.send_response(204 if allowed else 403)
            if allowed:
                self.send_header("Access-Control-Allow-Methods", "POST")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()
            log(event="preflight", status=204 if allowed else 403, model_version=scorer.version,
                latency_ms=int(round((time.perf_counter() - t0) * 1000)))

        def do_GET(self):                      # a D5 line for every call, GETs included
            t0 = time.perf_counter()
            if self.path == "/health":
                status, payload = 200, scorer.health()
            else:
                status, payload = 404, {"error": "not found: GET /health or POST /score"}
            log(event="health" if status == 200 else "not_found", status=status, model_version=scorer.version,
                latency_ms=int(round((time.perf_counter() - t0) * 1000)))
            self._send(status, payload)

        def do_POST(self):
            t0 = time.perf_counter()
            status, payload, extra = self._score()
            log(event="score", status=status, model_version=scorer.version,       # before the reply: in order
                latency_ms=int(round((time.perf_counter() - t0) * 1000)), **extra)
            self._send(status, payload)

        def _score(self):
            if self.path != "/score":
                return 404, {"error": "not found: POST /score"}, {}
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return 400, {"error": "bad Content-Length"}, {}
            if length < 0:
                return 400, {"error": "bad Content-Length"}, {}
            if length > MAX_BODY:
                # read (and drop) up to 4 × the cap first: closing on a client still sending resets the connection
                # (Windows), so the caller never sees the 413. Beyond that, it's not a client we owe an answer
                left = min(length, 4 * MAX_BODY)
                while left > 0:
                    chunk = self.rfile.read(min(left, 64 * 1024))
                    if not chunk:
                        break
                    left -= len(chunk)
                return 413, {"error": f"body over {MAX_BODY // 1024} KB"}, {}
            try:
                body = json.loads(self.rfile.read(length) or b"null")
            except (ValueError, UnicodeDecodeError):
                return 400, {"error": "the body isn't JSON"}, {}
            try:
                response, fields = scorer.score(body)
            except WrongRepo as e:
                return 422, {"error": str(e)}, {}
            except BadRequest as e:
                return 400, {"error": str(e)}, {}
            except Exception as e:             # never a stack trace out; the log line says what kind
                return 500, {"error": "scoring failed"}, {"error_type": type(e).__name__}
            return 200, response, fields

    return Handler


def scorer_from_env():
    env = os.environ.get
    history = env("MERIDIAN_HISTORY")
    if history and not Path(history).is_file():   # an image built without history.pkl: known SHAs only, said at start
        history = None
    return Scorer(champion=env("MERIDIAN_CHAMPION"), features=env("MERIDIAN_FEATURES"),
                  val_scores=env("MERIDIAN_VAL_SCORES"), block=env("MERIDIAN_BLOCK"), run_id=env("MERIDIAN_RUN_ID"),
                  history=history)


def cors_from_env():
    return [o for o in os.environ.get("MERIDIAN_CORS_ORIGINS", "").split(",") if o.strip()]


def main():
    t0 = time.perf_counter()
    scorer = scorer_from_env()
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(scorer, cors_from_env()))
    log(event="start", status=200, model_version=scorer.version, latency_ms=int((time.perf_counter() - t0) * 1000),
        detail=json.loads(describe(scorer)))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
