from contextlib import ExitStack
from dataclasses import replace
import json
import socket
from tempfile import TemporaryDirectory
import time
import unittest
from urllib.request import urlopen

import OpenRTM_aist
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from scripts.demo import motion, push, run, sample
from watchrtm.devices import DeviceWorker
from watchrtm.network import dashboard_server, ingest_server
from watchrtm.rtc import build
from watchrtm.settings import Settings
from watchrtm.store import Store


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class RealRTMTest(unittest.TestCase):
    def test_sensor_to_device_commands(self):
        with ExitStack() as stack:
            work = stack.enter_context(TemporaryDirectory())
            settings = Settings(port=free_port(), dashboard_port=free_port(), database=work + "/test.sqlite3")
            settings = replace(settings, rules=replace(settings.rules, light_enabled=True))
            store = Store(settings.database)
            stack.callback(store.close)
            secret = "integration-test-local-token"
            stack.enter_context(ingest_server(settings, store, secret))
            stack.enter_context(dashboard_server(settings, store))
            workers = [DeviceWorker(name, None, store, True).start() for name in ("Send", "ExternalAlarmRTC")]
            for worker in workers:
                stack.callback(worker.close)
            manager = OpenRTM_aist.Manager.init(["test", "-o", "naming.enable:NO", "-o", "logger.enable:NO",
                                                "-o", "corba.endpoints:127.0.0.1:0",
                                                "-o", "manager.shutdown_auto:NO", "-o", "manager.corba_servant:NO"])
            stack.callback(manager.shutdown)
            self.assertTrue(manager.activateManager())
            manager.runManager(True)
            build(manager, store, settings.rules, *workers, False)
            self.assertEqual(len(manager.getComponents()), 4)
            for component in manager.getComponents():
                self.assertEqual(component.getObjRef().get_owned_contexts()[0].get_rate(), 50.0)

            address = f"ws://127.0.0.1:{settings.port}/watch"
            with self.assertRaises(InvalidStatus) as unauthorized:
                connect(address, open_timeout=3)
            self.assertEqual(unauthorized.exception.response.status_code, 401)
            with connect(address, additional_headers={"Authorization": f"Bearer {secret}"}) as client:
                client.send('{"v":1,"samples":[]}')
                self.assertEqual(json.loads(client.recv(timeout=3))["error"], "invalid_batch")
                old = sample("heart", time.time() - 60, bpm=40)
                self.assertEqual(push(client, [old]), 1)
                self.assertEqual(push(client, [old]), 0)
                push(client, [motion(0, yaw=90), motion(.95, yaw=90)])
                run(client)

            api = f"http://127.0.0.1:{settings.dashboard_port}/api/v1/status"
            for _ in range(40):
                with urlopen(api, timeout=2) as response:
                    data = json.load(response)
                if len(data["actions"]) >= 4:
                    break
                time.sleep(.05)
            self.assertEqual({(row["target"], row["action"]) for row in data["actions"]}, {
                ("curtain", "open"), ("curtain", "close"), ("alarm", "ring"), ("light", "bright"),
            })
            self.assertEqual(len(data["actions"]), 4)
            self.assertTrue(all(row["status"] == "simulated" for row in data["actions"]))
            self.assertEqual(data["latest"]["heart"]["bpm"], 51)
            self.assertEqual(data["sleep"], {"hours": 7.25, "priority": "light", "goal_hours": 8})


if __name__ == "__main__":
    unittest.main()
