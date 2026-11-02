"""Local pause/resume/single-step controls. No signals or hooks in the desktop configuration."""
import fcntl
import json
import os
import tempfile
import time
import uuid
from pathlib import Path


def directory():
    return Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir())) / f"balatro-control-{os.getuid()}"


def write(path, data):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(data))
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def request(command, path=None):
    path = path or directory()
    if command not in ("pause", "resume", "step", "status"):
        raise ValueError("Unknown control command")
    try:
        status = json.loads((path / "status.json").read_text())
        if time.time() - status["heartbeat"] > 15:
            raise RuntimeError("Autopilot is not responding")
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        raise RuntimeError("No controlled autopilot is running") from None
    if command != "status":
        write(path / "request.json", {"command": command, "id": uuid.uuid4().hex})
    return status


class Control:
    def __init__(self, paused=False, path=None):
        self.path = path or directory()
        self.paused, self.steps, self.seen = paused, 0, None

    def __enter__(self):
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = open(self.path / "lock", "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError("An autopilot is already running") from None
        (self.path / "request.json").unlink(missing_ok=True)
        self.poll()
        return self

    def __exit__(self, *exc):
        (self.path / "status.json").unlink(missing_ok=True)
        self.lock.close()

    def poll(self):
        try:
            cmd = json.loads((self.path / "request.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            cmd = {}
        if cmd.get("id") and cmd["id"] != self.seen:
            self.seen = cmd["id"]
            command = cmd.get("command")
            if command == "step":
                self.paused, self.steps = True, self.steps + 1
            elif command in ("pause", "resume"):
                self.paused, self.steps = command == "pause", 0
        write(self.path / "status.json", {"paused": self.paused, "steps": self.steps,
                                           "pid": os.getpid(), "heartbeat": time.time()})
        return not self.paused or self.steps > 0

    def consume(self):
        if self.paused:
            self.steps = max(0, self.steps - 1)
