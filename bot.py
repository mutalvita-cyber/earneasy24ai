from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import keyboard
import pyautogui
import pyperclip
import requests


DEFAULT_CAPTURE_REGION = (700, 193, 478, 205)  # x, y, width, height
DEFAULT_INPUT_BOX = (100, 325)
DEFAULT_SUBMIT_BUTTON = (200, 400)

DEFAULT_NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_NVIDIA_MODEL = "meta/llama-3.2-11b-vision-instruct"

# Existing cleanup map kept as the default behavior.
DEFAULT_CHARACTER_REPLACEMENTS = {
    ":": "=",
    "5": "s",
    "9": "g",
    "O": "@",
    "?": "@",
}

OCR_MODE_ALIASES = {
    "ai": "nvidia",
    "easy": "easyocr",
    "fast": "easyocr",
    "local": "easyocr",
    "nim": "nvidia",
}


@dataclass(frozen=True)
class Config:
    capture_region: tuple[int, int, int, int]
    loop_delay_seconds: float
    change_cooldown_seconds: float
    post_change_settle_seconds: float
    process_initial_frame: bool
    ocr_mode: str
    easyocr_languages: tuple[str, ...]
    easyocr_gpu: bool
    easyocr_min_confidence: float
    nvidia_api_url: str
    nvidia_model: str
    nvidia_api_key: str
    ai_timeout_seconds: float
    ai_connect_timeout_seconds: float
    ai_max_tokens: int
    ai_fallback_min_interval_seconds: float
    duplicate_text_window_seconds: float
    min_seconds_between_submissions: float
    paste_delay_seconds: float
    submit_delay_seconds: float
    clear_input_before_paste: bool
    pyautogui_pause: float
    pyautogui_failsafe: bool
    character_replacements: dict[str, str]


def load_dotenv() -> None:
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


def env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_float(name: str, default: float) -> float:
    value = env_str(name)
    if not value:
        return default

    try:
        return float(value)
    except ValueError:
        print(f"Invalid {name}={value!r}; using {default}")
        return default


def env_int(name: str, default: int) -> int:
    value = env_str(name)
    if not value:
        return default

    try:
        return int(value)
    except ValueError:
        print(f"Invalid {name}={value!r}; using {default}")
        return default


def parse_int_tuple(
    name: str,
    expected_length: int,
    fallback: tuple[int, ...],
) -> tuple[int, ...]:
    value = env_str(name)
    if not value:
        return fallback

    try:
        parsed = tuple(int(part.strip()) for part in value.split(","))
    except ValueError:
        print(f"Invalid {name}={value!r}; using {format_tuple(fallback)}")
        return fallback

    if len(parsed) != expected_length:
        print(f"Invalid {name}={value!r}; using {format_tuple(fallback)}")
        return fallback

    return parsed


def parse_languages(value: str) -> tuple[str, ...]:
    languages = tuple(part.strip() for part in value.split(",") if part.strip())
    return languages or ("en",)


def parse_ocr_mode(value: str) -> str:
    mode = OCR_MODE_ALIASES.get(value.strip().lower(), value.strip().lower())
    if mode in {"easyocr", "hybrid", "nvidia"}:
        return mode

    print(f"Invalid OCR_MODE={value!r}; using hybrid")
    return "hybrid"


def parse_character_replacements() -> dict[str, str]:
    replacements = dict(DEFAULT_CHARACTER_REPLACEMENTS)
    raw_json = env_str("CHARACTER_REPLACEMENTS_JSON")
    if not raw_json:
        return replacements

    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        print(f"Invalid CHARACTER_REPLACEMENTS_JSON; using defaults: {exc}")
        return replacements

    if not isinstance(parsed, dict):
        print("Invalid CHARACTER_REPLACEMENTS_JSON; expected a JSON object")
        return replacements

    for old, new in parsed.items():
        replacements[str(old)] = str(new)

    return replacements


