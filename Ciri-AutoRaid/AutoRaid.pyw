"""
Auto Raid  — click-on-timer overlay
=====================================
Requirements:
    pip install mss pytesseract pillow pyautogui opencv-python numpy
"""

import tkinter as tk
import threading, time, re, sys, traceback, os, ctypes, json
from collections import Counter

try:
    import mss
    import pytesseract
    from PIL import Image, ImageEnhance
    import pyautogui
    import numpy as np
    import cv2
except ImportError as e:
    import tkinter.messagebox as _mb
    _mb.showerror("Missing dependency",
                  f"{e}\n\nRun:\npip install mss pytesseract pillow pyautogui opencv-python numpy")
    sys.exit(1)

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0.0   # remove built-in 0.1s delay between pyautogui calls

# ── Tesseract: prefer local copy next to this script ──────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCAL_TESS = os.path.join(_SCRIPT_DIR, "tesseract.exe")
if os.path.isfile(_LOCAL_TESS):
    pytesseract.pytesseract.tesseract_cmd = _LOCAL_TESS

# ── config ────────────────────────────────────────────────────────────────────
CONFIG_PATH = os.path.join(_SCRIPT_DIR, "overlay_config.json")

def _load_config():
    try:
        with open(CONFIG_PATH) as f: return json.load(f)
    except: return {}

def _save_config(data):
    try:
        with open(CONFIG_PATH, "w") as f: json.dump(data, f, indent=2)
    except: pass

# ── palette  (matches site screenshot) ────────────────────────────────────────
BG         = "#0d0f18"    # deepest background
PANEL      = "#13151f"    # card / panel
PANEL2     = "#181b27"    # slightly lighter panel
BORDER     = "#23263a"    # subtle card border
BORDER_HI  = "#343760"    # hover / active border
ACCENT     = "#7c6af7"    # purple  (site primary)
ACCENT2    = "#f0a500"    # gold    (site secondary)
GREEN      = "#2ecc71"
RED        = "#e74c3c"
FG         = "#cdd2e8"    # primary text
FG2        = "#6a7090"    # muted text
FG3        = "#333756"    # very dim

TRANSP_KEY = "#010203"    # colour-key for window transparency

# Per-slot accent colours matching the site's coloured card outlines
SLOT_COLS  = ["#c8a0ff", "#f0a500", "#e74c3c", "#4d96ff", "#2ecc71"]
ZONE_COLS  = [ACCENT] + SLOT_COLS   # index 0 = timer zone

TITLE_H  = 44
FOOTER_H = 140
BORDER_W = 1

# Slots start with no rows by default — user adds rows via "+ ADD TIME"
DEFAULT_SLOT_TIMES = [[]] * 5


def _default_zones():
    return [
        [0.38, 0.06, 0.50, 0.11],
        [0.04, 0.85, 0.15, 0.99],
        [0.21, 0.85, 0.32, 0.99],
        [0.37, 0.85, 0.48, 0.99],
        [0.53, 0.85, 0.64, 0.99],
        [0.69, 0.85, 0.80, 0.99],
        [0.84, 0.85, 0.95, 0.99],
    ]

# ── Win32 helpers ─────────────────────────────────────────────────────────────
def _make_layered(hwnd):
    try:
        GWL_EXSTYLE = -20; WS_EX_LAYERED = 0x80000
        s = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, s | WS_EX_LAYERED)
    except: pass

# ── time helpers ──────────────────────────────────────────────────────────────
def _str_to_cs(s):
    """'MM:SS:cs' string → centiseconds int.  Returns None on bad input."""
    s = s.strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})", s)
    if m:
        return int(m.group(1))*6000 + int(m.group(2))*100 + int(m.group(3))
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m:
        return int(m.group(1))*6000 + int(m.group(2))*100
    return None

def _cs_to_str(cs):
    mm, rem = divmod(cs, 6000)
    ss, ms  = divmod(rem, 100)
    return f"{mm:02d}:{ss:02d}:{ms:02d}"

