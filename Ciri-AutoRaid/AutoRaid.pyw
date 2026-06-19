"""
Auto Raid  — click-on-timer overlay
=====================================
Requirements:
    pip install mss pytesseract pillow pyautogui opencv-python numpy
"""

import tkinter as tk
import tkinter.simpledialog as simpledialog
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
pyautogui.PAUSE    = 0.0

# ── suppress tesseract's flashing console window on Windows ───────────────────
# pytesseract shells out to tesseract.exe for every OCR call. On Windows, every
# subprocess launch of a console exe briefly flashes a console window unless we
# explicitly tell it to start hidden. This patches subprocess.Popen globally
# (safe — only affects window visibility, not behaviour) so those popups stop.
if os.name == "nt":
    import subprocess as _subprocess
    _orig_popen_init = _subprocess.Popen.__init__
    def _hidden_popen_init(self, *args, **kwargs):
        si = kwargs.get("startupinfo") or _subprocess.STARTUPINFO()
        si.dwFlags |= _subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = si
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
        _orig_popen_init(self, *args, **kwargs)
    _subprocess.Popen.__init__ = _hidden_popen_init

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCAL_TESS = os.path.join(_SCRIPT_DIR, "tesseract.exe")
if os.path.isfile(_LOCAL_TESS):
    pytesseract.pytesseract.tesseract_cmd = _LOCAL_TESS

CONFIG_PATH = os.path.join(_SCRIPT_DIR, "overlay_config.json")

def _load_config():
    try:
        with open(CONFIG_PATH) as f: return json.load(f)
    except: return {}

def _save_config(data):
    try:
        with open(CONFIG_PATH, "w") as f: json.dump(data, f, indent=2)
    except: pass

# ── palette ───────────────────────────────────────────────────────────────────
BG         = "#0d0f18"
PANEL      = "#13151f"
PANEL2     = "#181b27"
PANEL3     = "#0f1120"    # sidebar background — slightly deeper than BG
BORDER     = "#23263a"
BORDER_HI  = "#343760"
ACCENT     = "#7c6af7"
ACCENT2    = "#f0a500"
GREEN      = "#2ecc71"
RED        = "#e74c3c"
FG         = "#cdd2e8"
FG2        = "#6a7090"
FG3        = "#333756"

TRANSP_KEY = "#010203"

SLOT_COLS  = ["#c8a0ff", "#f0a500", "#e74c3c", "#4d96ff", "#2ecc71"]
ZONE_COLS  = [ACCENT] + SLOT_COLS

TITLE_H      = 44
ROW_H        = 22
BORDER_W     = 1

SIDEBAR_W    = 190   # width of the profile library panel

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

def _make_layered(hwnd):
    try:
        GWL_EXSTYLE = -20; WS_EX_LAYERED = 0x80000
        s = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, s | WS_EX_LAYERED)
    except: pass

