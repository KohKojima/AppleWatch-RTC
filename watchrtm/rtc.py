import json
import logging
import time

import OpenRTM_aist
import RTC

from .protocol import dumps
from .rules import Analyzer


LOG = logging.getLogger(__name__)
PERIOD_HZ = 50.0


class Component(OpenRTM_aist.DataFlowComponentBase):
    def string_out(self, name):
        data = RTC.TimedString(RTC.Time(0, 0), "")
        port = OpenRTM_aist.OutPort(name, data)
        self.addOutPort(name, port)
        return data, port

    def string_in(self, name):
        data = RTC.TimedString(RTC.Time(0, 0), "")
        port = OpenRTM_aist.InPort(name, data)
        self.addInPort(name, port)
        return port

    @staticmethod
    def write(output, value):
        data, port = output
        data.data = dumps(value)
        OpenRTM_aist.setTimestamp(data)
        port.write()


class WatchDataInputRTC(Component):
    def __init__(self, manager, store):
        super().__init__(manager)
        self.store = store

    def onInitialize(self):
        self.watch_data = self.string_out("watchData")
        return RTC.RTC_OK

    def onExecute(self, _ec_id):
        self.write(self.watch_data, self.store.take())
        return RTC.RTC_OK


class WatchDataAnalyzerRTC(Component):
    def __init__(self, manager, rules):
        super().__init__(manager)
        self.rules = rules

    def onInitialize(self):
        self.watch_data = self.string_in("watchData")
        self.switchbot = self.string_out("switchbotCommand")
        self.alarm = self.string_out("alarmCommand")
        return RTC.RTC_OK

    def onActivated(self, _ec_id):
        self.analyzer = Analyzer(self.rules)
        return RTC.RTC_OK

    def onExecute(self, _ec_id):
        now = time.time()
        for _ in range(16):
            if not self.watch_data.isNew():
                break
            try:
                envelope = json.loads(self.watch_data.read().data)
                for sample in sorted(envelope["samples"], key=lambda value: value["t"]):
                    for command in self.analyzer.process(sample, now):
                        output = self.alarm if command["target"] == "alarm" else self.switchbot
                        self.write(output, command)
            except (ValueError, TypeError, KeyError):
                LOG.warning("Invalid watchData message")
        return RTC.RTC_OK


class DeviceOutputRTC(Component):
    def __init__(self, manager, worker):
        super().__init__(manager)
        self.worker = worker

    def onInitialize(self):
        self.commands = self.string_in("command")
        return RTC.RTC_OK

    def onExecute(self, _ec_id):
        for _ in range(16):
            if not self.commands.isNew():
                break
            try:
                command = json.loads(self.commands.read().data)
                if command["v"] == 1 and command["action"] in self.actions.get(command["target"], ()):
                    self.worker.submit(command)
            except (ValueError, TypeError, KeyError):
                LOG.warning("Invalid device command")
        return RTC.RTC_OK


class Send(DeviceOutputRTC):
    actions = {"curtain": ("open", "close"), "light": ("bright", "dim")}


class ExternalAlarmRTC(DeviceOutputRTC):
    actions = {"alarm": ("ring",)}


def register(manager, component, dependency):
    name = component.__name__
    profile = OpenRTM_aist.Properties(defaults_str=[
        "implementation_id", name, "type_name", name,
        "description", f"SI2026 {name}", "version", "1.0.0", "vendor", "WatchRTM",
        "category", "Wearable", "activity_type", "PERIODIC", "kind", "DataFlowComponent",
        "max_instance", "1", "language", "Python", "lang_type", "SCRIPT", "",
    ])
    manager.registerFactory(profile, lambda mgr: component(mgr, dependency), OpenRTM_aist.Delete)
    rtc = manager.createComponent(name)
    if rtc is None:
        raise RuntimeError(f"OpenRTM could not create {name}")
    return rtc.getObjRef()


def activate(rtc):
    ec = rtc.get_owned_contexts()[0]
    if ec.set_rate(PERIOD_HZ) != RTC.RTC_OK or ec.get_rate() != PERIOD_HZ:
        raise RuntimeError("Failed to set the RTC execution context to 50 Hz")
    if ec.activate_component(rtc) != RTC.RTC_OK:
        raise RuntimeError(f"Failed to activate {rtc.get_component_profile().instance_name}")


def connect(source, output, target, input_name):
    properties = OpenRTM_aist.Properties()
    for key, value in {
        "dataport.interface_type": "corba_cdr",
        "dataport.dataflow_type": "push",
        "dataport.subscription_type": "flush",
        "dataport.buffer.length": "32",
    }.items():
        properties.setProperty(key, value)
    src = OpenRTM_aist.CORBA_RTCUtil.get_port_by_name(source, output)
    dst = OpenRTM_aist.CORBA_RTCUtil.get_port_by_name(target, input_name)
    if OpenRTM_aist.CORBA_RTCUtil.connect(output, properties, src, dst) != RTC.RTC_OK:
        raise RuntimeError(f"Failed to connect {output} -> {input_name}")


def build(manager, store, rules, switchbot, alarm, manual):
    source = register(manager, WatchDataInputRTC, store)
    analyzer = register(manager, WatchDataAnalyzerRTC, rules)
    send = register(manager, Send, switchbot)
    external_alarm = register(manager, ExternalAlarmRTC, alarm)
    if not manual:
        connect(source, "WatchDataInputRTC0.watchData", analyzer, "WatchDataAnalyzerRTC0.watchData")
        connect(analyzer, "WatchDataAnalyzerRTC0.switchbotCommand", send, "Send0.command")
        connect(analyzer, "WatchDataAnalyzerRTC0.alarmCommand", external_alarm, "ExternalAlarmRTC0.command")
        for rtc in (send, external_alarm, analyzer, source):
            activate(rtc)
    LOG.info("OpenRTM RTCs ready (50 Hz; %s)", "manual wiring" if manual else "active")
