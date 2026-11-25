import json
import math
import uuid


MAX_BATCH = 96
MAX_MESSAGE = 65_536


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def identifier(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("invalid UUID")
    return str(uuid.UUID(value))


def number(value, minimum, maximum):
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise ValueError("number out of range")
    return float(value)


def decode_batch(message, now):
    if not isinstance(message, str):
        raise ValueError("expected a text frame")
    data = json.loads(message)
    if not isinstance(data, dict) or type(data.get("v")) is not int or data["v"] != 1:
        raise ValueError("unsupported protocol version")
    batch_id = identifier(data.get("id"))
    samples = data.get("samples")
    if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_BATCH:
        raise ValueError("invalid batch size")
    return batch_id, [decode_sample(sample, now) for sample in samples]


def decode_sample(sample, now):
    if not isinstance(sample, dict):
        raise ValueError("invalid sample")
    kind = sample.get("kind")
    t = number(sample.get("t"), 0, now + 5)
    clean = {"id": identifier(sample.get("id")), "kind": kind, "t": t}
    if kind == "motion":
        clean.update({key: number(sample.get(key), -32, 32) for key in (
            "ax", "ay", "az", "lateral", "vertical",
        )})
        yaw = number(sample.get("yaw"), 0, 360)
        clean["yaw"] = math.fmod(yaw, 360)
        frame = sample.get("frame")
        if frame not in ("magnetic", "relative"):
            raise ValueError("invalid attitude reference frame")
        clean["frame"] = frame
    elif kind == "heart":
        clean["bpm"] = number(sample.get("bpm"), 20, 250)
    elif kind == "sleep":
        clean["start"] = number(sample.get("start"), max(0, t - 86_400), t)
        stage = sample.get("stage")
        if type(stage) is not int or not 0 <= stage <= 5 or clean["start"] == t:
            raise ValueError("invalid sleep interval or stage")
        clean["stage"] = stage
    else:
        raise ValueError("unsupported sample kind")
    return clean
