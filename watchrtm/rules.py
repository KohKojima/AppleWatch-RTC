from collections import deque
from statistics import median
from uuid import uuid4


class Analyzer:
    def __init__(self, rules):
        self.rules = rules
        self.last_sample = {}
        self.gesture_armed = {"curtain": False, "light": False}
        self.last_command = {}
        self.baseline = deque(maxlen=12)
        self.low_count = 0
        self.alarm_latched = False

    def process(self, sample, now):
        kind, timestamp = sample["kind"], sample["t"]
        age_limit = self.rules.motion_max_age_s if kind == "motion" else self.rules.heart_max_age_s
        previous = self.last_sample.get(kind, float("-inf"))
        if kind == "sleep" or not -2 <= now - timestamp <= age_limit or timestamp <= previous:
            return []
        self.last_sample[kind] = timestamp
        if kind == "motion":
            return self.motion(sample, now, timestamp - previous)
        return self.heart(sample, now, timestamp - previous)

    def command(self, target, action, sample, now):
        self.last_command[target] = now
        return {"v": 1, "id": str(uuid4()), "target": target, "action": action,
                "t": now, "sample_id": sample["id"]}

    def motion(self, sample, now, gap):
        if gap > self.rules.motion_max_age_s:
            self.gesture_armed = {"curtain": False, "light": False}
        delta = (sample["yaw"] - self.rules.heading_deg + 180) % 360 - 180
        commands = []
        if abs(delta) <= self.rules.heading_tolerance_deg:
            self.gesture(commands, "curtain", sample["lateral"], self.rules.curtain_trigger_g,
                         "open", "close", sample, now)
        else:
            self.gesture_armed["curtain"] = False
        if self.rules.light_enabled:
            self.gesture(commands, "light", sample["vertical"], self.rules.light_trigger_g,
                         "bright", "dim", sample, now)
        return commands

    def gesture(self, commands, target, value, threshold, positive, negative, sample, now):
        if abs(value) <= self.rules.release_g:
            self.gesture_armed[target] = True
        elif abs(value) >= threshold and self.gesture_armed[target]:
            self.gesture_armed[target] = False
            if now - self.last_command.get(target, float("-inf")) >= self.rules.gesture_cooldown_s:
                action = positive if value > 0 else negative
                commands.append(self.command(target, action, sample, now))

    def heart(self, sample, now, gap):
        bpm = sample["bpm"]
        if gap > self.rules.heart_max_gap_s:
            self.low_count = 0
            self.baseline.clear()
        if bpm > self.rules.alarm_threshold_bpm:
            self.low_count = 0
            self.baseline.append(bpm)
            if bpm >= self.rules.alarm_threshold_bpm + 5:
                self.alarm_latched = False
            return []
        if len(self.baseline) < 3 or median(self.baseline) - bpm < self.rules.alarm_drop_bpm:
            self.low_count = 0
            return []
        self.low_count += 1
        elapsed = now - self.last_command.get("alarm", float("-inf"))
        if self.low_count >= self.rules.alarm_min_samples and not self.alarm_latched and elapsed >= self.rules.alarm_cooldown_s:
            self.alarm_latched = True
            return [self.command("alarm", "ring", sample, now)]
        return []
