from contextlib import contextmanager
from functools import partial
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import logging
from pathlib import Path
import ssl
from threading import Event, Lock, Thread
import time

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from .protocol import MAX_MESSAGE, decode_batch, dumps


LOG = logging.getLogger(__name__)
DASHBOARD = Path(__file__).with_name("dashboard.html")


@contextmanager
def ingest_server(settings, store, token):
    if not ipaddress.ip_address(settings.listen).is_loopback and len(token) < 20:
        raise ValueError("Set WATCHRTM_TOKEN to a random token of at least 20 characters for LAN access")
    token_header = f"Bearer {token}".encode("utf-8")
    tls = None
    if settings.tls_cert:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(settings.tls_cert, settings.tls_key)

    def handshake(connection, request):
        if request.path != "/watch":
            return connection.respond(HTTPStatus.NOT_FOUND, "Use /watch\n")
        headers = request.headers.get_all("Authorization")
        if token and (len(headers) != 1 or not hmac.compare_digest(headers[0].encode("utf-8"), token_header)):
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Invalid bearer token\n")

    stopping, writer = Event(), Lock()

    def receive(socket):
        if not writer.acquire(blocking=False):
            socket.close(1013, "Another iPhone is connected")
            return
        try:
            while not stopping.is_set():
                try:
                    message = socket.recv(timeout=1)
                except TimeoutError:
                    continue
                now = time.time()
                try:
                    batch, samples = decode_batch(message, now)
                except (ValueError, RecursionError):
                    socket.send(dumps({"v": 1, "error": "invalid_batch"}))
                    continue
                count = store.append(samples, now)
                socket.send(dumps({"v": 1, "ack": batch, "stored": count}))
        except ConnectionClosed:
            pass
        finally:
            writer.release()

    with serve(receive, settings.listen, settings.port, ssl=tls, origins=[None],
               process_request=handshake, max_size=MAX_MESSAGE, compression=None,
               open_timeout=3, close_timeout=1, ping_interval=10, ping_timeout=5) as server:
        thread = Thread(target=server.serve_forever, name="WatchWebSocket", daemon=True)
        thread.start()
        LOG.info("%s://%s:%d/watch", "wss" if tls else "ws", settings.listen, settings.port)
        try:
            yield
        finally:
            stopping.set()
            server.shutdown()
            thread.join()
            with writer:
                pass


class DashboardHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, store, goal_hours, dry_run, **kwargs):
        self.store, self.goal_hours, self.dry_run = store, goal_hours, dry_run
        super().__init__(*args, **kwargs)

    def do_GET(self):
        port = self.server.server_address[1]
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if self.path == "/api/v1/status":
            body = self.store.status(time.time(), self.goal_hours)
            body["dry_run"] = self.dry_run
            self.reply("application/json", json.dumps(body).encode())
        elif self.path == "/":
            self.reply("text/html; charset=utf-8", DASHBOARD.read_bytes())
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def reply(self, content_type, data):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


@contextmanager
def dashboard_server(settings, store):
    if settings.dashboard_port == 0:
        yield
        return
    handler = partial(DashboardHandler, store=store, goal_hours=settings.sleep_goal_hours,
                      dry_run=settings.output.dry_run)
    with ThreadingHTTPServer(("127.0.0.1", settings.dashboard_port), handler) as server:
        thread = Thread(target=server.serve_forever, name="WatchDashboard", daemon=True)
        thread.start()
        LOG.info("Dashboard http://127.0.0.1:%d/", settings.dashboard_port)
        try:
            yield
        finally:
            server.shutdown()
            thread.join()
