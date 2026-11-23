"""AIML Racer gesture control - drive the ESP32 car with hand gestures in front of the laptop webcam.

    python main.py              normal start (laptop joined to the car's Wi-Fi "AIML-Racer")
    python main.py --preview    camera + gestures only, nothing is sent to the car
    python main.py --camera 1   use another camera
"""
import argparse
import os
import sys
import tkinter as tk
from pathlib import Path

os.environ.setdefault("GLOG_minloglevel", "2")      # hide MediaPipe's harmless start-up log lines
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from app import App  # noqa: E402
from settings import Settings  # noqa: E402

MODEL = HERE / "models" / "hand_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")
SETTINGS = HERE / "settings.json"


def main():
    ap = argparse.ArgumentParser(description="Drive the AIML Racer with hand gestures.")
    ap.add_argument("--preview", action="store_true", help="never talk to the car (test without it)")
    ap.add_argument("--camera", type=int, help="camera number (0 = built-in webcam)")
    args = ap.parse_args()

    if not MODEL.exists():
        sys.exit(f"Hand model missing: {MODEL}\nRun setup.bat (needs internet), or download\n  {MODEL_URL}\n"
                 f"into the models folder.")

    settings = Settings.load(SETTINGS)
    if args.camera is not None:
        settings.camera_index = args.camera

    try:                                   # sharp text on high-DPI laptop screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass

    root = tk.Tk()
    App(root, settings, SETTINGS, MODEL, force_preview=args.preview)
    root.mainloop()


if __name__ == "__main__":
    main()
