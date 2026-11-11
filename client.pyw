import socket
import threading
import sounddevice as sd
import numpy as np
import json
import os
import sys
import queue
import time
import urllib.request
import subprocess
import tempfile
import tkinter as tk 
from tkinter import ttk, messagebox, scrolledtext

try:
    import mss
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

try:
    import cv2
    from PIL import Image, ImageTk
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False

# ТЕКУЩАЯ ВЕРСИЯ КЛИЕНТА
CURRENT_VERSION = "1.4"
UPDATE_CHECK_URL = "https://raw.githubusercontent.com/StillJey127/drunspeak/refs/heads/main/version.json"

def resource_path(relative_path):
    """Адаптивное получение пути к ресурсам для .pyw и скомпилированного PyInstaller .exe"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

# Строго папка, в которой находится скрипт / .exe
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def load_config():
    default_config = {
        "volume": 1.2,
        "gate": 45.0,
        "noise_suppression": "Улучшенное (Чистый голос)",
        "loopback": False,
        "server_ip": "127.0.0.1",
        "server_port": 12345,
        "nickname": "User",
        "avatar": "⚡ Молния",
        "mic_device": "",
        "speaker_device": "",
        "theme": "Discord Modern"
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                default_config.update(loaded)
        except Exception as e:
            print(f"Ошибка чтения конфига: {e}")
    else:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Ошибка создания конфига: {e}")
    return default_config

cfg = load_config()

SAMPLE_RATE = 16000  
CHANNELS = 1
BLOCKSIZE = 256  

BUILTIN_AVATARS = [
    "⚡ Молния", "🔥 Огонь", "💀 Череп", "🤖 Робот", "⭐ Звезда", 
    "🎮 Джойстик", "🐱 Кот", "🐶 Пёс", "👻 Призрак", "👑 Корона",
    "🚀 Ракета", "💎 Кристалл", "🐉 Дракон", "🦊 Лиса", "🦄 Единорог",
    "🎯 Цель", "🏆 Кубок", "🎧 Наушники", "💻 Ноутбук", "🍕 Пицца",
    "⚡ Вольт", "🔥 Пламя", "☠️ Пират", "🦾 Киборг", "🌟 Нова",
    "🕹️ Аркада", "🐱 Котэ", "🐺 Волк", "🧛 Вампир", "💎 Сапфир",
    "🛸 НЛО", "🔮 Шар", "🦅 Орёл", "🐯 Тигр", "🦁 Лев",
    "🎯 Снайпер", "🥇 Медаль", "🎤 Микрофон", "🖥️ Монитор", "🍔 Бургер"
]

THEMES = {
    "Discord Modern": {"bg": "#1e1f22", "panel": "#2b2d31", "card": "#313338", "fg": "#f2f3f5", "accent": "#5865f2", "accent_grad": "#4752c4", "input": "#111214", "subtext": "#949ba4"},
    "Emerald Green": {"bg": "#021712", "panel": "#042f25", "card": "#064e3b", "fg": "#ecfdf5", "accent": "#10b981", "accent_grad": "#047857", "input": "#010b08", "subtext": "#6ee7b7"},
    "Emerald Glow": {"bg": "#021712", "panel": "#042f25", "card": "#064e3b", "fg": "#ecfdf5", "accent": "#10b981", "accent_grad": "#047857", "input": "#010b08", "subtext": "#6ee7b7"},
    "Cyber Neon": {"bg": "#090414", "panel": "#120a2a", "card": "#1a0f3d", "fg": "#00ffcc", "accent": "#ff007f", "accent_grad": "#b30059", "input": "#05020a", "subtext": "#a78bfa"},
    "Midnight Wave": {"bg": "#080e18", "panel": "#0f172a", "card": "#1e293b", "fg": "#f8fafc", "accent": "#3b82f6", "accent_grad": "#1d4ed8", "input": "#04070d", "subtext": "#94a3b8"},
    "Sunset Gradient": {"bg": "#180a0a", "panel": "#2c1212", "card": "#3d1a1a", "fg": "#fff1f2", "accent": "#f97316", "accent_grad": "#c2410c", "input": "#0d0505", "subtext": "#fca5a5"},
    "Purple Aurora": {"bg": "#0d0a21", "panel": "#19153b", "card": "#262057", "fg": "#e0e7ff", "accent": "#8b5cf6", "accent_grad": "#6d28d9", "input": "#060412", "subtext": "#c7d2fe"},
    "Matrix Stream": {"bg": "#01120d", "panel": "#02241b", "card": "#04382a", "fg": "#4ade80", "accent": "#22c55e", "accent_grad": "#15803d", "input": "#000806", "subtext": "#86efac"},
    "Clean Glass": {"bg": "#f1f5f9", "panel": "#ffffff", "card": "#e2e8f0", "fg": "#0f172a", "accent": "#6366f1", "accent_grad": "#4338ca", "input": "#cbd5e1", "subtext": "#64748b"},
    "Deep Space": {"bg": "#020205", "panel": "#0b0b14", "card": "#141426", "fg": "#e2e8f0", "accent": "#6366f1", "accent_grad": "#4f46e5", "input": "#05050a", "subtext": "#64748b"},
    "Crimson Blood": {"bg": "#140202", "panel": "#260505", "card": "#3d0a0a", "fg": "#ffe4e6", "accent": "#e11d48", "accent_grad": "#be123c", "input": "#0a0101", "subtext": "#fda4af"},
    "Arctic Ice": {"bg": "#0c151d", "panel": "#132230", "card": "#1d3347", "fg": "#f0fdf4", "accent": "#0ea5e9", "accent_grad": "#0284c7", "input": "#080e14", "subtext": "#7dd3fc"},
    "Gold Luxury": {"bg": "#141108", "panel": "#26200f", "card": "#3d3219", "fg": "#fefce8", "accent": "#eab308", "accent_grad": "#ca8a04", "input": "#0a0804", "subtext": "#fde047"},
    "Toxic Waste": {"bg": "#081404", "panel": "#102608", "card": "#1a3d0e", "fg": "#f7fee7", "accent": "#84cc16", "accent_grad": "#65a30d", "input": "#040a02", "subtext": "#bef264"},
    "Cyberpunk Yellow": {"bg": "#141202", "panel": "#262204", "card": "#3d3606", "fg": "#fef9c3", "accent": "#facc15", "accent_grad": "#eab308", "input": "#0a0901", "subtext": "#fde047"},
    "Coffee Mocha": {"bg": "#140e0a", "panel": "#261a12", "card": "#3d2a1c", "fg": "#fdf8f6", "accent": "#d97706", "accent_grad": "#b45309", "input": "#0a0705", "subtext": "#fcd34d"},
    "Amethyst Mist": {"bg": "#120814", "panel": "#200e26", "card": "#33163d", "fg": "#faf5ff", "accent": "#c084fc", "accent_grad": "#a855f7", "input": "#0a040a", "subtext": "#e879f9"},
    "Neon Orange": {"bg": "#140a02", "panel": "#261304", "card": "#3d1f06", "fg": "#fff7ed", "accent": "#fb923c", "accent_grad": "#f97316", "input": "#0a0501", "subtext": "#fdba74"},
    "Steel Gray": {"bg": "#0f1115", "panel": "#1a1d24", "card": "#262a33", "fg": "#f3f4f6", "accent": "#9ca3af", "accent_grad": "#6b7280", "input": "#08090b", "subtext": "#d1d5db"},
    "Bubblegum Pink": {"bg": "#140810", "panel": "#26101f", "card": "#3d1a32", "fg": "#fdf2f8", "accent": "#f472b6", "accent_grad": "#ec4899", "input": "#0a0408", "subtext": "#fbcfe8"},
    "Deep Forest": {"bg": "#05140d", "panel": "#0a2618", "card": "#103d27", "fg": "#f0fdf4", "accent": "#2dd4bf", "accent_grad": "#14b8a6", "input": "#020a06", "subtext": "#99f6e4"},
    "Solar Flare": {"bg": "#1a0800", "panel": "#331200", "card": "#4d1a00", "fg": "#fff7ed", "accent": "#ea580c", "accent_grad": "#c2410c", "input": "#0d0400", "subtext": "#fdba74"},
    "Cyber Violet": {"bg": "#0d051a", "panel": "#1b0a33", "card": "#28104d", "fg": "#f5f3ff", "accent": "#7c3aed", "accent_grad": "#6d28d9", "input": "#06020d", "subtext": "#c4b5fd"},
    "Matrix Red": {"bg": "#140101", "panel": "#260202", "card": "#3d0404", "fg": "#fef2f2", "accent": "#dc2626", "accent_grad": "#b91c1c", "input": "#0a0000", "subtext": "#fca5a5"},
    "Neon Lime": {"bg": "#041406", "panel": "#0a260d", "card": "#0f3d14", "fg": "#f7fee7", "accent": "#65a30d", "accent_grad": "#4d7c0f", "input": "#020a03", "subtext": "#bef264"},
    "Ocean Depth": {"bg": "#020b14", "panel": "#04182b", "card": "#072542", "fg": "#f0fdf4", "accent": "#0284c7", "accent_grad": "#0369a1", "input": "#01050a", "subtext": "#7dd3fc"},
    "Ruby Velvet": {"bg": "#14030a", "panel": "#260614", "card": "#3d0a1f", "fg": "#fdf2f8", "accent": "#db2777", "accent_grad": "#be185d", "input": "#0a0105", "subtext": "#f472b6"},
    "Amber Glow": {"bg": "#140c02", "panel": "#261904", "card": "#3d2706", "fg": "#fffbeb", "accent": "#d97706", "accent_grad": "#b45309", "input": "#0a0601", "subtext": "#fcd34d"},
    "Emerald Mist": {"bg": "#02140d", "panel": "#042618", "card": "#063d24", "fg": "#ecfdf5", "accent": "#059669", "accent_grad": "#047857", "input": "#010a06", "subtext": "#6ee7b7"},
    "Dark Chocolate": {"bg": "#120a08", "panel": "#211310", "card": "#331c18", "fg": "#faf5f5", "accent": "#b45309", "accent_grad": "#92400e", "input": "#080403", "subtext": "#d7b59d"},
    "Slate Minimal": {"bg": "#111827", "panel": "#1f2937", "card": "#374151", "fg": "#f9fafb", "accent": "#4b5563", "accent_grad": "#374151", "input": "#030712", "subtext": "#9ca3af"},
    "Electric Blue": {"bg": "#030b1e", "panel": "#06173b", "card": "#0a255c", "fg": "#f0f9ff", "accent": "#2563eb", "accent_grad": "#1d4ed8", "input": "#01050f", "subtext": "#93c5fd"},
    "Neon Pink": {"bg": "#1a0312", "panel": "#330624", "card": "#4d0937", "fg": "#fdf2f8", "accent": "#ec4899", "accent_grad": "#db2777", "input": "#0d0109", "subtext": "#f472b6"},
    "Cyber Mint": {"bg": "#021410", "panel": "#04261e", "card": "#063d2e", "fg": "#f0fdf4", "accent": "#14b8a6", "accent_grad": "#0d9488", "input": "#010a08", "subtext": "#5eead4"},
    "Sunset Orange": {"bg": "#1a0c05", "panel": "#33190a", "card": "#4d260f", "fg": "#fff7ed", "accent": "#f97316", "accent_grad": "#ea580c", "input": "#0d0602", "subtext": "#fdba74"},
    "Royal Purple": {"bg": "#10031a", "panel": "#200633", "card": "#30094d", "fg": "#faf5ff", "accent": "#9333ea", "accent_grad": "#7e22ce", "input": "#08010d", "subtext": "#d8b4fe"},
    "Neon Cyan": {"bg": "#021414", "panel": "#042626", "card": "#063d3d", "fg": "#f0fdfa", "accent": "#06b6d4", "accent_grad": "#0891b2", "input": "#010a0a", "subtext": "#67e8f9"},
    "Coffee Bean": {"bg": "#120b08", "panel": "#211510", "card": "#332019", "fg": "#fdf8f6", "accent": "#b45309", "accent_grad": "#92400e", "input": "#080503", "subtext": "#d1a185"},
    "Toxic Green": {"bg": "#051402", "panel": "#0a2604", "card": "#0f3d06", "fg": "#f7fee7", "accent": "#84cc16", "accent_grad": "#65a30d", "input": "#020a01", "subtext": "#bef264"},
    "Deep Wine": {"bg": "#140206", "panel": "#26040c", "card": "#3d0613", "fg": "#fff1f2", "accent": "#e11d48", "accent_grad": "#be123c", "input": "#0a0103", "subtext": "#fda4af"},
    "Charcoal Gold": {"bg": "#111111", "panel": "#1c1c1c", "card": "#2a2a2a", "fg": "#fef08a", "accent": "#eab308", "accent_grad": "#ca8a04", "input": "#080808", "subtext": "#fde047"}
}

class VoiceClientApp:
    def __init__(self, root):
        self.root = root
        
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("drunspeak.client.app.1")
        except:
            pass

        self.root.title(f"друнспик - v{CURRENT_VERSION}")
        
        ico_path = resource_path("drunspeakicon.ico")
        if not os.path.exists(ico_path):
            ico_path = resource_path("icon.ico")
        
        try:
            if os.path.exists(ico_path):
                self.root.iconbitmap(ico_path)
        except Exception as e:
            print(f"Не удалось загрузить иконку: {e}")

        self.root.geometry("450x620")
        self.root.resizable(False, False)

        self.current_volume = tk.DoubleVar(value=cfg["volume"])
        self.rms_threshold = tk.DoubleVar(value=cfg["gate"])
        self.noise_suppression_mode = tk.StringVar(value=cfg.get("noise_suppression", "Улучшенное (Чистый голос)"))
        self.loopback_enabled = tk.BooleanVar(value=cfg["loopback"])
        self.muted = tk.BooleanVar(value=False)
        self.deafened = tk.BooleanVar(value=False) 
        self.server_muted = False
        
        self.server_ip = tk.StringVar(value=cfg["server_ip"])
        self.server_port = tk.IntVar(value=cfg["server_port"])
        self.nickname = tk.StringVar(value=cfg["nickname"])
        self.selected_avatar = tk.StringVar(value=cfg.get("avatar", "⚡ Молния"))
        self.current_theme = tk.StringVar(value=cfg.get("theme", "Discord Modern"))

        self.client_socket = None
        self.audio_queue = queue.Queue()
        
        self.is_connected = False
        self.running_audio = True
        self.is_sharing_screen = False
        self.remote_users = []
        
        self.remote_streams = {} 
        self.active_stream_windows = {} 
        self.user_card_frames = {} 
        
        self.chat_messages = []
        self.chat_visible = False
        self.animating_chat = False

        try:
            devices = sd.query_devices()
            self.mic_list = [d['name'] for d in devices if d['max_input_channels'] > 0]
            self.speaker_list = [d['name'] for d in devices if d['max_output_channels'] > 0]
        except:
            self.mic_list, self.speaker_list = [], []

        default_mic = cfg["mic_device"] if cfg["mic_device"] in self.mic_list else (self.mic_list[0] if self.mic_list else "")
        default_spk = cfg["speaker_device"] if cfg["speaker_device"] in self.speaker_list else (self.speaker_list[0] if self.speaker_list else "")

        self.selected_mic = tk.StringVar(value=default_mic)
        self.selected_speaker = tk.StringVar(value=default_spk)

        self.apply_theme()
        if self.is_connected:
            self.create_room_frame()
        else:
            self.create_login_frame()
            
        self.start_audio_streams()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def check_for_updates_interactive(self):
        try:
            req = urllib.request.Request(UPDATE_CHECK_URL, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=4) as response:
                data = json.loads(response.read().decode('utf-8'))
                latest_version = data.get("version")
                download_url = data.get("url")

                if latest_version != CURRENT_VERSION:
                    answer = messagebox.askyesno(
                        "Обновление", 
                        f"Доступна новая версия: {latest_version} (у вас {CURRENT_VERSION}). Обновиться сейчас?"
                    )
                    if answer:
                        self.download_and_apply_update(download_url)
                else:
                    messagebox.showinfo("Обновление", "У вас установлена самая последняя версия!")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось проверить обновления:\n{e}")

    def download_and_apply_update(self, url):
        try:
            temp_dir = tempfile.gettempdir()
            new_file_path = os.path.join(temp_dir, "client_new.pyw")
            
            urllib.request.urlretrieve(url, new_file_path)

            current_exe_dir = BASE_DIR
            bat_path = os.path.join(temp_dir, "updater.bat")
            
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(f"""@echo off
chcp 65001 > nul
timeout /t 2 /nobreak > nul
move /y "{new_file_path}" "{os.path.join(current_exe_dir, 'client.pyw')}"
start "" pythonw.exe "{os.path.join(current_exe_dir, 'client.pyw')}"
del "%~f0"
""")

            subprocess.Popen([bat_path], shell=True)
            self.on_close()
            
        except Exception as e:
            messagebox.showerror("Ошибка обновления", f"Не удалось применить обновление:\n{e}")

    def apply_theme(self):
        t = THEMES.get(self.current_theme.get(), THEMES["Discord Modern"])
        self.th_bg = t["bg"]
        self.th_panel = t["panel"]
        self.th_card = t["card"]
        self.th_fg = t["fg"]
        self.th_accent = t["accent"]
        self.th_accent_grad = t["accent_grad"]
        self.th_input = t["input"]
        self.th_subtext = t["subtext"]
        self.root.config(bg=self.th_bg)

    def bind_entry_shortcuts(self, entry_widget):
        def select_all(event):
            entry_widget.select_range(0, tk.END)
            return 'break'
        entry_widget.bind("<Control-a>", select_all)
        entry_widget.bind("<Control-A>", select_all)

    def clear_window(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def create_animated_btn(self, parent, text, bg_color, hover_color, command, font_size=10, py=8):
        btn = tk.Button(parent, text=text, bg=bg_color, fg="white", font=("Segoe UI", font_size, "bold"),
                        bd=0, relief="flat", activebackground=hover_color, activeforeground="white",
                        cursor="hand2", command=command)
        btn.bind("<Enter>", lambda e: btn.config(bg=hover_color))
        btn.bind("<Leave
