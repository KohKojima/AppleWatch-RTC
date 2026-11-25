import base64
import hashlib
import hmac
import json
import logging
import os
from queue import Empty, Full, Queue
from threading import Event, Thread
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from .protocol import dumps


LOG = logging.getLogger(__name__)


class SwitchBot:
    def __init__(self, settings):
        self.settings = settings
        self.token = os.environ.get("SWITCHBOT_TOKEN", "")
        self.secret = os.environ.get("SWITCHBOT_SECRET", "")

    def __call__(self, command):
        target, action = command["target"], command["action"]
        if target == "curtain":
            device = self.settings.curtain_id
            api_command, parameter = {"open": "turnOn", "close": "turnOff"}[action], "default"
        else:
            device = self.settings.light_id
            brightness = {"bright": self.settings.bright_percent, "dim": self.settings.dim_percent}
            api_command, parameter = "setBrightness", str(brightness[action])
        if not device:
            return "skipped", "SwitchBot device ID is unset"
        if not self.token or not self.secret:
            raise ValueError("Set SWITCHBOT_TOKEN and SWITCHBOT_SECRET")
        t, nonce = str(time.time_ns() // 1_000_000), str(uuid4())
        content = (self.token + t + nonce).encode()
        signature = base64.b64encode(hmac.new(self.secret.encode(), content, hashlib.sha256).digest()).decode()
        headers = {"Authorization": self.token, "sign": signature, "nonce": nonce,
                   "t": t, "Content-Type": "application/json"}
        body = dumps({"commandType": "command", "command": api_command, "parameter": parameter})
        url = f"https://api.switch-bot.com/v1.1/devices/{quote(device, safe='')}/commands"
        with urlopen(Request(url, body.encode(), headers, method="POST"), timeout=4) as response:
            result = json.loads(response.read(65_536))
        if result.get("statusCode") != 100:
            raise RuntimeError(f"SwitchBot statusCode={result.get('statusCode')!r}")
        return "accepted", "SwitchBot API acknowledged"


class SerialAlarm:
    def __init__(self, settings):
        self.settings = settings

    def __call__(self, command):
        if not self.settings.serial_port:
            return "skipped", "Bluetooth SPP port is unset"
        import serial

        identifier = command["id"].encode("ascii")
        with serial.Serial(self.settings.serial_port, self.settings.serial_baud,
                           timeout=0.20, write_timeout=1) as link:
            link.reset_input_buffer()
            link.write(b"ALARM " + identifier + b"\n")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if link.read_until(b"\n", 128).strip() == b"OK " + identifier:
                    return "accepted", "Arduino Nano acknowledged"
        raise TimeoutError("Arduino did not acknowledge over Bluetooth SPP")


class DeviceWorker:
    def __init__(self, name, handler, store, dry_run):
        self.name, self.handler, self.store, self.dry_run = name, handler, store, dry_run
        self.commands = Queue(maxsize=16)
        self.stopping = Event()
        self.thread = Thread(target=self.run, name=name, daemon=True)

    def submit(self, command):
        try:
            self.commands.put_nowait(command)
        except Full:
            LOG.warning("%s queue full, dropped %s", self.name, command["id"])

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.stopping.set()
        self.thread.join()

    def run(self):
        while not self.stopping.is_set():
            try:
                command = self.commands.get(timeout=0.2)
            except Empty:
                continue
            try:
                if time.time() - command["t"] > 8:
                    status, detail = "expired", "Command was queued for more than 8 seconds"
                elif self.dry_run:
                    status, detail = "simulated", "Dry run"
                else:
                    status, detail = self.handler(command)
            except Exception as error:
                status, detail = "error", str(error)
            try:
                self.store.record_action(command, status, detail)
            except Exception:
                LOG.exception("Could not record the %s command result", self.name)
            LOG.info("%s %s %s: %s", self.name, command["action"], status, detail)
