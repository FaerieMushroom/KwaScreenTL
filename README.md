# KwaScreenTL

⚠️ Warning: AI vibe coded slop ⚠️

Screen translation tool for Japanese applications. Captures the active monitor, runs OCR via PaddleOCR, translates using DeepL, and displays popup cards with dictionary data (JMdict/Jamdict).

## Setup

1. Python 3.10+ recommended.
2. Run `setup.bat` to create a virtual environment and install dependencies.
3. Run `run.bat` to start the app.
   The global `keyboard` module may require administrator rights on some systems.

## Usage

| Key | Action |
|---|---|
| `Ctrl+Alt+Shift+E` | Capture hovered window / dismiss OCR boxes |
| `Ctrl+Alt+Shift+R` | Snip mode (drag-select a region) |
| `Ctrl+Alt+Shift+S` | Toggle settings panel |
| `Escape` | (in snip mode) Cancel selection |

| Mouse (on OCR box) | Action |
|---|---|
| `Hover` | Show translation card |
| `Click+drag` | Select text to copy |
| `Ctrl+Click` | Show kanji info |
| `Right-click` | Open in Jisho |
| `Shift+Right-click` | Open in DeepL |
| `Middle-click` | Text-to-speech |
| `Mousewheel` (over card) | Scroll |

## Features

- **PaddleOCR** (`japan` model, ONNX runtime) with per-character bounding boxes
- **DeepL** translation (free API) with romaji/kana via pykakasi
- **JMdict** (via Jamdict) for word/kanji definitions — local offline dictionary
- **Snip mode** for manual region capture
- **Skip non-Japanese** mode to filter out non-JP OCR results
- **Timeout & retry** for translation/API calls
- **Text-to-speech** via edge-tts (ja-JP-NanamiNeural)
- All library output (paddle, onnxruntime) redirected to `app.log`

## Settings

- **Show romaji / Show translation**: toggles in the settings panel (`Ctrl+Alt+Shift+S`)
- **Translator**: change `TRANSLATOR` constant at the top of `main.py` (`"deepl"` or `"google"`)
- **DeepL API key**: place your key in `deeplapikey.txt` next to `main.py`

## Used Libraries

- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) — Japanese OCR
- [Deep-Translator](https://github.com/nidhaloff/deep-translator) — DeepL/Google translation
- [pykakasi](https://github.com/miurahr/pykakasi) — Japanese → romaji/kana
- [jamdict](https://github.com/neocl/jamdict) — JMdict dictionary lookup
- [SudachiPy](https://github.com/WorksApplications/SudachiPy) — Morphological analysis
- [jaconv](https://github.com/ikegami-yukino/jaconv) — Kana conversion
- [mss](https://github.com/BoboTiG/python-mss) — Screen capture
- [Pillow](https://python-pillow.org/) — Image processing
- [edge-tts](https://github.com/rany2/edge-tts) — Text-to-speech
- [PaddlePaddle](https://github.com/PaddlePaddle/Paddle) / [ONNX Runtime](https://github.com/microsoft/onnxruntime) — OCR backend
