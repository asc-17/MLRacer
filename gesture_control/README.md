# AIML Racer – Gesture Control

Drive the ESP32 car with hand gestures in front of a Windows laptop's webcam.

The app shows the camera with your hand tracked: joints as dots, bones as lines, a box around the
hand, and a label with the command and its confidence (for example `FORWARD LEFT 87%`). It sends the
command to the car over the car's own Wi-Fi.

The car runs a single sketch, `../wifi_rc.ino`. The browser page and this app both use its `/cmd`
commands, so whichever one you open drives the car. You don't need to switch anything on the car.

---

## 1. One-time setup (needs internet)

1. Install Python 3.10 or newer from python.org (3.14 is tested).
2. Double-click **`setup.bat`**. It creates `.venv`, installs the packages and downloads the
   MediaPipe hand model (~8 MB) into `models/`.

Do this while the laptop has normal internet. The car's Wi-Fi has none.

## 2. Update the car firmware (once)

The sketch now reports the battery percentage and has a read-only `/status` endpoint. Upload
`../wifi_rc.ino` the usual way: battery switch **OFF**, pull the **c6 → V5** wire, plug in USB, upload,
then reverse those steps.

The app also works with the old firmware. It then shows the battery voltage only, with no percentage.

## 3. Drive

1. Switch the car on and connect the laptop to Wi-Fi **`AIML-Racer`** (password `racer1234`).
2. Double-click **`run.bat`**.
3. Check that the side panel shows **Car: connected** and a battery reading.
4. Put the car on a box, wheels off the ground, for the first test.
5. Press **Space** to arm. The car ignores gestures until you arm it.
6. Make a gesture. Drop your hand or make a fist, and the car stops.
7. Press **Space** or **Esc** to disarm.

To try it without the car, run `run.bat --preview`, or turn on Preview mode in Settings. In preview
the app sends nothing to the car.

---

## Gestures

Use one hand, 40–80 cm from the camera, with your palm and wrist in view and good light.
The picture is mirrored like a selfie camera, so "left" means your left.

### Mode 1: Pointing direction (default)

Point your index finger and keep the other three fingers curled. The thumb doesn't matter.

| Point your index finger | Car |
|---|---|
| up | Forward |
| down | Backward |
| left | Spin left |
| right | Spin right |
| up-left / up-right | Forward-left / Forward-right |
| down-left / down-right | Backward-left / Backward-right |
| fist, any other pose, no hand | **Stop** |

### Mode 2: Hand shapes

The thumb's side always decides the steering side.

| Hand shape | Car |
|---|---|
| Open palm | Forward |
| Peace sign (V) | Backward |
| Thumb only, pointing left / right | Spin left / right |
| L-shape (thumb + index), thumb on the left / right | Forward-left / Forward-right |
| Shaka (thumb + pinky), thumb on the left / right | Backward-left / Backward-right |
| Fist, any other pose, no hand | **Stop** |

With your right hand, palm to the camera, the thumb sticks out to the left. For the right-hand
commands, use your left hand or turn your hand round. A thumbs-up (thumb pointing up) doesn't steer,
so the car stops.

Switch modes with **M**, the **Switch mode** button, or in Settings. The current mode's gestures are
listed in the side panel. *Help → Gesture guide* shows both modes.

## Keys

| Key | Action |
|---|---|
| Space | Arm / disarm |
| Esc | Disarm (car stops) |
| M | Switch gesture mode |
| Ctrl + , | Settings |
| Q | Quit (car stops) |

## Settings

Open Settings with *App → Settings…* or the **Settings…** button. Your choices are saved in
`settings.json`.

| Setting | Default | What it does |
|---|---|---|
| Gesture mode | Pointing | Which gesture set to use |
| Minimum confidence | 60 % | Below this the car gets Stop |
| Hold frames | 3 | A new gesture must be seen this many frames in a row (0.1–0.2 s) before it is sent. Stopping is always immediate. |
| Speed | 60 % | Fixed driving speed (10–100 %) |
| Car address | 192.168.4.1 | The car's IP on its own Wi-Fi |
| Preview mode | off | Never talk to the car |
| Camera number | 0 | 0 = built-in webcam; try 1, 2… for a USB camera |
| Mirror | on | Show the picture like a mirror. Gestures mean the same either way. |

Opening Settings disarms the car.

## What's on the screen

- **Box and label colour**
  - green: armed and driving
  - amber: a gesture is recognised, but the car is disarmed or in preview
  - grey: stop
  - red: fist
- **Arrow**: the direction the app reads from your finger or thumb.
- **Side panel**: the current command, a confidence bar with a tick at the threshold, the arm
  button, mode, speed, car link and round-trip time, camera fps and the battery.
