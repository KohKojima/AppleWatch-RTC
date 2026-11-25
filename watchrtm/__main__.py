from argparse import ArgumentParser
from contextlib import ExitStack
import logging
import os
import time

import OpenRTM_aist

from .devices import DeviceWorker, SerialAlarm, SwitchBot
from .network import dashboard_server, ingest_server
from .rtc import build
from .settings import Settings
from .store import Store


def main():
    parser = ArgumentParser(description="Apple Watch OpenRTM host")
    parser.add_argument("--config", help="WatchRTM JSON config")
    parser.add_argument("--rtm-config", help="OpenRTM rtc.conf for the naming service and ORB")
    parser.add_argument("--manual", action="store_true", help="Connect and activate RTCs in RTSystemEditor")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = Settings.load(args.config)
        run(settings, args)
    except KeyboardInterrupt:
        return 0
    except (ValueError, TypeError, OSError, RuntimeError) as error:
        logging.error("%s", error)
        return 1
    return 0


def run(settings, args):
    with ExitStack() as resources:
        store = Store(settings.database)
        resources.callback(store.close)
        switchbot = DeviceWorker("Send", SwitchBot(settings.output), store, settings.output.dry_run).start()
        resources.callback(switchbot.close)
        alarm = DeviceWorker("ExternalAlarmRTC", SerialAlarm(settings.output), store, settings.output.dry_run).start()
        resources.callback(alarm.close)
        resources.enter_context(ingest_server(settings, store, os.environ.get("WATCHRTM_TOKEN", "")))
        resources.enter_context(dashboard_server(settings, store))
        options = ["watchrtm", "-o", "exec_cxt.periodic.rate:50", "-o", "logger.enable:NO",
                   "-o", "manager.shutdown_auto:NO", "-o", "manager.corba_servant:NO"]
        if args.rtm_config:
            options.extend(["-f", args.rtm_config])
        else:
            options.extend(["-o", "naming.enable:NO", "-o", "corba.endpoints:127.0.0.1:0"])
        manager = OpenRTM_aist.Manager.init(options)
        resources.callback(manager.shutdown)
        if not manager.activateManager():
            raise RuntimeError("Failed to activate the OpenRTM manager")
        manager.runManager(True)
        build(manager, store, settings.rules, switchbot, alarm, args.manual)
        while True:
            time.sleep(0.25)


if __name__ == "__main__":
    raise SystemExit(main())
