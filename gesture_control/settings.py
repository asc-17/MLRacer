"""App settings, kept in settings.json next to main.py."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from gestures import MODE_POINTING, MODES


@dataclass
class Settings:
    mode: str = MODE_POINTING          # "pointing" or "shapes"
    speed: int = 60                    # % of full battery voltage, 10..100 (fixed while driving)
    confidence_threshold: float = 0.6  # below this the car gets STOP
    hold_frames: int = 3               # frames a new gesture must be held before it is sent
    camera_index: int = 0              # 0 = built-in webcam usually
    mirror: bool = True                # show the camera like a mirror
    car_host: str = "192.168.4.1"      # the car's address on its own Wi-Fi
    preview: bool = False              # never talk to the car

    def clean(self) -> "Settings":
        if self.mode not in MODES:
            self.mode = MODE_POINTING
        self.speed = int(min(100, max(10, round(int(self.speed) / 10) * 10)))
        self.confidence_threshold = float(min(0.95, max(0.3, float(self.confidence_threshold))))
        self.hold_frames = int(min(10, max(1, int(self.hold_frames))))
        self.camera_index = int(min(9, max(0, int(self.camera_index))))
        self.car_host = str(self.car_host).strip().removeprefix("http://").rstrip("/") or "192.168.4.1"
        self.mirror, self.preview = bool(self.mirror), bool(self.preview)
        return self

    @classmethod
    def load(cls, path: Path) -> "Settings":
        s = cls()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        for f in fields(cls):
            if f.name in data:
                try:
                    setattr(s, f.name, type(getattr(s, f.name))(data[f.name]))
                except (TypeError, ValueError):
                    pass
        return s.clean()

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(asdict(self.clean()), indent=2), encoding="utf-8")
