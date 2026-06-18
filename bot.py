import time
import pyautogui
import pyperclip
import cv2
import numpy as np
import easyocr
import keyboard

# ---------------- CONFIG ----------------

CAPTURE_REGION = (70, 175, 255, 175)  # x, y, width, height
INPUT_BOX = (100, 325)
SUBMIT_BUTTON = (200, 400)

RUNNING = False

# ---------------- OCR ----------------

reader = easyocr.Reader(['en'], gpu=False)

def capture_screen():
    screenshot = pyautogui.screenshot(region=CAPTURE_REGION)
    return np.array(screenshot)

def preprocess_image(img):
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    _, thresh = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    return thresh

def extract_text(img):
    results = reader.readtext(img)

    text = ""

    for result in results:
        detected_text = result[1]
        confidence = result[2]

        if confidence > 0.70:
            text += detected_text + " "

    return text.strip()

# ---------------- CLEANING ----------------

def clean_text(text):

    replacements = {
        ":": "=",
        "5": "s",
        "9": "g",
        "O": "@",
        "?": "@"
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text

# ---------------- PASTE ----------------

def paste_text(text):

    pyperclip.copy(text)

    pyautogui.click(*INPUT_BOX)

    pyautogui.hotkey("ctrl", "v")

    time.sleep(0.2)

    pyautogui.click(*SUBMIT_BUTTON)

# ---------------- MAIN LOOP ----------------

def start_bot():
    global RUNNING

    RUNNING = True

    print("Bot Started")

    while RUNNING:

        image = capture_screen()

        processed = preprocess_image(image)

        text = extract_text(processed)

        if text:

            text = clean_text(text)

            print(f"Detected: {text}")

            paste_text(text)

        time.sleep(1)

def stop_bot():
    global RUNNING

    RUNNING = False

    print("Bot Stopped")

# ---------------- HOTKEYS ----------------

keyboard.add_hotkey("F8", start_bot)
keyboard.add_hotkey("F9", stop_bot)

print("F8 = Start")
print("F9 = Stop")

keyboard.wait()