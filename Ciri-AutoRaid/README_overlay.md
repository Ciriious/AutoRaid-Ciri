# Game Overlay Auto-Clicker

A resizable overlay window (like LICEcap) that reads your game timer via OCR
and auto-clicks the character buttons at the right moments.

---

## Setup

### 1. Install Python dependencies
```
pip install mss pytesseract pillow pyautogui opencv-python numpy
```
### 2. Run
```
python AutoRaid.pyw

```

---

## How to Use

1. **Position the window** — drag it (left-click drag) so the cyan border surrounds your game area, matching where the timer and character buttons appear.
2. **Resize** — right-click drag the bottom-right to resize the window until it fits.
3. **Configure Rules** — click **Rules** to set: *"when timer hits X seconds, click characters Y"*.
4. **Adjust Zones** — if the auto-detection misses the timer or buttons, click **Zones** to tweak the fractional positions (all values are 0.0–1.0 relative to the capture area).
5. **Click ▶ Start** — the overlay starts reading the timer and will auto-click at your set thresholds.

---

## Default Rules
| Timer reaches | Clicks |
|---|---|
| ≤ 50 seconds | All 5 characters |
| ≤ 30 seconds | All 5 characters |
| ≤ 10 seconds | All 5 characters |

Each rule fires **once** per session. Click Stop → Start to reset.

---

## Controls
| Action | How |
|---|---|
| Move window | Left-click drag |
| Resize window | Right-click drag |
| Edit rules | "Rules" button |
| Edit zones | "Zones" button |
| Start/Stop | "▶ Start" / "⏹ Stop" |
| Quit | "✕" button |
