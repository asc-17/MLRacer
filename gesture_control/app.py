"""Desktop window: camera view with the hand overlay, a status panel, settings and hotkeys."""
from __future__ import annotations

import tkinter as tk
from dataclasses import fields
from pathlib import Path
from tkinter import messagebox, ttk

import cv2
from PIL import Image, ImageTk

import gestures as g
from car_link import CarLink
from settings import Settings
from vision import VisionWorker

BG = "#15171a"
PANEL = "#1f2226"
LINE = "#33373d"
FG = "#e9eaec"
MUTED = "#9aa0a6"
GREEN = "#3ccf5a"
AMBER = "#ffae00"
RED = "#ef4b4b"
GREY = "#8a8f96"
UI = "Segoe UI"
SYMBOL = "Segoe UI Symbol"


class App:
    def __init__(self, root: tk.Tk, settings: Settings, settings_path: Path, model_path: Path,
                 force_preview: bool = False):
        self.root = root
        self.settings = settings
        self.settings_path = settings_path
        self.model_path = model_path
        self.force_preview = force_preview
        self.armed = False
        self._space_down = False
        self._seq = -1
        self._photo = None
        self._cache = {}
        self.k = root.winfo_fpixels("1i") / 96.0          # screen scaling (125 %, 150 % ...)

        self.worker = self._start_worker()
        self.link = CarLink(self._desired_command, settings.car_host, settings.speed, self.preview)
        self.link.start()

        self._build_ui()
        self._bind_keys()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self._tick()

    # ------------------------------------------------------------ state
    @property
    def preview(self) -> bool:
        return self.force_preview or self.settings.preview

    def px(self, v: float) -> int:
        return int(round(v * self.k))

    def _desired_command(self) -> str:          # called from the car-link thread
        if not self.armed:
            return "S"
        return self.worker.command()

    def _start_worker(self) -> VisionWorker:
        w = VisionWorker(self.settings, self.model_path)
        w.armed, w.preview = self.armed, self.preview
        w.start()
        return w

    def set_armed(self, armed: bool):
        self.armed = bool(armed)
        self.worker.armed = self.armed

    def toggle_arm(self, _event=None):
        self.set_armed(not self.armed)

    def switch_mode(self, _event=None):
        i = g.MODES.index(self.settings.mode)
        self.settings.mode = g.MODES[(i + 1) % len(g.MODES)]
        self.settings.save(self.settings_path)
        self._fill_legend()

    def apply_settings(self, new: Settings):
        camera_changed = new.camera_index != self.settings.camera_index
        for f in fields(Settings):                      # same object: the camera thread holds it
            setattr(self.settings, f.name, getattr(new, f.name))
        self.settings.save(self.settings_path)
        self.link.configure(self.settings.car_host, self.settings.speed, self.preview)
        if camera_changed:
            self.worker.stop()
            self.worker = self._start_worker()
        self.worker.preview = self.preview
        self._fill_legend()

    def quit(self, _event=None):
        self.set_armed(False)
        self.link.stop()        # sends STOP on the way out
        self.worker.stop()
        self.root.destroy()

    # ------------------------------------------------------------ layout
    def _build_ui(self):
        r = self.root
        r.title("AIML Racer - Gesture Control")
        r.configure(bg=BG)
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        r.geometry(f"{min(self.px(1300), sw - 40)}x{min(self.px(800), sh - 90)}+20+20")
        r.minsize(self.px(900), self.px(600))

        menu = tk.Menu(r)
        app_menu = tk.Menu(menu, tearoff=False)
        app_menu.add_command(label="Settings...", accelerator="Ctrl+,", command=self.open_settings)
        app_menu.add_command(label="Arm / disarm", accelerator="Space", command=self.toggle_arm)
        app_menu.add_command(label="Switch gesture mode", accelerator="M", command=self.switch_mode)
        app_menu.add_separator()
        app_menu.add_command(label="Quit", accelerator="Q", command=self.quit)
        menu.add_cascade(label="App", menu=app_menu)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="Gesture guide", command=self.show_guide)
        help_menu.add_command(label="Keyboard shortcuts", command=self.show_keys)
        menu.add_cascade(label="Help", menu=help_menu)
        r.configure(menu=menu)

        main = tk.Frame(r, bg=BG)
        main.pack(fill="both", expand=True)

        side = tk.Frame(main, bg=PANEL, width=self.px(340))
        side.pack(side="right", fill="y", padx=(0, 10), pady=10)
        side.pack_propagate(False)

        self.video_box = tk.Frame(main, bg="black")
        self.video_box.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        self.video = tk.Label(self.video_box, bg="black", bd=0)
        self.video.place(relx=0.5, rely=0.5, anchor="center")

        W = self.px(340) - 2 * self.px(16)            # usable panel width
        pad = {"padx": self.px(16)}

        # current command
        top = tk.Frame(side, bg=PANEL)
        top.pack(fill="x", pady=(self.px(10), 0), **pad)
        self.arrow_lbl = tk.Label(top, text="■", font=(SYMBOL, 46), bg=PANEL, fg=GREY, width=2)
        self.arrow_lbl.pack(side="left")
        txt = tk.Frame(top, bg=PANEL)
        txt.pack(side="left", fill="x", expand=True, padx=(self.px(6), 0))
        self.cmd_lbl = tk.Label(txt, text="STOP", font=(UI, 20, "bold"), bg=PANEL, fg=FG, anchor="w")
        self.cmd_lbl.pack(fill="x")
        self.gesture_lbl = tk.Label(txt, text="", font=(UI, 9), bg=PANEL, fg=MUTED, anchor="w",
                                    justify="left", wraplength=W - self.px(80))
        self.gesture_lbl.pack(fill="x")

        self.conf = tk.Canvas(side, width=W, height=self.px(30), bg=PANEL, highlightthickness=0)
        self.conf.pack(pady=(self.px(4), 0), **pad)
        bh = self.px(10)
        self.conf.create_rectangle(0, self.px(18), W, self.px(18) + bh, fill=LINE, width=0)
        self._conf_fill = self.conf.create_rectangle(0, self.px(18), 0, self.px(18) + bh, fill=GREY, width=0)
        self._conf_mark = self.conf.create_line(0, self.px(15), 0, self.px(18) + bh + 3, fill=FG, width=2)
        self._conf_text = self.conf.create_text(0, 0, anchor="nw", text="Confidence", fill=MUTED, font=(UI, 9))

        # arm button
        self.arm_btn = tk.Button(side, command=self.toggle_arm, font=(UI, 12, "bold"), relief="flat",
                                 bd=0, takefocus=0, cursor="hand2", height=2)
        self.arm_btn.pack(fill="x", pady=(self.px(10), 0), **pad)
        self.arm_note = tk.Label(side, font=(UI, 9), bg=PANEL, fg=MUTED)
        self.arm_note.pack(fill="x", **pad)

        # info rows
        self._section(side, "Status")
        grid = tk.Frame(side, bg=PANEL)
        grid.pack(fill="x", **pad)
        grid.columnconfigure(1, weight=1)
        self.info = {}
        for row, key in enumerate(("Mode", "Speed", "Car", "Camera")):
            tk.Label(grid, text=key, font=(UI, 10), bg=PANEL, fg=MUTED, anchor="w").grid(
                row=row, column=0, sticky="w", pady=1)
            lbl = tk.Label(grid, text="", font=(UI, 10), bg=PANEL, fg=FG, anchor="w")
            lbl.grid(row=row, column=1, sticky="w", padx=(self.px(10), 0))
            self.info[key] = lbl

        # battery
        self._section(side, "Car battery (6 x AA Eneloop)")
        self.bat = tk.Canvas(side, width=W, height=self.px(44), bg=PANEL, highlightthickness=0)
        self.bat.pack(**pad)
        bw, bht, y = self.px(96), self.px(38), self.px(3)
        self.bat.create_rectangle(1, y, bw, y + bht, outline=MUTED, width=2)
        self.bat.create_rectangle(bw, y + bht * 0.3, bw + self.px(6), y + bht * 0.7, fill=MUTED, width=0)
        self._bat_geom = (self.px(4), y + self.px(3), bw - self.px(3), y + bht - self.px(3))
        self._bat_fill = self.bat.create_rectangle(*self._bat_geom, fill=GREEN, width=0)
        self._bat_in = self.bat.create_text(bw / 2, y + bht / 2, text="", fill=FG, font=(UI, 11, "bold"))
        self._bat_pct = self.bat.create_text(bw + self.px(18), y + self.px(1), anchor="nw", text="",
                                             fill=FG, font=(UI, 15, "bold"))
        self._bat_v = self.bat.create_text(bw + self.px(18), y + self.px(25), anchor="nw", text="",
                                           fill=MUTED, font=(UI, 9))

        # finger bars
        self._section(side, "Finger extension (what the rules see)")
        self.fing = tk.Canvas(side, width=W, height=self.px(52), bg=PANEL, highlightthickness=0)
        self.fing.pack(**pad)
        self._fing_bars = []
        col = W / 5
        for i, letter in enumerate(g.FINGER_LETTERS):
            x0 = i * col + col * 0.2
            x1 = x0 + col * 0.6
            self.fing.create_rectangle(x0, 0, x1, self.px(34), fill=LINE, width=0)
            bar = self.fing.create_rectangle(x0, self.px(34), x1, self.px(34), fill=GREY, width=0)
            self.fing.create_text((x0 + x1) / 2, self.px(44), text=letter, fill=MUTED, font=(UI, 9, "bold"))
            self._fing_bars.append((bar, x0, x1))

        # legend
        self.legend_title = self._section(side, "")
        self.legend = tk.Frame(side, bg=PANEL)
        self.legend.pack(fill="x", **pad)
        self._fill_legend()

        # buttons
        btns = tk.Frame(side, bg=PANEL)
        btns.pack(side="bottom", fill="x", pady=self.px(12), **pad)
        for text, cmd in (("Settings...", self.open_settings), ("Switch mode (M)", self.switch_mode)):
            tk.Button(btns, text=text, command=cmd, font=(UI, 10), relief="flat", bd=0, takefocus=0,
                      bg=LINE, fg=FG, activebackground="#454a52", activeforeground=FG,
                      cursor="hand2").pack(side="left", expand=True, fill="x", padx=2, ipady=4)

    def _section(self, parent, title):
        lbl = tk.Label(parent, text=title.upper(), font=(UI, 8, "bold"), bg=PANEL, fg=MUTED, anchor="w")
        lbl.pack(fill="x", padx=self.px(16), pady=(self.px(12), self.px(3)))
        return lbl

    def _fill_legend(self):
        for w in self.legend.winfo_children():
            w.destroy()
        mode = self.settings.mode
        self.legend_title.configure(text=f"GESTURES - {g.MODE_NAMES[mode].upper()}")
        self.legend.columnconfigure(1, weight=1)
        for row, (desc, cmd) in enumerate(g.LEGEND[mode]):
            tk.Label(self.legend, text=g.ARROWS[cmd], font=(SYMBOL, 10), bg=PANEL, fg=FG, width=2).grid(
                row=row, column=0, sticky="w")
            tk.Label(self.legend, text=desc, font=(UI, 9), bg=PANEL, fg=MUTED, anchor="w").grid(
                row=row, column=1, sticky="w")
            tk.Label(self.legend, text=g.COMMAND_NAMES[cmd].title(), font=(UI, 9), bg=PANEL, fg=FG,
                     anchor="e").grid(row=row, column=2, sticky="e")

    # ------------------------------------------------------------ keys
    def _bind_keys(self):
        r = self.root
        r.bind("<KeyPress-space>", self._space_press)
        r.bind("<KeyRelease-space>", lambda e: setattr(self, "_space_down", False))
        for key in ("m", "M"):
            r.bind(f"<KeyPress-{key}>", self.switch_mode)
        for key in ("q", "Q"):
            r.bind(f"<KeyPress-{key}>", self.quit)
        r.bind("<Escape>", lambda e: self.set_armed(False))
        r.bind("<Control-comma>", lambda e: self.open_settings())

    def _space_press(self, _event):
        if not self._space_down:              # ignore key auto-repeat while Space is held
            self._space_down = True
            self.toggle_arm()

    # ------------------------------------------------------------ refresh loop
    def _tick(self):
        frame, st, seq = self.worker.latest()
        if frame is not None and seq != self._seq:
            self._seq = seq
            self._show_frame(frame)
        self._update_panel(st)
        self.root.after(30, self._tick)

    def _show_frame(self, frame):
        bw, bh = self.video_box.winfo_width(), self.video_box.winfo_height()
        if bw < 20 or bh < 20:
            return
        fh, fw = frame.shape[:2]
        scale = min(bw / fw, bh / fh)
        size = (max(1, int(fw * scale)), max(1, int(fh * scale)))
        if size != (fw, fh):
            frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
        self._photo = ImageTk.PhotoImage(Image.fromarray(frame))
        self.video.configure(image=self._photo)

    def _set(self, widget, **kw):
        """configure() only when something changed (keeps the panel from flickering)."""
        key = id(widget)
        if self._cache.get(key) != kw:
            self._cache[key] = kw
            widget.configure(**kw)

    def _update_panel(self, st):
        s = self.settings
        res = st.result
        live = self.armed and not self.preview
        if st.cmd != "S":
            color = GREEN if live else AMBER
        else:
            color = RED if res.gesture == "Fist" else GREY

        # command
        self._set(self.arrow_lbl, text=g.ARROWS[st.cmd], fg=color)
        self._set(self.cmd_lbl, text=g.COMMAND_NAMES[st.cmd], fg=color if st.cmd != "S" else FG)
        if not st.camera_ok:
            sub = st.error
        elif not st.hand:
            sub = "No hand in view"
        else:
            sub = res.gesture
            if st.cmd == "S" and res.cmd != "S":
                sub += f"\nlooks like {g.COMMAND_NAMES[res.cmd].lower()}, " + (
                    "confidence too low" if res.confidence < s.confidence_threshold else "hold it...")
        self._set(self.gesture_lbl, text=sub)

        # confidence bar with threshold mark
        W = int(self.conf.cget("width"))
        c = res.confidence if st.hand else 0.0
        y0, y1 = self.px(18), self.px(18) + self.px(10)
        self.conf.coords(self._conf_fill, 0, y0, W * c, y1)
        self.conf.itemconfigure(self._conf_fill, fill=GREEN if c >= s.confidence_threshold else AMBER)
        tx = W * s.confidence_threshold
        self.conf.coords(self._conf_mark, tx, y0 - 3, tx, y1 + 3)
        self.conf.itemconfigure(self._conf_text,
                                text=f"Confidence {c * 100:.0f} %   (needs {s.confidence_threshold * 100:.0f} %)")

        # arm button
        if self.armed:
            self._set(self.arm_btn, text="DISARM   (Space)", bg=RED, fg="white",
                      activebackground="#c93b3b", activeforeground="white")
            note = "Preview: nothing is sent to the car" if self.preview else "Gestures are driving the car"
        else:
            self._set(self.arm_btn, text="ARM   (Space)", bg=GREEN, fg="black",
                      activebackground="#33b34e", activeforeground="black")
            note = "Car ignores gestures until armed"
        self._set(self.arm_note, text=note)

        # status rows
        self._set(self.info["Mode"], text=g.MODE_NAMES[s.mode] + "   (M)", fg=FG)
        self._set(self.info["Speed"], text=f"{s.speed} %   (fixed, change in Settings)", fg=FG)
        ls = self.link.status
        if self.preview:
            car, ccol = "preview - not sending", AMBER
        elif ls.link == "ok":
            car, ccol = f"connected  {ls.rtt_ms:.0f} ms" if ls.rtt_ms else "connected", GREEN
        elif ls.link == "nolink":
            car, ccol = "NO LINK - join Wi-Fi 'AIML-Racer'", RED
        else:
            car, ccol = f"connecting to {s.car_host}...", AMBER
        self._set(self.info["Car"], text=car, fg=ccol)
        cam = f"{st.fps:.0f} fps  (camera {s.camera_index})" if st.camera_ok else "not available"
        self._set(self.info["Camera"], text=cam, fg=FG if st.camera_ok else RED)

        self._update_battery(ls)

        # finger bars
        top, bottom = 0, self.px(34)
        for (bar, x0, x1), e in zip(self._fing_bars, res.fingers if st.hand else (0,) * 5):
            self.fing.coords(bar, x0, bottom - (bottom - top) * e, x1, bottom)
            self.fing.itemconfigure(bar, fill=GREEN if e >= 0.5 else GREY)

    def _update_battery(self, ls):
        x0, y0, x1, y1 = self._bat_geom
        connected = not self.preview and ls.link == "ok"
        pct, inner, fill = None, "", GREY
        if not connected:
            big, small = "--", "no data" if not self.preview else "preview mode"
        elif ls.usb:
            big, small = "USB", "battery switch off / USB power"
        elif ls.pct is not None:
            pct = max(0, min(100, ls.pct))
            big = f"{pct} %"
            small = f"{ls.vbat:.2f} V" if ls.vbat is not None else ""
            if pct < 15:
                fill, small = RED, small + "   LOW - recharge!"
            elif pct < 35:
                fill = AMBER
            else:
                fill = GREEN
        elif ls.vbat is not None:
            big, small = f"{ls.vbat:.2f} V", "re-upload firmware for %"
        else:
            big, small = "--", ""
        w = (x1 - x0) * (pct / 100 if pct is not None else 0)
        self.bat.coords(self._bat_fill, x0, y0, x0 + w, y1)
        self.bat.itemconfigure(self._bat_fill, fill=fill)
        if pct is None:
            inner = "?" if connected and not ls.usb else ""
        self.bat.itemconfigure(self._bat_in, text=inner)
        self.bat.itemconfigure(self._bat_pct, text=big, fill=fill if pct is not None and pct < 15 else FG)
        self.bat.itemconfigure(self._bat_v, text=small)

    # ------------------------------------------------------------ dialogs
    def open_settings(self):
        self.set_armed(False)               # never drive while settings are open
        SettingsDialog(self)

    def show_guide(self):
        parts = []
        for mode in g.MODES:
            lines = [f"  {g.ARROWS[c]}  {d}  ->  {g.COMMAND_NAMES[c]}" for d, c in g.LEGEND[mode]]
            parts.append(f"{g.MODE_NAMES[mode].upper()}\n" + "\n".join(lines))
        tips = ("Tips: keep one hand 40-80 cm from the camera with good light. In hand-shape mode the thumb's "
                "side steers - use your other hand, or turn your hand round, for the other side.")
        messagebox.showinfo("Gesture guide", "\n\n".join(parts) + "\n\n" + tips, parent=self.root)

    def show_keys(self):
        messagebox.showinfo("Keyboard shortcuts",
                            "Space      arm / disarm\nEsc          disarm (car stops)\n"
                            "M            switch gesture mode\nCtrl+,      settings\nQ            quit",
                            parent=self.root)


class SettingsDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app.root)
        self.app = app
        s = app.settings
        self.title("Settings")
        self.transient(app.root)
        self.resizable(False, False)

        self.v_mode = tk.StringVar(value=s.mode)
        self.v_speed = tk.IntVar(value=s.speed)
        self.v_conf = tk.IntVar(value=int(round(s.confidence_threshold * 100)))
        self.v_hold = tk.StringVar(value=str(s.hold_frames))
        self.v_cam = tk.StringVar(value=str(s.camera_index))
        self.v_mirror = tk.BooleanVar(value=s.mirror)
        self.v_host = tk.StringVar(value=s.car_host)
        self.v_preview = tk.BooleanVar(value=s.preview)

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)

        gest = ttk.LabelFrame(body, text="Gestures", padding=10)
        gest.pack(fill="x")
        for mode in g.MODES:
            ttk.Radiobutton(gest, text=g.MODE_NAMES[mode], value=mode, variable=self.v_mode).pack(anchor="w")
        ttk.Label(gest, text="Minimum confidence (%) - below this the car stops").pack(anchor="w", pady=(8, 0))
        tk.Scale(gest, from_=30, to=95, resolution=5, orient="horizontal", variable=self.v_conf,
                 length=320).pack(anchor="w")
        row = ttk.Frame(gest)
        row.pack(anchor="w", pady=(4, 0))
        ttk.Label(row, text="Hold a new gesture for").pack(side="left")
        ttk.Spinbox(row, from_=1, to=10, width=4, textvariable=self.v_hold).pack(side="left", padx=6)
        ttk.Label(row, text="frames before it is sent").pack(side="left")

        car = ttk.LabelFrame(body, text="Car", padding=10)
        car.pack(fill="x", pady=(10, 0))
        ttk.Label(car, text="Speed (%) - fixed while driving by gesture").pack(anchor="w")
        tk.Scale(car, from_=10, to=100, resolution=10, orient="horizontal", variable=self.v_speed,
                 length=320).pack(anchor="w")
        row = ttk.Frame(car)
        row.pack(anchor="w", pady=(4, 0))
        ttk.Label(row, text="Car address").pack(side="left")
        ttk.Entry(row, textvariable=self.v_host, width=20).pack(side="left", padx=6)
        ttk.Checkbutton(car, text="Preview mode (camera and gestures only, never talk to the car)",
                        variable=self.v_preview).pack(anchor="w", pady=(6, 0))
        if app.force_preview:
            ttk.Label(car, text="(started with --preview: preview stays on until restart)",
                      foreground="#a05a00").pack(anchor="w")

        cam = ttk.LabelFrame(body, text="Camera", padding=10)
        cam.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(cam)
        row.pack(anchor="w")
        ttk.Label(row, text="Camera number").pack(side="left")
        ttk.Spinbox(row, from_=0, to=9, width=4, textvariable=self.v_cam).pack(side="left", padx=6)
        ttk.Label(row, text="(0 = built-in webcam)").pack(side="left")
        ttk.Checkbutton(cam, text="Mirror the picture (like a selfie camera)",
                        variable=self.v_mirror).pack(anchor="w", pady=(6, 0))

        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Save", command=self.save).pack(side="right", padx=6)

        self.bind("<Return>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.focus_set()

    def save(self):
        old = self.app.settings

        def as_int(var, fallback):
            try:
                return int(var.get())
            except (ValueError, tk.TclError):
                return fallback

        new = Settings(
            mode=self.v_mode.get(),
            speed=as_int(self.v_speed, old.speed),
            confidence_threshold=as_int(self.v_conf, 60) / 100,
            hold_frames=as_int(self.v_hold, old.hold_frames),
            camera_index=as_int(self.v_cam, old.camera_index),
            mirror=self.v_mirror.get(),
            car_host=self.v_host.get(),
            preview=self.v_preview.get(),
        ).clean()
        self.destroy()
        self.app.apply_settings(new)