- **Finger extension bars (T I M R P)**: how straight the app thinks each finger is. If a gesture
  isn't recognised, these show which finger is being misread.
- **"looks like …, confidence too low"**: the app sees a command but isn't sure enough, so it sends
  Stop.

## Battery indicator

The car measures the 6 × AA Eneloop pack and converts the voltage to a percentage. The bar shows in
this app and on the car's web page. It turns amber below 35 % and red below 15 %; recharge when it is
red.

| Pack voltage at rest | ≈ charge |
|---|---|
| 8.4 V | 100 % |
| 7.8 V | 70 % |
| 7.56 V | 50 % |
| 7.2 V | 20 % |
| 7.08 V | 15 % |
| 6.0 V | 0 % |

NiMH cells hold an almost flat voltage for most of their charge, so treat the percentage as an
estimate. Driving makes the voltage sag. The percentage only updates after the motors have been off
for 2 seconds, while the voltage shown is always live. On USB power the panel shows **USB**.

## Safety

- The app starts **disarmed**. Opening Settings, Esc, Q and closing the window all disarm it.
- No hand, low confidence, an unknown pose or a camera problem all send Stop.
- If the camera thread stalls for more than 0.3 s, the command becomes Stop.
- The car stops by itself if it hears nothing for 0.5 s, for example out of range or the laptop asleep.
- Drive from one place at a time. If the browser page and the app both drive at once, the car follows
  whichever spoke last. An open browser page that isn't driving does not interfere.

## Troubleshooting

| Problem | Fix |
|---|---|
| **Car: NO LINK** | Join Wi-Fi `AIML-Racer`. Check the car is on and the address in Settings is `192.168.4.1`. |
| **Camera: not available** | Close other apps using the camera (Teams, Zoom, the Camera app). Try another camera number. Check *Windows Settings → Privacy → Camera*. |
| Hand not detected | Get closer, add light, keep the palm and wrist in view, and avoid a background that is close to skin colour. |
| Gesture flickers or reads wrong | Watch the finger bars. Hold the fingers clearly straight or clearly curled, and point across the camera, not at it. |
| Battery shows only volts | The car still runs the old firmware. Re-upload `wifi_rc.ino`. |
| Slow or jerky video | The fps is shown in the panel. 15 or more is fine. Many laptop webcams drop to 15 fps in dim light, so more light helps. Close other heavy apps. |

---

## How it works

```
webcam ─► mirror ─► MediaPipe Hand Landmarker ─► 21 joints (2-D image + 3-D "world")
                    (neural network, runs on CPU)          │
                                                           ▼
                        gestures.py: geometry rules → command + confidence
                                                           │
                        debounce (hold frames, instant stop) ▼
          overlay drawing ◄── vision.py thread ──► car_link.py thread ──► GET /cmd?c=F&v=60
```

- **Hand tracking (ML):** Google's MediaPipe Hand Landmarker, a pre-trained neural network, finds
  21 joints per frame.
- **Finger straightness:** comes from the 3-D joint angles. Each finger's bend is added up over its
  three joints, so it works whichever way the hand is turned. The thumb counts as extended when its
  tip points away from the palm and is well clear of the index knuckle.
- **Pointing direction:** the angle of the index finger in the image, snapped to 8 directions of 45°.
  The current direction keeps an extra 8° so the command doesn't flicker on a boundary.
- **Confidence:** with rules there is no class probability, so the score is built from geometry:
  1. how clearly each finger is straight or curled, as the pose needs it (the weakest finger counts);
  2. how close the finger is to the centre of its direction;
  3. how foreshortened the finger is (pointing at the camera is unreliable);
  4. MediaPipe's own hand score.

  The score is all of these multiplied together.

The thresholds are at the top of `gestures.py` and were checked against MediaPipe's public sample
photos of a pointing finger, thumbs up/down and a peace sign. Their landmarks are saved in
`tests/fixtures/real_hands.json`.

### Files

| File | Purpose |
|---|---|
| `main.py` | Start-up (`--preview`, `--camera N`) |
| `app.py` | Window, side panel, Settings dialog, keys |
| `vision.py` | Camera thread: MediaPipe, classification, overlay drawing |
| `gestures.py` | The gesture rules (no camera or GUI, easy to test) |
| `car_link.py` | Sends commands and reads the battery, in its own thread |
| `settings.py` | `settings.json` load and save |
| `tests/` | Unit tests |

### Tests

```
.venv\Scripts\python.exe -m pytest tests
```

The tests cover every gesture in both modes (real photo landmarks plus a synthetic 3-D hand), the
debounce, and the car link against a fake car server. The fake car speaks both the new and the old
firmware's protocol.
