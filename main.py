import ctypes
import ctypes.wintypes
import queue
import time
import re
import threading

# ── OCR engine selector ──────────────────────────────────────────────────────
# Set to "manga" for manga-ocr (best accuracy, uses ML model, slower first run)
# Set to "windows" for Windows Native OCR (fast, no extra model download)
OCR_ENGINE = "manga"
# ─────────────────────────────────────────────────────────────────────────────

# torch MUST be imported before tkinter on Windows to avoid c10.dll WinError 1114
if OCR_ENGINE == "manga":
    try:
        import torch  # noqa: F401 - side-effect import fixes DLL load order
    except Exception:
        pass

import tkinter as tk
from PIL import Image, ImageTk
import mss
import winocr
import pykakasi
from deep_translator import GoogleTranslator
from concurrent.futures import ThreadPoolExecutor

# Lazy-initialised manga-ocr instance (loaded only if OCR_ENGINE == "manga")
_manga_ocr = None
_manga_ocr_lock = threading.Lock()

def get_manga_ocr():
    global _manga_ocr
    if _manga_ocr is None:
        with _manga_ocr_lock:
            if _manga_ocr is None:
                from manga_ocr import MangaOcr
                _manga_ocr = MangaOcr()
    return _manga_ocr

def prewarm_manga_ocr():
    """Load manga-ocr model in background so first hotkey press is instant."""
    if OCR_ENGINE == "manga":
        try:
            get_manga_ocr()
            print("manga-ocr model loaded and ready.")
        except Exception as e:
            print(f"manga-ocr failed to pre-load: {e}")


# Win32 structures for mouse cursor position
class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

def get_mouse_pos():
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

def capture_moused_monitor():
    mx, my = get_mouse_pos()
    with mss.MSS() as sct:
        # sct.monitors[0] is the virtual screen spanning all monitors
        # sct.monitors[1:] are the individual physical monitors
        for monitor in sct.monitors[1:]:
            left = monitor["left"]
            top = monitor["top"]
            width = monitor["width"]
            height = monitor["height"]
            if left <= mx < left + width and top <= my < top + height:
                return sct.grab(monitor), monitor
        return sct.grab(sct.monitors[1]), sct.monitors[1]

# Initialize PyKakasi globally (thread-safe)
# GoogleTranslator is NOT thread-safe, so we instantiate it per-call instead
kks = pykakasi.kakasi()

def contains_japanese(text):
    """Check if the text contains any Japanese characters (Hiragana, Katakana, Kanji)."""
    # Unicode ranges:
    # Hiragana: \u3040-\u309f
    # Katakana: \u30a0-\u30ff
    # Kanji (CJK Unified Ideographs): \u4e00-\u9faf
    pattern = re.compile(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf]')
    return bool(pattern.search(text))

def translate_and_convert(japanese_text):
    """Convert Japanese to Romaji, Hiragana, and English translation."""
    try:
        # Get PyKakasi conversions
        result = kks.convert(japanese_text)
        romaji = " ".join([item['hepburn'] for item in result])
        # hira contains hiragana, but we fallback to orig for symbols/numbers
        kana = " ".join([item['hira'] if item['hira'] else item['orig'] for item in result])
        
        # Translate to English — create a fresh instance per call (thread-safe)
        english = GoogleTranslator(source='ja', target='en').translate(japanese_text)
    except Exception as e:
        romaji = "[Error]"
        kana = japanese_text
        english = f"Translation error: {e}"
        
    return {
        'original': japanese_text,
        'romaji': romaji,
        'kana': kana,
        'english': english
    }

def get_line_bounding_rect(line):
    """Calculate the bounding rectangle of a line based on its words."""
    words = line.get('words', [])
    if not words:
        return {'x': 0, 'y': 0, 'width': 0, 'height': 0}
    
    xs = [w['bounding_rect']['x'] for w in words]
    ys = [w['bounding_rect']['y'] for w in words]
    rights = [w['bounding_rect']['x'] + w['bounding_rect']['width'] for w in words]
    bottoms = [w['bounding_rect']['y'] + w['bounding_rect']['height'] for w in words]
    
    min_x = min(xs)
    min_y = min(ys)
    max_right = max(rights)
    max_bottom = max(bottoms)
    
    return {
        'x': min_x,
        'y': min_y,
        'width': max_right - min_x,
        'height': max_bottom - min_y
    }

