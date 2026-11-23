"""Talks to the car over its Wi-Fi, in a background thread so the video never waits on the network.

Same protocol as the car's own web page:
  GET /cmd?c=<letter>&v=<speed %>   drive (repeated every ~120 ms; the car stops itself after 0.5 s of silence)
  GET /status                       battery only, doesn't touch the motors (new firmware)
Both reply with {"vbat":7.92,"pct":73,"usb":false,"cmd":"F"}. Older firmware replies "7.92 V" or "USB"
and has no /status, so the idle battery poll falls back to /cmd?c=S like the old web page did.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

import requests

RESEND_S = 0.12        # repeat the driving command this often
POLL_S = 1.0           # battery poll interval while stopped
TIMEOUT = (0.5, 0.8)   # connect, read (seconds)


@dataclass
class CarStatus:
    link: str = "connecting"     # "preview" | "connecting" | "ok" | "nolink"
    vbat: float | None = None    # volts
    pct: int | None = None       # battery %, None with old firmware
    usb: bool = False            # running from USB (no battery reading)
    car_cmd: str | None = None   # what the car says it is doing
    rtt_ms: float | None = None  # last round-trip time
    sent_cmd: str = "S"          # last command we sent


def parse_reply(text: str) -> dict:
    """Battery info from a /cmd or /status reply, new (JSON) or old ("7.92 V" / "USB") firmware."""
    text = text.strip()
    try:
        j = json.loads(text)
        if isinstance(j, dict):
            pct = j.get("pct")
            vbat = j.get("vbat")
            return {"vbat": float(vbat) if vbat is not None else None,
                    "pct": int(pct) if pct is not None else None,
                    "usb": bool(j.get("usb", False)),
                    "car_cmd": j.get("cmd")}
    except ValueError:
        pass
    if text.upper().startswith("USB"):
        return {"vbat": None, "pct": None, "usb": True, "car_cmd": None}
    m = re.search(r"(\d+(?:\.\d+)?)\s*V", text)
    return {"vbat": float(m.group(1)) if m else None, "pct": None, "usb": False, "car_cmd": None}


class CarLink(threading.Thread):
    def __init__(self, get_command: Callable[[], str], host: str, speed: int, preview: bool):
        super().__init__(daemon=True, name="car-link")
        self.get_command = get_command
        self.host, self.speed, self.preview = host, int(speed), bool(preview)
        self.status = CarStatus(link="preview" if preview else "connecting")
        self._quit = threading.Event()
        self._session = requests.Session()
        # One short connection per request: the ESP32 web server serves one client at a time, and a
        # kept-alive idle connection from this app would make the browser page wait (and vice versa).
        self._session.headers["Connection"] = "close"
        self._has_status = True        # set False when the firmware answers /status with 404
        self._fails = 0

    # -------- called from the GUI thread
    def configure(self, host: str, speed: int, preview: bool):
        if host != self.host:
            self._has_status = True
            self._fails = 0
        self.host, self.speed, self.preview = host, int(speed), bool(preview)

    def stop(self, timeout: float = 2.0):
        self._quit.set()
        self.join(timeout)

    # -------- thread
    def run(self):
        last_cmd, last_send = None, 0.0
        while not self._quit.is_set():
            if self.preview:
                self.status.link = "preview"
                last_cmd = None              # say STOP again when preview is switched off
                self._quit.wait(0.05)
                continue
            cmd = self.get_command()
            now = time.monotonic()
            if cmd != "S":
                if cmd != last_cmd or now - last_send >= RESEND_S:
                    self._send(cmd)
                    last_cmd, last_send = cmd, now
            elif last_cmd != "S":
                # stopping: say it once, right away; if it didn't get through, retry every 0.25 s
                if last_cmd != "S?" or now - last_send >= 0.25:
                    last_cmd = "S" if self._send("S") else "S?"
                    last_send = now
            elif now - last_send >= POLL_S:
                self._poll()
                last_send = now
            self._quit.wait(0.01)
        if not self.preview:                 # leaving: make sure the car is stopped
            for _ in range(3):
                if self._send("S"):
                    break

    def _send(self, cmd: str) -> bool:
        ok = self._get("/cmd", {"c": cmd, "v": self.speed})
        if ok:
            self.status.sent_cmd = cmd
        return ok

    def _poll(self):
        if self._has_status:
            self._get("/status", None)
        else:
            self._send("S")

    def _get(self, path: str, params) -> bool:
        t0 = time.monotonic()
        try:
            r = self._session.get(f"http://{self.host}{path}", params=params, timeout=TIMEOUT)
            if r.status_code == 404 and path == "/status":
                self._has_status = False     # old firmware
                return self._send("S")
            r.raise_for_status()
        except requests.RequestException:
            self._fails += 1
            if self._fails >= 2:
                self.status.link = "nolink"
            return False
        self._fails = 0
        info = parse_reply(r.text)
        s = self.status
        s.vbat, s.pct, s.usb, s.car_cmd = info["vbat"], info["pct"], info["usb"], info["car_cmd"]
        s.rtt_ms = (time.monotonic() - t0) * 1000
        s.link = "ok"
        return True
