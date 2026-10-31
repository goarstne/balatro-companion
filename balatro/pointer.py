"""Mouse clicks on Hyprland without root/uinput: a tiny Wayland client speaking
zwlr_virtual_pointer_v1 over the compositor socket (raw wire protocol, stdlib only).
The cursor is placed with Hyprland's cursor dispatcher (logical coordinates), sent straight to
Hyprland's request socket (what hyprctl does, without forking a process per call).
"""
import json
import os
import socket
import struct
import subprocess
import time

BTN_LEFT = 0x110


def _str(s):
    b = s.encode() + b"\0"
    return struct.pack("I", len(b)) + b + b"\0" * (-len(b) % 4)


def hypr_request(cmd):
    """One Hyprland IPC request ("j/clients", "dispatch ...") -> response text. Falls back to hyprctl."""
    try:
        path = os.path.join(os.environ["XDG_RUNTIME_DIR"], "hypr", os.environ["HYPRLAND_INSTANCE_SIGNATURE"],
                            ".socket.sock")
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(2)
            s.connect(path)
            s.sendall(cmd.encode())
            out = b""
            while chunk := s.recv(65536):
                out += chunk
        return out.decode()
    except (KeyError, OSError):
        args = cmd.split(" ", 1)
        if args[0].startswith("j/"):
            args = [args[0][2:], "-j"] + args[1:]
        r = subprocess.run(["hyprctl", *args], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 or args[0] != "dispatch" else "error: " + r.stdout


def hypr_json(what):
    return json.loads(hypr_request("j/" + what))


def _dispatch(*variants):
    """Hyprland >= 0.55 with Lua config uses hl.dsp.*, older ones the classic dispatchers."""
    return any(hypr_request("dispatch " + v).strip() == "ok" for v in variants)


def move_cursor(x, y):
    x, y = round(x), round(y)
    if not _dispatch("hl.dsp.cursor.move({x=%d,y=%d})" % (x, y), f"movecursor {x} {y}"):
        raise RuntimeError("Hyprland can't move the cursor")


def focus_window_at(x, y):
    """Focus the window under logical (x, y) if it isn't focused yet -> True if focus changed.
    Measured: a click into the unfocused Shadow window only focuses it and is swallowed."""
    active = {m["activeWorkspace"]["id"] for m in hypr_json("monitors")}
    hit = next((c for c in hypr_json("clients") if c["workspace"]["id"] in active
                and c["at"][0] <= x < c["at"][0] + c["size"][0] and c["at"][1] <= y < c["at"][1] + c["size"][1]), None)
    if hit is None or hit["address"] == hypr_json("activewindow").get("address"):
        return False
    a = "address:" + hit["address"]
    return _dispatch('hl.dsp.focus({ window = "%s" })' % a, f"focuswindow {a}")


class Pointer:
    def __init__(self):
        path = os.path.join(os.environ["XDG_RUNTIME_DIR"], os.environ.get("WAYLAND_DISPLAY", "wayland-1"))
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(path)
        self._send(1, 1, struct.pack("I", 2))  # wl_display.get_registry -> registry id 2
        self._send(1, 0, struct.pack("I", 3))  # wl_display.sync -> callback id 3
        globals_ = {}
        while True:
            obj, op, body = self._recv()
            if obj == 2 and op == 0:  # wl_registry.global(name, interface, version)
                name, n = struct.unpack_from("II", body)
                globals_[body[8:8 + n - 1].decode()] = name
            elif obj == 3:
                break
            elif obj == 1 and op == 0:
                raise RuntimeError("Wayland error")
        name = globals_.get("zwlr_virtual_pointer_manager_v1")
        if name is None:
            raise RuntimeError("Compositor bietet keinen virtuellen Zeiger an")
        # wl_registry.bind(name, new_id<interface, version, id=4>)
        self._send(2, 0, struct.pack("I", name) + _str("zwlr_virtual_pointer_manager_v1") + struct.pack("II", 1, 4))
        self._send(4, 0, struct.pack("II", 0, 5))  # create_virtual_pointer(seat=null, id=5)

    def _send(self, obj, op, body):
        self.s.sendall(struct.pack("II", obj, (8 + len(body)) << 16 | op) + body)

    def _recv(self):
        hdr = self._read(8)
        obj, so = struct.unpack("II", hdr)
        return obj, so & 0xFFFF, self._read((so >> 16) - 8)

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("Wayland-Verbindung geschlossen")
            buf += chunk
        return buf

    def _ms(self):
        return int(time.monotonic() * 1000) & 0xFFFFFFFF

    def nudge(self):
        """1px there and back so the client under the cursor gets a motion event after a warp."""
        for d in (256, -256):  # wl_fixed 1.0
            self._send(5, 0, struct.pack("Iii", self._ms(), d, 0))
            self._send(5, 4, b"")  # frame

    def click(self, x, y, hold=0.06):
        move_cursor(x, y)
        time.sleep(0.05)
        self.nudge()
        time.sleep(0.06)
        for state in (1, 0):
            self._send(5, 2, struct.pack("III", self._ms(), BTN_LEFT, state))  # button
            self._send(5, 4, b"")  # frame
            if state:
                time.sleep(hold)  # nothing to wait for after the release

    def close(self):
        self._send(5, 8, b"")  # destroy
        self.s.close()
