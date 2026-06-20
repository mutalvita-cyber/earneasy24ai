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
DEFAULT_STATUS_POINT = (0, 0)

DEFAULT_NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_NVIDIA_MODEL = "nvidia/llama-3.1-nemotron-nano-vl-8b-v1"


@dataclass(frozen=True)
class Config:
    capture_region: tuple[int, int, int, int]
    loop_delay_seconds: float
    change_cooldown_seconds: float
    post_change_settle_seconds: float
    process_initial_frame: bool
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
    min_captcha_length: int
    max_captcha_length: int
    character_replacements: dict[str, str]
    status_point: tuple[int, int]
    double_check: bool


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



def read_config() -> Config:
    replacements_str = env_str("CHARACTER_REPLACEMENTS_JSON", "")
    replacements = {}
    if replacements_str:
        try:
            replacements = json.loads(replacements_str)
        except Exception as e:
            print(f"Error parsing CHARACTER_REPLACEMENTS_JSON: {e}")

    return Config(
        capture_region=parse_int_tuple("CAPTURE_REGION", 4, DEFAULT_CAPTURE_REGION),
        loop_delay_seconds=env_float("LOOP_DELAY_SECONDS", 0.15),
        change_cooldown_seconds=env_float("CHANGE_COOLDOWN_SECONDS", 3.0),
        post_change_settle_seconds=env_float("POST_CHANGE_SETTLE_SECONDS", 0.15),
        process_initial_frame=env_bool("PROCESS_INITIAL_FRAME", True),
        nvidia_api_url=env_str("NVIDIA_API_URL", DEFAULT_NVIDIA_API_URL),
        nvidia_model=env_str("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL),
        nvidia_api_key=env_str("NVIDIA_API_KEY", env_str("NIM_API_KEY")),
        ai_timeout_seconds=env_float("AI_TIMEOUT_SECONDS", 60.0),
        ai_connect_timeout_seconds=env_float("AI_CONNECT_TIMEOUT_SECONDS", 5.0),
        ai_max_tokens=env_int("AI_MAX_TOKENS", 512),
        ai_fallback_min_interval_seconds=env_float(
            "AI_FALLBACK_MIN_INTERVAL_SECONDS",
            0.5,
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
        min_captcha_length=env_int("MIN_CAPTCHA_LENGTH", 6),
        max_captcha_length=env_int("MAX_CAPTCHA_LENGTH", 20),
        character_replacements=replacements,
        status_point=parse_int_tuple("STATUS_POINT", 2, DEFAULT_STATUS_POINT),
        double_check=env_bool("DOUBLE_CHECK", True),
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
    
    # Support Chain-of-Thought output: search for "CAPTCHA: <value>" (case-insensitive)
    match = re.search(r"CAPTCHA\s*:\s*([^\s\n]+)", text, re.IGNORECASE)
    if match:
        text = match.group(1).strip()

    # Remove markdown formatting characters (bold, italics, headers, etc.)
    text = text.replace("**", "").replace("__", "").replace("*", "").replace("_", "")
    
    # Strip common code blocks
    text = re.sub(r"^```(?:text)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    
    # Strip common prefixes like "Answer:", "Captcha:", "Text:", "Result:", "Value:", etc.
    # including "The answer is:", "The captcha is:"
    prefix_pattern = r"^(?:the\s+)?(?:captcha|answer|text|value|solution|challenge|result)(?:\s+is)?\s*[:=-]\s*"
    text = re.sub(prefix_pattern, "", text, flags=re.IGNORECASE).strip()
    
    # Strip quotes, backticks, and extra spaces again
    text = text.strip("\"'` ")

    lower_text = text.lower()
    if any(phrase in lower_text for phrase in ["sorry", "cannot read", "unable to", "don't see", "no text", "clear text", "i see", "loading spinner", "blinking cursor"]):
        return ""

    # Reject literal template placeholders the AI might echo from the prompt
    if text in {"<answer>", "VALUE", "value", "[answer]", "[value]"}:
        return ""

    # Strip trailing punctuation for the none-check (handles 'NONE.' / 'NONE!' etc.)
    stripped_lower = lower_text.rstrip(".,!?;:")
    if stripped_lower in {"none", "no text", "no visible text", "n/a", ""}:
        return ""

    return text


def clean_detected_text(text: str, replacements: dict[str, str] = None) -> str:
    text = clean_model_output(text)
    if replacements:
        for old, new in replacements.items():
            text = text.replace(old, new)
    text = text.replace("\r", "").replace("\n", "")
    text = "".join(text.split())
    # Strip non-printable-ASCII characters (e.g. £, accented letters, control chars)
    text = "".join(ch for ch in text if 32 <= ord(ch) <= 126)
    return text


def solve_math_captcha(text: str) -> str:
    """If *text* is a pure arithmetic expression, evaluate and return the result.

    Handles expressions like: 6-8, +53-208, 3+5*2, 10/2
    Returns the original text unchanged if it does not look like math.
    """
    # Only attempt eval when the entire text is an arithmetic expression
    # Allow digits, +, -, *, /, spaces, parentheses — nothing else
    if not re.match(r"^[\d\s+\-*/().]+$", text):
        return text
    # Must contain at least one operator to be a math expression
    if not re.search(r"[+\-*/]", text):
        return text
    try:
        result = eval(  # noqa: S307  # safe: only arithmetic chars allowed
            text,
            {"__builtins__": {}},
            {},
        )
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return str(result)
    except Exception:
        return text



def duplicate_key(text: str) -> str:
    return " ".join(text.casefold().split())


def _preprocess_for_ai(image: Any) -> Any:
    """Upscale the CAPTCHA image so the AI model can read it clearly.

    The captured region is scaled up 2× with a high-quality filter to give the
    vision model cleaner character shapes, without sharpening artifacts.
    """
    try:
        from PIL import Image

        # Convert to RGB so all downstream operations are consistent
        img = image.convert("RGB")

        # Upscale 2× with LANCZOS (best quality for downsampled text)
        w, h = img.size
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = Image.ANTIALIAS  # type: ignore[attr-defined]
        img = img.resize((w * 2, h * 2), resample)

        return img
    except Exception:
        # If preprocessing fails for any reason, return original image
        return image


class NvidiaVisionClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._last_call_at = 0.0
        self._missing_key_reported = False

    def read_text(
        self,
        image: Any,
        stop_event: threading.Event | None = None,
        bypass_cooldown: bool = False,
    ) -> str:
        if not self.config.nvidia_api_key:
            if not self._missing_key_reported:
                print("NVIDIA fallback unavailable: NVIDIA_API_KEY is not set")
                self._missing_key_reported = True
            return ""

        if not bypass_cooldown:
            now = time.monotonic()
            elapsed = now - self._last_call_at
            if elapsed < self.config.ai_fallback_min_interval_seconds:
                remaining = self.config.ai_fallback_min_interval_seconds - elapsed
                if stop_event is not None and stop_event.wait(remaining):
                    return ""
                if stop_event is None:
                    time.sleep(remaining)

        self._last_call_at = time.monotonic()
        payload = {
            "model": self.config.nvidia_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analyze this CAPTCHA image and output ONLY the answer in this exact format:\n"
                                "CAPTCHA: VALUE\n"
                                "\n"
                                "Guidelines:\n"
                                "1. Transcribe every character exactly from left to right, including '%', '/', etc.\n"
                                "2. Ignore background noise (strike-through lines, grid lines).\n"
                                "3. For math equations, compute and output ONLY the numeric result.\n"
                                "4. Be careful with look-alike characters (0/O, 1/l/I, 9/g, S/5, Z/2).\n"
                                "5. Do NOT write any steps, explanations, or thoughts. Output ONLY 'CAPTCHA: <value>' (or 'CAPTCHA: NONE' if unreadable)."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_to_png_data_url(
                                _preprocess_for_ai(image)
                            )},
                        },
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": self.config.ai_max_tokens,
            "stream": False,
        }

        for attempt in range(1, 4):
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
                print(f"[NVIDIA] Attempt {attempt} failed: {exc}")
                if attempt < 3:
                    if stop_event is not None and stop_event.wait(1.0):
                        return ""
                    elif stop_event is None:
                        time.sleep(1.0)

        print("NVIDIA fallback failed after 3 attempts")
        return ""

    def _auth_header(self) -> str:
        key = self.config.nvidia_api_key.strip()
        if key.lower().startswith("bearer "):
            return key
        return f"Bearer {key}"


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
        self.status_point = parse_int_tuple(
            "STATUS_POINT",
            2,
            DEFAULT_STATUS_POINT,
        )
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

        print("Bot started (NVIDIA vision mode)")

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

    def calibrate_status_point(self) -> None:
        position = pyautogui.position()
        with self._position_lock:
            self.status_point = (position.x, position.y)
        print(f"Status point set to {position.x},{position.y}")

    def print_positions(self) -> None:
        position = pyautogui.position()
        with self._position_lock:
            input_box = self.input_box
            submit_button = self.submit_button
            status_point = self.status_point

        print(f"Mouse position: {position.x},{position.y}")
        print(f"CAPTURE_REGION={format_tuple(self.config.capture_region)}")
        print(f"INPUT_BOX={format_tuple(input_box)}")
        print(f"SUBMIT_BUTTON={format_tuple(submit_button)}")
        print(f"STATUS_POINT={format_tuple(status_point)}")

        try:
            image = capture_screen(self.config)
            debug_path = Path("debug_capture.png")
            image.save(debug_path)
            print(f"[Debug] Saved screenshot of CAPTCHA region to: {debug_path.resolve()}")
        except Exception as e:
            print(f"[Debug] Failed to save screenshot: {e}")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._step()
            except Exception as exc:
                print(f"Bot loop error: {exc}")

            self._stop_event.wait(self.config.loop_delay_seconds)

    def _step(self) -> None:
        image = capture_screen(self.config)
        
        current_hash = frame_hash(image)
        if self._last_frame_hash and current_hash == self._last_frame_hash:
            return
        self._last_frame_hash = current_hash

        detected_text = self._read_and_confirm_text(image)
        cleaned_text = clean_detected_text(detected_text, self.config.character_replacements)

        if not cleaned_text:
            time.sleep(1.0)
            self._last_frame_hash = None
            return

        # Solve calculative CAPTCHAs (e.g. "6-8" → "-2", "+53-208" → "-155")
        cleaned_text = solve_math_captcha(cleaned_text)

        # Check if it's a pure number (math CAPTCHA result)
        is_pure_numeric = bool(re.match(r"^-?\d+$", cleaned_text))

        if not is_pure_numeric and len(cleaned_text) < self.config.min_captcha_length:
            time.sleep(1.0)
            self._last_frame_hash = None
            return

        if len(cleaned_text) > self.config.max_captcha_length:
            time.sleep(1.0)
            self._last_frame_hash = None
            return

        if self._is_recent_duplicate(cleaned_text):
            return

        self._wait_for_submit_cooldown()
        if self._stop_event.is_set():
            return

        self._paste_and_submit(cleaned_text)
        self._mark_submitted(cleaned_text)
        print(f"Submitted CAPTCHA: {cleaned_text}")

        check_start = time.monotonic()
        self._check_submission_status()
        check_duration = time.monotonic() - check_start

        # Wait a few seconds until new CAPTCHA comes and repeat
        remaining_cooldown = max(0.1, self.config.change_cooldown_seconds - check_duration)
        print(f"Waiting {remaining_cooldown:.1f} seconds for new CAPTCHA...")
        self._stop_event.wait(remaining_cooldown)

    def _check_submission_status(self) -> None:
        with self._position_lock:
            status_point = self.status_point

        if not status_point or status_point == (0, 0):
            return

        # Poll for up to 3.5 seconds
        start_time = time.monotonic()
        detected_status = None
        while time.monotonic() - start_time < 3.5:
            if self._stop_event.is_set():
                break
            try:
                # Get the pixel color at status_point
                r, g, b = pyautogui.pixel(*status_point)
                # Red-ish: r > 120 and r > g + 40 and r > b + 40
                if r > 120 and r > g + 40 and r > b + 40:
                    detected_status = "Wrong"
                    break
                # Green-ish: g > 120 and g > r + 40 and g > b + 20
                elif g > 120 and g > r + 40 and g > b + 20:
                    detected_status = "Correct"
                    break
            except Exception:
                # Sometimes pyautogui.pixel fails if coordinates are out of bounds
                pass
            time.sleep(0.1)

        if detected_status:
            print(f"Submission Result: {detected_status}")
        else:
            print("Submission Result: Unknown (No status banner detected)")

    def _read_and_confirm_text(self, image: Any) -> str:
        """Read CAPTCHA text using NVIDIA vision API with parallel verification for speed and accuracy."""
        import concurrent.futures

        if not self.config.double_check:
            return self.nvidia.read_text(image, self._stop_event)

        # Prepare the three images: original, jittered1 (2px crop), jittered2 (4px crop)
        images = [image]
        
        try:
            w, h = image.size
            if w > 4 and h > 4:
                images.append(image.crop((2, 2, w - 2, h - 2)))
            else:
                images.append(image)
        except Exception:
            images.append(image)

        try:
            w, h = image.size
            if w > 8 and h > 8:
                images.append(image.crop((4, 4, w - 4, h - 4)))
            else:
                images.append(image)
        except Exception:
            images.append(image)

        # Run all 3 reads in parallel
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            # We bypass cooldown for individual parallel requests of the same frame
            futures = [
                executor.submit(self.nvidia.read_text, img, self._stop_event, True)
                for img in images
            ]
            # Gather results in order
            for future in futures:
                try:
                    results.append(future.result())
                except Exception as e:
                    print(f"[Confirm] Parallel read error: {e}")
                    results.append("")

        # Update last call time to now so the next loop step respects the cooldown
        self.nvidia._last_call_at = time.monotonic()

        text1, text2, text3 = results[0], results[1], results[2]

        # Process and clean the results for voting
        cleaned1 = clean_detected_text(text1, self.config.character_replacements) if text1 else ""
        cleaned2 = clean_detected_text(text2, self.config.character_replacements) if text2 else ""
        cleaned3 = clean_detected_text(text3, self.config.character_replacements) if text3 else ""

        # Solve math captcha if applicable
        cleaned1 = solve_math_captcha(cleaned1)
        cleaned2 = solve_math_captcha(cleaned2)
        cleaned3 = solve_math_captcha(cleaned3)

        # Voting logic
        if cleaned1 and cleaned1 == cleaned2:
            print(f"[Confirm] Parallel Agree (Run 1 & 2): '{cleaned1}'")
            return text1
        elif cleaned1 and cleaned1 == cleaned3:
            print(f"[Confirm] Parallel Agree (Run 1 & 3): '{cleaned1}'")
            return text1
        elif cleaned2 and cleaned2 == cleaned3:
            print(f"[Confirm] Parallel Agree (Run 2 & 3): '{cleaned2}'")
            return text2
        else:
            # If no consensus, fallback to the first read if it succeeded, else second, else third
            fallback = text1 or text2 or text3
            fallback_cleaned = cleaned1 or cleaned2 or cleaned3
            print(f"[Confirm] Parallel Mismatch ('{cleaned1}' vs '{cleaned2}' vs '{cleaned3}'). Best guess: '{fallback_cleaned}'")
            return fallback

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

        # Ensure the text is properly copied to the clipboard
        pyperclip.copy(text)
        start_copy = time.monotonic()
        while time.monotonic() - start_copy < 1.0:
            try:
                if pyperclip.paste() == text:
                    break
            except Exception:
                pass
            time.sleep(0.05)

        # Focus the input field
        pyautogui.click(*input_box)
        # Give emulator/OS time to focus window and input field
        time.sleep(max(0.2, self.config.paste_delay_seconds))

        # Clear existing text reliably
        if self.config.clear_input_before_paste:
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.1)
            pyautogui.press("backspace")
            time.sleep(0.1)

        # Paste the text
        pyautogui.hotkey("ctrl", "v")
        # Give emulator time to process paste and draw characters
        time.sleep(max(0.2, self.config.submit_delay_seconds))

        # Click submit
        pyautogui.click(*submit_button)

    def _mark_submitted(self, text: str) -> None:
        now = time.monotonic()
        self._last_submitted_at = now
        self._recent_submissions[duplicate_key(text)] = now


def print_hotkeys(config: Config) -> None:
    print("F6 = Set input box to current mouse position")
    print("F7 = Set submit button to current mouse position")
    print("F11 = Set status banner point to current mouse position")
    print("F8 = Start bot")
    print("F9 = Stop bot")
    print("F12 = Print current mouse/config positions")
    print(f"CAPTURE_REGION={format_tuple(config.capture_region)}")


def make_dpi_aware() -> None:
    import sys
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def main() -> None:
    make_dpi_aware()
    load_dotenv()
    config = read_config()

    pyautogui.PAUSE = config.pyautogui_pause
    pyautogui.FAILSAFE = config.pyautogui_failsafe

    bot = ScreenOcrBot(config)
    keyboard.add_hotkey("F6", bot.calibrate_input_box)
    keyboard.add_hotkey("F7", bot.calibrate_submit_button)
    keyboard.add_hotkey("F11", bot.calibrate_status_point)
    keyboard.add_hotkey("F8", bot.start)
    keyboard.add_hotkey("F9", bot.stop)
    keyboard.add_hotkey("F12", bot.print_positions)

    print_hotkeys(config)
    keyboard.wait()


if __name__ == "__main__":
    main()