def resolve_overlaps(boxes, screen_w, screen_h, padding=10):
    """Lay boxes out so they don't overlap: try horizontal neighbours first,
    then fall back to moving down. Finally clamp every box to the screen."""

    def overlaps(a, b):
        return (a['x'] < b['x'] + b['w'] + padding and
                a['x'] + a['w'] + padding > b['x'] and
                a['y'] < b['y'] + b['h'] + padding and
                a['y'] + a['h'] + padding > b['y'])

    # Sort top-to-bottom, left-to-right so earlier boxes "win" their position
    placed = sorted(range(len(boxes)),
                    key=lambda i: (boxes[i]['y'], boxes[i]['x']))

    for i in placed:
        box = boxes[i]
        # Try up to 5 horizontal slots before giving up and going vertical
        for attempt in range(50):
            collision = False
            for j in placed:
                if j == i:
                    continue
                if overlaps(box, boxes[j]):
                    collision = True
                    # First try moving right
                    right_x = boxes[j]['x'] + boxes[j]['w'] + padding
                    if right_x + box['w'] <= screen_w:
                        box['x'] = right_x
                    else:
                        # No room to the right – push down and reset x
                        box['y'] = boxes[j]['y'] + boxes[j]['h'] + padding
                        box['x'] = max(0, boxes[i]['orig_x'])
                    break
            if not collision:
                break

        # Clamp to screen bounds
        box['x'] = max(0, min(box['x'], screen_w - box['w']))
        box['y'] = max(0, min(box['y'], screen_h - box['h']))

class ScreenFreezerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()  # Hide root window
        self.msg_queue = queue.Queue()
        self.active_window = None
        self.previous_hwnd = None
        self.pil_img = None
        self.tk_img = None
        
        # Start checking the queue for trigger events or translation results
        self.root.after(100, self.check_queue)

    def check_queue(self):
        try:
            while True:
                msg_type, data = self.msg_queue.get_nowait()
                if msg_type == "trigger":
                    self.freeze_screen()
                elif msg_type == "ocr_complete":
                    self.display_translations(data)
        except queue.Empty:
            pass
        self.root.after(50, self.check_queue)

    def trigger(self):
        self.msg_queue.put(("trigger", None))

    def freeze_screen(self):
        if self.active_window is not None:
            return  # Already active

        # 1. Capture currently active window handle + its screen bounds
        self.previous_hwnd = ctypes.windll.user32.GetForegroundWindow()

        # Get the focused window's bounding rect in screen coordinates
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self.previous_hwnd, ctypes.byref(rect))
        win_rect = {
            'left': rect.left,
            'top': rect.top,
            'right': rect.right,
            'bottom': rect.bottom,
        }

        # 2. Capture the full monitor screen where the mouse cursor is located
        sct_img, monitor = capture_moused_monitor()
        self.pil_img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

        # Convert window rect to monitor-local coordinates
        mx_off = monitor['left']
        my_off = monitor['top']
        win_local = {
            'x': max(0, win_rect['left'] - mx_off),
            'y': max(0, win_rect['top']  - my_off),
            'w': min(monitor['width'],  win_rect['right']  - mx_off) - max(0, win_rect['left'] - mx_off),
            'h': min(monitor['height'], win_rect['bottom'] - my_off) - max(0, win_rect['top']  - my_off),
        }

        # 3. Create borderless fullscreen window matching the monitor geometry
        self.active_window = tk.Toplevel(self.root)
        self.active_window.overrideredirect(True)
        self.active_window.geometry(f"{monitor['width']}x{monitor['height']}+{monitor['left']}+{monitor['top']}")
        
        # Use Canvas instead of Label so we can draw text boxes on top
        self.canvas = tk.Canvas(self.active_window, borderwidth=0, highlightthickness=0, bg="black")
        self.canvas.pack(fill="both", expand=True)

        # 4a. Full screen as the base (seamless background layer)
        self.tk_img = ImageTk.PhotoImage(self.pil_img)
        self.canvas.create_image(0, 0, image=self.tk_img, anchor="nw")

        # 4b. Dim everything outside the focused window to draw the eye to it
        #     Four dark rectangles forming a vignette around the window region
        wx, wy, ww, wh = win_local['x'], win_local['y'], win_local['w'], win_local['h']
        mw, mh = monitor['width'], monitor['height']
        DIM = "#00000088"  # semi-transparent black (tkinter stipple trick)
        _STIPPLE = "gray50"  # 50% stipple for alpha-like dimming
        for coords in [
            (0,      0,       mw,       wy),         # top
            (0,      wy+wh,   mw,       mh),         # bottom
            (0,      wy,      wx,       wy+wh),      # left
            (wx+ww,  wy,      mw,       wy+wh),      # right
        ]:
            if coords[2] > coords[0] and coords[3] > coords[1]:
                self.canvas.create_rectangle(
                    *coords, fill="black", stipple=_STIPPLE, outline=""
                )

        # 4c. Blue highlight border around the focused window
        self.canvas.create_rectangle(
            wx - 2, wy - 2, wx + ww + 2, wy + wh + 2,
            outline="#007aff", width=2
        )

        # Draw a loader overlay
        self.loader_text = self.canvas.create_text(
            monitor['width'] // 2, 
            40, 
            text="[ Freezing & Running OCR / Translation... ]", 
            fill="#ffffff", 
            font=("Segoe UI", 16, "bold"),
            justify="center"
        )
        # Background shadow for loader text
        self.canvas.create_rectangle(
            monitor['width'] // 2 - 200, 15,
            monitor['width'] // 2 + 200, 65,
            fill="#1c1c1e", outline="#3a3a3c", width=2,
            tags="loader_bg"
        )
        self.canvas.tag_raise(self.loader_text)

        # 5. Bind escape key to close the window
        self.active_window.bind("<Escape>", lambda e: self.unfreeze_screen())

        # 6. Bring window to the front and focus it
        self.active_window.attributes("-topmost", True)
        self.active_window.focus_force()

        # 7. Start background thread to process OCR & Translation (window crop only)
        threading.Thread(
            target=self.process_ocr,
            args=(self.pil_img, win_local),
            daemon=True
        ).start()

    def process_ocr(self, pil_img, win_local):
        """Run OCR on the focused window crop only, then offset boxes to screen coords."""
        win_x, win_y = win_local['x'], win_local['y']
        win_crop = pil_img.crop((win_x, win_y, win_x + win_local['w'], win_y + win_local['h']))
        
        OCR_SCALE = 3
        try:
            # Upscale the focused window crop for better OCR accuracy
            ocr_img = win_crop.resize(
                (win_crop.width * OCR_SCALE, win_crop.height * OCR_SCALE),
                Image.LANCZOS
            )
            ocr_res = winocr.recognize_pil_sync(ocr_img, 'ja')
            lines = ocr_res.get('lines', [])
            # Scale bboxes from OCR space -> win_crop space -> full-screen space
            for line in lines:
                for word in line.get('words', []):
                    br = word['bounding_rect']
                    br['x'] = br['x'] / OCR_SCALE + win_x
                    br['y'] = br['y'] / OCR_SCALE + win_y
                    br['width']  /= OCR_SCALE
                    br['height'] /= OCR_SCALE
            
            # Prepare targets — Windows OCR provides bounding boxes for layout.
            # If using manga-ocr, we still use Windows boxes but replace the text
            # with manga-ocr's superior recognition on each crop.
            translation_targets = []
            for line in lines:
                win_text = line.get('text', '').strip()
                bbox = get_line_bounding_rect(line)  # now in full-screen coords

                if OCR_ENGINE == "manga":
                    # Crop from the full-screen image using full-screen coords
                    crop_pil = pil_img.crop((
                        max(0, int(bbox['x'])),
                        max(0, int(bbox['y'])),
                        min(pil_img.width,  int(bbox['x'] + bbox['width'])),
                        min(pil_img.height, int(bbox['y'] + bbox['height']))
                    ))
                    # Skip tiny/empty crops
                    if crop_pil.width < 4 or crop_pil.height < 4:
                        continue
                    mocr = get_manga_ocr()
                    text = mocr(crop_pil)
                else:
                    text = win_text
                    crop_pil = None

                text = text.strip()
                if not text or not contains_japanese(text):
                    continue
                translation_targets.append((text, bbox, crop_pil))

            # Fetch translations in parallel
            boxes = []
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {
                    executor.submit(translate_and_convert, text): (bbox, crop_pil)
                    for text, bbox, crop_pil in translation_targets
                }
                for future in futures:
                    bbox, crop_pil = futures[future]
                    try:
                        res = future.result()
                        # For windows engine, crop now using full-screen coords
                        if crop_pil is None:
                            crop_pil = pil_img.crop((
                                max(0, int(bbox['x'])),
                                max(0, int(bbox['y'])),
                                min(pil_img.width,  int(bbox['x'] + bbox['width'])),
                                min(pil_img.height, int(bbox['y'] + bbox['height']))
                            ))
                        
                        # Estimate width based on longest line of text or crop width
                        max_chars = max(len(res['original']), len(res['romaji']), len(res['kana']), len(res['english']))
                        text_w = max_chars * 7 + 24
                        w = min(max(text_w, bbox['width'] + 24, 180), 400)
                        
                        # Estimate height including cropped image height
                        h = bbox['height'] + 110 + (len(res['english']) // 40) * 16
                        
                        # Position translation box below the original text (full-screen coords)
                        x = max(0, bbox['x'] + (bbox['width'] - w) // 2)
                        y = max(0, bbox['y'] + bbox['height'] + 5)
                        # Clamp initial position to screen
                        x = min(x, pil_img.width - w)
                        y = min(y, pil_img.height - h)
                        
                        boxes.append({
                            'x': x,
                            'y': y,
                            'orig_x': x,   # remember preferred x for reset
                            'w': w,
                            'h': h,
                            'data': res,
                            'orig_bbox': bbox,
                            'crop_pil': crop_pil
                        })
                    except Exception as e:
                        print("Error processing translation:", e)

            # Resolve overlaps with horizontal-first stacking, clamped to screen
            resolve_overlaps(boxes, pil_img.width, pil_img.height, padding=12)
            
            # Post result back to main GUI thread
            self.msg_queue.put(("ocr_complete", boxes))
        except Exception as e:
            print("OCR process failed:", e)
            self.msg_queue.put(("ocr_complete", []))

    def display_translations(self, boxes):
        """Draw the translations over the frozen screen."""
        if not self.active_window:
            return

        # Remove loader text and background
        self.canvas.delete(self.loader_text)
        self.canvas.delete("loader_bg")

        self.crop_tk_imgs = []
        for box in boxes:
            data = box['data']
            crop_pil = box['crop_pil']
            
            # Highlight original text area
            orig = box['orig_bbox']
            self.canvas.create_rectangle(
                orig['x'] - 2, orig['y'] - 2,
                orig['x'] + orig['width'] + 2, orig['y'] + orig['height'] + 2,
                outline="#007aff", width=2
            )

            # Create a card frame for the translation
            frame = tk.Frame(
                self.canvas,
                bg="#ffffff",
                padx=8,
                pady=6,
                highlightbackground="#e5e5ea",
                highlightcolor="#e5e5ea",
                highlightthickness=1,
                bd=0
            )
            
            # Row 1: Cropped image of original text
            crop_tk = ImageTk.PhotoImage(crop_pil)
            self.crop_tk_imgs.append(crop_tk)
            lbl_crop = tk.Label(frame, image=crop_tk, bg="#ffffff", bd=0)
            lbl_crop.pack(anchor="w", pady=(0, 4))
            
            # Row 2: Raw OCR (Kanji/original, bold, dark red)
            lbl_raw = tk.Label(
                frame, 
                text=data['original'], 
                fg="#a31515", 
                bg="#ffffff", 
                font=("Segoe UI", 11, "bold"),
                anchor="w",
                justify="left"
            )
            lbl_raw.pack(fill="x", pady=(0, 2))

            # Row 3: Kana (Hiragana / Katakana, normal, green)
            lbl_kana = tk.Label(
                frame, 
                text=data['kana'], 
                fg="#248a3d", 
                bg="#ffffff", 
                font=("Segoe UI", 10, "normal"),
                anchor="w",
                justify="left"
            )
            lbl_kana.pack(fill="x", pady=(0, 2))

            # Row 4: Romaji (italic, blue)
            lbl_romaji = tk.Label(
                frame, 
                text=data['romaji'], 
                fg="#0066cc", 
                bg="#ffffff", 
                font=("Segoe UI", 9, "italic"),
                anchor="w",
                justify="left"
            )
            lbl_romaji.pack(fill="x", pady=(0, 2))

            # Row 5: English Translation (bold, black)
            lbl_english = tk.Label(
                frame, 
                text=data['english'], 
                fg="#1c1c1e", 
                bg="#ffffff", 
                font=("Segoe UI", 11, "bold"),
                anchor="w",
                justify="left",
                wraplength=box['w'] - 16
            )
            lbl_english.pack(fill="x")

            # Place frame on canvas
            self.canvas.create_window(box['x'], box['y'], window=frame, anchor="nw")

    def unfreeze_screen(self):
        if self.active_window:
            self.active_window.destroy()
            self.active_window = None
            self.tk_img = None
            self.pil_img = None
            self.crop_tk_imgs = []

        # Restore focus to the previously active window
        if self.previous_hwnd:
            ctypes.windll.user32.SetForegroundWindow(self.previous_hwnd)
            self.previous_hwnd = None

    def run(self):
        self.root.mainloop()

# ── Win32 RegisterHotKey (works across elevation & fullscreen) ────────────────
user32 = ctypes.windll.user32
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

def register_hotkey_win32(app):
    # PeekMessageW to ensure this thread has a message queue
    msg = ctypes.wintypes.MSG()
    user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # PM_NOREMOVE

    HOTKEY_ID = 1
    mods = MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT

    if not user32.RegisterHotKey(None, HOTKEY_ID, mods, ord('E')):
        err = ctypes.get_last_error()
        print(f"[DEBUG] RegisterHotKey failed with error {err}. Falling back to GetAsyncKeyState polling.")

        # ── Fallback: GetAsyncKeyState polling ──────────────────────────────
        def is_key_down(vk):
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)

        VK_MAP = {
            "ctrl":  (0xA2, 0xA3),   # L/R CONTROL
            "alt":   (0xA4, 0xA5),   # L/R MENU (Alt)
            "shift": (0xA0, 0xA1),   # L/R SHIFT
            "e":     (0x45, None),
        }
        pressed = False
        while True:
            ctrl  = is_key_down(VK_MAP["ctrl"][0])  or is_key_down(VK_MAP["ctrl"][1])
            alt   = is_key_down(VK_MAP["alt"][0])   or is_key_down(VK_MAP["alt"][1])
            shift = is_key_down(VK_MAP["shift"][0]) or is_key_down(VK_MAP["shift"][1])
            e     = is_key_down(VK_MAP["e"][0])
            if ctrl and alt and shift and e:
                if not pressed:
                    pressed = True
                    app.trigger()
            else:
                pressed = False
            time.sleep(0.05)
        return

    print("[DEBUG] RegisterHotKey succeeded – waiting for WM_HOTKEY.")
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0):
        if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
            app.trigger()
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

def main():
    app = ScreenFreezerApp()

    # Start Win32 hotkey thread (RegisterHotKey with fallback to GetAsyncKeyState)
    threading.Thread(target=register_hotkey_win32, args=(app,), daemon=True).start()

    print("Application started. Press Ctrl+Alt+Shift+E to freeze and translate.")
    print("Press Escape while frozen to unfreeze and restore focus.")

    # Pre-warm the manga-ocr model in the background so first use is instant
    threading.Thread(target=prewarm_manga_ocr, daemon=True).start()

    try:
        app.run()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()
