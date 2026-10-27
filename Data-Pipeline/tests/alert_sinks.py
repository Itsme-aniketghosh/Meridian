"""Local stand-ins for Slack and an SMTP server, so an alert can be sent for real over the network and read back.

    with sinks() as s:      # s.webhook: a Slack-style incoming-webhook URL; s.smtp_port: an SMTP server on 127.0.0.1
        ...                 # s.slack: the JSON bodies posted; s.mail: the raw messages delivered

Used by tests/test_alert.py and run_demo.py. A real channel is the same code with a real webhook URL in .env and a real
SMTP host in settings.toml [alerts].
"""
import contextlib
import http.server
import json
import socketserver
import threading


class _Slack(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):   # quiet: the caller prints what arrived
        pass


class _SMTP(socketserver.StreamRequestHandler):
    """Just enough SMTP for smtplib: 250 to everything, collect what follows DATA up to the lone '.'."""
    def handle(self):
        def say(s):
            self.wfile.write(s.encode() + b"\r\n")
        say("220 sink")
        body = None
        for raw in self.rfile:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if body is not None:
                if line == ".":
                    self.server.got.append("\n".join(body))
                    body = None
                    say("250 queued")
                else:
                    body.append(line[1:] if line.startswith("..") else line)
            elif line[:4].upper() == "DATA":
                body = []
                say("354 go ahead")
            elif line[:4].upper() == "QUIT":
                say("221 bye")
                return
            else:
                say("250 ok")


@contextlib.contextmanager
def sinks():
    servers = [socketserver.ThreadingTCPServer(("127.0.0.1", 0), h) for h in (_Slack, _SMTP)]
    for s in servers:
        s.got, s.daemon_threads = [], True
        threading.Thread(target=s.serve_forever, daemon=True).start()
    slack, smtp = servers
    try:
        yield type("Sinks", (), {"webhook": f"http://127.0.0.1:{slack.server_address[1]}/hook",
                                 "smtp_port": smtp.server_address[1], "slack": slack.got, "mail": smtp.got})
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()
