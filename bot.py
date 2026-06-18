import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

import cv2
import keyboard
import numpy as np
import pyautogui
import pyperclip
import requests


# ---------------- CONFIG ----------------

CAPTURE_REGION = (70, 175, 255, 175)  # x, y, width, height
INPUT_BOX = (100, 325)
SUBMIT_BUTTON = (200, 400)

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = "mistralai/ministral-14b-instruct-2512"

RUNNING = False
READER = None
EASYOCR_IMPORT_FAILED = False
LAST_FRAME_HASH = None
LAST_SUBMITTED_TEXT = None
LAST_SUBMITTED_AT = 0.0
LAST_AI_CALL_AT = 0.0


def load_dotenv():
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()

OCR_ENGINE = os.getenv("OCR_ENGINE", "hybrid").strip().lower()
LOOP_DELAY_SECONDS = float(os.getenv("LOOP_DELAY_SECONDS", "0.25"))
SUBMIT_COOLDOWN_SECONDS = float(os.getenv("SUBMIT_COOLDOWN_SECONDS", "0.7"))
DUPLICATE_TEXT_WINDOW_SECONDS = float(os.getenv("DUPLICATE_TEXT_WINDOW_SECONDS", "4"))
LOCAL_OCR_MIN_CONFIDENCE = float(os.getenv("LOCAL_OCR_MIN_CONFIDENCE", "0.70"))
AI_FALLBACK_MIN_INTERVAL_SECONDS = float(os.getenv("AI_FALLBACK_MIN_INTERVAL_SECONDS", "2.0"))
AI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "2.5"))
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "32"))

pyautogui.PAUSE = float(os.getenv("PYAUTOGUI_PAUSE", "0.02"))


# ---------------- OCR ----------------

def get_reader():
    global EASYOCR_IMPORT_FAILED, READER

    if READER is None:
        try:
            import easyocr
        except ImportError as exc:
            EASYOCR_IMPORT_FAILED = True
            print(f"EasyOCR unavailable, using NVIDIA fallback only: {exc}")
            return None

        READER = easyocr.Reader(["en"], gpu=False)

    return READER


def capture_screen():
    screenshot = pyautogui.screenshot(region=CAPTURE_REGION)
    return np.array(screenshot)


def preprocess_image(img):
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh


def frame_hash(img):
    return hashlib.blake2b(img.tobytes(), digest_size=12).hexdigest()


def extract_text_with_easyocr(img):
    if EASYOCR_IMPORT_FAILED:
        return ""

    reader = get_reader()
    if reader is None:
        return ""

    results = reader.readtext(img)
    parts = []

    for result in results:
        detected_text = result[1]
        confidence = result[2]

        if confidence >= LOCAL_OCR_MIN_CONFIDENCE:
            parts.append(detected_text)

    return " ".join(parts).strip()


def get_nvidia_auth_header():
    api_key = os.getenv("NVIDIA_API_KEY") or os.getenv("NIM_API_KEY")
    if not api_key:
        return None

    return api_key if api_key.lower().startswith("bearer ") else f"Bearer {api_key}"


def encode_png_data_url(img):
    ok, encoded = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("Could not encode screenshot as PNG.")

    image_b64 = base64.b64encode(encoded.tobytes()).decode("ascii")
    return f"data:image/png;base64,{image_b64}"


def clean_ai_output(text):
    text = text.strip()
    text = re.sub(r"^```(?:text)?|```$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^(text|answer)\s*:\s*", "", text, flags=re.IGNORECASE).strip()
    text = text.strip("\"'` ")

    if text.lower() in {"none", "no text", "no visible text", "n/a"}:
        return ""

    return text


def extract_text_with_nvidia(img):
    auth_header = get_nvidia_auth_header()
    if not auth_header:
        return ""

    payload = {
        "model": os.getenv("NVIDIA_MODEL", NVIDIA_MODEL),
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Read the exact visible text in this small screenshot. "
                            "Return only the text. No explanation."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": encode_png_data_url(img)},
                    },
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": AI_MAX_TOKENS,
        "stream": False,
    }

    try:
        response = requests.post(
            os.getenv("NVIDIA_API_URL", NVIDIA_API_URL),
            headers={
                "Authorization": auth_header,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=(0.75, AI_TIMEOUT_SECONDS),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return clean_ai_output(content)
    except Exception as exc:
        print(f"NVIDIA OCR skipped: {exc}")
        return ""


def extract_text(image, processed):
    global LAST_AI_CALL_AT

    if OCR_ENGINE == "ai":
        return extract_text_with_nvidia(image)

    local_text = extract_text_with_easyocr(processed)
    if local_text or OCR_ENGINE == "fast":
        return local_text

    if OCR_ENGINE != "hybrid":
        return ""

    now = time.monotonic()
    if now - LAST_AI_CALL_AT < AI_FALLBACK_MIN_INTERVAL_SECONDS:
        return ""

    LAST_AI_CALL_AT = now
    return extract_text_with_nvidia(image)


# ---------------- CLEANING ----------------

def clean_text(text):
    replacements = {
        ":": "=",
        "5": "s",
        "9": "g",
        "O": "@",
        "?": "@",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return " ".join(text.split())


# ---------------- PASTE ----------------

def should_submit(text):
    global LAST_SUBMITTED_TEXT, LAST_SUBMITTED_AT

    now = time.monotonic()
    if now - LAST_SUBMITTED_AT < SUBMIT_COOLDOWN_SECONDS:
        return False

    if (
        text == LAST_SUBMITTED_TEXT
        and now - LAST_SUBMITTED_AT < DUPLICATE_TEXT_WINDOW_SECONDS
    ):
        return False

    LAST_SUBMITTED_TEXT = text
    LAST_SUBMITTED_AT = now
    return True


def paste_text(text):
    pyperclip.copy(text)
    pyautogui.click(*INPUT_BOX)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.05)
    pyautogui.click(*SUBMIT_BUTTON)


# ---------------- MAIN LOOP ----------------

def start_bot():
    global LAST_FRAME_HASH, RUNNING

    if RUNNING:
        print("Bot already running")
        return

    RUNNING = True
    LAST_FRAME_HASH = None
    print(f"Bot Started ({OCR_ENGINE} mode)")

    while RUNNING:
        image = capture_screen()
        processed = preprocess_image(image)

        current_hash = frame_hash(processed)
        if current_hash == LAST_FRAME_HASH:
            time.sleep(LOOP_DELAY_SECONDS)
            continue

        LAST_FRAME_HASH = current_hash
        text = extract_text(image, processed)

        if text:
            text = clean_text(text)

            if should_submit(text):
                print(f"Detected: {text}")
                paste_text(text)

        time.sleep(LOOP_DELAY_SECONDS)


def stop_bot():
    global RUNNING

    RUNNING = False
    print("Bot Stopped")


def main():
    keyboard.add_hotkey("F8", start_bot)
    keyboard.add_hotkey("F9", stop_bot)

    print("F8 = Start")
    print("F9 = Stop")
    print(f"OCR_ENGINE = {OCR_ENGINE}")

    keyboard.wait()


if __name__ == "__main__":
    main()