def _str_to_cs(s):
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
    def __init__(self, parent, slot_idx, colour, initial_times, on_rows_changed=None):
        super().__init__(parent, bg=PANEL2, bd=0,
                         highlightbackground=colour, highlightthickness=1)
        self.slot_idx         = slot_idx
        self.colour           = colour
        self._time_rows       = []
        self._on_rows_changed = on_rows_changed

        hdr = tk.Frame(self, bg=PANEL2)
        hdr.pack(fill="x", padx=4, pady=(5, 2))

        bar_line = tk.Frame(self, bg=colour, height=2)
        bar_line.place(x=0, y=0, relwidth=1)

        dot = tk.Canvas(hdr, bg=PANEL2, width=8, height=8, highlightthickness=0)
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

        self.list_frame = tk.Frame(self, bg=PANEL2)
        self.list_frame.pack(fill="both", expand=True, padx=4)

        add_btn_frame = tk.Frame(self, bg=PANEL2)
        add_btn_frame.pack(fill="x", padx=4, pady=(1,4))
        self._add_btn = tk.Button(add_btn_frame, text="+ ADD TIME", command=self._add_row,
                  bg=PANEL, fg=colour, relief="flat", bd=0,
                  font=("Segoe UI", 7, "bold"), cursor="hand2",
                  pady=2, activebackground=BORDER_HI,
                  activeforeground=colour)
        self._add_btn.pack(fill="x")

        for t in initial_times:
            self._add_row(t)

    MAX_ROWS = 3

    def _update_add_btn(self):
        if len(self._time_rows) >= self.MAX_ROWS:
            self._add_btn.config(state="disabled", fg=FG3, cursor="",
                                 text="MAX 3 TIMES")
        else:
            self._add_btn.config(state="normal", fg=self.colour, cursor="hand2",
                                 text="+ ADD TIME")

    def _add_row(self, initial_text=""):
        if len(self._time_rows) >= self.MAX_ROWS:
            return
        row = tk.Frame(self.list_frame, bg=PANEL2)
        row.pack(fill="x", pady=1)

        val_var = tk.StringVar(value=initial_text)
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

        digits_state = [re.sub(r"[^0-9]", "", initial_text)[:6]]
        PLACEHOLDER = "Time"

        def _render():
            digits = digits_state[0]
            if not digits:
                ent.config(fg=FG3)
                val_var.set(PLACEHOLDER)
                ent.icursor(0)
                return
            ent.config(fg=self.colour)
            parts = [digits[i:i+2] for i in range(0, len(digits), 2)]
            val_var.set(":".join(parts))
            ent.icursor(tk.END)

        def _on_key(event, e=ent):
            if event.keysym in ("Tab", "Return", "KP_Enter"):
                return
            if event.keysym in ("BackSpace", "Delete"):
                digits_state[0] = digits_state[0][:-1]
                _render()
                return "break"
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
            self._update_add_btn()
            if self._on_rows_changed:
                self._on_rows_changed()

        tk.Button(row, text="×", command=remove,
                  bg=PANEL2, fg=FG3, relief="flat",
                  font=("Segoe UI", 9), cursor="hand2", bd=0, padx=3,
                  activeforeground=RED,
                  activebackground=PANEL2).pack(side="left")

        self._time_rows.append(entry)
        self._update_add_btn()
        if self._on_rows_changed:
            self._on_rows_changed()

    def get_triggers(self):
        if not self.en_var.get(): return []
        out = []
        for e in self._time_rows:
            if not e["digits"][0]:
                continue
            cs = _str_to_cs(e["val"].get())
            if cs is not None:
                out.append(cs)
        return out

    def get_times_data(self):
        """Return list of time strings for serialisation."""
        result = []
        for e in self._time_rows:
            if e["digits"][0]:
                result.append(e["val"].get())
        return result

    def set_times_data(self, times):
        """Clear all rows and load from a list of time strings."""
        for entry in list(self._time_rows):
            entry["frame"].destroy()
        self._time_rows.clear()
        self._update_add_btn()
        for t in times:
            self._add_row(t)


