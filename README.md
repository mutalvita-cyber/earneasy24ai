# earneasy24ai

Fully automated screen OCR submitter. It watches a configured screen region,
detects pixel changes, reads text with EasyOCR first, optionally falls back to
the NVIDIA vision API, cleans the text, pastes it into the configured input box,
and clicks submit.

## Install

```powershell
python -m pip install -r requirements.txt
```

## Configure

Copy `.env.example` to `.env` and adjust the values.

```env
CAPTURE_REGION=700,193,478,205
INPUT_BOX=100,325
SUBMIT_BUTTON=200,400

OCR_MODE=hybrid
NVIDIA_API_KEY=

DUPLICATE_TEXT_WINDOW_SECONDS=10.0
```

OCR modes:

- `hybrid`: EasyOCR first, NVIDIA fallback only when EasyOCR returns nothing.
- `easyocr`: EasyOCR only.
- `nvidia`: NVIDIA vision API for every processed screenshot.

## Run

```powershell
python bot.py
```

Hotkeys:

- `F6`: set input box to the current mouse position
- `F7`: set submit button to the current mouse position
- `F8`: start the bot
- `F9`: stop the bot
- `F12`: print the current mouse/config positions

Once `F8` is pressed, the bot runs without manual text entry.