def read_config() -> Config:
    return Config(
        capture_region=parse_int_tuple("CAPTURE_REGION", 4, DEFAULT_CAPTURE_REGION),
        loop_delay_seconds=env_float("LOOP_DELAY_SECONDS", 0.15),
        change_cooldown_seconds=env_float("CHANGE_COOLDOWN_SECONDS", 0.7),
        post_change_settle_seconds=env_float("POST_CHANGE_SETTLE_SECONDS", 0.15),
        process_initial_frame=env_bool("PROCESS_INITIAL_FRAME", True),
        ocr_mode=parse_ocr_mode(env_str("OCR_MODE", env_str("OCR_ENGINE", "hybrid"))),
        easyocr_languages=parse_languages(env_str("EASYOCR_LANGUAGES", "en")),
        easyocr_gpu=env_bool("EASYOCR_GPU", False),
        easyocr_min_confidence=env_float("EASYOCR_MIN_CONFIDENCE", 0.7),
        nvidia_api_url=env_str("NVIDIA_API_URL", DEFAULT_NVIDIA_API_URL),
        nvidia_model=env_str("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL),
        nvidia_api_key=env_str("NVIDIA_API_KEY", env_str("NIM_API_KEY")),
        ai_timeout_seconds=env_float("AI_TIMEOUT_SECONDS", 8.0),
        ai_connect_timeout_seconds=env_float("AI_CONNECT_TIMEOUT_SECONDS", 2.0),
        ai_max_tokens=env_int("AI_MAX_TOKENS", 32),
        ai_fallback_min_interval_seconds=env_float(
            "AI_FALLBACK_MIN_INTERVAL_SECONDS",
            2.0,
        ),
        duplicate_text_window_seconds=env_float(
            "DUPLICATE_TEXT_WINDOW_SECONDS",
            10.0,
        ),
        min_seconds_between_submissions=env_float(
            "MIN_SECONDS_BETWEEN_SUBMISSIONS",
            0.7,
        ),
        paste_delay_seconds=env_float("PASTE_DELAY_SECONDS", 0.05),
        submit_delay_seconds=env_float("SUBMIT_DELAY_SECONDS", 0.05),
        clear_input_before_paste=env_bool("CLEAR_INPUT_BEFORE_PASTE", True),
        pyautogui_pause=env_float("PYAUTOGUI_PAUSE", 0.02),
        pyautogui_failsafe=env_bool("PYAUTOGUI_FAILSAFE", True),
        character_replacements=parse_character_replacements(),
    )


def format_tuple(values: tuple[int, ...]) -> str:
    return ",".join(str(value) for value in values)


def capture_screen(config: Config):
    return pyautogui.screenshot(region=config.capture_region)


def frame_hash(image: Any) -> str:
    rgb_image = image.convert("RGB")
    return hashlib.blake2b(rgb_image.tobytes(), digest_size=16).hexdigest()


def image_to_png_data_url(image: Any) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def clean_model_output(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:text)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    text = re.sub(r"^(text|answer)\s*:\s*", "", text, flags=re.IGNORECASE).strip()
    text = text.strip("\"'` ")

    if text.lower() in {"none", "no text", "no visible text", "n/a"}:
        return ""

    return text


def clean_detected_text(text: str, replacements: dict[str, str]) -> str:
    text = clean_model_output(text)
    for old, new in replacements.items():
        text = text.replace(old, new)

    return " ".join(text.split())


def duplicate_key(text: str) -> str:
    return " ".join(text.casefold().split())


class EasyOcrReader:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._reader = None
        self._load_failed = False

    def read_text(self, image: Any) -> str:
        reader = self._get_reader()
        if reader is None:
            return ""

        try:
            import numpy as np

            results = reader.readtext(
                np.array(image.convert("RGB")),
                detail=1,
                paragraph=False,
            )
        except Exception as exc:
            print(f"EasyOCR failed: {exc}")
            return ""

        parts: list[str] = []
        for result in results:
            if len(result) < 3:
                continue

            detected_text = str(result[1]).strip()
            confidence = float(result[2])
            if detected_text and confidence >= self.config.easyocr_min_confidence:
                parts.append(detected_text)

        return " ".join(parts).strip()

    def _get_reader(self):
        if self._reader is not None:
            return self._reader

        if self._load_failed:
            return None

        try:
            import easyocr
        except ImportError as exc:
            self._load_failed = True
            print(f"EasyOCR unavailable: {exc}")
            return None

        try:
            self._reader = easyocr.Reader(
                list(self.config.easyocr_languages),
                gpu=self.config.easyocr_gpu,
            )
        except Exception as exc:
            self._load_failed = True
            print(f"EasyOCR could not initialize: {exc}")
            return None

        return self._reader


class NvidiaVisionClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._last_call_at = 0.0
        self._missing_key_reported = False

    def read_text(self, image: Any, stop_event: threading.Event | None = None) -> str:
        if not self.config.nvidia_api_key:
            if not self._missing_key_reported:
                print("NVIDIA fallback unavailable: NVIDIA_API_KEY is not set")
                self._missing_key_reported = True
            return ""

        now = time.monotonic()
        elapsed = now - self._last_call_at
        if elapsed < self.config.ai_fallback_min_interval_seconds:
            remaining = self.config.ai_fallback_min_interval_seconds - elapsed
            if stop_event is not None and stop_event.wait(remaining):
                return ""
            if stop_event is None:
                time.sleep(remaining)

        self._last_call_at = now
        payload = {
            "model": self.config.nvidia_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Read the exact visible text in this screenshot. "
                                "Return only the text. If there is no readable text, "
                                "return an empty string."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_to_png_data_url(image)},
                        },
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": self.config.ai_max_tokens,
            "stream": False,
        }

        try:
            response = requests.post(
                self.config.nvidia_api_url,
                headers={
                    "Authorization": self._auth_header(),
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=(
                    self.config.ai_connect_timeout_seconds,
                    self.config.ai_timeout_seconds,
                ),
            )
            response.raise_for_status()
            data = response.json()
            return clean_model_output(extract_message_content(data))
        except Exception as exc:
            print(f"NVIDIA fallback failed: {exc}")
            return ""

    def _auth_header(self) -> str:
        if self.config.nvidia_api_key.lower().startswith("bearer "):
            return self.config.nvidia_api_key

        return f"Bearer {self.config.nvidia_api_key}"


def extract_message_content(data: dict[str, Any]) -> str:
    message = data["choices"][0]["message"]
    content = message.get("content", "")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return " ".join(parts)

    return str(content)


class ScreenOcrBot:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.input_box = parse_int_tuple("INPUT_BOX", 2, DEFAULT_INPUT_BOX)
        self.submit_button = parse_int_tuple(
            "SUBMIT_BUTTON",
            2,
            DEFAULT_SUBMIT_BUTTON,
        )
        self.easyocr = EasyOcrReader(config)
        self.nvidia = NvidiaVisionClient(config)
        self._position_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._last_frame_hash: str | None = None
        self._last_change_at = 0.0
        self._last_submitted_at = 0.0
        self._recent_submissions: dict[str, float] = {}

    def start(self) -> None:
        with self._state_lock:
            if self._worker and self._worker.is_alive():
                print("Bot already running")
                return

            self._stop_event.clear()
            self._last_frame_hash = None
            self._last_change_at = 0.0
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()

        print(f"Bot started in {self.config.ocr_mode} mode")

    def stop(self) -> None:
        self._stop_event.set()
        print("Bot stopped")

    def calibrate_input_box(self) -> None:
        position = pyautogui.position()
        with self._position_lock:
            self.input_box = (position.x, position.y)
        print(f"Input box set to {position.x},{position.y}")

    def calibrate_submit_button(self) -> None:
        position = pyautogui.position()
        with self._position_lock:
            self.submit_button = (position.x, position.y)
        print(f"Submit button set to {position.x},{position.y}")

    def print_positions(self) -> None:
        position = pyautogui.position()
        with self._position_lock:
            input_box = self.input_box
            submit_button = self.submit_button

        print(f"Mouse position: {position.x},{position.y}")
        print(f"CAPTURE_REGION={format_tuple(self.config.capture_region)}")
        print(f"INPUT_BOX={format_tuple(input_box)}")
        print(f"SUBMIT_BUTTON={format_tuple(submit_button)}")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._watch_once()
            except Exception as exc:
                print(f"Bot loop error: {exc}")

            self._stop_event.wait(self.config.loop_delay_seconds)

    def _watch_once(self) -> None:
        image = capture_screen(self.config)
        current_hash = frame_hash(image)
        now = time.monotonic()

        if self._last_frame_hash is None:
            self._last_frame_hash = current_hash
            if self.config.process_initial_frame:
                self._handle_changed_image(image)
            return

        if current_hash == self._last_frame_hash:
            return

        self._last_frame_hash = current_hash
        if now - self._last_change_at < self.config.change_cooldown_seconds:
            return

        self._last_change_at = now
        if self.config.post_change_settle_seconds > 0:
            if self._stop_event.wait(self.config.post_change_settle_seconds):
                return
            image = capture_screen(self.config)
            self._last_frame_hash = frame_hash(image)

        self._handle_changed_image(image)

    def _handle_changed_image(self, image: Any) -> None:
        detected_text = self._read_text(image)
        cleaned_text = clean_detected_text(
            detected_text,
            self.config.character_replacements,
        )

        if not cleaned_text:
            print("No text detected")
            return

        if self._is_recent_duplicate(cleaned_text):
            print(f"Duplicate skipped: {cleaned_text}")
            return

        self._wait_for_submit_cooldown()
        if self._stop_event.is_set():
            return

        self._paste_and_submit(cleaned_text)
        self._mark_submitted(cleaned_text)
        print(f"Submitted: {cleaned_text}")

    def _read_text(self, image: Any) -> str:
        if self.config.ocr_mode == "nvidia":
            return self.nvidia.read_text(image, self._stop_event)

        easyocr_text = self.easyocr.read_text(image)
        if easyocr_text or self.config.ocr_mode == "easyocr":
            return easyocr_text

        return self.nvidia.read_text(image, self._stop_event)

    def _is_recent_duplicate(self, text: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.config.duplicate_text_window_seconds
        key = duplicate_key(text)

        expired = [
            submitted_key
            for submitted_key, submitted_at in self._recent_submissions.items()
            if submitted_at < cutoff
        ]
        for submitted_key in expired:
            del self._recent_submissions[submitted_key]

        return key in self._recent_submissions

    def _wait_for_submit_cooldown(self) -> None:
        elapsed = time.monotonic() - self._last_submitted_at
        remaining = self.config.min_seconds_between_submissions - elapsed
        if remaining > 0:
            self._stop_event.wait(remaining)

    def _paste_and_submit(self, text: str) -> None:
        with self._position_lock:
            input_box = self.input_box
            submit_button = self.submit_button

        pyperclip.copy(text)
        pyautogui.click(*input_box)
        if self.config.paste_delay_seconds > 0:
            time.sleep(self.config.paste_delay_seconds)

        if self.config.clear_input_before_paste:
            pyautogui.hotkey("ctrl", "a")

        pyautogui.hotkey("ctrl", "v")
        if self.config.submit_delay_seconds > 0:
            time.sleep(self.config.submit_delay_seconds)

        pyautogui.click(*submit_button)

    def _mark_submitted(self, text: str) -> None:
        now = time.monotonic()
        self._last_submitted_at = now
        self._recent_submissions[duplicate_key(text)] = now


def print_hotkeys(config: Config) -> None:
    print("F6 = Set input box to current mouse position")
    print("F7 = Set submit button to current mouse position")
    print("F8 = Start bot")
    print("F9 = Stop bot")
    print("F12 = Print current mouse/config positions")
    print(f"OCR_MODE={config.ocr_mode}")
    print(f"CAPTURE_REGION={format_tuple(config.capture_region)}")


def main() -> None:
    load_dotenv()
    config = read_config()

    pyautogui.PAUSE = config.pyautogui_pause
    pyautogui.FAILSAFE = config.pyautogui_failsafe

    bot = ScreenOcrBot(config)
    keyboard.add_hotkey("F6", bot.calibrate_input_box)
    keyboard.add_hotkey("F7", bot.calibrate_submit_button)
    keyboard.add_hotkey("F8", bot.start)
    keyboard.add_hotkey("F9", bot.stop)
    keyboard.add_hotkey("F12", bot.print_positions)

    print_hotkeys(config)
    keyboard.wait()


if __name__ == "__main__":
    main()
