"""CarLink against a fake car on localhost (new JSON firmware and the old plain-text one)."""
import pathlib
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import car_link  # noqa: E402
from car_link import CarLink, parse_reply  # noqa: E402


class FakeCar:
    def __init__(self, old_firmware=False):
        self.old = old_firmware
        self.log = []                      # (time, path, c, v)
        car = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                car.log.append((time.monotonic(), u.path, q.get("c", [None])[0], q.get("v", [None])[0]))
                if u.path == "/cmd":
                    body = "7.91 V" if car.old else '{"vbat":7.91,"pct":64,"usb":false,"cmd":"S"}'
                elif u.path == "/status" and not car.old:
                    body = '{"vbat":7.95,"pct":66,"usb":false,"cmd":"S"}'
                else:
                    self.send_error(404)
                    return
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def cmds(self, path="/cmd"):
        return [e for e in self.log if e[1] == path]


@pytest.fixture
def car():
    c = FakeCar()
    yield c
    c.close()


class Desired:
    def __init__(self, cmd="S"):
        self.cmd = cmd

    def __call__(self):
        return self.cmd


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_parse_reply_formats():
    assert parse_reply('{"vbat":7.92,"pct":73,"usb":false,"cmd":"F"}') == \
        {"vbat": 7.92, "pct": 73, "usb": False, "car_cmd": "F"}
    assert parse_reply('{"vbat":0,"pct":null,"usb":true,"cmd":"S"}')["usb"] is True
    assert parse_reply("7.92 V") == {"vbat": 7.92, "pct": None, "usb": False, "car_cmd": None}
    assert parse_reply("USB")["usb"] is True


def test_idle_sends_one_stop_then_polls_status(car):
    link = CarLink(Desired("S"), car.host, 60, preview=False)
    link.start()
    try:
        assert wait_for(lambda: link.status.pct == 66, 2.5)  # 66 only comes from /status
        assert [e[2] for e in car.cmds()] == ["S"]        # only the start-up stop, no /cmd spam
        assert link.status.link == "ok"
    finally:
        link.stop()


def test_driving_resends_and_stops(car):
    want = Desired("F")
    link = CarLink(want, car.host, 70, preview=False)
    link.start()
    try:
        time.sleep(1.0)
        fwd = [e for e in car.cmds() if e[2] == "F"]
        assert 6 <= len(fwd) <= 11                          # every ~120 ms
        assert all(e[3] == "70" for e in fwd)
        gaps = [b[0] - a[0] for a, b in zip(fwd, fwd[1:])]
        assert max(gaps) < 0.3                              # well inside the car's 0.5 s failsafe
        want.cmd = "S"
        assert wait_for(lambda: car.cmds()[-1][2] == "S", 0.5)
        n = len(car.cmds())
        time.sleep(0.5)
        assert len(car.cmds()) == n                         # stop is sent once, not repeated
        assert link.status.sent_cmd == "S"
    finally:
        link.stop()


def test_direction_change_is_sent_immediately(car):
    want = Desired("F")
    link = CarLink(want, car.host, 60, preview=False)
    link.start()
    try:
        assert wait_for(lambda: any(e[2] == "F" for e in car.cmds()))
        want.cmd = "G"
        t0 = time.monotonic()
        assert wait_for(lambda: car.cmds()[-1][2] == "G", 0.5)
        assert time.monotonic() - t0 < 0.1
    finally:
        link.stop()


def test_quitting_sends_stop(car):
    link = CarLink(Desired("F"), car.host, 60, preview=False)
    link.start()
    assert wait_for(lambda: any(e[2] == "F" for e in car.cmds()))
    link.get_command = Desired("F")                         # still "driving" when the app closes
    link.stop()
    assert car.cmds()[-1][2] == "S"


def test_preview_sends_nothing(car):
    link = CarLink(Desired("F"), car.host, 60, preview=True)
    link.start()
    try:
        time.sleep(0.5)
        assert car.log == [] and link.status.link == "preview"
    finally:
        link.stop()
    assert car.log == []


def test_old_firmware_falls_back(car):
    car.close()
    old = FakeCar(old_firmware=True)
    link = CarLink(Desired("S"), old.host, 60, preview=False)
    link.start()
    try:
        assert wait_for(lambda: link.status.vbat == 7.91 and len(old.cmds("/status")) == 1, 2.5)
        assert link.status.pct is None
        n = len(old.cmds("/status"))
        time.sleep(1.3)
        assert len(old.cmds("/status")) == n                # stops asking for /status after the 404
        assert len(old.cmds()) >= 2                         # polls with /cmd?c=S like the old page
    finally:
        link.stop()
        old.close()


def test_no_car_reports_nolink(monkeypatch):
    monkeypatch.setattr(car_link, "TIMEOUT", (0.2, 0.2))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                               # nothing listens here
    link = CarLink(Desired("F"), f"127.0.0.1:{port}", 60, preview=False)
    link.start()
    try:
        assert wait_for(lambda: link.status.link == "nolink", 2.0)
    finally:
        link.stop()