# ── ProfileLibrary ────────────────────────────────────────────────────────────
class ProfileLibrary(tk.Frame):
    """
    Left-side panel.  Profiles stored as a list under cfg["profiles"].
    Each profile: {"name": str, "slots": [[time_str, ...], ...]}
    """
    PANEL_W = SIDEBAR_W

    def __init__(self, parent, on_load, on_save_current):
        super().__init__(parent, bg=PANEL3, width=self.PANEL_W)
        self.pack_propagate(False)
        self._on_load        = on_load    # callback(slot_times_list)
        self._on_save_current = on_save_current  # callback() -> slot_times_list

        self._profiles       = []   # list of {"name":..., "slots":...}
        self._selected_idx   = None
        self._row_frames     = []

        self._build()
        self._load_from_config()

    # ── layout ────────────────────────────────────────────────────────────────
    def _build(self):
        # Header bar
        hdr = tk.Frame(self, bg=PANEL3, height=TITLE_H)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        # purple top accent stripe
        tk.Frame(hdr, bg=ACCENT, height=2).place(x=0, y=0, relwidth=1)

        tk.Label(hdr, text="⊞", bg=PANEL3, fg=ACCENT,
                 font=("Segoe UI", 12)).pack(side="left", padx=(10, 4), pady=4)
        tk.Label(hdr, text="PROFILES", bg=PANEL3, fg=FG,
                 font=("Segoe UI", 8, "bold")).pack(side="left")

        # divider
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # Scrollable list area
        list_outer = tk.Frame(self, bg=PANEL3)
        list_outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(list_outer, bg=PANEL3, highlightthickness=0,
                           bd=0)
        scroll = tk.Scrollbar(list_outer, orient="vertical",
                               command=canvas.yview, width=6,
                               bg=PANEL3, troughcolor=PANEL3,
                               activebackground=ACCENT)
        canvas.configure(yscrollcommand=scroll.set)

        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._list_canvas = canvas
        self._inner = tk.Frame(canvas, bg=PANEL3)
        self._inner_win = canvas.create_window((0, 0), window=self._inner,
                                                anchor="nw")

        self._inner.bind("<Configure>", self._on_inner_configure)
        canvas.bind("<Configure>", self._on_canvas_configure)
        canvas.bind("<MouseWheel>", lambda e:
            canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        # ── bottom action bar ─────────────────────────────────────────────────
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        btn_bar = tk.Frame(self, bg=PANEL3)
        btn_bar.pack(fill="x", pady=6, padx=6)

        # NEW profile button
        self._btn_new = tk.Button(btn_bar, text="+ NEW",
                  command=self._new_profile,
                  bg=ACCENT, fg="white", relief="flat", bd=0,
                  font=("Segoe UI", 7, "bold"), padx=8, pady=4,
                  cursor="hand2",
                  activebackground="#6a5ae0", activeforeground="white")
        self._btn_new.pack(fill="x", pady=(0, 3))

        # SAVE INTO SELECTED
        self._btn_save = tk.Button(btn_bar, text="💾  SAVE",
                  command=self._save_into_selected,
                  bg=PANEL, fg=FG2, relief="flat", bd=0,
                  font=("Segoe UI", 7, "bold"), padx=8, pady=4,
                  cursor="hand2",
                  activebackground=BORDER_HI, activeforeground=FG,
                  state="disabled")
        self._btn_save.pack(fill="x", pady=(0, 3))

        # DELETE SELECTED
        self._btn_del = tk.Button(btn_bar, text="✕  DELETE",
                  command=self._delete_selected,
                  bg=PANEL, fg=FG2, relief="flat", bd=0,
                  font=("Segoe UI", 7, "bold"), padx=8, pady=4,
                  cursor="hand2",
                  activebackground=BORDER_HI, activeforeground=RED,
                  state="disabled")
        self._btn_del.pack(fill="x")

        # tip label
        tk.Label(self, text="Click to load · double-click to rename",
                 bg=PANEL3, fg=FG3, font=("Segoe UI", 6),
                 wraplength=SIDEBAR_W - 10).pack(pady=(2, 6))

    def _on_inner_configure(self, e):
        self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))

    def _on_canvas_configure(self, e):
        self._list_canvas.itemconfig(self._inner_win, width=e.width)

    # ── data helpers ──────────────────────────────────────────────────────────
    def _load_from_config(self):
        cfg = _load_config()
        self._profiles = cfg.get("profiles", [])
        idx = cfg.get("selected_profile_idx")
        if isinstance(idx, int) and 0 <= idx < len(self._profiles):
            self._selected_idx = idx
        self._refresh_list()

    def get_selected_idx(self):
        return self._selected_idx

    def save_to_config(self):
        cfg = _load_config()
        cfg["profiles"] = self._profiles
        _save_config(cfg)

    # ── list rendering ────────────────────────────────────────────────────────
    def _refresh_list(self):
        for f in self._row_frames:
            f.destroy()
        self._row_frames.clear()

        if not self._profiles:
            lbl = tk.Label(self._inner,
                           text="No profiles yet.\nClick  + NEW  to save\nyour current timings.",
                           bg=PANEL3, fg=FG3,
                           font=("Segoe UI", 7), justify="center")
            lbl.pack(pady=20)
            self._row_frames.append(lbl)
            self._selected_idx = None
            self._update_buttons()
            return

        for i, prof in enumerate(self._profiles):
            self._build_row(i, prof)

        self._update_buttons()

    def _build_row(self, idx, prof):
        is_sel = (idx == self._selected_idx)
        bg_row = BORDER_HI if is_sel else PANEL3
        fg_row = FG if is_sel else FG2

        row = tk.Frame(self._inner, bg=bg_row, cursor="hand2")
        row.pack(fill="x", padx=4, pady=2)
        self._row_frames.append(row)

        # coloured left stripe
        stripe_col = SLOT_COLS[idx % len(SLOT_COLS)]
        tk.Frame(row, bg=stripe_col if is_sel else FG3,
                 width=3).pack(side="left", fill="y")

        content = tk.Frame(row, bg=bg_row)
        content.pack(side="left", fill="both", expand=True, padx=(6, 4), pady=5)

        # Profile name
        tk.Label(content, text=prof["name"], bg=bg_row, fg=fg_row,
                 font=("Segoe UI", 8, "bold" if is_sel else "normal"),
                 anchor="w").pack(fill="x")

        # Summary of slot counts
        slots = prof.get("slots", [[] for _ in range(5)])
        summary_parts = []
        for si, times in enumerate(slots):
            if times:
                summary_parts.append(f"S{si+1}:{len(times)}")
        summary = "  ".join(summary_parts) if summary_parts else "empty"
        tk.Label(content, text=summary, bg=bg_row, fg=FG3,
                 font=("Segoe UI", 6), anchor="w").pack(fill="x")

        # Selection indicator
        if is_sel:
            tk.Label(row, text="▶", bg=bg_row, fg=ACCENT,
                     font=("Segoe UI", 8)).pack(side="right", padx=4)

        # Bindings
        for w in [row, content] + list(content.winfo_children()):
            w.bind("<Button-1>",        lambda e, i=idx: self._select(i))
            w.bind("<Double-Button-1>", lambda e, i=idx: self._rename(i))
            w.bind("<Enter>",           lambda e, f=row, b=bg_row:
                f.config(bg="#1e2035") if not (f == self._get_sel_frame()) else None)
            w.bind("<Leave>",           lambda e, f=row, b=bg_row:
                f.config(bg=b))

    def _get_sel_frame(self):
        if self._selected_idx is not None and self._selected_idx < len(self._row_frames):
            return self._row_frames[self._selected_idx]
        return None

    def _select(self, idx):
        self._selected_idx = idx
        self._refresh_list()
        self._on_load(self._profiles[idx].get("slots", [[] for _ in range(5)]))

    def _rename(self, idx):
        old = self._profiles[idx]["name"]
        new = simpledialog.askstring("Rename Profile", "Profile name:",
                                     initialvalue=old, parent=self)
        if new and new.strip():
            self._profiles[idx]["name"] = new.strip()
            self.save_to_config()
            self._refresh_list()

    def _new_profile(self):
        name = simpledialog.askstring("New Profile", "Profile name:", parent=self)
        if not name or not name.strip():
            return
        slots_data = self._on_save_current()
        self._profiles.append({"name": name.strip(), "slots": slots_data})
        self._selected_idx = len(self._profiles) - 1
        self.save_to_config()
        self._refresh_list()

    def _save_into_selected(self):
        if self._selected_idx is None: return
        slots_data = self._on_save_current()
        self._profiles[self._selected_idx]["slots"] = slots_data
        self.save_to_config()
        self._refresh_list()
        # Flash "Saved!" briefly
        self._btn_save.config(text="✓  SAVED!", fg=GREEN)
        self.after(1200, lambda: self._btn_save.config(text="💾  SAVE", fg=FG2))

    def _delete_selected(self):
        if self._selected_idx is None: return
        name = self._profiles[self._selected_idx]["name"]
        # Confirm via a simple tk dialog
        import tkinter.messagebox as mb
        if not mb.askyesno("Delete Profile",
                           f'Delete profile  "{name}"?',
                           parent=self):
            return
        del self._profiles[self._selected_idx]
        self._selected_idx = None
        self.save_to_config()
        self._refresh_list()

    def _update_buttons(self):
        has_sel = self._selected_idx is not None
        state = "normal" if has_sel else "disabled"
        fg_save = FG2 if has_sel else FG3
        fg_del  = FG2 if has_sel else FG3
        self._btn_save.config(state=state, fg=fg_save)
        self._btn_del.config(state=state, fg=fg_del)


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
        self.geometry(cfg.get("geometry", "1290x660+60+60"))
        self.zones      = cfg.get("zones", _default_zones())
        self.running    = False
        self.edit_mode  = False
        self.fired      = set()
        self._handles   = []
        self._slots     = []

        self._build()
        self.attributes("-transparentcolor", TRANSP_KEY)
        self.bind("<Configure>", lambda e: self.after_idle(self._on_resize))
        self.after(300, lambda: _make_layered(
            ctypes.windll.user32.GetForegroundWindow()))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._canvas_h = None
        self.after(150, self._init_canvas_h)
        self.after(200, self._maybe_show_disclaimer)

    def _maybe_show_disclaimer(self):
        cfg = _load_config()
        if not cfg.get("disclaimer_dismissed", False):
            self._show_disclaimer()

    # ── disclaimer ────────────────────────────────────────────────────────────
    def _show_disclaimer(self):
        banner = tk.Frame(self, bg="#7b0e0e", bd=0)
        banner.place(relx=0, rely=0, relwidth=1, anchor="nw", y=TITLE_H)

        tk.Frame(banner, bg="#e74c3c", width=4).pack(side="left", fill="y")
        tk.Label(banner, text="⚠", bg="#7b0e0e", fg="#ffb3b3",
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=(8, 4), pady=6)

        msg_frame = tk.Frame(banner, bg="#7b0e0e")
        msg_frame.pack(side="left", fill="both", expand=True, pady=6)

        tk.Label(msg_frame, text="ACCURACY DISCLAIMER — Click timings are approximate.",
                 bg="#7b0e0e", fg="#ffe0e0", font=("Segoe UI", 8, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(msg_frame,
                 text="Expected range per trigger:  +0.05 s  |  +0.03 s  |  Exact  |  −0.01 s  |  −0.03 s",
                 bg="#7b0e0e", fg="#ffb3b3", font=("Segoe UI", 7), anchor="w").pack(fill="x")
        tk.Label(msg_frame,
                 text="OCR latency, system load, and game framerate all affect precision. Fine-tune your trigger times if needed.",
                 bg="#7b0e0e", fg="#cc8888", font=("Segoe UI", 7), anchor="w").pack(fill="x")

        def _dismiss():
            cfg = _load_config()
            cfg["disclaimer_dismissed"] = True
            _save_config(cfg)
            banner.place_forget(); banner.destroy()

        tk.Button(banner, text="✕", command=_dismiss,
                  bg="#7b0e0e", fg="#ffb3b3", relief="flat", bd=0,
                  font=("Segoe UI", 10, "bold"), padx=10, cursor="hand2",
                  activebackground="#9b1a1a", activeforeground="white").pack(
                      side="right", padx=(0, 6), pady=4)

    # ── build ─────────────────────────────────────────────────────────────────
    def _build(self):
        self._build_titlebar()

        # Main body: sidebar + right content column
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        # ── Sidebar ───────────────────────────────────────────────────────────
        self._sidebar = ProfileLibrary(
            body,
            on_load=self._load_profile,
            on_save_current=self._get_current_slot_data)
        self._sidebar.pack(side="left", fill="y")

        # vertical divider
        tk.Frame(body, bg=BORDER, width=1).pack(side="left", fill="y")

        # ── Right column: canvas + footer ─────────────────────────────────────
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        self._right = right

        self.canvas = tk.Canvas(right, bg=TRANSP_KEY,
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

        self._build_footer(right)
        self._draw_border()

    # ── title bar ─────────────────────────────────────────────────────────────
    def _build_titlebar(self):
        bar = tk.Frame(self, bg=PANEL, height=TITLE_H)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        self._bar = bar

        tk.Frame(bar, bg=ACCENT, height=2).place(x=0, y=0, relwidth=1)

        dot_c = tk.Canvas(bar, bg=PANEL, width=10, height=10, highlightthickness=0)
        dot_c.create_oval(1,1,9,9, fill=ACCENT, outline="")
        dot_c.pack(side="left", padx=(14,4), pady=4)

        lbl_title = tk.Label(bar, text="AUTO RAID", bg=PANEL, fg=FG,
                             font=("Segoe UI", 10, "bold"))
        lbl_title.pack(side="left", padx=(0,2))

        tk.Label(bar, text="Made By Ciri", bg=PANEL, fg=GREEN,
                 font=("Segoe UI", 7, "italic")).pack(side="left", padx=(0,6))

        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", pady=8)

        self.btn_edit = tk.Button(
            bar, text="EDIT ZONES", command=self._toggle_edit,
            bg=PANEL, fg=FG2, relief="flat", bd=0,
            font=("Segoe UI", 8, "bold"), padx=12, pady=0,
            cursor="hand2", activebackground=BORDER_HI, activeforeground=FG)
        self.btn_edit.pack(side="left", padx=2, pady=8)

        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", pady=8)

        self.btn_run = tk.Button(
            bar, text="▶  START", command=self._toggle_run,
            bg=ACCENT, fg="white", relief="flat", bd=0,
            font=("Segoe UI", 8, "bold"), padx=14, pady=0,
            cursor="hand2", activebackground="#6a5ae0", activeforeground="white")
        self.btn_run.pack(side="left", padx=8, pady=8)

        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", pady=8)

        self.lbl_timer = tk.Label(bar, text="--:--:--", bg=PANEL, fg=ACCENT,
                                  font=("Consolas", 14, "bold"))
        self.lbl_timer.pack(side="left", padx=12)

        tk.Frame(bar, bg=BORDER, width=1).pack(side="left", fill="y", pady=8)

        self.lbl_status = tk.Label(bar, text="Ready", bg=PANEL, fg=FG2,
                                   font=("Segoe UI", 8))
        self.lbl_status.pack(side="left", padx=8)

        btn_close = tk.Button(
            bar, text="✕", command=self._on_close,
            bg=PANEL, fg=FG2, relief="flat", bd=0,
            font=("Segoe UI", 10), padx=12, pady=0,
            cursor="hand2", activebackground=RED, activeforeground="white")
        btn_close.pack(side="right", pady=8)

        tk.Label(bar, text="right-click + drag to resize", bg=PANEL, fg=GREEN,
                 font=("Segoe UI", 7, "italic")).pack(side="right", padx=(0, 8))

        for w in [bar, lbl_title, dot_c, self.lbl_timer, self.lbl_status]:
            w.bind("<ButtonPress-1>",  self._drag_start)
            w.bind("<B1-Motion>",      self._drag_move)
            w.bind("<ButtonPress-3>",  self._resize_start)
            w.bind("<B3-Motion>",      self._resize_move)

    # ── footer ────────────────────────────────────────────────────────────────
    def _build_footer(self, parent):
        foot = tk.Frame(parent, bg=PANEL)
        foot.pack(fill="x", side="bottom")
        self._foot = foot

        tk.Frame(foot, bg=BORDER, height=1).pack(fill="x")

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

        cfg = _load_config()
        saved_slots    = cfg.get("current_slots", [])
        saved_enabled  = cfg.get("current_enabled", [])

        self._slots = []
        for i in range(5):
            init = saved_slots[i] if i < len(saved_slots) else (
                DEFAULT_SLOT_TIMES[i] if i < len(DEFAULT_SLOT_TIMES) else [])
            col = SlotColumn(inner, i, SLOT_COLS[i], init,
                             on_rows_changed=self._on_rows_changed)
            if i < len(saved_enabled):
                col.en_var.set(bool(saved_enabled[i]))
            col.grid(row=0, column=i, sticky="nsew", padx=3)
            self._slots.append(col)

    # ── profile callbacks ─────────────────────────────────────────────────────
    def _get_current_slot_data(self):
        return [s.get_times_data() for s in self._slots]

    def _load_profile(self, slots_data):
        for i, slot in enumerate(self._slots):
            times = slots_data[i] if i < len(slots_data) else []
            slot.set_times_data(times)
        self._on_rows_changed()
        self.lbl_status.config(text="Profile loaded")

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
        nw = max(900, self._rw + (e.x_root - self._rx))
        nh = max(400, self._rh + (e.y_root - self._ry))
        self.geometry(f"{nw}x{nh}")

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

    # ── dynamic footer / window height ────────────────────────────────────────
    def _init_canvas_h(self):
        self._canvas_h = self.canvas.winfo_height()
        if self._canvas_h < 10:
            self.after(100, self._init_canvas_h)
            return
        self._fit_window_height()

    def _on_rows_changed(self):
        self.after_idle(self._fit_window_height)

    def _fit_window_height(self):
        if not self._canvas_h:
            return
        FOOTER_FIXED = 86
        max_rows = max((len(s._time_rows) for s in self._slots), default=0)
        new_foot_h = FOOTER_FIXED + max_rows * ROW_H
        new_total = TITLE_H + self._canvas_h + new_foot_h
        x = self.winfo_x()
        y = self.winfo_y()
        w = self.winfo_width()
        self.geometry(f"{w}x{new_total}+{x}+{y}")

    # ── persistence ───────────────────────────────────────────────────────────
    def _save_cfg(self):
        geom = f"{self.winfo_width()}x{self.winfo_height()}+{self.winfo_x()}+{self.winfo_y()}"
        cfg = _load_config()
        cfg["geometry"]            = geom
        cfg["zones"]               = self.zones
        cfg["current_slots"]       = self._get_current_slot_data()
        cfg["current_enabled"]     = [s.en_var.get() for s in self._slots]
        cfg["selected_profile_idx"] = self._sidebar.get_selected_idx()
        _save_config(cfg)
        self._sidebar.save_to_config()

    def _on_close(self):
        self._save_cfg()
        self.destroy()

    # ── screen coords ─────────────────────────────────────────────────────────
    def _capture_rect(self):
        return (self.winfo_rootx() + SIDEBAR_W + BORDER_W,
                self.winfo_rooty() + TITLE_H + BORDER_W,
                self.winfo_width() - SIDEBAR_W - 2*BORDER_W,
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
            mm = int(m.group(1)); ss = int(m.group(2)); cs = int(m.group(3))
            return mm*6000 + ss*100 + cs
        m = re.search(r"(\d{1,2}):(\d{2})", text)
        if m:
            return int(m.group(1))*6000 + int(m.group(2))*100
        return None

    def _preprocess_images(self, rgb):
        r, g, b = rgb[:,:,0], rgb[:,:,1], rgb[:,:,2]
        brightness  = r.astype(np.uint16) + g + b
        color_range = (np.maximum(np.maximum(r,g),b).astype(np.int16) -
                       np.minimum(np.minimum(r,g),b))
        white_mask = ((brightness > 570) & (color_range < 40)).astype(np.uint8) * 255
        wm = cv2.resize(white_mask, (white_mask.shape[1]*4, white_mask.shape[0]*4),
                        interpolation=cv2.INTER_NEAREST)
        wm = cv2.dilate(wm, np.ones((2,2), np.uint8), iterations=1)
        wm_inv = cv2.bitwise_not(wm)
        grey    = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        grey_up = cv2.resize(grey, (grey.shape[1]*4, grey.shape[0]*4),
                             interpolation=cv2.INTER_LANCZOS4)
        _, b_fixed = cv2.threshold(grey_up, 160, 255, cv2.THRESH_BINARY)
        return [wm_inv, cv2.bitwise_not(b_fixed)]

    @staticmethod
    def _ocr_one(arr):
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
        if not self._sct: return None
        shot = self._sct.grab(zone)
        rgb = np.array(Image.frombytes("RGB", shot.size, shot.rgb))
        images = self._preprocess_images(rgb)
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
        tally_conf  = {}
        tally_votes = {}
        for val, conf in scored:
            tally_conf[val]  = tally_conf.get(val, 0) + conf
            tally_votes[val] = tally_votes.get(val, 0) + 1
        # Prefer a value two variants agree on over one with higher raw confidence.
        # This stops a single garbled read (e.g. 4→8) winning on confidence alone.
        best_val   = max(tally_conf, key=tally_conf.get)
        max_votes  = max(tally_votes.values())
        if tally_votes[best_val] < max_votes:
            candidates = {v: c for v, c in tally_conf.items()
                          if tally_votes[v] == max_votes}
            best_val = max(candidates, key=candidates.get)
        return best_val

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
            self._click_queue = []
            for _ in range(5):
                ev = threading.Event()
                slot_holder = [None]
                def _worker(event=ev, holder=slot_holder):
                    while True:
                        event.wait(); event.clear()
                        zi = holder[0]
                        if zi is None: break
                        pt = self._slot_center(zi)
                        if pt: pyautogui.click(*pt)
                self._click_queue.append((ev, slot_holder,
                    threading.Thread(target=_worker, daemon=True)))
            for _, _, t in self._click_queue: t.start()
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
        FIRE_OFFSET_CS = 24
        FREEZE_READS   = 3
        triggers = self._collect_triggers()
        self._read_lock = threading.Lock()
        self._sct       = None
        self._anchor_cs   = None
        self._anchor_wall = None
        self._frozen      = False
        self._has_signal  = False

        def _reader():
            prev_cs = None; repeat_count = 0; signal_was_lost = True
            reset_candidate = None; reset_candidate_n = 0
            regain_candidate = None; regain_candidate_n = 0
            drift_candidate = None; drift_candidate_n = 0
            with mss.mss() as sct:
                self._sct = sct
                while self.running:
                    try:
                        cs = self._read_timer()
                        if cs is not None:
                            if signal_was_lost:
                                if regain_candidate is not None and abs(cs - regain_candidate) <= 50:
                                    regain_candidate_n += 1
                                else:
                                    regain_candidate = cs; regain_candidate_n = 1
                                if regain_candidate_n < 3: continue
                                self.fired.clear()
                                regain_candidate = None; regain_candidate_n = 0
                            elif prev_cs is not None and abs(cs - prev_cs) > 500:
                                if cs > prev_cs:
                                    if reset_candidate is not None and abs(cs - reset_candidate) <= 50:
                                        reset_candidate_n += 1
                                    else:
                                        reset_candidate = cs; reset_candidate_n = 1
                                    if reset_candidate_n >= 3:
                                        self.fired.clear()
                                        reset_candidate = None; reset_candidate_n = 0
                                    else: continue
                                else: continue
                            else:
                                reset_candidate = None; reset_candidate_n = 0
                                # Small-deviation guard: a single garbled OCR read can
                                # be off by a second or two without tripping the big-jump
                                # check above. Cross-check against where the countdown
                                # should be (extrapolated from the current anchor) and
                                # require a second confirming read before trusting an
                                # outlier as the new anchor — this is what was causing
                                # the timer to desync at random moments.
                                if self._anchor_cs is not None and self._anchor_wall is not None:
                                    expected = self._anchor_cs - int(
                                        (time.perf_counter() - self._anchor_wall) * 100)
                                    deviation = abs(cs - expected)
                                else:
                                    deviation = 0
                                if deviation > 25:
                                    if drift_candidate is not None and abs(cs - drift_candidate) <= 10:
                                        drift_candidate_n += 1
                                    else:
                                        drift_candidate = cs; drift_candidate_n = 1
                                    if drift_candidate_n < 2:
                                        continue
                                    drift_candidate = None; drift_candidate_n = 0
                                else:
                                    drift_candidate = None; drift_candidate_n = 0
                            signal_was_lost = False
                            now = time.perf_counter()
                            if cs == prev_cs:
                                repeat_count += 1
                            else:
                                repeat_count = 0
                                with self._read_lock:
                                    self._anchor_cs = cs; self._anchor_wall = now
                                    self._frozen = False; self._has_signal = True
                                prev_cs = cs
                            if repeat_count >= FREEZE_READS:
                                with self._read_lock:
                                    if not self._frozen:
                                        self._anchor_cs = cs; self._anchor_wall = now
                                        self._frozen = True
                            elif 0 < repeat_count < FREEZE_READS:
                                with self._read_lock:
                                    if not self._frozen:
                                        self._anchor_cs = cs; self._anchor_wall = now
                        else:
                            signal_was_lost = True; prev_cs = None; repeat_count = 0
                            reset_candidate = None; reset_candidate_n = 0
                            regain_candidate = None; regain_candidate_n = 0
                            with self._read_lock:
                                self._has_signal = False
                    except Exception: pass

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()
        time.sleep(0.15)

        last_ui_update = 0.0
        UI_UPDATE_INTERVAL = 0.04
        last_shown_no_signal = False

        while self.running:
            try:
                with self._read_lock:
                    anchor_cs = self._anchor_cs; anchor_wall = self._anchor_wall
                    frozen = self._frozen; has_signal = self._has_signal
                now_wall = time.perf_counter()
                due_ui   = (now_wall - last_ui_update) >= UI_UPDATE_INTERVAL
                if not has_signal:
                    if not last_shown_no_signal:
                        self.lbl_timer.config(text="??:??:??")
                        last_shown_no_signal = True; last_ui_update = now_wall
                elif anchor_cs is not None:
                    last_shown_no_signal = False
                    if frozen:
                        # Game timer is paused (ult/cutscene) — hold value exactly,
                        # and explicitly resync to the OCR'd value every time so any
                        # drift can't accumulate while paused.
                        cs = anchor_cs
                        self._prev_frozen = True
                    else:
                        if getattr(self, '_prev_frozen', False):
                            # Timer just resumed: anchor_wall is from when the freeze
                            # started, so elapsed would include the entire freeze
                            # duration and jump the timer far ahead. Reset anchor_wall
                            # to now so we count from the correct resumed position.
                            with self._read_lock:
                                self._anchor_wall = now_wall
                                anchor_wall = now_wall
                            self._prev_frozen = False
                        _prev_frozen = False
                        elapsed_cs = int((now_wall - anchor_wall) * 100)
                        cs = max(0, anchor_cs - elapsed_cs)
                    if due_ui:
                        self.lbl_timer.config(text=_cs_to_str(cs))
                        last_ui_update = now_wall
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
            time.sleep(0.005)

    def _click_slot(self, zi):
        for ev, holder, _ in self._click_queue:
            if not ev.is_set():
                holder[0] = zi; ev.set(); return
        pt = self._slot_center(zi)
        if pt: pyautogui.click(*pt)


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
