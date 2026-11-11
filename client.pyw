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
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog

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

CURRENT_VERSION = "2.7"
UPDATE_CHECK_URL = "https://raw.githubusercontent.com/StillJey127/drunspeak/refs/heads/main/version.json"

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def load_config():
    default_config = {
        "volume": 1.2,
        "gate": 30.0,
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

# Стандартные стабильные параметры для чистого звука без задержек и слоумо
SAMPLE_RATE = 16000  
CHANNELS = 1
BLOCKSIZE = 256  

BUILTIN_AVATARS = [
    "⚡ Молния", "🔥 Огонь", "💀 Череп", "🤖 Робот", "⭐ Звезда", 
    "🎮 Джойстик", "🐱 Кот", "🐶 Пёс", "👻 Призрак", "👑 Корона",
    "🚀 Ракета", "💎 Кристалл", "🐉 Дракон", "🦊 Лиса", "🦄 Единорог",
    "🎯 Цель", "🏆 Кубок", "🎧 Наушники", "💻 Ноутбук", "🍕 Пицца"
]

THEMES = {
    "Discord Modern": {"bg": "#1e1f22", "panel": "#2b2d31", "card": "#313338", "fg": "#f2f3f5", "accent": "#5865f2", "accent_grad": "#4752c4", "input": "#111214", "subtext": "#949ba4"},
    "Emerald Green": {"bg": "#021712", "panel": "#042f25", "card": "#064e3b", "fg": "#ecfdf5", "accent": "#10b981", "accent_grad": "#047857", "input": "#010b08", "subtext": "#6ee7b7"},
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
    "Coffee Mocha": {"bg": "#140e0a", "panel": "#261a12", "card": "#3d2a1c", "fg": "#fdf8f6", "accent": "#d97706", "accent_grad": "#b45309", "input": "#0a0705", "subtext": "#fcd34d"},
    "Steel Gray": {"bg": "#0f1115", "panel": "#1a1d24", "card": "#262a33", "fg": "#f3f4f6", "accent": "#9ca3af", "accent_grad": "#6b7280", "input": "#08090b", "subtext": "#d1d5db"}
}

class VoiceClientApp:
    def __init__(self, root):
        self.root = root
        
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("drunspeak.client.app.1")
        except: pass

        self.root.title(f"друнспик - v{CURRENT_VERSION}")
        
        ico_path = resource_path("drunspeakicon.ico")
        if not os.path.exists(ico_path): ico_path = resource_path("icon.ico")
        try:
            if os.path.exists(ico_path): self.root.iconbitmap(ico_path)
        except: pass

        self.root.geometry("950x650")
        self.root.minsize(800, 550)

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

        self.current_channel = tk.StringVar(value="друнспик 1")
        self.available_channels_data = {"друнспик 1": {"has_password": False}}
        self.current_ping = 0

        self.client_socket = None
        self.audio_queue = queue.Queue()
        
        self.is_connected = False
        self.running_audio = True
        self.is_sharing_screen = False
        self.remote_users = []
        
        self.remote_streams = {} 
        self.active_stream_windows = {} 
        self.user_card_frames = {} 
        self.chat_images_cache = {} 
        
        self.chat_messages = []

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
        self.create_login_frame()
            
        self.start_audio_streams()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

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
        
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.style.configure("Vertical.TScrollbar", 
                             gripcount=0,
                             background=self.th_panel,
                             darkcolor=self.th_panel,
                             lightcolor=self.th_panel,
                             troughcolor=self.th_card,
                             bordercolor=self.th_card,
                             arrowcolor=self.th_subtext)
        self.style.map("Vertical.TScrollbar",
                       background=[('active', self.th_accent), ('pressed', self.th_accent_grad)])

    def bind_entry_shortcuts(self, entry_widget):
        def select_all(event):
            entry_widget.select_range(0, tk.END)
            return 'break'
        entry_widget.bind("<Control-a>", select_all)
        entry_widget.bind("<Control-A>", select_all)

    def clear_window(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def create_rounded_btn(self, parent, text, bg_color, hover_color, command, font_size=10, py=6, px=10):
        btn = tk.Button(parent, text=text, bg=bg_color, fg="white", font=("Segoe UI", font_size, "bold"),
                        bd=1, relief="solid", activebackground=hover_color, activeforeground="white",
                        cursor="hand2", command=command, padx=px, pady=py)
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
        
        settings_btn = self.create_rounded_btn(top_bar, "⚙ Настройки", self.th_panel, self.th_accent, self.open_settings_window, font_size=9, py=3)
        settings_btn.pack(side="right")

        tk.Label(main_frame, text="ДРУНСПИК", bg=self.th_bg, fg=self.th_fg, font=("Segoe UI", 20, "bold")).pack(pady=(15, 20))

        tk.Label(main_frame, text="Ваш никнейм", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
        nick_entry = tk.Entry(main_frame, textvariable=self.nickname, bg=self.th_input, fg=self.th_fg, insertbackground=self.th_fg, font=("Segoe UI", 11), relief="solid", bd=1)
        nick_entry.pack(fill="x", pady=(4, 12), ipady=6)
        self.bind_entry_shortcuts(nick_entry)
        nick_entry.bind("<FocusOut>", lambda e: self.save_config())

        tk.Label(main_frame, text="IP / Домен Сервера", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
        ip_entry = tk.Entry(main_frame, textvariable=self.server_ip, bg=self.th_input, fg=self.th_fg, insertbackground=self.th_fg, font=("Segoe UI", 11), relief="solid", bd=1)
        ip_entry.pack(fill="x", pady=(4, 12), ipady=6)
        self.bind_entry_shortcuts(ip_entry)
        ip_entry.bind("<FocusOut>", lambda e: self.save_config())

        tk.Label(main_frame, text="Порт", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
        port_entry = tk.Entry(main_frame, textvariable=self.server_port, bg=self.th_input, fg=self.th_fg, insertbackground=self.th_fg, font=("Segoe UI", 11), relief="solid", bd=1)
        port_entry.pack(fill="x", pady=(4, 18), ipady=6)
        self.bind_entry_shortcuts(port_entry)
        port_entry.bind("<FocusOut>", lambda e: self.save_config())

        tk.Label(main_frame, text="Ваша аватарка", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
        avatar_combo = ttk.Combobox(main_frame, textvariable=self.selected_avatar, values=BUILTIN_AVATARS, state="readonly", font=("Segoe UI", 10))
        avatar_combo.pack(fill="x", pady=(4, 25), ipady=4)
        avatar_combo.bind("<<ComboboxSelected>>", lambda e: self.save_config())

        self.connect_btn = self.create_rounded_btn(main_frame, "🚀 Подключиться к серверу", self.th_accent, self.th_accent_grad, self.start_connecting, font_size=11, py=10)
        self.connect_btn.pack(fill="x")

        self.status_label = tk.Label(main_frame, text=f"Статус: Отключено (v{CURRENT_VERSION})", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"))
        self.status_label.pack(pady=15)

    def create_room_frame(self):
        self.clear_window()
        self.apply_theme()
        self.root.geometry("1000x650")

        self.main_layout = tk.Frame(self.root, bg=self.th_bg)
        self.main_layout.pack(fill="both", expand=True)

        left_panel = tk.Frame(self.main_layout, bg=self.th_panel, width=280)
        left_panel.pack(side="left", fill="y")
        left_panel.pack_propagate(False)

        serv_header = tk.Frame(left_panel, bg=self.th_panel, height=50)
        serv_header.pack(fill="x", padx=10, pady=5)
        tk.Label(serv_header, text="🔥 друнспик сервер", bg=self.th_panel, fg=self.th_fg, font=("Segoe UI", 10, "bold")).pack(side="left")
        
        self.create_rounded_btn(serv_header, "⚙", self.th_panel, self.th_accent, self.open_settings_window, font_size=9, py=2, px=6).pack(side="right")

        self.chan_scroll_frame = tk.Frame(left_panel, bg=self.th_panel)
        self.chan_scroll_frame.pack(fill="both", expand=True, padx=8, pady=5)

        self.rebuild_channel_list_ui()

        # Нижняя статусная панель с расширенной шириной для гарантированного размещения всех кнопок
        self.voice_status_bar = tk.Frame(left_panel, bg=self.th_card, height=65)
        self.voice_status_bar.pack(side="bottom", fill="x")
        self.voice_status_bar.pack_propagate(False)

        v_info_frame = tk.Frame(self.voice_status_bar, bg=self.th_card)
        v_info_frame.pack(side="left", padx=6, fill="y", expand=True)
        
        self.ping_indicator_lbl = tk.Label(v_info_frame, text="● Качество: Отличное", bg=self.th_card, fg="#23a55a", font=("Segoe UI", 7, "bold"), anchor="w")
        self.ping_indicator_lbl.pack(anchor="w", pady=(3, 0))

        tk.Label(v_info_frame, text="🟢 Связь активна", bg=self.th_card, fg="#23a55a", font=("Segoe UI", 8, "bold"), anchor="w").pack(anchor="w")
        self.current_chan_lbl = tk.Label(v_info_frame, text=self.current_channel.get(), bg=self.th_card, fg=self.th_subtext, font=("Segoe UI", 7), anchor="w")
        self.current_chan_lbl.pack(anchor="w")

        # Кнопки управления (сброс, наушники, микрофон) со строгим порядком и отступами
        self.disconnect_btn = self.create_rounded_btn(self.voice_status_bar, "📞", "#da373c", "#b82e32", self.disconnect_server, font_size=8, py=3, px=5)
        self.disconnect_btn.pack(side="right", padx=3)

        self.deafen_btn = self.create_rounded_btn(self.voice_status_bar, "🎧", self.th_card, self.th_accent, self.toggle_deafen, font_size=8, py=3, px=5)
        self.deafen_btn.pack(side="right", padx=2)

        mute_bg = "#da373c" if self.muted.get() else self.th_card
        self.mute_btn = self.create_rounded_btn(self.voice_status_bar, "🎤", mute_bg, self.th_accent, self.toggle_mute, font_size=8, py=3, px=5)
        self.mute_btn.pack(side="right", padx=2)

        right_panel = tk.Frame(self.main_layout, bg=self.th_panel, width=220)
        right_panel.pack(side="right", fill="y")
        right_panel.pack_propagate(False)

        tk.Label(right_panel, text="УЧАСТНИКИ ОНЛАЙН", bg=self.th_panel, fg=self.th_subtext, font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", padx=12, pady=(12, 6))
        
        self.cards_container = tk.Frame(right_panel, bg=self.th_panel)
        self.cards_container.pack(fill="both", expand=True, padx=8)

        center_panel = tk.Frame(self.main_layout, bg=self.th_bg)
        center_panel.pack(side="left", fill="both", expand=True)

        chat_top = tk.Frame(center_panel, bg=self.th_bg, height=50)
        chat_top.pack(fill="x", padx=15, pady=5)
        chat_top.pack_propagate(False)
        
        tk.Label(chat_top, text="# общий чат", bg=self.th_bg, fg=self.th_fg, font=("Segoe UI", 11, "bold")).pack(side="left")
        
        self.screen_btn = self.create_rounded_btn(chat_top, "📺 Трансляция экрана", self.th_accent, self.th_accent_grad, self.prompt_screen_share, font_size=9, py=5, px=10)
        self.screen_btn.pack(side="right", padx=5)

        chat_wrapper = tk.Frame(center_panel, bg=self.th_panel, bd=1, relief="solid")
        chat_wrapper.pack(fill="both", expand=True, padx=15, pady=5)

        self.chat_display = scrolledtext.ScrolledText(
            chat_wrapper, 
            bg=self.th_input, 
            fg=self.th_fg, 
            font=("Segoe UI", 10), 
            state="normal", 
            bd=0, 
            highlightthickness=0,
            padx=12,
            pady=12
        )
        self.chat_display.pack(side="left", fill="both", expand=True)

        chat_scroll = ttk.Scrollbar(chat_wrapper, orient="vertical", command=self.chat_display.yview, style="Vertical.TScrollbar")
        chat_scroll.pack(side="right", fill="y")
        self.chat_display.config(yscrollcommand=chat_scroll.set)
        
        self.redraw_chat_history()

        input_container = tk.Frame(center_panel, bg=self.th_panel, height=60)
        input_container.pack(fill="x", padx=15, pady=10)
        input_container.pack_propagate(False)

        self.img_btn = self.create_rounded_btn(input_container, "🖼️", self.th_panel, self.th_accent, self.send_image_dialog, font_size=10, py=6, px=8)
        self.img_btn.pack(side="left", padx=8)

        self.chat_entry = tk.Entry(input_container, bg=self.th_input, fg=self.th_fg, insertbackground=self.th_fg, font=("Segoe UI", 10), relief="solid", bd=1)
        self.chat_entry.pack(side="left", fill="both", expand=True, padx=5, ipady=6)
        self.bind_entry_shortcuts(self.chat_entry)
        self.chat_entry.bind("<Return>", self.send_chat_message)

        self.send_chat_btn = self.create_rounded_btn(input_container, "➤ Отправить", self.th_accent, self.th_accent_grad, self.send_chat_message, font_size=9, py=6, px=10)
        self.send_chat_btn.pack(side="right", padx=8)

        self.refresh_user_cards()

    def rebuild_channel_list_ui(self):
        for widget in self.chan_scroll_frame.winfo_children():
            widget.destroy()

        tk.Label(self.chan_scroll_frame, text="ТЕКСТОВЫЕ КАНАЛЫ", bg=self.th_panel, fg=self.th_subtext, font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", pady=(5, 2))
        tk.Button(self.chan_scroll_frame, text="# общий чат", bg=self.th_card, fg=self.th_fg, font=("Segoe UI", 9), anchor="w", bd=0, relief="flat", padx=8, pady=4).pack(fill="x", pady=2)

        tk.Label(self.chan_scroll_frame, text="ГОЛОСОВЫЕ КАНАЛЫ", bg=self.th_panel, fg=self.th_subtext, font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", pady=(12, 2))

        self.chan_buttons = {}
        for cname, cdata in self.available_channels_data.items():
            lock_icon = " 🔒" if cdata.get("has_password") else ""
            is_current = (self.current_channel.get() == cname)
            btn = tk.Button(self.chan_scroll_frame, text=f"🔊 {cname}{lock_icon}", bg=self.th_card if is_current else self.th_panel, 
                            fg=self.th_fg, font=("Segoe UI", 9), anchor="w", bd=1, relief="solid", padx=8, pady=5,
                            command=lambda cn=cname: self.try_switch_channel(cn))
            btn.pack(fill="x", pady=2)
            self.chan_buttons[cname] = btn

    def try_switch_channel(self, channel_name):
        if channel_name == self.current_channel.get(): return
        
        password = ""
        if self.is_connected and self.client_socket:
            if self.available_channels_data.get(channel_name, {}).get("has_password", False):
                password = simpledialog.askstring("Пароль канала", f"Канал '{channel_name}' защищен паролем. Введите пароль:", show="*") or ""
            
            try:
                pkt = f"SWITCHCHAN:{channel_name}:pass:{password}".encode("utf-8")
                self.client_socket.sendto(pkt, self.server_address)
            except: pass

    def redraw_chat_history(self):
        if not hasattr(self, 'chat_display') or not self.chat_display.winfo_exists(): return
        self.chat_display.config(state="normal")
        self.chat_display.delete("1.0", tk.END)
        
        for msg in self.chat_messages:
            if msg["type"] == "text":
                self.chat_display.insert(tk.END, f"[{msg['sender']}]: {msg['content']}\n\n")
            elif msg["type"] == "image":
                self.chat_display.insert(tk.END, f"[{msg['sender']} отправил(а) изображение]:\n")
                try:
                    import io
                    image = Image.open(io.BytesIO(msg["bytes"]))
                    image.thumbnail((260, 260))
                    photo = ImageTk.PhotoImage(image)
                    self.chat_images_cache[id(photo)] = photo
                    self.chat_display.image_create(tk.END, image=photo)
                    self.chat_display.insert(tk.END, "\n\n")
                except:
                    self.chat_display.insert(tk.END, "[Не удалось отобразить картинку]\n\n")
        self.chat_display.config(state="disabled")
        self.chat_display.see(tk.END)

    def send_image_dialog(self):
        file_path = filedialog.askopenfilename(
            title="Выберите картинку для чата",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.gif *.bmp")]
        )
        if file_path and self.is_connected and self.client_socket:
            try:
                with open(file_path, "rb") as f:
                    img_bytes = f.read()
                if len(img_bytes) > 2000 * 1024:
                    messagebox.showwarning("Ошибка", "Картинка слишком большая! До 2 МБ.")
                    return
                my_nick = self.nickname.get() or "User"
                ext = os.path.splitext(file_path)[1].lower()
                self.append_chat_image(my_nick, img_bytes, ext)

                chunk_size = 1024
                total_chunks = (len(img_bytes) + chunk_size - 1) // chunk_size
                for i in range(total_chunks):
                    chunk = img_bytes[i*chunk_size : (i+1)*chunk_size]
                    header = f"IMGCHUNK:{my_nick}:{ext}:{i}:{total_chunks}:".encode("utf-8")
                    self.client_socket.sendto(header + chunk, self.server_address)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось отправить картинку:\n{e}")

    def append_chat_message(self, sender, text, play_sound=False):
        self.chat_messages.append({"type": "text", "sender": sender, "content": text})
        if play_sound:
            try: self.root.bell()
            except: pass
        if hasattr(self, 'chat_display') and self.chat_display.winfo_exists():
            self.chat_display.config(state="normal")
            self.chat_display.insert(tk.END, f"[{sender}]: {text}\n\n")
            self.chat_display.config(state="disabled")
            self.chat_display.see(tk.END)

    def append_chat_image(self, sender, img_bytes, ext):
        self.chat_messages.append({"type": "image", "sender": sender, "bytes": img_bytes, "ext": ext})
        if hasattr(self, 'chat_display') and self.chat_display.winfo_exists():
            self.chat_display.config(state="normal")
            self.chat_display.insert(tk.END, f"[{sender} отправил(а) изображение]:\n")
            try:
                import io
                image = Image.open(io.BytesIO(img_bytes))
                image.thumbnail((260, 260))
                photo = ImageTk.PhotoImage(image)
                self.chat_images_cache[id(photo)] = photo
                self.chat_display.image_create(tk.END, image=photo)
                self.chat_display.insert(tk.END, "\n\n")
            except:
                self.chat_display.insert(tk.END, "[Не удалось отобразить картинку]\n\n")
            self.chat_display.config(state="disabled")
            self.chat_display.see(tk.END)

    def send_chat_message(self, event=None):
        if not hasattr(self, 'chat_entry'): return
        text = self.chat_entry.get().strip()
        if text and self.is_connected and self.client_socket:
            try:
                my_nick = self.nickname.get() or "User"
                self.append_chat_message(my_nick, text)
                packet = f"CHAT:{my_nick}:{text}".encode("utf-8")
                self.client_socket.sendto(packet, self.server_address)
                self.chat_entry.delete(0, tk.END)
            except Exception as e: print(e)

    def refresh_user_cards(self):
        if not hasattr(self, 'cards_container') or not self.cards_container.winfo_exists():
            return

        for widget in self.cards_container.winfo_children():
            widget.destroy()

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
                "avatar": self.selected_avatar.get(),
                "channel": self.current_channel.get()
            })
        else:
            for u in users:
                if u.get("name") == my_nick:
                    u["sharing"] = self.is_sharing_screen
                    u["avatar"] = self.selected_avatar.get()
                    u["channel"] = self.current_channel.get()

        channels_map = {c: [] for c in self.available_channels_data.keys()}
        for u in users:
            chan = u.get("channel", "друнспик 1")
            if chan not in channels_map:
                channels_map[chan] = []
            channels_map[chan].append(u)

        for chan_name, chan_users in channels_map.items():
            tk.Label(self.cards_container, text=f"🔊 {chan_name} ({len(chan_users)})", bg=self.th_panel, fg=self.th_subtext, font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", pady=(8, 2))
            
            for u in chan_users:
                name = u.get("name")
                is_me = (name == my_nick)
                display_name = f"{name} (Вы)" if is_me else name
                
                avatar_val = u.get("avatar", "⚡ Молния")
                avatar_symbol = avatar_val.split()[0] if avatar_val else "⚡"

                is_in_my_channel = (u.get("channel") == self.current_channel.get())

                is_talking = u.get("talking", False) if is_in_my_channel else False
                is_admin_muted = u.get("muted", False)
                is_mic_muted = u.get("mic_muted", False)
                is_deafened = u.get("deafened", False)

                if not is_in_my_channel:
                    status_sub = f"В [{u.get('channel')}]"
                    stat_color = self.th_subtext
                elif is_admin_muted:
                    status_sub = "🔇 Мут админа"
                    stat_color = "#da373c"
                elif is_mic_muted:
                    status_sub = "🎤 Микрофон выкл"
                    stat_color = "#e67e22"
                elif is_deafened:
                    status_sub = "🎧 Без звука"
                    stat_color = "#f0b232"
                elif is_talking:
                    status_sub = "🟢 Говорит..."
                    stat_color = "#23a55a"
                else:
                    status_sub = "В сети"
                    stat_color = self.th_subtext

                card = tk.Frame(self.cards_container, bg=self.th_card, padx=6, pady=4)
                card.pack(fill="x", pady=2)

                tk.Label(card, text=avatar_symbol, bg=self.th_input, fg=self.th_fg, font=("Segoe UI", 12), width=2, height=1, relief="solid", bd=1).pack(side="left", padx=(0, 6))
                
                info_f = tk.Frame(card, bg=self.th_card)
                info_f.pack(side="left", fill="both", expand=True)

                tk.Label(info_f, text=display_name, bg=self.th_card, fg=self.th_fg, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
                tk.Label(info_f, text=status_sub, bg=self.th_card, fg=stat_color, font=("Segoe UI", 7), anchor="w").pack(fill="x")

                if is_in_my_channel and u.get("sharing", False):
                    stream_btn = tk.Button(card, text="📺", bg=self.th_accent, fg="white", font=("Segoe UI", 8), relief="solid", bd=1, command=lambda n=name: self.open_remote_stream_window(n))
                    stream_btn.pack(side="right", padx=2)

    def send_my_status(self):
        if self.is_connected and self.client_socket:
            try:
                status_data = {
                    "mic_muted": self.muted.get() or self.server_muted,
                    "deafened": self.deafened.get(),
                    "sharing": self.is_sharing_screen,
                    "avatar": self.selected_avatar.get(),
                    "channel": self.current_channel.get()
                }
                self.client_socket.sendto(b"STATUS:" + json.dumps(status_data).encode("utf-8"), self.server_address)
            except: pass

    def send_talking_status(self, is_talking):
        if self.is_connected and self.client_socket:
            try:
                cmd = b"TALKING:1" if is_talking else b"TALKING:0"
                self.client_socket.sendto(cmd, self.server_address)
            except: pass

    def prompt_screen_share(self):
        if self.is_sharing_screen:
            self.is_sharing_screen = False
            self.screen_btn.config(text="📺 Трансляция экрана", bg=self.th_accent)
            self.send_my_status()
            self.refresh_user_cards()
            return

        self.is_sharing_screen = True
        self.screen_btn.config(text="⏹ Стоп трансляцию", bg="#da373c")
        self.send_my_status()
        self.refresh_user_cards()
        threading.Thread(target=self.screen_share_loop, daemon=True).start()

    def screen_share_loop(self):
        delay = 1.0 / 60.0  # 60 FPS
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

                            # Ультимативное качество сжатия 98% для четкой и плавной демки
                            _, encoded = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 98])
                            total_bytes = encoded.tobytes()
                            chunk_size = 1024  
                            total_chunks = (len(total_bytes) + chunk_size - 1) // chunk_size
                            
                            for i in range(total_chunks):
                                chunk = total_bytes[i*chunk_size : (i+1)*chunk_size]
                                header = f"SCRCHUNK:{self.nickname.get()}:{i}:{total_chunks}:".encode("utf-8")
                                if self.client_socket:
                                    self.client_socket.sendto(header + chunk, self.server_address)
                        except Exception as e: print(e)
                        
                        elapsed = time.time() - start_time
                        if elapsed < delay: time.sleep(delay - elapsed)
            except Exception as e: print(e)

        self.is_sharing_screen = False
        try:
            self.send_my_status()
            self.refresh_user_cards()
            if hasattr(self, 'screen_btn') and self.screen_btn.winfo_exists():
                self.root.after(0, lambda: self.screen_btn.config(text="📺 Трансляция экрана", bg=self.th_accent))
        except: pass

    def open_remote_stream_window(self, streamer_name):
        if streamer_name in self.active_stream_windows and self.active_stream_windows[streamer_name].winfo_exists():
            self.active_stream_windows[streamer_name].lift()
            return

        stream_win = tk.Toplevel(self.root)
        stream_win.title(f"Трансляция: {streamer_name}")
        stream_win.geometry("960x580")
        stream_win.config(bg="#111214")

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
                            h = stream_win.winfo_height()
                            if w > 50 and h > 50:
                                image = Image.fromarray(frame)
                                image = image.resize((w, h), Image.Resampling.BILINEAR)
                                photo = ImageTk.PhotoImage(image=image)
                                if video_label.winfo_exists():
                                    video_label.config(image=photo)
                                    video_label.image = photo
                except Exception as e: print(e)
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
        except Exception as e: print(e)

    def update_remote_image_chunk(self, sender, ext, raw_or_jpg):
        try:
            self.append_chat_image(sender, raw_or_jpg, ext)
        except Exception as e: print(e)

    def toggle_deafen(self):
        if self.deafened.get():
            self.deafened.set(False)
            self.deafen_btn.config(bg=self.th_card)
        else:
            self.deafened.set(True)
            self.deafen_btn.config(bg="#da373c")
            while not self.audio_queue.empty():
                try: self.audio_queue.get_nowait()
                except queue.Empty: break
        self.send_my_status()

    def toggle_mute(self):
        if self.muted.get():
            self.muted.set(False)
            self.mute_btn.config(bg=self.th_card)
        else:
            self.muted.set(True)
            self.mute_btn.config(bg="#da373c")
        self.send_my_status()
        self.refresh_user_cards()

    def open_settings_window(self):
        settings_win = tk.Toplevel(self.root)
        settings_win.title("Настройки друнспика")
        settings_win.geometry("440x720")
        settings_win.config(bg=self.th_bg)
        settings_win.resizable(False, False)
        settings_win.grab_set()

        tk.Label(settings_win, text="ПАРАМЕТРЫ И АУДИО", bg=self.th_bg, fg=self.th_fg, font=("Segoe UI", 12, "bold")).pack(pady=15)

        tk.Label(settings_win, text="Тема оформления", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=25)
        theme_combo = ttk.Combobox(settings_win, textvariable=self.current_theme, values=list(THEMES.keys()), state="readonly", width=40)
        theme_combo.pack(padx=25, pady=(4, 10))
        theme_combo.bind("<<ComboboxSelected>>", lambda e: self.change_theme_live())

        tk.Label(settings_win, text="Система шумоподавления", bg=self.th_bg, fg=self.th_subtext, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=25)
        ns_combo = ttk.Combobox(settings_win, textvariable=self.noise_suppression_mode, values=["Выключено", "Базовое", "Улучшенное (Чистый голос)", "Максимальное"], state="readonly", width=40)
        ns_combo.pack(padx=25, pady=(4, 10))
        ns_combo.bind("<<ComboboxSelected>>", lambda e: self.save_config())

        loopback_chk = tk.Checkbutton(settings_win, text="Прослушивать свой микрофон (Loopback)", variable=self.loopback_enabled, bg=self.th_bg, fg=self.th_fg, selectcolor=self.th_input, activebackground=self.th_bg, activeforeground=self.th_fg, font=("Segoe UI", 9, "bold"), command=self.save_config)
        loopback_chk.pack(anchor="w", padx=25, pady=(0, 10))

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

        update_btn = self.create_rounded_btn(settings_win, "🔄 Проверить обновления", self.th_accent, self.th_accent_grad, lambda: self.check_for_updates_interactive(), font_size=10, py=6)
        update_btn.pack(fill="x", padx=25, pady=(5, 5))

        close_btn = self.create_rounded_btn(settings_win, "Готово", self.th_panel, self.th_accent, settings_win.destroy, font_size=10, py=6)
        close_btn.pack(fill="x", padx=25, pady=10)

    def change_theme_live(self):
        self.save_config()
        self.apply_theme()
        if not self.is_connected:
            self.create_login_frame()
            return
        self.create_room_frame()

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
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e: print(e)

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
        except: pass
        return None

    def start_audio_streams(self):
        threading.Thread(target=self.audio_input_loop, daemon=True).start()
        threading.Thread(target=self.audio_output_loop, daemon=True).start()
        threading.Thread(target=self.ping_measurer_loop, daemon=True).start()

    def restart_audio_streams(self):
        self.running_audio = False
        time.sleep(0.2)
        self.running_audio = True
        self.start_audio_streams()

    def ping_measurer_loop(self):
        while self.running_audio:
            if self.is_connected and self.client_socket:
                try:
                    start_t = time.time()
                    self.client_socket.sendto(b"PING", self.server_address)
                    self.client_socket.settimeout(1.0)
                    try:
                        data, _ = self.client_socket.recvfrom(1024)
                        if data:
                            rtt = int((time.time() - start_t) * 1000)
                            self.current_ping = rtt
                            self.root.after(0, self.update_ping_ui, rtt)
                    except socket.timeout:
                        self.current_ping = 999
                        self.root.after(0, self.update_ping_ui, 999)
                    self.client_socket.settimeout(None)
                except: pass
            time.sleep(2.0)

    def update_ping_ui(self, ping_val):
        if not hasattr(self, 'ping_indicator_lbl') or not self.ping_indicator_lbl.winfo_exists(): return
        
        if ping_val <= 150:
            text = "● Качество: Отличное"
            color = "#23a55a"
        elif ping_val <= 450:
            text = "● Качество: Среднее"
            color = "#f0ad4e"
        else:
            text = "● Качество: Плохое"
            color = "#da373c"
            
        self.ping_indicator_lbl.config(text=text, fg=color)

    def audio_input_loop(self):
        mic_name = self.selected_mic.get()
        mic_id = self.get_device_index(mic_name, 'in') if mic_name else None

        def callback(indata, frames, time_info, status):
            if not self.running_audio: return
            try:
                audio_data = np.frombuffer(indata, dtype=np.int16).astype(np.float32)
                ns_mode = self.noise_suppression_mode.get()
                if ns_mode != "Выключено":
                    noise_limit = 5.0 if ns_mode == "Базовое" else (12.0 if ns_mode == "Улучшенное (Чистый голос)" else 22.0)
                    mask = np.abs(audio_data) > noise_limit
                    audio_data = audio_data * mask * 0.98

                rms_energy = np.sqrt(np.mean(audio_data ** 2))
                threshold = self.rms_threshold.get()
                is_talking = rms_energy >= threshold

                self.send_talking_status(is_talking)

                if self.muted.get() or self.server_muted: return

                if is_talking:
                    boosted = audio_data * self.current_volume.get()
                    boosted = np.clip(boosted, -32768, 32767)
                    clean_int16 = boosted.astype(np.int16)

                    if self.is_connected and self.client_socket:
                        try:
                            self.client_socket.sendto(clean_int16.tobytes(), self.server_address)
                        except: pass

                    if self.loopback_enabled.get():
                        self.audio_queue.put(clean_int16.reshape(-1, 1))
            except: pass

        try:
            with sd.InputStream(device=mic_id, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='int16', blocksize=BLOCKSIZE, callback=callback):
                while self.running_audio: time.sleep(0.05)
        except Exception as e: print(e)

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
                
                # Защита от накопления очереди (предотвращает эффект слоумо при микро-лаге)
                if self.audio_queue.qsize() > 5:
                    while self.audio_queue.qsize() > 2:
                        try: self.audio_queue.get_nowait()
                        except queue.Empty: break

                outdata.fill(0)
                try:
                    voice_data = self.audio_queue.get_nowait()
                    v_len = min(len(outdata), len(voice_data))
                    outdata[:v_len] += voice_data[:v_len]
                except queue.Empty: pass
            except: outdata.fill(0)

        try:
            with sd.OutputStream(device=speaker_id, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='int16', blocksize=BLOCKSIZE, callback=playback_callback):
                while self.running_audio: time.sleep(0.05)
        except Exception as e: print(e)

    def start_connecting(self):
        ip = self.server_ip.get().strip()
        port = self.server_port.get()
        nick = self.nickname.get().strip() or "User"

        self.status_label.config(text="Статус: Подключение...", fg="#f0ad4e")
        self.root.update()

        try:
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_socket.settimeout(1.5)
            test_socket.bind(("", 0))
            
            server_addr = (ip, port)
            test_socket.sendto(f"AUTH:{nick}:CHAN:друнспик 1:PASS:".encode("utf-8"), server_addr)
            data, _ = test_socket.recvfrom(65535)
            
            if data == b"ACK":
                self.client_socket = test_socket
                self.client_socket.settimeout(None)
                self.server_address = server_addr
                
                self.status_label.config(text="Статус: Подключено", fg="#23a55a")
                self.is_connected = True
                self.server_muted = False
                self.current_channel.set("друнспик 1")
                self.root.update()
                
                self.root.after(100, self.create_room_frame)
                self.send_my_status()

                threading.Thread(target=self.receive_network_loop, daemon=True).start()
            elif data == b"WRONG_PASS":
                messagebox.showerror("Ошибка", "Неверный пароль для канала!")
                self.status_label.config(text="Статус: Неверный пароль", fg="#da373c")
            else:
                raise Exception("Сервер отклонил подключение.")
        except Exception as err:
            self.status_label.config(text="Статус: Ошибка подключения!", fg="#da373c")
            messagebox.showerror("Ошибка", f"Не удалось подключиться:\n{err}")
            self.is_connected = False

    def disconnect_server(self):
        if self.is_connected:
            try:
                if self.client_socket:
                    self.client_socket.sendto(b"DISCONNECT", self.server_address)
                    self.client_socket.close()
            except: pass
            self.is_connected = False
            self.server_muted = False
            self.is_sharing_screen = False
            
            for win in self.active_stream_windows.values():
                try: win.destroy()
                except: pass
            self.active_stream_windows.clear()
            self.user_card_frames.clear()

            self.create_login_frame()

    def receive_network_loop(self):
        chunk_assembler = {} 
        img_assembler = {}

        while self.is_connected:
            try:
                if not self.client_socket: break
                data, _ = self.client_socket.recvfrom(65535)
                
                if data == b"KICKED":
                    self.disconnect_server()
                    messagebox.showwarning("Внимание", "Вас кикнули с сервера!")
                    break

                if data == b"WRONG_PASS":
                    messagebox.showerror("Ошибка", "Неверный пароль канала!")
                    continue

                if data == b"ACK_CHAN":
                    continue

                if data.startswith(b"USERLIST:"):
                    try:
                        packet = json.loads(data[9:].decode("utf-8"))
                        self.remote_users = packet.get("users", [])
                        self.available_channels_data = packet.get("channels", {"друнспик 1": {"has_password": False}})
                        
                        my_nick = self.nickname.get() or "User"
                        for u in self.remote_users:
                            if u.get("name") == my_nick:
                                s_chan = u.get("channel", "друнспик 1")
                                if s_chan != self.current_channel.get():
                                    self.current_channel.set(s_chan)
                                    if hasattr(self, 'current_chan_lbl') and self.current_chan_lbl.winfo_exists():
                                        self.root.after(0, lambda c=s_chan: self.current_chan_lbl.config(text=c))
                                    if hasattr(self, 'chan_scroll_frame') and self.chan_scroll_frame.winfo_exists():
                                        self.root.after(0, self.rebuild_channel_list_ui)
                        
                        if hasattr(self, 'cards_container') and self.cards_container.winfo_exists():
                            self.root.after(0, self.refresh_user_cards)
                        if hasattr(self, 'chan_scroll_frame') and self.chan_scroll_frame.winfo_exists():
                            self.root.after(0, self.rebuild_channel_list_ui)
                    except: pass
                    continue

                if data.startswith(b"SCRCHUNK:"):
                    try:
                        h1 = data.find(b":", 9)
                        h2 = data.find(b":", h1 + 1)
                        h3 = data.find(b":", h2 + 1)
                        streamer_name = data[9:h1].decode("utf-8")
                        chunk_idx = int(data[h1+1:h2].decode("utf-8"))
                        total_chunks = int(data[h2+1:h3].decode("utf-8"))
                        payload = data[h3+1:]

                        key = (streamer_name, total_chunks)
                        if key not in chunk_assembler: chunk_assembler[key] = {}
                        chunk_assembler[key][chunk_idx] = payload

                        if len(chunk_assembler[key]) == total_chunks:
                            full_jpg = b"".join(chunk_assembler[key][i] for i in sorted(chunk_assembler[key].keys()))
                            del chunk_assembler[key]
                            self.root.after(0, lambda n=streamer_name, b=full_jpg: self.update_remote_stream_frame(n, b))
                    except: pass
                    continue

                if data.startswith(b"IMGCHUNK:"):
                    try:
                        h1 = data.find(b":", 9)
                        h2 = data.find(b":", h1 + 1)
                        h3 = data.find(b":", h2 + 1)
                        h4 = data.find(b":", h3 + 1)
                        sender = data[9:h1].decode("utf-8")
                        ext = data[h1+1:h2].decode("utf-8")
                        chunk_idx = int(data[h2+1:h3].decode("utf-8"))
                        total_chunks = int(data[h3+1:h4].decode("utf-8"))
                        payload = data[h4+1:]

                        key = (sender, total_chunks)
                        if key not in img_assembler: img_assembler[key] = {}
                        img_assembler[key][chunk_idx] = payload

                        if len(img_assembler[key]) == total_chunks:
                            full_img = b"".join(img_assembler[key][i] for i in sorted(img_assembler[key].keys()))
                            del img_assembler[key]
                            self.root.after(0, lambda s=sender, x=ext, b=full_img: self.update_remote_image_chunk(s, x, b))
                    except: pass
                    continue

                if data.startswith(b"CHAT:"):
                    try:
                        parts = data.decode("utf-8", errors="ignore").split(":", 2)
                        sender, text = parts[1], parts[2]
                        if sender != self.nickname.get():
                            self.root.after(0, lambda s=sender, t=text: self.append_chat_message(s, t, play_sound=True))
                    except: pass
                    continue

                if data == b"MUTE":
                    self.server_muted = True
                    self.send_my_status()
                    continue
                if data == b"UNMUTE":
                    self.server_muted = False
                    self.send_my_status()
                    continue

                if data in [b"CONNECT", b"PING", b"DISCONNECT", b"ACK"]: continue

                audio_data = np.frombuffer(data, dtype=np.int16).reshape(-1, 1)
                self.audio_queue.put(audio_data)
            except:
                if not self.is_connected: break
                time.sleep(0.05)

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
            messagebox.showerror("Ошибка", f"Не удалось проверить обновления (проверьте интернет-соединение или ссылку):\n{e}")

    def download_and_apply_update(self, url):
        try:
            temp_dir = tempfile.gettempdir()
            new_file_path = os.path.join(temp_dir, "drunspeak_update.pyw")
            urllib.request.urlretrieve(url, new_file_path)

            current_exe_dir = BASE_DIR
            target_file_path = os.path.join(current_exe_dir, "client.pyw")
            bat_path = os.path.join(temp_dir, "drunspeak_updater.bat")
            
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(f"""@echo off
chcp 65001 > nul
timeout /t 2 /nobreak > nul
del /f /q "{target_file_path}"
move /y "{new_file_path}" "{target_file_path}"
start "" pythonw.exe "{target_file_path}"
del "%~f0"
""")

            subprocess.Popen([bat_path], shell=True)
            self.on_close()
        except Exception as e:
            messagebox.showerror("Ошибка обновления", f"Не удалось применить обновление:\n{e}")

    def on_close(self):
        self.running_audio, self.is_sharing_screen = False, False
        self.disconnect_server()
        self.root.destroy()
        sys.exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = VoiceClientApp(root)
    root.mainloop()