# ── ZoneHandle ────────────────────────────────────────────────────────────────
class ZoneHandle:
    RESIZE_EDGE = 14

    def __init__(self, canvas, idx, label, colour, fracs, on_change):
        self.canvas    = canvas
        self.idx       = idx
        self.colour    = colour
        self.fracs     = list(fracs)
        self.on_change = on_change
        self._mode     = None
        self._ox = self._oy = 0
        self._of = None
        tag = f"zh{idx}"
        self.tag = tag

        self.rect = canvas.create_rectangle(0,0,1,1,
                        outline=colour, width=2,
                        fill=colour, stipple="gray25", tags=tag)
        self.text = canvas.create_text(0,0, text=label, fill=colour,
                        font=("Segoe UI", 8, "bold"), anchor="nw", tags=tag)
        self.knob = canvas.create_rectangle(0,0,1,1,
                        fill=colour, outline="", tags=tag)

        canvas.tag_bind(tag, "<ButtonPress-1>", self._press)
        canvas.tag_bind(tag, "<Enter>", lambda e: canvas.config(cursor="fleur"))
        canvas.tag_bind(tag, "<Leave>", lambda e: (
            canvas.config(cursor="") if self._mode is None else None))

    def redraw(self, cw, ch):
        f = self.fracs
        x1,y1 = int(f[0]*cw), int(f[1]*ch)
        x2,y2 = int(f[2]*cw), int(f[3]*ch)
        e = self.RESIZE_EDGE
        self.canvas.coords(self.rect, x1, y1, x2, y2)
        self.canvas.coords(self.text, x1+5, y1+4)
        self.canvas.coords(self.knob, x2-e, y2-e, x2, y2)

    def _cw_ch(self):
        return self.canvas.winfo_width(), self.canvas.winfo_height()

    def _press(self, e):
        cw, ch = self._cw_ch()
        f = self.fracs
        nx = e.x >= f[2]*cw - self.RESIZE_EDGE
        ny = e.y >= f[3]*ch - self.RESIZE_EDGE
        self._mode = ("resize-se" if nx and ny else
                      "resize-e"  if nx else
                      "resize-s"  if ny else "move")
        self._ox, self._oy = e.x, e.y
        self._of = list(self.fracs)
        self.canvas.config(cursor="fleur")

    def motion(self, e):
        if not self._mode: return
        cw, ch = self._cw_ch()
        dx = (e.x - self._ox) / cw
        dy = (e.y - self._oy) / ch
        f  = list(self._of)
        if self._mode == "move":
            W = f[2]-f[0]; H = f[3]-f[1]
            f[0] = max(0.0, min(1.0-W, f[0]+dx))
            f[1] = max(0.0, min(1.0-H, f[1]+dy))
            f[2] = f[0]+W; f[3] = f[1]+H
        elif self._mode == "resize-se":
            f[2] = max(f[0]+0.02, min(1.0, f[2]+dx))
            f[3] = max(f[1]+0.02, min(1.0, f[3]+dy))
        elif self._mode == "resize-e":
            f[2] = max(f[0]+0.02, min(1.0, f[2]+dx))
        elif self._mode == "resize-s":
            f[3] = max(f[1]+0.02, min(1.0, f[3]+dy))
        self.fracs = f
        self.redraw(cw, ch)

    def release(self, e):
        if self._mode is None: return
        self.on_change(self.idx, self.fracs)
        self._mode = None
        self.canvas.config(cursor="")

    def set_visible(self, v):
        s = "normal" if v else "hidden"
        for item in (self.rect, self.text, self.knob):
            self.canvas.itemconfigure(item, state=s)


