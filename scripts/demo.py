"""Send synthetic Apple Watch data through the real WebSocket and RTCs."""

from argparse import ArgumentParser
import json
import os
import time
from urllib.request import urlopen
from uuid import uuid4
import webbrowser

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect


def sample(kind, t=None, **values):
    return {"id": str(uuid4()), "kind": kind, "t": time.time() if t is None else t, **values}


def motion(lateral, yaw=5, vertical=0):
    return sample("motion", ax=lateral, ay=0, az=vertical, lateral=lateral,
                  vertical=vertical, yaw=yaw, frame="relative")


def push(socket, samples, batch_id=None):
    batch_id = batch_id or str(uuid4())
    data = {"v": 1, "id": batch_id, "samples": samples}
    socket.send(json.dumps(data))
    reply = json.loads(socket.recv(timeout=3))
    if reply.get("ack") != batch_id:
        raise RuntimeError(f"Unexpected host reply: {reply}")
    return reply["stored"]


def run(socket):
    now = time.time()
    push(socket, [sample("sleep", now - 3600, start=now - 8.25 * 3600, stage=3)])
    for row in [motion(0), motion(.9)] + [sample("heart", bpm=bpm) for bpm in (79, 80, 78, 52, 51)]:
        push(socket, [row])
        time.sleep(.08)
    time.sleep(1.8)
    push(socket, [motion(0)])
    time.sleep(.08)
    push(socket, [motion(-.9)])
    push(socket, [motion(0, vertical=.9)])


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:8765/watch")
    parser.add_argument("--dashboard", default="http://127.0.0.1:8080/")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("WATCHRTM_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else None
    deadline = time.monotonic() + (15 if args.wait else 0)
    while True:
        try:
            with urlopen(args.dashboard.rstrip('/') + '/api/v1/status', timeout=2) as response:
                if not json.load(response)["dry_run"]:
                    raise SystemExit("Synthetic playback requires dry_run=true.")
            client = connect(args.url, additional_headers=headers, open_timeout=2)
            break
        except (OSError, WebSocketException) as error:
            if time.monotonic() >= deadline:
                raise SystemExit(f"Cannot connect: {error}") from error
            time.sleep(.25)
    if args.open_browser:
        webbrowser.open(args.dashboard)
    with client:
        run(client)
    print("Sent synthetic sleep, heart rate, curtain and lighting gestures.")


if __name__ == "__main__":
    main()
