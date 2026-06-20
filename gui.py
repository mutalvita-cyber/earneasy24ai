from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import keyboard
import pyautogui

# Import bot modules safely
from bot import Config, ScreenOcrBot, capture_screen, load_dotenv, read_config


def make_dpi_aware() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


# Safe queue writer for stdout redirection
class QueueWriter:

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, text: str):
        if text.strip():
            self.q.put(text.strip())

    def flush(self):
        pass


class SnippingOverlay:

    def __init__(self, parent: tk.Tk, callback: callable):
        self.parent = parent
        self.callback = callback

        self.window = tk.Toplevel(parent)
        self.window.attributes("-alpha", 0.25)  # Semi-transparent
        self.window.attributes("-fullscreen", True)
        self.window.attributes("-topmost", True)
        self.window.config(cursor="cross")
        self.window.overrideredirect(True)

        self.canvas = tk.Canvas(self.window, bg="grey15", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Escape>", lambda e: self.window.destroy())

        self.start_x = 0
        self.start_y = 0
        self.rect = None

        # Display calibration help text overlay
        self.canvas.create_text(
            self.window.winfo_screenwidth() // 2,
            50,
            text="DRAG A RECTANGLE AROUND THE CAPTCHA TEXT BOX\nPress ESC to Cancel",
            fill="white",
            font=("Segoe UI", 16, "bold"),
            justify="center",
        )

    def on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.canvas.create_rectangle(
            self.start_x,
            self.start_y,
            self.start_x,
            self.start_y,
            outline="#2ecc71",
            width=2,
        )

    def on_drag(self, event):
        if self.rect:
            self.canvas.coords(
                self.rect, self.start_x, self.start_y, event.x, event.y
            )

    def on_release(self, event):
        end_x = event.x
        end_y = event.y
        self.window.destroy()

        left = min(self.start_x, end_x)
        top = min(self.start_y, end_y)
        width = abs(self.start_x - end_x)
        height = abs(self.start_y - end_y)

        if width > 5 and height > 5:
            self.callback((left, top, width, height))


class ClickOverlay:

    def __init__(self, parent: tk.Tk, label: str, callback: callable):
        self.parent = parent
        self.callback = callback

        self.window = tk.Toplevel(parent)
        self.window.attributes("-alpha", 0.25)
        self.window.attributes("-fullscreen", True)
        self.window.attributes("-topmost", True)
        self.window.config(cursor="crosshair")
        self.window.overrideredirect(True)

        self.canvas = tk.Canvas(self.window, bg="grey15", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_click)
        self.canvas.bind("<Escape>", lambda e: self.window.destroy())

        self.canvas.create_text(
            self.window.winfo_screenwidth() // 2,
            50,
            text=f"CLICK ON: {label.upper()}\nPress ESC to Cancel",
            fill="white",
            font=("Segoe UI", 16, "bold"),
            justify="center",
        )

    def on_click(self, event):
        x, y = event.x, event.y
        self.window.destroy()
        self.callback((x, y))


class BotApp:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Earneasy24 Screen Bot Controller")
        self.root.geometry("1000x680")
        self.root.minsize(850, 550)

        # Style colors
        self.bg_color = "#1e1e1e"
        self.sidebar_bg = "#252526"
        self.accent_green = "#2ecc71"
        self.accent_red = "#e74c3c"
        self.text_color = "#ffffff"

        self.root.configure(bg=self.bg_color)

        # Load environment config
        load_dotenv()
        self.config = read_config()

        # Initialize Bot Backend
        self.bot = ScreenOcrBot(self.config)

        # Set up stdout redirection queue
        self.log_queue = queue.Queue()
        sys.stdout = QueueWriter(self.log_queue)

        # Hotkeys bindings
        self.setup_keyboard_hotkeys()

        # Build UI Components
        self.setup_styles()
        self.build_menu()
        self.build_layout()

        # Start periodic log checks and coordinate checks
        self.root.after(100, self.process_log_queue)
        self.root.after(500, self.poll_coordinates)

        self.add_log_row("System Initialization", "GUI started and ready.")

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Treeview styling
        style.configure(
            "Treeview",
            background="#2d2d2d",
            foreground="white",
            rowheight=25,
            fieldbackground="#2d2d2d",
            gridcolor="#444444",
            font=("Segoe UI", 10),
        )
        style.map("Treeview", background=[("selected", "#007acc")])
        style.configure(
            "Treeview.Heading",
            background="#252526",
            foreground="white",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
        )

        # Scrollbar styling
        style.configure(
            "Vertical.TScrollbar",
            gripcount=0,
            background="#444444",
            troughcolor="#2d2d2d",
            bordercolor="#2d2d2d",
            arrowcolor="white",
        )

    def build_menu(self):
        menu_bar = tk.Menu(self.root)
        file_menu = tk.Menu(menu_bar, tearoff=0)
        file_menu.add_command(label="Exit", command=self.on_closing)
        menu_bar.add_cascade(label="File", menu=file_menu)

        cal_menu = tk.Menu(menu_bar, tearoff=0)
        cal_menu.add_command(
            label="Calibrate CAPTCHA Region",
            command=self.start_region_calibration,
        )
        cal_menu.add_command(
            label="Calibrate Input Box Position",
            command=lambda: self.start_click_calibration("Input Box"),
        )
        cal_menu.add_command(
            label="Calibrate Submit Button Position",
            command=lambda: self.start_click_calibration("Submit Button"),
        )
        cal_menu.add_command(
            label="Calibrate Status Banner Point",
            command=lambda: self.start_click_calibration("Status Point"),
        )
        menu_bar.add_cascade(label="Calibrate", menu=cal_menu)

        self.root.config(menu=menu_bar)

    def build_layout(self):
        # 1. Sidebar (Settings) - Left side
        sidebar = tk.Frame(
            self.root, bg=self.sidebar_bg, width=320, bd=0, relief="flat"
        )
        sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=0, pady=0)
        sidebar.pack_propagate(False)

        # Sidebar Title
        tk.Label(
            sidebar,
            text="SETTINGS",
            font=("Segoe UI", 12, "bold"),
            bg=self.sidebar_bg,
            fg="#858585",
            anchor="w",
        ).pack(fill=tk.X, padx=15, pady=(15, 10))

        # Scrollable Settings Container
        settings_canvas = tk.Canvas(
            sidebar, bg=self.sidebar_bg, highlightthickness=0
        )
        settings_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=0)

        scrollbar = ttk.Scrollbar(
            sidebar, orient="vertical", command=settings_canvas.yview
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        settings_canvas.configure(yscrollcommand=scrollbar.set)

        self.settings_frame = tk.Frame(settings_canvas, bg=self.sidebar_bg)
        settings_canvas.create_window(
            (0, 0), window=self.settings_frame, anchor="nw", width=290
        )

        def configure_canvas(event):
            settings_canvas.configure(
                scrollregion=settings_canvas.bbox("all")
            )

        self.settings_frame.bind("<Configure>", configure_canvas)

        # Build Settings Inputs
        self.inputs = {}
        self.build_settings_form()

        # 2. Main Area - Right side
        main_panel = tk.Frame(self.root, bg=self.bg_color)
        main_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=0)

        # Toolbar
        toolbar = tk.Frame(main_panel, bg=self.bg_color, height=85)
        toolbar.pack(fill=tk.X, pady=(10, 5))

        # Play/Start Button (Styled like Macro Recorder)
        self.btn_play = tk.Button(
            toolbar,
            text="▶\nPlay",
            font=("Segoe UI", 10, "bold"),
            bg="#2d2d2d",
            fg=self.accent_green,
            activebackground="#3e3e3e",
            activeforeground=self.accent_green,
            relief="flat",
            bd=0,
            width=8,
            height=3,
            cursor="hand2",
            command=self.start_bot,
        )
        self.btn_play.pack(side=tk.LEFT, padx=5)

        # Stop Button
        self.btn_stop = tk.Button(
            toolbar,
            text="■\nStop",
            font=("Segoe UI", 10, "bold"),
            bg="#2d2d2d",
            fg=self.accent_red,
            activebackground="#3e3e3e",
            activeforeground=self.accent_red,
            relief="flat",
            bd=0,
            width=8,
            height=3,
            cursor="hand2",
            command=self.stop_bot,
        )
        self.btn_stop.pack(side=tk.LEFT, padx=5)

        # Separator Line
        sep = tk.Frame(toolbar, bg="#444444", width=2, height=50)
        sep.pack(side=tk.LEFT, padx=15, pady=10)

        # Calibration Controls Group
        self.btn_cal_region = self.create_toolbar_button(
            toolbar, "⛶\nRegion", self.start_region_calibration
        )
        self.btn_cal_input = self.create_toolbar_button(
            toolbar, "🎯\nInput", lambda: self.start_click_calibration("Input")
        )
        self.btn_cal_submit = self.create_toolbar_button(
            toolbar,
            "🎯\nSubmit",
            lambda: self.start_click_calibration("Submit"),
        )
        self.btn_cal_status = self.create_toolbar_button(
            toolbar, "🎯\nStatus", lambda: self.start_click_calibration("Status")
        )

        sep2 = tk.Frame(toolbar, bg="#444444", width=2, height=50)
        sep2.pack(side=tk.LEFT, padx=15, pady=10)

        # Screenshot, Clear, & Copy logs Buttons
        self.btn_screenshot = self.create_toolbar_button(
            toolbar, "📸\nCapture", self.trigger_debug_screenshot
        )
        self.btn_clear = self.create_toolbar_button(
            toolbar, "🧹\nClear", self.clear_logs
        )
        self.btn_copy = self.create_toolbar_button(
            toolbar, "📋\nCopy", self.copy_logs
        )

        # Central Table (Action List)
        table_frame = tk.Frame(main_panel, bg=self.bg_color)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        cols = ("Time", "Action", "Value")
        self.tree = ttk.Treeview(
            table_frame, columns=cols, show="headings", selectmode="browse"
        )

        self.tree.heading("Time", text="Time")
        self.tree.heading("Action", text="Action")
        self.tree.heading("Value", text="Value / Details")

        self.tree.column("Time", width=120, minwidth=100, stretch=False)
        self.tree.column("Action", width=180, minwidth=150, stretch=False)
        self.tree.column("Value", width=350, minwidth=250, stretch=True)

        scrollbar_tree = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar_tree.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_tree.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.tag_configure("correct", foreground=self.accent_green)
        self.tree.tag_configure("wrong", foreground=self.accent_red)
        self.tree.tag_configure("mismatch", foreground="#FFA500")

        # Bottom Status Bar
        self.status_bar = tk.Frame(main_panel, bg="#252526", height=28)
        self.status_bar.pack(fill=tk.X, pady=(5, 10))

        self.lbl_status = tk.Label(
            self.status_bar,
            text="STATUS: STOPPED",
            font=("Segoe UI", 9, "bold"),
            bg="#252526",
            fg=self.accent_red,
        )
        self.lbl_status.pack(side=tk.LEFT, padx=10, pady=3)

        self.lbl_coords = tk.Label(
            self.status_bar,
            text="CAPTURE: None | INPUT: None | SUBMIT: None",
            font=("Segoe UI", 9),
            bg="#252526",
            fg="#aaaaaa",
        )
        self.lbl_coords.pack(side=tk.RIGHT, padx=10, pady=3)

        # Initialize coordinate labels
        self.update_coordinates_display()

    def create_toolbar_button(
        self, parent: tk.Frame, text: str, command: callable
    ) -> tk.Button:
        btn = tk.Button(
            parent,
            text=text,
            font=("Segoe UI", 9),
            bg="#2d2d2d",
            fg=self.text_color,
            activebackground="#3e3e3e",
            activeforeground=self.text_color,
            relief="flat",
            bd=0,
            width=8,
            height=3,
            cursor="hand2",
            command=command,
        )
        btn.pack(side=tk.LEFT, padx=3)
        return btn

    def build_settings_form(self):
        f = self.settings_frame

        # Helper to create styled entries
        def add_entry(
            label: str, env_key: str, default_val: str, show_mask: str = None
        ):
            frame = tk.Frame(f, bg=self.sidebar_bg)
            frame.pack(fill=tk.X, padx=10, pady=5)
            tk.Label(
                frame,
                text=label,
                font=("Segoe UI", 9),
                bg=self.sidebar_bg,
                fg="#bbbbbb",
                anchor="w",
            ).pack(fill=tk.X)
            val = os.getenv(env_key, default_val)
            entry = tk.Entry(
                frame,
                bg="#3d3d3d",
                fg="white",
                insertbackground="white",
                bd=1,
                relief="flat",
                show=show_mask,
                font=("Segoe UI", 9),
            )
            entry.insert(0, val)
            entry.pack(fill=tk.X, pady=(2, 0))
            self.inputs[env_key] = entry

        # Helper for Checkboxes
        def add_checkbox(label: str, env_key: str, default_val: bool):
            frame = tk.Frame(f, bg=self.sidebar_bg)
            frame.pack(fill=tk.X, padx=10, pady=5)
            var = tk.BooleanVar(
                value=(os.getenv(env_key, str(default_val)).lower() == "true")
            )
            cb = tk.Checkbutton(
                frame,
                text=label,
                variable=var,
                bg=self.sidebar_bg,
                fg="#bbbbbb",
                selectcolor=self.sidebar_bg,
                activebackground=self.sidebar_bg,
                activeforeground="white",
                font=("Segoe UI", 9),
                bd=0,
            )
            cb.pack(anchor="w")
            self.inputs[env_key] = var

        # Helper for AI mode dropdown
        frame_ocr = tk.Frame(f, bg=self.sidebar_bg)
        frame_ocr.pack(fill=tk.X, padx=10, pady=5)
        tk.Label(
            frame_ocr,
            text="AI Mode",
            font=("Segoe UI", 9),
            bg=self.sidebar_bg,
            fg="#bbbbbb",
            anchor="w",
        ).pack(fill=tk.X)
        self.ocr_var = tk.StringVar(value="nvidia")
        ocr_dropdown = ttk.OptionMenu(
            frame_ocr,
            self.ocr_var,
            "nvidia",
            "nvidia",
        )
        ocr_dropdown.pack(fill=tk.X, pady=(2, 0))

        # Add Configuration Inputs
        add_entry("NVIDIA API Key", "NVIDIA_API_KEY", "", show_mask="*")
        add_entry(
            "NVIDIA Vision Model",
            "NVIDIA_MODEL",
            "nvidia/llama-3.1-nemotron-nano-vl-8b-v1",
        )
        add_entry("Min CAPTCHA Length", "MIN_CAPTCHA_LENGTH", "6")
        add_entry("Max CAPTCHA Length", "MAX_CAPTCHA_LENGTH", "20")

        # Delays & Timeouts
        add_entry("Loop Delay (sec)", "LOOP_DELAY_SECONDS", "0.15")
        add_entry("Change Cooldown (sec)", "CHANGE_COOLDOWN_SECONDS", "3.0")
        add_entry("Paste Delay (sec)", "PASTE_DELAY_SECONDS", "0.05")
        add_entry("Submit Delay (sec)", "SUBMIT_DELAY_SECONDS", "0.05")
        add_entry("AI Connect Timeout (sec)", "AI_CONNECT_TIMEOUT_SECONDS", "3.0")
        add_entry("AI Read Timeout (sec)", "AI_TIMEOUT_SECONDS", "15.0")

        # Checkboxes
        add_checkbox("Clear Input Before Paste", "CLEAR_INPUT_BEFORE_PASTE", True)
        add_checkbox("Double-Check (Confirm) CAPTCHAs", "DOUBLE_CHECK", True)

        # Save Button
        btn_save = tk.Button(
            f,
            text="Save Settings",
            font=("Segoe UI", 10, "bold"),
            bg="#2d2d2d",
            fg="white",
            activebackground="#3e3e3e",
            activeforeground="white",
            relief="flat",
            bd=0,
            height=2,
            cursor="hand2",
            command=self.save_settings,
        )
        btn_save.pack(fill=tk.X, padx=10, pady=20)

    # Keyboard global hotkeys handler
    def setup_keyboard_hotkeys(self):
        keyboard.add_hotkey("F6", self.hotkey_calibrate_input)
        keyboard.add_hotkey("F7", self.hotkey_calibrate_submit)
        keyboard.add_hotkey("F11", self.hotkey_calibrate_status)
        keyboard.add_hotkey("F8", self.hotkey_start_bot)
        keyboard.add_hotkey("F9", self.hotkey_stop_bot)
        keyboard.add_hotkey("F12", self.hotkey_print_positions)

    def hotkey_calibrate_input(self):
        self.bot.calibrate_input_box()
        self.update_coordinates_display()
        self.add_log_row(
            "Calibrate Input",
            f"Hotkey F6: Set to {self.bot.input_box[0]},{self.bot.input_box[1]}",
        )

    def hotkey_calibrate_submit(self):
        self.bot.calibrate_submit_button()
        self.update_coordinates_display()
        self.add_log_row(
            "Calibrate Submit",
            f"Hotkey F7: Set to {self.bot.submit_button[0]},{self.bot.submit_button[1]}",
        )

    def hotkey_calibrate_status(self):
        self.bot.calibrate_status_point()
        self.update_coordinates_display()
        self.add_log_row(
            "Calibrate Status",
            f"Hotkey F11: Set to {self.bot.status_point[0]},{self.bot.status_point[1]}",
        )

    def hotkey_start_bot(self):
        self.root.after(0, self.start_bot)

    def hotkey_stop_bot(self):
        self.root.after(0, self.stop_bot)

    def hotkey_print_positions(self):
        self.bot.print_positions()
        self.add_log_row(
            "System Debug", "F12 triggered: printed config positions to CLI"
        )

    # Execution controls
    def start_bot(self):
        # Reload config and refresh all bot internals
        new_config = read_config()
        self.bot.config = new_config
        self.bot.nvidia.config = new_config

        # Update status labels
        self.lbl_status.config(text="STATUS: RUNNING...", fg=self.accent_green)
        self.btn_play.config(bg="#3e3e3e", relief="sunken")

        # Run bot
        self.bot.start()

    def stop_bot(self):
        self.bot.stop()
        self.lbl_status.config(text="STATUS: STOPPED", fg=self.accent_red)
        self.btn_play.config(bg="#2d2d2d", relief="flat")

    # Calibration overlays
    def start_region_calibration(self):
        self.root.iconify()  # Minimize main window
        time.sleep(0.3)  # Wait for minimize animation
        SnippingOverlay(self.root, self.save_region_calibration)

    def save_region_calibration(self, region: tuple[int, int, int, int]):
        self.root.deiconify()  # Restore main window
        self.save_env_values(
            {
                "CAPTURE_REGION": f"{region[0]},{region[1]},{region[2]},{region[3]}"
            }
        )
        self.bot.config = read_config()
        self.update_coordinates_display()
        self.add_log_row(
            "Calibrate Region", f"Set CAPTCHA region to: {region}"
        )

    def start_click_calibration(self, target: str):
        self.root.iconify()
        time.sleep(0.3)
        ClickOverlay(
            self.root, target, lambda pos: self.save_click_calibration(target, pos)
        )

    def save_click_calibration(self, target: str, pos: tuple[int, int]):
        self.root.deiconify()
        key_map = {
            "Input Box": "INPUT_BOX",
            "Input": "INPUT_BOX",
            "Submit Button": "SUBMIT_BUTTON",
            "Submit": "SUBMIT_BUTTON",
            "Status Point": "STATUS_POINT",
            "Status": "STATUS_POINT",
        }
        env_key = key_map.get(target, "INPUT_BOX")

        self.save_env_values({env_key: f"{pos[0]},{pos[1]}"})

        # Sync in-memory coordinates
        if "Input" in target:
            self.bot.input_box = pos
        elif "Submit" in target:
            self.bot.submit_button = pos
        elif "Status" in target:
            self.bot.status_point = pos

        self.update_coordinates_display()
        self.add_log_row(f"Calibrate {target}", f"Set to {pos[0]},{pos[1]}")

    # Settings panel interaction
    def save_settings(self):
        new_values = {"OCR_MODE": self.ocr_var.get()}

        for key, entry in self.inputs.items():
            if isinstance(entry, tk.BooleanVar):
                new_values[key] = str(entry.get())
            else:
                new_values[key] = entry.get()

        self.save_env_values(new_values)
        self.bot.config = read_config()
        messagebox.showinfo("Settings", "Settings saved successfully!")
        self.add_log_row("System Settings", "Updated configurations successfully.")

    def save_env_values(self, updates: dict[str, str]):
        env_path = Path(__file__).parent / ".env"
        lines = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)

        existing_keys = {}
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, _ = stripped.split("=", 1)
                existing_keys[key.strip()] = idx

        for key, val in updates.items():
            if key in existing_keys:
                idx = existing_keys[key]
                lines[idx] = f"{key}={val}\n"
            else:
                lines.append(f"{key}={val}\n")

        env_path.write_text("".join(lines), encoding="utf-8")

    # Logging and table population
    def add_log_row(self, action: str, value: str, tag: str = None):
        current_time = datetime.now().strftime("%H:%M:%S")
        tags = (tag,) if tag else ()
        self.tree.insert("", 0, values=(current_time, action, value), tags=tags)

    def clear_logs(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def copy_logs(self):
        log_lines = []
        for item in reversed(self.tree.get_children()):
            values = self.tree.item(item, "values")
            if values:
                log_lines.append(f"[{values[0]}] {values[1]}: {values[2]}")
        logs_text = "\n".join(log_lines)
        
        self.root.clipboard_clear()
        self.root.clipboard_append(logs_text)
        self.root.update()
        self.add_log_row("System Action", "Logs copied to clipboard.")

    def trigger_debug_screenshot(self):
        try:
            image = capture_screen(self.bot.config)
            debug_path = Path("debug_capture.png")
            image.save(debug_path)
            self.add_log_row(
                "Screenshot Captured", f"Saved to {debug_path.resolve()}"
            )
        except Exception as e:
            self.add_log_row("Screenshot Failed", str(e))

    def parse_log_message(self, msg: str) -> tuple[str, str]:
        if "Submitted CAPTCHA:" in msg:
            val = msg.split("Submitted CAPTCHA:")[-1].strip()
            return "Submitted CAPTCHA", val
        elif "Waiting" in msg and "seconds for new CAPTCHA" in msg:
            return "Loop Control", msg

        elif "[AI] Skipped" in msg:
            return "AI Skipped", msg.split("[AI] Skipped")[-1].strip().lstrip(":- ")
        elif "NVIDIA fallback failed" in msg or "NVIDIA fallback" in msg:
            return "NVIDIA vision AI", msg
        elif "Input box set to" in msg:
            return "Calibrate Input", msg.split("set to")[-1].strip()
        elif "Submit button set to" in msg:
            return "Calibrate Submit", msg.split("set to")[-1].strip()
        elif "Play button set to" in msg:
            return "Calibrate Play", msg.split("set to")[-1].strip()
        elif "Bot started" in msg:
            return "Status Change", msg
        elif "Bot stopped" in msg:
            return "Status Change", "Stopped Bot"
        elif "Clicked Play button at" in msg:
            return "Playback Click", msg.split("at")[-1].strip()
        elif "No text detected" in msg:
            return "AI Fail", "No text detected in region"
        elif "Submission Result:" in msg:
            val = msg.split("Submission Result:")[-1].strip()
            return "Submission Result", val
        elif "Status point set to" in msg:
            return "Calibrate Status", msg.split("set to")[-1].strip()
        elif "[Confirm] Mismatch" in msg:
            val = msg.split("[Confirm] Mismatch")[-1].strip().lstrip(" —-")
            return "Confirm Mismatch", val
        elif "[Confirm] Agreed" in msg:
            val = msg.split("[Confirm] Agreed:")[-1].strip().strip("'")
            return "Confirm OK", val
        elif "[Confirm]" in msg:
            return "Confirm", msg.split("[Confirm]")[-1].strip()

        return "Log", msg

    # Background threads loops
    def process_log_queue(self):
        while not self.log_queue.empty():
            try:
                raw_msg = self.log_queue.get_nowait()
                action, value = self.parse_log_message(raw_msg)
                tag = None
                if action == "Submission Result":
                    if "Correct" in value:
                        tag = "correct"
                    elif "Wrong" in value:
                        tag = "wrong"
                elif action == "Confirm Mismatch":
                    tag = "mismatch"
                elif action == "Confirm OK":
                    tag = "correct"
                self.add_log_row(action, value, tag)
            except queue.Empty:
                break
        self.root.after(100, self.process_log_queue)

    def poll_coordinates(self):
        self.update_coordinates_display()
        self.root.after(500, self.poll_coordinates)

    def update_coordinates_display(self):
        c = self.bot.config
        region_str = ",".join(str(x) for x in c.capture_region)
        input_str = f"{self.bot.input_box[0]},{self.bot.input_box[1]}"
        submit_str = f"{self.bot.submit_button[0]},{self.bot.submit_button[1]}"
        status_str = f"{self.bot.status_point[0]},{self.bot.status_point[1]}"

        text = f"CAPTURE REGION: {region_str}  |  INPUT BOX: {input_str}  |  SUBMIT: {submit_str}  |  STATUS: {status_str}"
        self.lbl_coords.config(text=text)

    def on_closing(self):
        self.bot.stop()
        self.root.destroy()
        sys.exit(0)


def main():
    make_dpi_aware()
    root = tk.Tk()
    app = BotApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
