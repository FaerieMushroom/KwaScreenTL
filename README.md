# Screen Freezer App

A lightweight utility written in Python that captures the moused-over display, freezes it on screen, and releases it on pressing `Escape`, returning focus to the previously active window.

## Setup

1. Make sure Python 3 is installed.
2. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the application:
   ```bash
   python main.py
   ```
   *(Note: The global `keyboard` module may require administrator/root privileges on some operating systems to listen to global keyboard events, but on Windows it should run normally in standard user cmd/powershell).*

## How it works

1. **Hotkey Listener**: Listens globally for `Ctrl + Alt + Shift + E`.
2. **Foreground Window Capture**: Saves the window handle (`hwnd`) of the active window using `ctypes` (`GetForegroundWindow`).
3. **Display Identifier**: Uses the mouse coordinate (`GetCursorPos`) to check which monitor bounds the cursor is currently in.
4. **Frame Capture**: Captures the exact bounds of the target monitor using `mss` (very fast screen capture).
5. **Frame Display**: Opens a borderless Tkinter window positioned exactly at the monitor's coordinates, displaying the captured frame.
6. **Focus Restoration**: On pressing `Escape`, the borderless window is destroyed, and the previous active window is refocused using `ctypes` (`SetForegroundWindow`).
