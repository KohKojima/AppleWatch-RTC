from dataclasses import dataclass, field
import ipaddress
import json
from pathlib import Path


@dataclass(frozen=True)
class Rules:
    heading_deg: float = 0
    heading_tolerance_deg: float = 35
    curtain_trigger_g: float = 0.65
    light_trigger_g: float = 0.75
    release_g: float = 0.25
    gesture_cooldown_s: float = 1.6
    motion_max_age_s: float = 1
    alarm_threshold_bpm: float = 55
    alarm_drop_bpm: float = 12
    alarm_min_samples: int = 2
    heart_max_age_s: float = 15
    heart_max_gap_s: float = 15
    alarm_cooldown_s: float = 60
    light_enabled: bool = False

    def __post_init__(self):
        if not 0 <= self.heading_deg < 360 or not 0 < self.heading_tolerance_deg <= 180:
            raise ValueError("heading settings must be in degrees")
        if not 0 < self.release_g < min(self.curtain_trigger_g, self.light_trigger_g):
            raise ValueError("release_g must be below both gesture thresholds")
        if not 20 <= self.alarm_threshold_bpm <= 245 or not 0 < self.alarm_drop_bpm <= 100:
            raise ValueError("invalid heart-rate thresholds")
        if type(self.alarm_min_samples) is not int or not 1 <= self.alarm_min_samples <= 12:
            raise ValueError("alarm_min_samples must be an integer from 1 to 12")
        if not all(0 < value < 3600 for value in (
            self.gesture_cooldown_s, self.motion_max_age_s, self.heart_max_age_s,
            self.heart_max_gap_s, self.alarm_cooldown_s,
        )):
            raise ValueError("invalid rule duration")


@dataclass(frozen=True)
class Output:
    dry_run: bool = True
    curtain_id: str = ""
    light_id: str = ""
    bright_percent: int = 75
    dim_percent: int = 20
    serial_port: str = ""
    serial_baud: int = 9600

    def __post_init__(self):
        if not 1 <= self.dim_percent < self.bright_percent <= 100:
            raise ValueError("require 1 <= dim_percent < bright_percent <= 100")
        if self.serial_baud <= 0:
            raise ValueError("invalid serial baud rate")


@dataclass(frozen=True)
class Settings:
    listen: str = "127.0.0.1"
    port: int = 8765
    dashboard_port: int = 8080
    database: str = "data/watch.sqlite3"
    tls_cert: str = ""
    tls_key: str = ""
    rules: Rules = field(default_factory=Rules)
    output: Output = field(default_factory=Output)
    sleep_goal_hours: float = 8

    def __post_init__(self):
        ipaddress.ip_address(self.listen)
        if not 0 <= self.dashboard_port <= 65535 or not 1 <= self.port <= 65535:
            raise ValueError("invalid TCP port")
        if bool(self.tls_cert) != bool(self.tls_key):
            raise ValueError("configure both TLS certificate and private key")
        if not 0 < self.sleep_goal_hours <= 24:
            raise ValueError("invalid sleep goal")

    @classmethod
    def load(cls, path):
        if path is None:
            return cls()
        path = Path(path)
        values = json.loads(path.read_text(encoding="utf-8"))
        values["rules"] = Rules(**values.get("rules", {}))
        values["output"] = Output(**values.get("output", {}))
        for name in ("database", "tls_cert", "tls_key"):
            if values.get(name):
                values[name] = str(path.parent / values[name])
        return cls(**values)
