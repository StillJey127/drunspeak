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

CURRENT_VERSION = "1.2"
UPDATE_CHECK_URL = "https://raw.githubusercontent.com/StillJey127/drunspeak/refs/heads/main/version.json"

def resource_path(relative_path):
    """ Адаптивное получение пути к ресурсам для .pyw и скомпилированного PyInstaller .exe """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

# Сохраняем конфиг в AppData пользователя, чтобы не было проблем с правами Program Files
APPDATA_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), "Drunspeak")
if not os.path.exists(APPDATA_DIR):
    try:
        os.makedirs(APPDATA_DIR)
    except:
        APPDATA_DIR = os.path.dirname(os.path.abspath(__file__))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(APPDATA_DIR, "config.json")

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
        except:
            pass
    return default_config

cfg = load_config()

SAMPLE_RATE = 16000  
CHANNELS = 1
BLOCKSIZE = 256  

class VoiceClientApp:
    def __init__(self, root):
        self.root = root
        
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("drunspeak.client.app.1")
        except:
            pass

        self.root.title("друнспик - Ultimate Edition")
        
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
        btn.bind("<Leave>", lambda e: btn.config(bg=bg_color))
        return btn

    def create_login_frame(self):
        self.clear_window()
        self.apply_theme()

        self.root.geometry("450x660")
        main_frame = tk.Frame(self.root, bg=self.th_bg)
        main_frame.pack(fill="both", expand=True, padx=30, pady=25)

        top_bar = tk.Frame(main_frame, bg=self.th_bg)
        top_bar.pack(fill="x", anchor="e")
        
        settings_btn = self.create_animated_btn(top_bar, "⚙ Настройки", self.th_panel, self.th_accent, self.open_settings_window, font_size=9, py=4)
        settings_btn.pack(side="right", ipadx=10, ipady=4)

        tk.Label(main_frame, text="ДРУНСПИК", bg=self.th_bg, fg=self.th_fg, font=("Segoe UI", 18, "bold")).pack(pady=(10, 15))

        tk.Label(main_frame, text="Ваш никнейм", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
        nick_entry = tk.Entry(main_frame, textvariable=self.nickname, bg=self.th_input, fg=self.th_fg, insertbackground=self.th_fg, font=("Segoe UI", 11), relief="flat")
        nick_entry.pack(fill="x", pady=(4, 10), ipady=8)
        self.bind_entry_shortcuts(nick_entry)
        nick_entry.bind("<FocusOut>", lambda e: self.save_config())

        tk.Label(main_frame, text="IP / Домен Сервера", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
        ip_entry = tk.Entry(main_frame, textvariable=self.server_ip, bg=self.th_input, fg=self.th_fg, insertbackground=self.th_fg, font=("Segoe UI", 11), relief="flat")
        ip_entry.pack(fill="x", pady=(4, 10), ipady=8)
        self.bind_entry_shortcuts(ip_entry)
        ip_entry.bind("<FocusOut>", lambda e: self.save_config())

        tk.Label(main_frame, text="Порт", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
        port_entry = tk.Entry(main_frame, textvariable=self.server_port, bg=self.th_input, fg=self.th_fg, insertbackground=self.th_fg, font=("Segoe UI", 11), relief="flat")
        port_entry.pack(fill="x", pady=(4, 15), ipady=8)
        self.bind_entry_shortcuts(port_entry)
        port_entry.bind("<FocusOut>", lambda e: self.save_config())

        tk.Label(main_frame, text="Ваша аватарка", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
        avatar_combo = ttk.Combobox(main_frame, textvariable=self.selected_avatar, values=BUILTIN_AVATARS, state="readonly", font=("Segoe UI", 10))
        avatar_combo.pack(fill="x", pady=(4, 20), ipady=4)
        avatar_combo.bind("<<ComboboxSelected>>", lambda e: [self.save_config(), self.send_my_status()])

        self.connect_btn = self.create_animated_btn(main_frame, "Подключиться к друнспику", self.th_accent, self.th_accent_grad, self.start_connecting, font_size=11, py=10)
        self.connect_btn.pack(fill="x", ipady=8)

        self.status_label = tk.Label(main_frame, text="Статус: Отключено", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9))
        self.status_label.pack(pady=10)

    def create_room_frame(self):
        self.clear_window()
        self.apply_theme()

        self.root.geometry("820x620" if self.chat_visible else "450x620")

        self.top_bar = tk.Frame(self.root, bg=self.th_panel, height=55)
        self.top_bar.pack(fill="x", side="top")
        
        self.room_title_lbl = tk.Label(self.top_bar, text="🔊 друнспик", bg=self.th_panel, fg=self.th_fg, font=("Segoe UI", 11, "bold"))
        self.room_title_lbl.pack(side="left", padx=15)
        
        self.settings_top_btn = self.create_animated_btn(self.top_bar, "⚙", self.th_panel, self.th_accent, self.open_settings_window, font_size=10)
        self.settings_top_btn.pack(side="right", padx=10)

        self.screen_btn = self.create_animated_btn(self.top_bar, "📺 Экран", self.th_accent, self.th_accent_grad, self.prompt_screen_share, font_size=9)
        self.screen_btn.pack(side="right", padx=5)

        chat_btn_text = "✕ Закрыть чат" if self.chat_visible else "💬 Чат"
        self.toggle_chat_btn = self.create_animated_btn(self.top_bar, chat_btn_text, self.th_panel, self.th_accent, self.toggle_chat_panel, font_size=9)
        self.toggle_chat_btn.pack(side="right", padx=5)

        self.main_split = tk.Frame(self.root, bg=self.th_bg)
        self.main_split.pack(fill="both", expand=True, padx=15, pady=10)

        self.left_column = tk.Frame(self.main_split, bg=self.th_bg)
        self.left_column.pack(side="left", fill="both", expand=True, padx=(0, 5 if self.chat_visible else 0))

        self.participants_lbl = tk.Label(self.left_column, text="УЧАСТНИКИ В ДРУНСПИКЕ", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w")
        self.participants_lbl.pack(fill="x", pady=(0, 6))
        
        self.cards_container = tk.Frame(self.left_column, bg=self.th_bg)
        self.cards_container.pack(fill="both", expand=True)

        self.indicator_frame = tk.Frame(self.left_column, bg=self.th_bg)
        self.indicator_frame.pack(fill="x", pady=5)
        
        self.mic_level_lbl = tk.Label(self.indicator_frame, text="Уровень микрофона:", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9))
        self.mic_level_lbl.pack(anchor="w")
        
        canvas_width = 390 if self.chat_visible else 410
        self.canvas_indicator = tk.Canvas(self.indicator_frame, width=canvas_width, height=8, bg=self.th_input, highlightthickness=0)
        self.canvas_indicator.pack(pady=3)
        self.indicator_bar = self.canvas_indicator.create_rectangle(0, 0, 0, 8, fill="#23a55a", width=0)

        self.right_column = tk.Frame(self.main_split, bg=self.th_panel, width=360)
        if self.chat_visible:
            self.right_column.pack(side="right", fill="both", padx=(5, 0))
            self.right_column.pack_propagate(False)

            self.chat_title_lbl = tk.Label(self.right_column, text="💬 ЧАТ ДРУНСПИКА", bg=self.th_panel, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w")
            self.chat_title_lbl.pack(fill="x", padx=12, pady=(10, 5))

            self.chat_text_widget = scrolledtext.ScrolledText(self.right_column, bg=self.th_input, fg=self.th_fg, font=("Segoe UI", 9), state="disabled", relief="flat", highlightthickness=0)
            self.chat_text_widget.pack(fill="both", expand=True, padx=12, pady=(0, 8))

            self.chat_text_widget.config(state="normal")
            for msg in self.chat_messages:
                self.chat_text_widget.insert(tk.END, msg + "\n")
            self.chat_text_widget.config(state="disabled")
            self.chat_text_widget.see(tk.END)

            self.chat_input_frame = tk.Frame(self.right_column, bg=self.th_panel)
            self.chat_input_frame.pack(fill="x", padx=12, pady=(0, 12))

            self.chat_entry = tk.Entry(self.chat_input_frame, bg=self.th_input, fg=self.th_fg, insertbackground=self.th_fg, font=("Segoe UI", 10), relief="flat")
            self.chat_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 6))
            self.bind_entry_shortcuts(self.chat_entry)
            self.chat_entry.bind("<Return>", self.send_chat_message)

            self.send_chat_btn = self.create_animated_btn(self.chat_input_frame, "➤", self.th_accent, self.th_accent_grad, self.send_chat_message, font_size=9, py=5)
            self.send_chat_btn.pack(side="right")

        self.bottom_bar = tk.Frame(self.root, bg=self.th_panel, height=65)
        self.bottom_bar.pack(fill="x", side="bottom")

        self.mute_room_btn = self.create_animated_btn(self.bottom_bar, "🎤 Микрофон", "#23a55a", "#1f924f", self.toggle_mute, font_size=9)
        self.mute_room_btn.pack(side="left", padx=(15, 5), pady=12, ipadx=8, ipady=4)

        self.deafen_room_btn = self.create_animated_btn(self.bottom_bar, "🎧 Наушники", "#23a55a", "#1f924f", self.toggle_deafen, font_size=9)
        self.deafen_room_btn.pack(side="left", padx=5, pady=12, ipadx=8, ipady=4)

        self.disconnect_btn = self.create_animated_btn(self.bottom_bar, "Отключиться", "#da373c", "#b82e32", self.disconnect_server, font_size=9)
        self.disconnect_btn.pack(side="right", padx=15, pady=12, ipadx=8, ipady=4)
        
        self.refresh_user_cards()

    def toggle_chat_panel(self):
        if self.animating_chat:
            return
        self.animating_chat = True
        
        if self.chat_visible:
            start_w = 820
            end_w = 450
            steps = 10
            step_size = (start_w - end_w) / steps
            
            self.chat_visible = False
            if hasattr(self, 'right_column') and self.right_column.winfo_exists():
                self.right_column.destroy()
            if hasattr(self, 'toggle_chat_btn') and self.toggle_chat_btn.winfo_exists():
                self.toggle_chat_btn.config(text="💬 Чат")

            def animate_close(current_step):
                if current_step <= steps:
                    curr_w = int(start_w - step_size * current_step)
                    self.root.geometry(f"{curr_w}x620")
                    self.root.after(15, lambda: animate_close(current_step + 1))
                else:
                    self.animating_chat = False
                    self.refresh_user_cards()
            animate_close(1)
        else:
            start_w = 450
            end_w = 820
            steps = 10
            step_size = (end_w - start_w) / steps
            
            self.chat_visible = True
            if hasattr(self, 'toggle_chat_btn') and self.toggle_chat_btn.winfo_exists():
                self.toggle_chat_btn.config(text="✕ Закрыть чат")

            self.right_column = tk.Frame(self.main_split, bg=self.th_panel, width=360)
            self.right_column.pack(side="right", fill="both", padx=(5, 0))
            self.right_column.pack_propagate(False)

            self.chat_title_lbl = tk.Label(self.right_column, text="💬 ЧАТ ДРУНСПИКА", bg=self.th_panel, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w")
            self.chat_title_lbl.pack(fill="x", padx=12, pady=(10, 5))

            self.chat_text_widget = scrolledtext.ScrolledText(self.right_column, bg=self.th_input, fg=self.th_fg, font=("Segoe UI", 9), state="disabled", relief="flat", highlightthickness=0)
            self.chat_text_widget.pack(fill="both", expand=True, padx=12, pady=(0, 8))

            self.chat_text_widget.config(state="normal")
            for msg in self.chat_messages:
                self.chat_text_widget.insert(tk.END, msg + "\n")
            self.chat_text_widget.config(state="disabled")
            self.chat_text_widget.see(tk.END)

            self.chat_input_frame = tk.Frame(self.right_column, bg=self.th_panel)
            self.chat_input_frame.pack(fill="x", padx=12, pady=(0, 12))

            self.chat_entry = tk.Entry(self.chat_input_frame, bg=self.th_input, fg=self.th_fg, insertbackground=self.th_fg, font=("Segoe UI", 10), relief="flat")
            self.chat_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 6))
            self.bind_entry_shortcuts(self.chat_entry)
            self.chat_entry.bind("<Return>", self.send_chat_message)

            self.send_chat_btn = self.create_animated_btn(self.chat_input_frame, "➤", self.th_accent, self.th_accent_grad, self.send_chat_message, font_size=9, py=5)
            self.send_chat_btn.pack(side="right")

            def animate_open(current_step):
                if current_step <= steps:
                    curr_w = int(start_w + step_size * current_step)
                    self.root.geometry(f"{curr_w}x620")
                    self.root.after(15, lambda: animate_open(current_step + 1))
                else:
                    self.animating_chat = False
                    self.refresh_user_cards()
            animate_open(1)

    def send_chat_message(self, event=None):
        if not hasattr(self, 'chat_entry'):
            return
        text = self.chat_entry.get().strip()
        if text and self.is_connected and self.client_socket:
            try:
                my_nick = self.nickname.get() or "User"
                self.append_chat_message(my_nick, text)
                
                packet = f"CHAT:{my_nick}:{text}".encode("utf-8")
                self.client_socket.sendto(packet, self.server_address)
                self.chat_entry.delete(0, tk.END)
            except Exception as e:
                print(f"Chat send error: {e}")

    def append_chat_message(self, sender, text, play_sound=False):
        formatted = f"[{sender}]: {text}"
        self.chat_messages.append(formatted)
        
        if play_sound:
            try:
                self.root.bell()
            except:
                pass

        if hasattr(self, 'chat_text_widget') and self.chat_text_widget.winfo_exists():
            self.chat_text_widget.config(state="normal")
            self.chat_text_widget.insert(tk.END, formatted + "\n")
            self.chat_text_widget.config(state="disabled")
            self.chat_text_widget.see(tk.END)

    def refresh_user_cards(self):
        if not hasattr(self, 'cards_container') or not self.cards_container.winfo_exists():
            return

        users = list(self.remote_users) if self.remote_users else []
        my_nick = self.nickname.get() or "User"
        
        my_found = any(u.get("name") == my_nick for u in users)
        if not my_found:
            users.append({
                "name": my_nick,
                "muted": False,
                "mic_muted": self.muted.get(),
                "deafened": self.deafened.get(),
                "sharing": self.is_sharing_screen,
                "talking": False,
                "avatar": self.selected_avatar.get()
            })
        else:
            for u in users:
                if u.get("name") == my_nick:
                    u["sharing"] = self.is_sharing_screen
                    u["avatar"] = self.selected_avatar.get()

        current_names = {u.get("name") for u in users}
        
        for name in list(self.user_card_frames.keys()):
            if name not in current_names:
                self.user_card_frames[name]["frame"].destroy()
                del self.user_card_frames[name]
                if name in self.active_stream_windows:
                    try: self.active_stream_windows[name].destroy()
                    except: pass

        for u in users:
            name = u.get("name")
            is_me = (name == my_nick)
            display_name = f"{name} (Вы)" if is_me else name
            
            avatar_val = u.get("avatar", "⚡ Молния")
            avatar_symbol = avatar_val.split()[0] if avatar_val else "⚡"

            is_admin_muted = u.get("muted", False)
            is_mic_muted = u.get("mic_muted", False)
            is_deafened = u.get("deafened", False)
            is_sharing = u.get("sharing", False)
            is_talking = u.get("talking", False)

            if is_admin_muted:
                border_color = "#da373c"
                status_text = "🔇 Заглушен админом"
            elif is_mic_muted:
                border_color = "#e67e22"
                status_text = "🎤 Микрофон выкл"
            elif is_deafened:
                border_color = "#f0b232"
                status_text = "🎧 Без звука"
            elif is_talking:
                border_color = "#23a55a"
                status_text = "🟢 Говорит..."
            else:
                border_color = self.th_subtext
                status_text = "⚪ Молчит"

            if name not in self.user_card_frames:
                card = tk.Frame(self.cards_container, bg=self.th_card, padx=12, pady=8)
                card.pack(fill="x", pady=3)

                avatar_lbl = tk.Label(card, text=avatar_symbol, bg=self.th_input, fg=self.th_fg, font=("Segoe UI", 16), width=3, height=2, relief="flat")
                avatar_lbl.pack(side="left", padx=(0, 10))

                info_frame = tk.Frame(card, bg=self.th_card)
                info_frame.pack(side="left", fill="both", expand=True)

                name_lbl = tk.Label(info_frame, text=display_name, bg=self.th_card, fg=self.th_fg, font=("Segoe UI", 9, "bold"), anchor="w")
                name_lbl.pack(fill="x")
                
                status_lbl = tk.Label(info_frame, text=status_text, bg=self.th_card, fg=border_color, font=("Segoe UI", 8), anchor="w")
                status_lbl.pack(fill="x")

                stream_btn = tk.Button(card, text="📺 Смотреть", bg=self.th_accent, fg="white", font=("Segoe UI", 8, "bold"), relief="flat", cursor="hand2")

                self.user_card_frames[name] = {
                    "frame": card,
                    "avatar_lbl": avatar_lbl,
                    "name_lbl": name_lbl,
                    "status_lbl": status_lbl,
                    "stream_btn": stream_btn
                }
            
            widgets = self.user_card_frames[name]
            widgets["frame"].config(bg=self.th_card)
            widgets["avatar_lbl"].config(text=avatar_symbol, bg=self.th_input, fg=self.th_fg)
            widgets["name_lbl"].config(text=display_name, bg=self.th_card, fg=self.th_fg)
            widgets["status_lbl"].config(text=status_text, fg=border_color)
            widgets["status_lbl"].master.config(bg=self.th_card)
            widgets["stream_btn"].config(bg=self.th_accent)

            if is_sharing:
                widgets["stream_btn"].config(command=lambda n=name: self.open_remote_stream_window(n))
                widgets["stream_btn"].pack(side="right", padx=5)
            else:
                widgets["stream_btn"].pack_forget()

    def send_my_status(self):
        if self.is_connected and self.client_socket:
            try:
                status_data = {
                    "mic_muted": self.muted.get() or self.server_muted,
                    "deafened": self.deafened.get(),
                    "sharing": self.is_sharing_screen,
                    "avatar": self.selected_avatar.get()
                }
                self.client_socket.sendto(b"STATUS:" + json.dumps(status_data).encode("utf-8"), self.server_address)
            except:
                pass

    def send_talking_status(self, is_talking):
        if self.is_connected and self.client_socket:
            try:
                cmd = b"TALKING:1" if is_talking else b"TALKING:0"
                self.client_socket.sendto(cmd, self.server_address)
            except:
                pass

    def prompt_screen_share(self):
        if self.is_sharing_screen:
            self.is_sharing_screen = False
            self.screen_btn.config(text="📺 Экран", bg=self.th_accent)
            self.send_my_status()
            self.refresh_user_cards()
            return

        self.is_sharing_screen = True
        self.screen_btn.config(text="⏹ Стоп экран", bg="#da373c")
        
        self.send_my_status()
        self.refresh_user_cards()

        threading.Thread(target=self.screen_share_loop, daemon=True).start()

    def screen_share_loop(self):
        delay = 1.0 / 60.0

        if MSS_AVAILABLE and VISION_AVAILABLE:
            try:
                import mss
                with mss.mss() as sct:
                    monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]

                    while self.is_sharing_screen and self.is_connected:
                        start_time = time.time()
                        try:
                            img = sct.grab(monitor)
                            frame = np.ascontiguousarray(np.array(img))
                            if frame.shape[2] == 4:
                                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                            
                            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                            self.remote_streams[self.nickname.get()] = rgb_frame

                            _, encoded = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                            
                            chunk_size = 1024  
                            total_bytes = encoded.tobytes()
                            total_chunks = (len(total_bytes) + chunk_size - 1) // chunk_size
                            
                            for i in range(total_chunks):
                                chunk = total_bytes[i*chunk_size : (i+1)*chunk_size]
                                header = f"SCRCHUNK:{self.nickname.get()}:{i}:{total_chunks}:".encode("utf-8")
                                if self.client_socket:
                                    self.client_socket.sendto(header + chunk, self.server_address)
                        except Exception as inner_err:
                            print(f"Frame capture tick error: {inner_err}")
                        
                        elapsed = time.time() - start_time
                        if elapsed < delay:
                            time.sleep(delay - elapsed)
            except Exception as e:
                print(f"MSS screen capture initialization error: {e}")

        self.is_sharing_screen = False
        try:
            self.send_my_status()
            self.refresh_user_cards()
            if hasattr(self, 'screen_btn') and self.screen_btn.winfo_exists():
                self.root.after(0, lambda: self.screen_btn.config(text="📺 Экран", bg=self.th_accent))
        except:
            pass

    def open_remote_stream_window(self, streamer_name):
        if streamer_name in self.active_stream_windows and self.active_stream_windows[streamer_name].winfo_exists():
            self.active_stream_windows[streamer_name].lift()
            return

        stream_win = tk.Toplevel(self.root)
        stream_win.title(f"Трансляция экрана: {streamer_name}")
        stream_win.geometry("960x580")
        stream_win.config(bg="#111214")

        control_panel = tk.Frame(stream_win, bg="#1b1d21", height=45)
        control_panel.pack(fill="x", side="top")

        video_label = tk.Label(stream_win, bg="#111214")
        video_label.pack(fill="both", expand=True)

        self.active_stream_windows[streamer_name] = stream_win

        def update_frame_loop():
            while stream_win.winfo_exists() and self.is_connected:
                try:
                    if streamer_name in self.remote_streams:
                        frame = self.remote_streams[streamer_name]
                        if frame is not None and frame.size > 0:
                            w = stream_win.winfo_width()
                            h = stream_win.winfo_height() - 45 
                            if w > 50 and h > 50:
                                image = Image.fromarray(frame)
                                image = image.resize((w, h), Image.Resampling.BILINEAR)
                                photo = ImageTk.PhotoImage(image=image)
                                
                                if video_label.winfo_exists():
                                    video_label.config(image=photo)
                                    video_label.image = photo
                except Exception as e:
                    print(f"Render frame error: {e}")
                time.sleep(0.01)

            if streamer_name in self.active_stream_windows:
                del self.active_stream_windows[streamer_name]

        threading.Thread(target=update_frame_loop, daemon=True).start()

    def update_remote_stream_frame(self, streamer_name, raw_or_jpg):
        try:
            nparr = np.frombuffer(raw_or_jpg, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is not None and frame.size > 0:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                self.remote_streams[streamer_name] = frame
        except Exception as decode_err:
            print(f"Decode frame error: {decode_err}")

    def toggle_deafen(self):
        if self.deafened.get():
            self.deafened.set(False)
            self.deafen_room_btn.config(text="🎧 Наушники", bg="#23a55a")
        else:
            self.deafened.set(True)
            self.deafen_room_btn.config(text="🎧 Наушники ВЫКЛ", bg="#da373c")
            while not self.audio_queue.empty():
                try: self.audio_queue.get_nowait()
                except queue.Empty: break
        self.send_my_status()

    def toggle_mute(self):
        if self.muted.get():
            self.muted.set(False)
            self.mute_room_btn.config(text="🎤 Микрофон", bg="#23a55a")
        else:
            self.muted.set(True)
            self.mute_room_btn.config(text="🎤 Микрофон ВЫКЛ", bg="#d9534f")
        self.send_my_status()

    def open_settings_window(self):
        settings_win = tk.Toplevel(self.root)
        settings_win.title("Настройки друнспика")
        settings_win.geometry("440x670")
        settings_win.config(bg=self.th_bg)
        settings_win.resizable(False, False)
        settings_win.grab_set()

        tk.Label(settings_win, text="ПАРАМЕТРЫ И АУДИО", bg=self.th_bg, fg=self.th_fg, font=("Segoe UI", 12, "bold")).pack(pady=15)

        tk.Label(settings_win, text="Тема оформления (40+ тем)", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=25)
        theme_combo = ttk.Combobox(settings_win, textvariable=self.current_theme, values=list(THEMES.keys()), state="readonly", width=40)
        theme_combo.pack(padx=25, pady=(4, 10))
        theme_combo.bind("<<ComboboxSelected>>", lambda e: self.change_theme_live())

        tk.Label(settings_win, text="Система шумоподавления", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=25)
        ns_combo = ttk.Combobox(settings_win, textvariable=self.noise_suppression_mode, values=["Выключено", "Базовое", "Улучшенное (Чистый голос)", "Максимальное"], state="readonly", width=40)
        ns_combo.pack(padx=25, pady=(4, 10))
        ns_combo.bind("<<ComboboxSelected>>", lambda e: self.save_config())

        tk.Label(settings_win, text="Аватарка", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=25)
        avatar_combo = ttk.Combobox(settings_win, textvariable=self.selected_avatar, values=BUILTIN_AVATARS, state="readonly", width=40)
        avatar_combo.pack(padx=25, pady=(4, 10))
        avatar_combo.bind("<<ComboboxSelected>>", lambda e: [self.save_config(), self.send_my_status()])

        tk.Label(settings_win, text="Микрофон", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=25)
        mic_combo = ttk.Combobox(settings_win, textvariable=self.selected_mic, values=self.mic_list, state="readonly", width=40)
        mic_combo.pack(padx=25, pady=(4, 10))
        mic_combo.bind("<<ComboboxSelected>>", lambda e: self.on_device_changed())

        tk.Label(settings_win, text="Динамики / Наушники", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=25)
        spk_combo = ttk.Combobox(settings_win, textvariable=self.selected_speaker, values=self.speaker_list, state="readonly", width=40)
        spk_combo.pack(padx=25, pady=(4, 10))
        spk_combo.bind("<<ComboboxSelected>>", lambda e: self.on_device_changed())

        tk.Label(settings_win, text="Порог чувствительности (Gate)", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=25)
        tk.Scale(settings_win, from_=1.0, to=200.0, resolution=1.0, orient="horizontal", variable=self.rms_threshold, bg=self.th_bg, fg=self.th_fg, highlightbackground=self.th_bg, troughcolor=self.th_input, command=lambda v: self.save_config()).pack(fill="x", padx=25, pady=(0, 10))

        tk.Label(settings_win, text="Усиление микрофона", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=25)
        tk.Scale(settings_win, from_=0.1, to=3.0, resolution=0.1, orient="horizontal", variable=self.current_volume, bg=self.th_bg, fg=self.th_fg, highlightbackground=self.th_bg, troughcolor=self.th_input, command=lambda v: self.save_config()).pack(fill="x", padx=25, pady=(0, 10))

        update_btn = self.create_animated_btn(settings_win, "🔄 Проверить обновления", self.th_accent, self.th_accent_grad, lambda: self.check_for_updates_interactive(), font_size=10)
        update_btn.pack(fill="x", padx=25, pady=(5, 5), ipady=5)

        close_btn = self.create_animated_btn(settings_win, "Готово", self.th_panel, self.th_accent, settings_win.destroy, font_size=10)
        close_btn.pack(fill="x", padx=25, pady=10, ipady=6)

    def change_theme_live(self):
        """ Применение темы на лету для любого экрана (и для логина, и для комнаты) """
        self.save_config()
        self.apply_theme()
        
        if not self.is_connected:
            self.create_login_frame()
            return

        if hasattr(self, 'top_bar') and self.top_bar.winfo_exists():
            self.top_bar.config(bg=self.th_panel)
            self.room_title_lbl.config(bg=self.th_panel, fg=self.th_fg)
            self.settings_top_btn.config(bg=self.th_panel)
            self.screen_btn.config(bg=self.th_accent)
            self.toggle_chat_btn.config(bg=self.th_panel)
            
            self.main_split.config(bg=self.th_bg)
            self.left_column.config(bg=self.th_bg)
            self.participants_lbl.config(bg=self.th_bg, fg=self.th_subtext)
            self.cards_container.config(bg=self.th_bg)
            self.indicator_frame.config(bg=self.th_bg)
            self.mic_level_lbl.config(bg=self.th_bg, fg=self.th_subtext)
            self.canvas_indicator.config(bg=self.th_input)

            if self.chat_visible and hasattr(self, 'right_column') and self.right_column.winfo_exists():
                self.right_column.config(bg=self.th_panel)
                self.chat_title_lbl.config(bg=self.th_panel, fg=self.th_subtext)
                self.chat_text_widget.config(bg=self.th_input, fg=self.th_fg)
                self.chat_input_frame.config(bg=self.th_panel)
                self.chat_entry.config(bg=self.th_input, fg=self.th_fg, insertbackground=self.th_fg)
                self.send_chat_btn.config(bg=self.th_accent)

            self.bottom_bar.config(bg=self.th_panel)
            self.refresh_user_cards()

    def save_config(self, *args):
        config = {
            "volume": self.current_volume.get(),
            "gate": self.rms_threshold.get(),
            "noise_suppression": self.noise_suppression_mode.get(),
            "loopback": self.loopback_enabled.get(),
            "server_ip": self.server_ip.get(),
            "server_port": self.server_port.get(),
            "nickname": self.nickname.get(),
            "avatar": self.selected_avatar.get(),
            "mic_device": self.selected_mic.get(),
            "speaker_device": self.selected_speaker.get(),
            "theme": self.current_theme.get()
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            print(f"Config save error: {e}")

    def on_device_changed(self):
        self.save_config()
        self.restart_audio_streams()

    def get_device_index(self, name, kind):
        try:
            devices = sd.query_devices()
            for i, d in enumerate(devices):
                if d['name'] == name:
                    if kind == 'in' and d['max_input_channels'] > 0: return i
                    if kind == 'out' and d['max_output_channels'] > 0: return i
        except:
            pass
        return None

    def start_audio_streams(self):
        threading.Thread(target=self.audio_input_loop, daemon=True).start()
        threading.Thread(target=self.audio_output_loop, daemon=True).start()

    def restart_audio_streams(self):
        self.running_audio = False
        time.sleep(0.2)
        self.running_audio = True
        self.start_audio_streams()

    def audio_input_loop(self):
        mic_name = self.selected_mic.get()
        mic_id = self.get_device_index(mic_name, 'in') if mic_name else None

        def callback(indata, frames, time_info, status):
            if not self.running_audio:
                return
            try:
                audio_data = np.frombuffer(indata, dtype=np.int16).astype(np.float32)
                
                ns_mode = self.noise_suppression_mode.get()
                if ns_mode != "Выключено":
                    noise_limit = 8.0 if ns_mode == "Базовое" else (22.0 if ns_mode == "Улучшенное (Чистый голос)" else 42.0)
                    mask = np.abs(audio_data) > noise_limit
                    audio_data = audio_data * mask * 0.95

                rms_energy = np.sqrt(np.mean(audio_data ** 2))
                threshold = self.rms_threshold.get()
                is_talking = rms_energy >= threshold

                self.send_talking_status(is_talking)

                bar_color = "#23a55a" if is_talking else "#f0b232"
                if self.server_muted or self.muted.get():
                    bar_color = "#da373c"

                if hasattr(self, 'canvas_indicator') and self.canvas_indicator.winfo_exists():
                    canvas_w = 390 if self.chat_visible else 410
                    fill_w = min(canvas_w, int(rms_energy * 1.8))
                    self.root.after(0, lambda c=bar_color, w=fill_w: [
                        self.canvas_indicator.coords(self.indicator_bar, 0, 0, w, 8),
                        self.canvas_indicator.itemconfig(self.indicator_bar, fill=c)
                    ])

                if self.muted.get() or self.server_muted:
                    return

                if is_talking:
                    boosted = audio_data * self.current_volume.get()
                    boosted = np.clip(boosted, -32768, 32767)
                    clean_int16 = boosted.astype(np.int16)

                    if self.is_connected and self.client_socket:
                        try:
                            self.client_socket.sendto(clean_int16.tobytes(), self.server_address)
                        except:
                            pass

                    if self.loopback_enabled.get():
                        self.audio_queue.put(clean_int16.reshape(-1, 1))
            except:
                pass

        try:
            with sd.InputStream(device=mic_id, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='int16', blocksize=BLOCKSIZE, callback=callback):
                while self.running_audio:
                    time.sleep(0.05)
        except Exception as e:
            print(f"Mic input error: {e}")

    def audio_output_loop(self):
        speaker_name = self.selected_speaker.get()
        speaker_id = self.get_device_index(speaker_name, 'out') if speaker_name else None

        def playback_callback(outdata, frames, time_info, status):
            try:
                if self.deafened.get():
                    outdata.fill(0)
                    while not self.audio_queue.empty():
                        try: self.audio_queue.get_nowait()
                        except queue.Empty: break
                    return

                outdata.fill(0)

                try:
                    voice_data = self.audio_queue.get_nowait()
                    v_len = min(len(outdata), len(voice_data))
                    outdata[:v_len] += voice_data[:v_len]
                except queue.Empty:
                    pass

            except:
                outdata.fill(0)

        try:
            with sd.OutputStream(device=speaker_id, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='int16', blocksize=BLOCKSIZE, callback=playback_callback):
                while self.running_audio:
                    time.sleep(0.05)
        except Exception as e:
            print(f"Speaker output error: {e}")

    def start_connecting(self):
        ip = self.server_ip.get().strip()
        port = self.server_port.get()
        nick = self.nickname.get().strip() or "User"

        self.status_label.config(text="Статус: Подключение...", fg="#f0ad4e")
        if hasattr(self, 'connect_btn'):
            self.connect_btn.config(state="disabled", text="Подключение...")
        self.root.update()
        time.sleep(0.2)

        try:
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_socket.settimeout(1.2)
            test_socket.bind(("", 0))
            
            server_addr = (ip, port)
            test_socket.sendto(f"NICK:{nick}".encode("utf-8"), server_addr)
            data, _ = test_socket.recvfrom(65535)
            
            if data == b"ACK":
                self.client_socket = test_socket
                self.client_socket.settimeout(None)
                self.server_address = server_addr
                
                self.status_label.config(text="Статус: Подключено", fg="#23a55a")
                self.is_connected = True
                self.server_muted = False
                self.root.update()
                
                self.root.after(100, self.create_room_frame)
                self.send_my_status()

                threading.Thread(target=self.receive_network_loop, daemon=True).start()
            else:
                raise Exception("Сервер не ответил.")
        except:
            self.status_label.config(text="Статус: Ошибка сервера!", fg="#da373c")
            messagebox.showerror("Ошибка", "Не удалось подключиться к друнспику.")
            self.is_connected = False
            if hasattr(self, 'connect_btn') and self.connect_btn.winfo_exists():
                self.connect_btn.config(state="normal", text="Подключиться к друнспику")

    def disconnect_server(self):
        if self.is_connected:
            try:
                if self.client_socket:
                    self.client_socket.sendto(b"DISCONNECT", self.server_address)
                    self.client_socket.close()
            except:
                pass
            self.is_connected = False
            self.server_muted = False
            self.is_sharing_screen = False
            
            for win in self.active_stream_windows.values():
                try: win.destroy()
                except: pass
            self.active_stream_windows.clear()
            self.user_card_frames.clear()

            self.create_login_frame()
            self.status_label.config(text="Статус: Отключено", fg="#da373c")

    def receive_network_loop(self):
        chunk_assembler = {} 

        while self.is_connected:
            try:
                if not self.client_socket:
                    break
                data, _ = self.client_socket.recvfrom(65535)
                
                if data == b"KICKED":
                    self.disconnect_server()
                    messagebox.showwarning("Внимание", "Вас кикнули с друнспика!")
                    break

                if data.startswith(b"USERLIST:"):
                    try:
                        self.remote_users = json.loads(data[9:].decode("utf-8"))
                        if hasattr(self, 'cards_container') and self.cards_container.winfo_exists():
                            self.root.after(0, self.refresh_user_cards)
                    except:
                        pass
                    continue

                if data.startswith(b"SCRCHUNK:"):
                    try:
                        header_end = data.find(b":", 9)
                        header_end2 = data.find(b":", header_end + 1)
                        header_end3 = data.find(b":", header_end2 + 1)
                        
                        streamer_name = data[9:header_end].decode("utf-8")
                        chunk_idx = int(data[header_end+1:header_end2].decode("utf-8"))
                        total_chunks = int(data[header_end2+1:header_end3].decode("utf-8"))
                        chunk_payload = data[header_end3+1:]

                        key = (streamer_name, total_chunks)
                        if key not in chunk_assembler:
                            chunk_assembler[key] = {}
                        
                        chunk_assembler[key][chunk_idx] = chunk_payload

                        if len(chunk_assembler[key]) == total_chunks:
                            full_jpg = b"".join(chunk_assembler[key][i] for i in sorted(chunk_assembler[key].keys()))
                            del chunk_assembler[key]
                            self.root.after(0, lambda n=streamer_name, b=full_jpg: self.update_remote_stream_frame(n, b))
                    except:
                        pass
                    continue

                if data.startswith(b"CHAT:"):
                    try:
                        parts = data.decode("utf-8", errors="ignore").split(":", 2)
                        sender = parts[1]
                        text = parts[2]
                        if sender != self.nickname.get():
                            self.root.after(0, lambda s=sender, t=text: self.append_chat_message(s, t, play_sound=True))
                    except:
                        pass
                    continue

                if data == b"MUTE":
                    self.server_muted = True
                    self.send_my_status()
                    continue
                
                if data == b"UNMUTE":
                    self.server_muted = False
                    self.send_my_status()
                    continue

                if data in [b"CONNECT", b"PING", b"DISCONNECT", b"ACK"]:
                    continue

                audio_data = np.frombuffer(data, dtype=np.int16).reshape(-1, 1)
                self.audio_queue.put(audio_data)
            except:
                if not self.is_connected:
                    break
                time.sleep(0.05)

    def on_close(self):
        self.running_audio = False
        self.is_sharing_screen = False
        self.disconnect_server()
        self.root.destroy()
        sys.exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = VoiceClientApp(root)
    root.mainloop()