# ── SlotColumn ────────────────────────────────────────────────────────────────
class SlotColumn(tk.Frame):
    """One slot card.  Times stored/displayed as 'MM:SS:cs' strings."""

    def __init__(self, parent, slot_idx, colour, initial_times):
        super().__init__(parent, bg=PANEL2, bd=0,
                         highlightbackground=colour, highlightthickness=1)
        self.slot_idx   = slot_idx
        self.colour     = colour
        self._time_rows = []

        # ── header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=PANEL2)
        hdr.pack(fill="x", padx=4, pady=(5, 2))

        # coloured top-bar accent line
        bar_line = tk.Frame(self, bg=colour, height=2)
        bar_line.place(x=0, y=0, relwidth=1)

        # dot + label
        dot = tk.Canvas(hdr, bg=PANEL2, width=8, height=8,
                        highlightthickness=0)
        dot.create_oval(1,1,7,7, fill=colour, outline="")
        dot.pack(side="left", padx=(0,4))

        tk.Label(hdr, text=f"SLOT  {slot_idx+1}", bg=PANEL2, fg=FG,
                 font=("Segoe UI", 7, "bold")).pack(side="left")

        self.en_var = tk.BooleanVar(value=True)
        tk.Checkbutton(hdr, variable=self.en_var,
                       bg=PANEL2, fg=colour,
                       selectcolor=BG, activebackground=PANEL2,
                       relief="flat", bd=0, padx=0, pady=0,
                       cursor="hand2").pack(side="right")

        # ── time list ─────────────────────────────────────────────────────────
        self.list_frame = tk.Frame(self, bg=PANEL2)
        self.list_frame.pack(fill="both", expand=True, padx=4)

        # ── add button ────────────────────────────────────────────────────────
        add_btn = tk.Frame(self, bg=PANEL2)
        add_btn.pack(fill="x", padx=4, pady=(1,4))
        tk.Button(add_btn, text="+ ADD TIME", command=self._add_row,
                  bg=PANEL, fg=colour, relief="flat", bd=0,
                  font=("Segoe UI", 7, "bold"), cursor="hand2",
                  pady=2, activebackground=BORDER_HI,
                  activeforeground=colour).pack(fill="x")

        for t in initial_times:
            self._add_row(t)

    def _add_row(self, initial_text=""):
        row = tk.Frame(self.list_frame, bg=PANEL2)
        row.pack(fill="x", pady=1)

        val_var = tk.StringVar(value=initial_text)

        # Single entry with auto-formatting: digits only, auto-inserts colons
        # at positions 2 and 4, max 8 chars (MM:SS:cs)
        ent = tk.Entry(row, textvariable=val_var, width=9,
                       bg=PANEL, fg=self.colour,
                       insertbackground=self.colour,
                       font=("Segoe UI", 8, "bold"),
                       relief="flat", bd=0,
                       highlightthickness=1,
                       highlightbackground=BORDER,
                       highlightcolor=self.colour,
                       justify="center")
        ent.pack(side="left", fill="x", expand=True, ipady=3)

        # We track the raw digits ourselves rather than parsing the widget's
        # cursor/selection state — typing is always append-to-end and
        # backspace always removes the last digit, like a PIN-code field.
        # This avoids bugs where stray cursor positions (e.g. from clicking
        # mid-field) cause new digits to land in the wrong place and the
        # value reading backwards (e.g. typing "15" producing "51").
        digits_state = [re.sub(r"[^0-9]", "", initial_text)[:6]]

        PLACEHOLDER = "Time"

        def _render():
            digits = digits_state[0]
            if not digits:
                # Show greyed-out placeholder; this is never parsed as a
                # real trigger (get_triggers checks digits_state directly).
                ent.config(fg=FG3)
                val_var.set(PLACEHOLDER)
                ent.icursor(0)
                return
            ent.config(fg=self.colour)
            parts = [digits[i:i+2] for i in range(0, len(digits), 2)]
            val_var.set(":".join(parts))
            ent.icursor(tk.END)

        def _on_key(event, e=ent):
            # Allow Tab/Return through for normal focus navigation.
            if event.keysym in ("Tab", "Return", "KP_Enter"):
                return
            if event.keysym in ("BackSpace", "Delete"):
                digits_state[0] = digits_state[0][:-1]
                _render()
                return "break"
            # Block everything else (including Left/Right/Home/End — this
            # field is append/backspace only, by design) except digits.
            if not event.char or not event.char.isdigit():
                return "break"
            if len(digits_state[0]) < 6:
                digits_state[0] += event.char
                _render()
            return "break"

        def _on_focus_in(event, e=ent):
            e.icursor(tk.END if digits_state[0] else 0)

        ent.bind("<Key>", _on_key)
        ent.bind("<FocusIn>", _on_focus_in)
        _render()

        entry = {"frame": row, "val": val_var, "digits": digits_state}

        def remove(en=entry, r=row):
            self._time_rows.remove(en)
            r.destroy()

        tk.Button(row, text="×", command=remove,
                  bg=PANEL2, fg=FG3, relief="flat",
                  font=("Segoe UI", 9), cursor="hand2", bd=0, padx=3,
                  activeforeground=RED,
                  activebackground=PANEL2).pack(side="left")

        self._time_rows.append(entry)

    def get_triggers(self):
        if not self.en_var.get(): return []
        out = []
        for e in self._time_rows:
            if not e["digits"][0]:
                continue  # empty / still showing placeholder — skip
            cs = _str_to_cs(e["val"].get())
            if cs is not None:
                out.append(cs)
        return out


# ── App ───────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Auto Raid")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.97)
        self.configure(bg=BG)

        cfg = _load_config()
        self.geometry(cfg.get("geometry", "1100x660+60+60"))
        self.zones     = cfg.get("zones", _default_zones())
        self.running   = False
        self.edit_mode = False
        self.fired     = set()
        self._handles  = []
        self._slots    = []

        self._build()
        self.attributes("-transparentcolor", TRANSP_KEY)
        self.bind("<Configure>", lambda e: self.after_idle(self._on_resize))
        self.after(300, lambda: _make_layered(
            ctypes.windll.user32.GetForegroundWindow()))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self._show_disclaimer)

    # ── disclaimer banner ─────────────────────────────────────────────────────
    def _show_disclaimer(self):
        """Red banner overlay with accuracy warning — dismissible via ✕ button."""
        banner = tk.Frame(self, bg="#7b0e0e", bd=0)
        banner.place(relx=0, rely=0, relwidth=1, anchor="nw",
                     y=TITLE_H)   # sit flush below the title bar

        # left red accent stripe
        tk.Frame(banner, bg="#e74c3c", width=4).pack(side="left", fill="y")

        # icon
        tk.Label(banner, text="⚠", bg="#7b0e0e", fg="#ffb3b3",
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=(8, 4), pady=6)

        # message block
        msg_frame = tk.Frame(banner, bg="#7b0e0e")
        msg_frame.pack(side="left", fill="both", expand=True, pady=6)

        tk.Label(msg_frame,
                 text="ACCURACY DISCLAIMER — Click timings are approximate.",
                 bg="#7b0e0e", fg="#ffe0e0",
                 font=("Segoe UI", 8, "bold"),
                 anchor="w").pack(fill="x")

        tk.Label(msg_frame,
                 text="Expected range per trigger:  +0.05 s  |  +0.03 s  |  Exact  |  −0.01 s  |  −0.03 s",
                 bg="#7b0e0e", fg="#ffb3b3",
                 font=("Segoe UI", 7),
                 anchor="w").pack(fill="x")

        tk.Label(msg_frame,
                 text="OCR latency, system load, and game framerate all affect precision. Fine-tune your trigger times if needed.",
                 bg="#7b0e0e", fg="#cc8888",
                 font=("Segoe UI", 7),
                 anchor="w").pack(fill="x")

        # close button
        def _dismiss():
            banner.place_forget()
            banner.destroy()

        tk.Button(banner, text="✕", command=_dismiss,
                  bg="#7b0e0e", fg="#ffb3b3", relief="flat", bd=0,
                  font=("Segoe UI", 10, "bold"), padx=10,
                  cursor="hand2",
                  activebackground="#9b1a1a",
                  activeforeground="white").pack(side="right", padx=(0, 6), pady=4)

    # ── build ─────────────────────────────────────────────────────────────────
    def _build(self):
        self._build_titlebar()
        self.canvas = tk.Canvas(self, bg=TRANSP_KEY,
                                highlightthickness=0, cursor="")
        self.canvas.pack(fill="both", expand=True)

        zone_labels = ["Timer","Slot 1","Slot 2","Slot 3","Slot 4","Slot 5"]
        for i in range(6):
            fracs = self.zones[i] if i < len(self.zones) else [0.05,0.8,0.15,0.99]
            h = ZoneHandle(self.canvas, i, zone_labels[i],
                           ZONE_COLS[i], fracs, self._zone_changed)
            self._handles.append(h)
            h.set_visible(False)

        self.canvas.bind("<B1-Motion>",       self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)

        self._build_footer()
        self._draw_border()

    # ── title bar ─────────────────────────────────────────────────────────────
    def _build_titlebar(self):
        cfg = _load_config()
        bar = tk.Frame(self, bg=PANEL, height=TITLE_H)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        self._bar = bar

        # thin accent line along the very top
        tk.Frame(bar, bg=ACCENT, height=2).place(x=0, y=0, relwidth=1)

        # ── left side ─────────────────────────────────────────────────────────
        # icon dot
        dot_c = tk.Canvas(bar, bg=PANEL, width=10, height=10,
                          highlightthickness=0)
        dot_c.create_oval(1,1,9,9, fill=ACCENT, outline="")
        dot_c.pack(side="left", padx=(14,4), pady=4)

        lbl_title = tk.Label(bar, text="AUTO RAID", bg=PANEL, fg=FG,
                             font=("Segoe UI", 10, "bold"))
        lbl_title.pack(side="left", padx=(0,2))

        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", pady=8)

        # EDIT ZONES
        self.btn_edit = tk.Button(
            bar, text="EDIT ZONES", command=self._toggle_edit,
            bg=PANEL, fg=FG2, relief="flat", bd=0,
            font=("Segoe UI", 8, "bold"), padx=12, pady=0,
            cursor="hand2", activebackground=BORDER_HI, activeforeground=FG)
        self.btn_edit.pack(side="left", padx=2, pady=8)

        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", pady=8)

        # START / STOP
        self.btn_run = tk.Button(
            bar, text="▶  START", command=self._toggle_run,
            bg=ACCENT, fg="white", relief="flat", bd=0,
            font=("Segoe UI", 8, "bold"), padx=14, pady=0,
            cursor="hand2", activebackground="#6a5ae0", activeforeground="white")
        self.btn_run.pack(side="left", padx=8, pady=8)

        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", pady=8)

        # Timer readout
        self.lbl_timer = tk.Label(bar, text="--:--:--", bg=PANEL, fg=ACCENT,
                                  font=("Consolas", 14, "bold"))
        self.lbl_timer.pack(side="left", padx=12)

        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", pady=8)

        # Status
        self.lbl_status = tk.Label(bar, text="Ready", bg=PANEL, fg=FG2,
                                   font=("Segoe UI", 8))
        self.lbl_status.pack(side="left", padx=8)

        # ── right side ────────────────────────────────────────────────────────
        btn_close = tk.Button(
            bar, text="✕", command=self._on_close,
            bg=PANEL, fg=FG2, relief="flat", bd=0,
            font=("Segoe UI", 10), padx=12, pady=0,
            cursor="hand2", activebackground=RED, activeforeground="white")
        btn_close.pack(side="right", pady=8)

        # drag targets
        for w in [bar, lbl_title, dot_c, self.lbl_timer, self.lbl_status]:
            w.bind("<ButtonPress-1>",  self._drag_start)
            w.bind("<B1-Motion>",      self._drag_move)
            w.bind("<ButtonPress-3>",  self._resize_start)
            w.bind("<B3-Motion>",      self._resize_move)

    # ── footer (slot cards) ───────────────────────────────────────────────────
    def _build_footer(self):
        foot = tk.Frame(self, bg=PANEL, height=FOOTER_H)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)
        self._foot = foot

        # top separator
        tk.Frame(foot, bg=BORDER, height=1).pack(fill="x")

        # section label
        lbl_row = tk.Frame(foot, bg=PANEL)
        lbl_row.pack(fill="x", padx=10, pady=(5,2))
        tk.Label(lbl_row, text="CLICK TRIGGERS", bg=PANEL, fg=FG2,
                 font=("Segoe UI", 7, "bold")).pack(side="left")
        tk.Label(lbl_row, text="MM:SS:cs  per slot", bg=PANEL, fg=FG3,
                 font=("Segoe UI", 7)).pack(side="left", padx=(8,0))

        inner = tk.Frame(foot, bg=PANEL)
        inner.pack(fill="both", expand=True, padx=8, pady=(0,6))
        for i in range(5):
            inner.columnconfigure(i, weight=1)

        self._slots = []
        for i in range(5):
            init = DEFAULT_SLOT_TIMES[i] if i < len(DEFAULT_SLOT_TIMES) else []
            col = SlotColumn(inner, i, SLOT_COLS[i], init)
            col.grid(row=0, column=i, sticky="nsew", padx=3)
            self._slots.append(col)

    # ── border ────────────────────────────────────────────────────────────────
    def _draw_border(self):
        self.canvas.delete("border")
        w = self.canvas.winfo_width()  or 1000
        h = self.canvas.winfo_height() or 400
        self.canvas.create_rectangle(1, 1, w-1, h-1,
                                     outline=BORDER_HI, width=1, tags="border")

    def _on_resize(self):
        self._draw_border()
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        for h in self._handles:
            h.redraw(cw, ch)

    # ── window drag / resize ──────────────────────────────────────────────────
    def _drag_start(self, e):
        self._dx = e.x_root - self.winfo_x()
        self._dy = e.y_root - self.winfo_y()

    def _drag_move(self, e):
        self.geometry(f"+{e.x_root-self._dx}+{e.y_root-self._dy}")

    def _resize_start(self, e):
        self._rx = e.x_root; self._ry = e.y_root
        self._rw = self.winfo_width(); self._rh = self.winfo_height()

    def _resize_move(self, e):
        nw = max(700, self._rw + (e.x_root - self._rx))
        nh = max(400, self._rh + (e.y_root - self._ry))
        self.geometry(f"{nw}x{nh}")

    # ── canvas drag dispatch ──────────────────────────────────────────────────
    def _canvas_drag(self, e):
        for h in self._handles:
            if h._mode is not None:
                h.motion(e); return

    def _canvas_release(self, e):
        for h in self._handles:
            if h._mode is not None:
                h.release(e); return

    # ── edit zones ────────────────────────────────────────────────────────────
    def _toggle_edit(self):
        self.edit_mode = not self.edit_mode
        for h in self._handles:
            h.set_visible(self.edit_mode)
        if self.edit_mode:
            self.btn_edit.config(text="✓ DONE", bg=ACCENT, fg="white",
                                 activebackground="#6a5ae0")
            self.lbl_status.config(text="Drag zone to move  ·  bottom-right corner to resize")
            self._on_resize()
        else:
            self.btn_edit.config(text="EDIT ZONES", bg=PANEL, fg=FG2,
                                 activebackground=BORDER_HI)
            self.lbl_status.config(text="Zones saved")
            self._save_cfg()
        self._draw_border()

    def _zone_changed(self, idx, fracs):
        while len(self.zones) <= idx:
            self.zones.append([0.0,0.0,0.1,0.1])
        self.zones[idx] = list(fracs)

    # ── persistence ───────────────────────────────────────────────────────────
    def _save_cfg(self):
        geom = f"{self.winfo_width()}x{self.winfo_height()}+{self.winfo_x()}+{self.winfo_y()}"
        _save_config({"geometry": geom, "zones": self.zones})

    def _on_close(self):
        self._save_cfg()
        self.destroy()

    # ── screen coords ─────────────────────────────────────────────────────────
    def _capture_rect(self):
        return (self.winfo_rootx() + BORDER_W,
                self.winfo_rooty() + TITLE_H + BORDER_W,
                self.winfo_width()         - 2*BORDER_W,
                self.canvas.winfo_height() - 2*BORDER_W)

    def _abs_mss_zone(self, frac):
        l,t,w,h = self._capture_rect()
        x1=int(l+frac[0]*w); y1=int(t+frac[1]*h)
        x2=int(l+frac[2]*w); y2=int(t+frac[3]*h)
        return {"left":x1,"top":y1,"width":max(1,x2-x1),"height":max(1,y2-y1)}

    def _slot_center(self, zi):
        if zi >= len(self.zones): return None
        f = self.zones[zi]
        l,t,w,h = self._capture_rect()
        return int(l+((f[0]+f[2])/2)*w), int(t+((f[1]+f[3])/2)*h)

    # ── OCR ───────────────────────────────────────────────────────────────────
    @staticmethod
    def _parse_time(text):
        m = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", text)
        if m:
            mm = int(m.group(1))
            ss = int(m.group(2))
            # Units digit of centiseconds updates faster than OCR can capture —
            # only the tens digit is reliable (e.g. "31" → treat as 30).
            cs_tens = (int(m.group(3)) // 10) * 10
            return mm*6000 + ss*100 + cs_tens
        m = re.search(r"(\d{1,2}):(\d{2})", text)
        if m:
            return int(m.group(1))*6000 + int(m.group(2))*100
        return None

    def _preprocess_images(self, rgb):
        """Return the 2 most reliable image variants for OCR."""
        r, g, b = rgb[:,:,0], rgb[:,:,1], rgb[:,:,2]

        # Strategy A — white pixel isolation (best for coloured/flashing BG)
        brightness  = r.astype(np.uint16) + g + b
        color_range = (np.maximum(np.maximum(r,g),b).astype(np.int16) -
                       np.minimum(np.minimum(r,g),b))
        white_mask = ((brightness > 570) & (color_range < 40)).astype(np.uint8) * 255
        wm = cv2.resize(white_mask,
                        (white_mask.shape[1]*4, white_mask.shape[0]*4),
                        interpolation=cv2.INTER_NEAREST)
        wm = cv2.dilate(wm, np.ones((2,2), np.uint8), iterations=1)
        wm_inv = cv2.bitwise_not(wm)

        # Strategy B — inverted greyscale threshold (fast fallback)
        grey    = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        grey_up = cv2.resize(grey, (grey.shape[1]*4, grey.shape[0]*4),
                             interpolation=cv2.INTER_LANCZOS4)
        _, b_fixed = cv2.threshold(grey_up, 160, 255, cv2.THRESH_BINARY)

        # Only 2 strategies — fastest path with best coverage
        return [wm_inv, cv2.bitwise_not(b_fixed)]

    @staticmethod
    def _ocr_one(arr):
        """Run Tesseract on a single image array. Returns (value, confidence)."""
        cfg = "--psm 7 -c tessedit_char_whitelist=0123456789:"
        try:
            data = pytesseract.image_to_data(
                Image.fromarray(arr), config=cfg,
                output_type=pytesseract.Output.DICT)
            texts = [t for t in data["text"] if t.strip()]
            confs = [c for t, c in zip(data["text"], data["conf"])
                     if t.strip() and c >= 0]
            if not texts: return None, 0
            combined = "".join(texts)
            conf_avg = sum(confs) / len(confs) if confs else 0
            return combined, conf_avg
        except Exception:
            return None, 0

    def _read_timer(self):
        zone = self._abs_mss_zone(self.zones[0])
        # Reuse the shared mss instance (created in _loop) to avoid
        # open/close overhead on every read; _sct is set by the reader thread
        if not self._sct:
            return None
        shot = self._sct.grab(zone)
        rgb = np.array(Image.frombytes("RGB", shot.size, shot.rgb))
        images = self._preprocess_images(rgb)

        # Run only the 2 best strategies in parallel
        results = [None] * len(images)
        def _run(idx, arr):
            text, conf = self._ocr_one(arr)
            if text:
                val = self._parse_time(text)
                results[idx] = (val, conf)

        threads = [threading.Thread(target=_run, args=(i, arr), daemon=True)
                   for i, arr in enumerate(images)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=0.25)

        scored = [(val, conf) for val, conf in results
                  if val is not None and conf is not None]
        if not scored: return None

        tally = {}
        for val, conf in scored:
            tally[val] = tally.get(val, 0) + conf
        return max(tally, key=tally.get)

    # ── run / loop ────────────────────────────────────────────────────────────
    def _toggle_run(self):
        if self.running:
            self.running = False
            self.btn_run.config(text="▶  START", bg=ACCENT,
                                activebackground="#6a5ae0")
            self.lbl_status.config(text="Stopped")
        else:
            self.running = True
            self.fired.clear()
            self._read_in_flight = False
            # Pre-warm a pool of click workers — each sits idle on an Event,
            # fires the moment it's set.  Zero thread-spawn latency at click time.
            self._click_queue = []
            for _ in range(5):
                ev = threading.Event()
                slot_holder = [None]
                def _worker(event=ev, holder=slot_holder):
                    while True:
                        event.wait()
                        event.clear()
                        zi = holder[0]
                        if zi is None:
                            break
                        pt = self._slot_center(zi)
                        if pt:
                            pyautogui.click(*pt)
                self._click_queue.append((ev, slot_holder,
                    threading.Thread(target=_worker, daemon=True)))
            for _, _, t in self._click_queue:
                t.start()
            self.btn_run.config(text="■  STOP", bg=RED,
                                activebackground="#c0392b")
            self.lbl_status.config(text="Running…")
            threading.Thread(target=self._loop, daemon=True).start()

    def _collect_triggers(self):
        pairs = []
        for i, slot in enumerate(self._slots):
            for t in slot.get_triggers():
                pairs.append((i+1, t))
        return pairs

    def _loop(self):
        """
        Decoupled read/fire loop with wall-clock interpolation + freeze detection
        + auto-stop on lost signal.

        Freeze detection: 3+ identical OCR reads = game timer paused (ult anim).
        Interpolator holds still. Resumes the instant OCR sees a new value.

        Auto-stop: if OCR returns None for 4 consecutive seconds (loading
        screen, between raids) the loop stops itself and resets UI to Ready.
        """
        # Fire FIRE_OFFSET_CS hundredths-of-a-second before the exact typed
        # value, to compensate the measured end-to-end click latency
        # (OCR -> decision -> worker thread -> pyautogui). Tune in small
        # steps if you measure a different consistent lag.
        FIRE_OFFSET_CS = 17
        FREEZE_READS   = 3     # identical reads before declaring freeze

        triggers = self._collect_triggers()
        self._read_lock = threading.Lock()
        self._sct       = None

        self._anchor_cs   = None
        self._anchor_wall = None
        self._frozen      = False
        self._has_signal  = False  # True when OCR is actively reading numbers

        def _reader():
            prev_cs           = None
            repeat_count      = 0
            signal_was_lost   = True   # True until we get a first good reading
            reset_candidate   = None   # value seen once during a big jump-up
            reset_candidate_n = 0
            regain_candidate  = None   # value seen once while reacquiring signal
            regain_candidate_n = 0

            with mss.mss() as sct:
                self._sct = sct
                while self.running:
                    try:
                        cs = self._read_timer()
                        if cs is not None:
                            if signal_was_lost:
                                # Signal was lost — don't trust a single read,
                                # since a stray OCR misread on a blank/changed
                                # screen could fake a "regain" with garbage and
                                # resume the countdown from nonsense. Require
                                # two consistent reads before resyncing.
                                if regain_candidate is not None and abs(cs - regain_candidate) <= 50:
                                    regain_candidate_n += 1
                                else:
                                    regain_candidate   = cs
                                    regain_candidate_n = 1

                                if regain_candidate_n < 2:
                                    continue
                                # Confirmed — a fresh timer has appeared after
                                # a real dropout (almost always a new run/raid
                                # starting). Re-arm all triggers so they can
                                # fire again on this new countdown.
                                self.fired.clear()
                                regain_candidate   = None
                                regain_candidate_n = 0

                            elif prev_cs is not None and abs(cs - prev_cs) > 500:
                                # Large jump while signal was already locked on.
                                # Could be a single-frame OCR misread, OR the
                                # in-game timer genuinely restarting (new round).
                                # Require the same jumped-to value to repeat
                                # before trusting it, to filter out noise.
                                if cs > prev_cs:
                                    if reset_candidate is not None and abs(cs - reset_candidate) <= 50:
                                        reset_candidate_n += 1
                                    else:
                                        reset_candidate   = cs
                                        reset_candidate_n = 1

                                    if reset_candidate_n >= 2:
                                        # Confirmed genuine reset — re-arm all
                                        # triggers for the new countdown.
                                        self.fired.clear()
                                        reset_candidate   = None
                                        reset_candidate_n = 0
                                    else:
                                        continue
                                else:
                                    # Jump downward this large is almost
                                    # certainly OCR noise — ignore it.
                                    continue
                            else:
                                reset_candidate   = None
                                reset_candidate_n = 0

                            # If we just regained signal after a confirmed
                            # dropout, accept this reading as the new anchor
                            # (resync) instead of comparing it against a
                            # now-stale prev_cs.
                            signal_was_lost = False
                            now = time.perf_counter()

                            if cs == prev_cs:
                                repeat_count += 1
                            else:
                                repeat_count = 0
                                with self._read_lock:
                                    self._anchor_cs   = cs
                                    self._anchor_wall = now
                                    self._frozen      = False
                                    self._has_signal  = True
                                prev_cs = cs

                            if repeat_count >= FREEZE_READS:
                                with self._read_lock:
                                    if not self._frozen:
                                        self._anchor_cs   = cs
                                        self._anchor_wall = now
                                        self._frozen      = True
                            elif 0 < repeat_count < FREEZE_READS:
                                with self._read_lock:
                                    if not self._frozen:
                                        self._anchor_cs   = cs
                                        self._anchor_wall = now
                        else:
                            # No number read — signal lost instantly
                            signal_was_lost     = True
                            prev_cs              = None
                            repeat_count         = 0
                            reset_candidate      = None
                            reset_candidate_n    = 0
                            regain_candidate     = None
                            regain_candidate_n   = 0
                            with self._read_lock:
                                self._has_signal = False

                    except Exception:
                        pass

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        time.sleep(0.15)

        last_ui_update     = 0.0
        UI_UPDATE_INTERVAL = 0.04   # 25Hz — plenty smooth for a human-readable
                                    # label; the fire-check below still runs
                                    # at full speed regardless of this
        last_shown_no_signal = False

        while self.running:
            try:
                with self._read_lock:
                    anchor_cs   = self._anchor_cs
                    anchor_wall = self._anchor_wall
                    frozen      = self._frozen
                    has_signal  = self._has_signal

                now_wall = time.perf_counter()
                due_ui   = (now_wall - last_ui_update) >= UI_UPDATE_INTERVAL

                if not has_signal:
                    # Lost OCR signal — clear display immediately (not throttled,
                    # so the clear is never delayed/eaten), keep running.
                    if not last_shown_no_signal:
                        self.lbl_timer.config(text="??:??:??")
                        last_shown_no_signal = True
                        last_ui_update = now_wall
                elif anchor_cs is not None:
                    last_shown_no_signal = False

                    if frozen:
                        cs = anchor_cs
                    else:
                        elapsed_cs = int((now_wall - anchor_wall) * 100)
                        cs = max(0, anchor_cs - elapsed_cs)

                    # Display label only needs to repaint ~25x/sec — throttling
                    # this keeps the tight loop below from hammering the Tk
                    # widget (which isn't thread-safe) on every 5ms tick.
                    if due_ui:
                        self.lbl_timer.config(text=_cs_to_str(cs))
                        last_ui_update = now_wall

                    # Fire check always runs at full loop speed (every tick),
                    # independent of the UI throttle above, for precise timing.
                    for (zi, trig) in triggers:
                        key = (zi, trig)
                        if key not in self.fired:
                            if cs <= trig + FIRE_OFFSET_CS:
                                self.fired.add(key)
                                self.lbl_status.config(
                                    text=f"Slot {zi}  fired at  {_cs_to_str(cs)}")
                                self._click_slot(zi)

            except Exception as ex:
                self.lbl_status.config(text=f"Err: {str(ex)[:60]}")

            time.sleep(0.005)  # 200Hz fire-check tick — display label is
                               # throttled separately above to stay Tk-safe

    def _click_slot(self, zi):
        # Use next available pre-warmed worker — no thread spawn cost
        for ev, holder, _ in self._click_queue:
            if not ev.is_set():
                holder[0] = zi
                ev.set()
                return
        # Fallback: all workers busy, spawn one (rare)
        pt = self._slot_center(zi)
        if pt:
            pyautogui.click(*pt)


if __name__ == "__main__":
    try:
        App().mainloop()
    except Exception:
        log = os.path.join(_SCRIPT_DIR, "autoraid_error.log")
        with open(log, "w") as f: f.write(traceback.format_exc())
        try:
            import tkinter.messagebox as mb
            mb.showerror("Auto Raid — Crash", f"Details saved to:\n{log}")
        except: pass
