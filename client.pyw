# -*- coding: utf-8 -*-
"""
Друнспик — клиент, протокол v4.
  контроль:      b"J" + len(4, big-endian) + JSON
  голос:         b"A" + seq(2) + int16 mono 16 kHz
  картинки чата: b"I" + [S->C: nick_len+nick] + id(4) + ext_len(1) + ext + idx(2) + total(2) + данные
  видео экрана:  b"V" + [S->C: nick_len+nick] + fid(4) + total(2) + idx(2) + jpeg-данные
  звук экрана:   b"S" + [S->C: nick_len+nick] + seq(4) + int16 mono 24 kHz
Трансляция экрана идёт ЧЕРЕЗ СЕРВЕР (как в Discord) — не зависит от NAT.
"""
import os
import io
import sys
import base64
import socket
import threading
import time
import json
import queue
import hashlib
import inspect
import struct
import subprocess
import tempfile
import ctypes
import re
import urllib.request
import webbrowser
import tkinter as tk
import tkinter.font as tkFont
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog
from tkinter import colorchooser
import numpy as np
import sounddevice as sd

try:
    import mss
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

try:
    from PIL import Image, ImageTk, ImageFont, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

CURRENT_VERSION = "3.7"

# Единый мягкий шрифт интерфейса: переменный Segoe UI (Win11) -> Candara
# -> Calibri -> обычный Segoe UI. Выбирается ДО описания классов, потому что
# значения font= по умолчанию вычисляются в момент def.
UI_FONT = "Segoe UI"


def pick_ui_font():
    global UI_FONT
    fam = set()
    try:
        probe = tk.Tk()
        probe.withdraw()
        fam = set(tkFont.families(probe))
        probe.destroy()
    except Exception:
        pass
    for cand in ("Segoe UI Variable Text", "Segoe UI Variable Display",
                 "Candara", "Calibri Light", "Calibri", "Segoe UI"):
        if cand in fam:
            UI_FONT = cand
            return cand
    return UI_FONT


pick_ui_font()
UPDATE_REPO = "StillJey127/drunspeak"
UPDATE_CHECK_URLS = [
    "https://raw.githubusercontent.com/StillJey127/drunspeak/main/version.json",
    "https://raw.githubusercontent.com/StillJey127/drunspeak/refs/heads/main/version.json",
    "https://cdn.jsdelivr.net/gh/StillJey127/drunspeak@main/version.json",
]
UPDATE_CHECK_URL = UPDATE_CHECK_URLS[0]
SCREENSHARE_AVAILABLE = MSS_AVAILABLE and PIL_AVAILABLE

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCKSIZE = 320          # 20 мс голос
STREAM_FPS = 60          # целевые кадры трансляции в секунду
STREAM_MIN_FPS = 30      # авто-деградация, если кодирование не успевает
STREAM_MAX_W = 2560      # режем ТОЛЬКО на 4K+ мониторах; 1080p шлём как есть
STREAM_JPEG_Q = 82       # стартовое качество (без «шакалов»)
STREAM_MIN_Q = 62        # ниже не опускаемся никогда
STREAM_MAX_BYTES = 200000  # бюджет на кадр
STREAM_CHUNK = 1400      # размер куска кадра (меньше чанков -> меньше потерь)
STREAM_FEC_GROUP = 12    # 1 XOR-паритетный чанк на 12 данных (чинит потерю без RTT)
FILE_MAX_SEND = 300 * 1024 * 1024  # 300 МБ
FILE_BIG_THRESHOLD = 8 * 1024 * 1024  # больше — на диск, а не в память чата
STREAM_AUDIO_RATE = 24000  # звук трансляции: 24 кГц mono
STREAM_AUDIO_BLOCK = 480   # 20 мс

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

def _resolve_base_dir():
    """Папка с config.json. Ярлык установщика передаёт её аргументом,
    поэтому настройки всегда берутся из каталога приложения, а не из
    случайного CWD (pythonw запускается из разных мест)."""
    env = os.environ.get("DRUNSPEAK_HOME")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    for arg in sys.argv[1:]:
        if os.path.isdir(arg) and (
                os.path.exists(os.path.join(arg, "config.json"))
                or os.path.exists(os.path.join(arg, "client.pyw"))):
            return os.path.abspath(arg)
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _resolve_base_dir()
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
CUSTOM_THEMES_FILE = os.path.join(BASE_DIR, "custom_themes.json")


def force_high_performance_gpu():
    """Просим Windows запускать приложение на дискретном GPU, а не на
    энергосберегающем (важно для mss/скринкаста на 60 fps).

    HKCU\\Software\\Microsoft\\DirectX\\UserGpuPreferences:
      GpuPreference=0 — авто, 1 — энергосбережение, 2 — высокая производительность.
    Ничего не ломает, если ключ недоступен.
    """
    try:
        import winreg
        exe = os.path.abspath(sys.executable if getattr(sys, "frozen", False)
                              else sys.argv[0])
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\DirectX\UserGpuPreferences")
        try:
            winreg.SetValueEx(key, exe, 0, winreg.REG_SZ, "GpuPreference=2;")
        finally:
            winreg.CloseKey(key)
        return True
    except Exception:
        return False

DEFAULT_CONFIG = {
    "volume": 1.0,
    "gate": 30.0,
    "noise_suppression": "Улучшенное (Чистый голос)",
    "loopback": False,
    "server_ip": "drunspeak.playit.plus",
    "server_port": 5321,
    "server_password": "",
    "account_password": "",
    "nickname": "User",
    "avatar_b64": "",
    "mic_device": "",
    "speaker_device": "",
    "theme": "Discord Modern",
    "stream_audio_enabled": True,
    "stream_volume": 1.0,
    "stream_capture_volume": 1.0,
}

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    cfg.update(loaded)
        else:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Ошибка конфига: {e}")
    return cfg

cfg = load_config()

# размеры/ограничения аватарок
AVATAR_SIZE = 64          # внутренний размер (по сети 64x64 PNG)
AVATAR_B64_LIMIT = 12000  # ~9 КБ картинки
AVATAR_PART_SIZE = 800    # кусок base64; пакет < MTU туннеля (без фрагментации)

THEMES = {
    "Discord Modern": {"bg": "#1a1b20", "panel": "#232429", "card": "#2a2b31", "fg": "#e8eaed", "accent": "#5c6ac4", "accent_grad": "#4a56a8", "input": "#131417", "subtext": "#8f959e", "line": "#37393f", "ok": "#3ba55c", "warn": "#c9973c", "bad": "#b0413e"},
    "Emerald Green": {"bg": "#101a16", "panel": "#15231e", "card": "#1b2c26", "fg": "#d9e6e0", "accent": "#3f9d74", "accent_grad": "#2f7c5c", "input": "#0b1210", "subtext": "#7ea294", "line": "#263c34", "ok": "#4f9e6b", "warn": "#b79a4a", "bad": "#b05c55"},
    "Cyber Neon": {"bg": "#0d0b16", "panel": "#141126", "card": "#1a1730", "fg": "#d6d9e8", "accent": "#8f5cc9", "accent_grad": "#7148a3", "input": "#090712", "subtext": "#8d87ab", "line": "#241f42", "ok": "#4f9e6b", "warn": "#b79a4a", "bad": "#a35067"},
    "Midnight Wave": {"bg": "#0d1220", "panel": "#131a2c", "card": "#19223a", "fg": "#dbe2ee", "accent": "#4a6fbd", "accent_grad": "#3a5a9d", "input": "#090d17", "subtext": "#8291a8", "line": "#212c47", "ok": "#4f9e6b", "warn": "#b79a4a", "bad": "#b05c55"},
    "Sunset Gradient": {"bg": "#1a1312", "panel": "#251a18", "card": "#302220", "fg": "#e9dedb", "accent": "#b2694a", "accent_grad": "#93543b", "input": "#120d0c", "subtext": "#a08a83", "line": "#3d2c29", "ok": "#4f9e6b", "warn": "#b79a4a", "bad": "#a85050"},
    "Purple Aurora": {"bg": "#131022", "panel": "#1b1731", "card": "#241f41", "fg": "#dcd9ea", "accent": "#7a63b8", "accent_grad": "#614e94", "input": "#0d0b18", "subtext": "#918aa8", "line": "#2d2749", "ok": "#4f9e6b", "warn": "#b79a4a", "bad": "#a35a70"},
    "Matrix Stream": {"bg": "#0d1512", "panel": "#112019", "card": "#152b21", "fg": "#cfe2d6", "accent": "#4d8a5f", "accent_grad": "#3b6d4a", "input": "#080f0c", "subtext": "#7d9c86", "line": "#1e3a2b", "ok": "#4d8a5f", "warn": "#a9924a", "bad": "#a35050"},
    "Clean Glass": {"bg": "#eef1f5", "panel": "#f7f9fb", "card": "#e2e7ee", "fg": "#2b3442", "accent": "#5b6f9e", "accent_grad": "#48597f", "input": "#ffffff", "subtext": "#6c7a8c", "line": "#c8d1dd", "ok": "#3d8a5c", "warn": "#a9883d", "bad": "#ad4a45"},
    "Deep Space": {"bg": "#0b0c10", "panel": "#12131a", "card": "#191b24", "fg": "#d7dae2", "accent": "#5e66a8", "accent_grad": "#4a5189", "input": "#07080b", "subtext": "#7f8594", "line": "#22242f", "ok": "#4f9e6b", "warn": "#b79a4a", "bad": "#a85656"},
    "Crimson Blood": {"bg": "#171012", "panel": "#221619", "card": "#2e1c20", "fg": "#e8dcde", "accent": "#9e4a5c", "accent_grad": "#7e3a49", "input": "#0f0a0b", "subtext": "#9d8489", "line": "#3c252b", "ok": "#4f9e6b", "warn": "#b79a4a", "bad": "#a85060"},
    "Arctic Ice": {"bg": "#10161c", "panel": "#16202a", "card": "#1c2a36", "fg": "#d8e3ea", "accent": "#4a7f9e", "accent_grad": "#3a677f", "input": "#0a0f13", "subtext": "#7f95a3", "line": "#243543", "ok": "#4f9e6b", "warn": "#b79a4a", "bad": "#b05c55"},
    "Gold Luxury": {"bg": "#17140d", "panel": "#221d13", "card": "#2e2719", "fg": "#e6e0d1", "accent": "#a08438", "accent_grad": "#81682c", "input": "#0f0c07", "subtext": "#9a9078", "line": "#3b3220", "ok": "#4f9e6b", "warn": "#a08438", "bad": "#a85050"},
    "Toxic Waste": {"bg": "#121710", "panel": "#1a2317", "card": "#222f1e", "fg": "#dde4d5", "accent": "#6e8a44", "accent_grad": "#577035", "input": "#0b0f09", "subtext": "#8b9a7c", "line": "#2a3a25", "ok": "#5e8a44", "warn": "#a9924a", "bad": "#a35050"},
    "Coffee Mocha": {"bg": "#171310", "panel": "#231c16", "card": "#30261e", "fg": "#e6ddd4", "accent": "#96683c", "accent_grad": "#7a5330", "input": "#0e0b09", "subtext": "#998a7c", "line": "#3b2e24", "ok": "#4f9e6b", "warn": "#a98f4a", "bad": "#a85050"},
    "Steel Gray": {"bg": "#121418", "panel": "#191c21", "card": "#20242b", "fg": "#dcdfe4", "accent": "#6f7784", "accent_grad": "#585f6a", "input": "#0c0e11", "subtext": "#8a9099", "line": "#2b3038", "ok": "#4f9e6b", "warn": "#b79a4a", "bad": "#a85050"},
}

# ------------------------------------------------------------ иконки (пак пользователя)
ICONS_DIR = resource_path("icons")
_icon_cache = {}
_round_cache = {}
_VIRTUAL_ICONS = {
    "icon_power", "icon_exit", "icon_file", "icon_sound_on", "icon_sound_off",
    "icon_logo", "icon_settings", "icon_plus", "icon_users", "icon_theme",
    "icon_update", "icon_share", "icon_lock", "icon_info", "icon_save",
    "icon_hash", "icon_move", "icon_volume", "icon_ping", "icon_close",
}
_ICON_FALLBACK_GLYPH = {
    "icon_image": "🖼", "icon_send": "➤", "icon_mic": "🎤",
    "icon_headphones": "🎧", "icon_power": "⏻", "icon_exit": "⇥",
    "icon_sound_on": "🔊", "icon_sound_off": "🔇", "icon_file": "📎",
    "icon_folder": "📁", "icon_logo": "◈", "icon_settings": "⚙",
    "icon_plus": "+", "icon_users": "👥", "icon_theme": "🎨",
    "icon_update": "🔄", "icon_share": "📺", "icon_lock": "🔒",
    "icon_info": "ⓘ", "icon_save": "💾", "icon_hash": "#",
    "icon_move": "↔", "icon_volume": "🔊", "icon_ping": "●",
    "icon_close": "✕",
}


def _hex_rgb(h):
    h = str(h).lstrip("#")
    if len(h) < 6:
        h = "ffffff"
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rounded_photo(w, h, radius, fill):
    """Скруглённая форма с антиалиасингом (супер-сэмплинг ×4), кэш по параметрам."""
    key = (int(w), int(h), int(radius), fill)
    if key in _round_cache:
        return _round_cache[key]
    if not PIL_AVAILABLE:
        return None
    try:
        S = 4
        W, H = int(w) * S, int(h) * S
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        rgba = _hex_rgb(fill) + (255,)
        r = max(0, int(radius) * S)
        try:
            d.rounded_rectangle((0, 0, W - 1, H - 1), radius=r, fill=rgba)
        except (AttributeError, TypeError):
            # старый Pillow: две окружности + прямоугольник
            d.ellipse((0, 0, 2 * r, H - 1), fill=rgba)
            d.ellipse((W - 1 - 2 * r, 0, W - 1, H - 1), fill=rgba)
            d.rectangle((r, 0, W - 1 - r, H - 1), fill=rgba)
        im = im.resize((int(w), int(h)), Image.LANCZOS)
        photo = ImageTk.PhotoImage(im)
        _round_cache[key] = photo
        if len(_round_cache) > 400:
            _round_cache.clear()
        return photo
    except Exception:
        return None


def _gradient_photo(w, h, c1, c2):
    """Горизонтальный градиент для акцентных полос (кэшируется)."""
    key = ("grad", int(w), int(h), c1, c2)
    if key in _round_cache:
        return _round_cache[key]
    if not PIL_AVAILABLE:
        return None
    try:
        w, h = int(w), max(1, int(h))
        a, b = _hex_rgb(c1), _hex_rgb(c2)
        row = Image.new("RGB", (w, 1))
        px = row.load()
        for x in range(w):
            t = x / max(1, w - 1)
            px[x, 0] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
        photo = ImageTk.PhotoImage(row.resize((w, h), Image.BILINEAR))
        _round_cache[key] = photo
        return photo
    except Exception:
        return None


def gradient_bar(parent, w, h, c1, c2):
    """Акцентная полоса с плавным градиентом."""
    cv = tk.Canvas(parent, width=w, height=h, highlightthickness=0, bd=0)
    photo = _gradient_photo(w, h, c1, c2)
    if photo:
        cv._grad_photo = photo
        cv.create_image(0, 0, image=photo, anchor="nw")
    else:
        cv.config(bg=c1)
    return cv


def _draw_virtual_icon(name, size):
    """Рисует недостающие иконки (выход/питание) в 4× AA, как чёрный глиф."""
    S = 4
    n = max(8, int(size) * S)
    im = Image.new("L", (n, n), 255)  # белый = прозрачный фон
    d = ImageDraw.Draw(im)
    lw = max(2, n // 13)
    if name == "icon_power":
        box = (int(n * 0.16), int(n * 0.16), int(n * 0.84), int(n * 0.84))
        d.arc(box, start=48, end=312, fill=0, width=lw)
        d.line((n // 2, int(n * 0.10), n // 2, int(n * 0.52)), fill=0, width=lw)
    elif name == "icon_exit":  # дверь + стрелка
        d.rectangle((int(n * 0.16), int(n * 0.14), int(n * 0.60), int(n * 0.86)),
                    outline=0, width=lw)
        d.line((int(n * 0.52), n // 2, int(n * 0.88), n // 2), fill=0, width=lw)
        d.polygon([(int(n * 0.72), int(n * 0.34)), (int(n * 0.90), n // 2),
                   (int(n * 0.72), int(n * 0.66))], fill=0)
        d.ellipse((int(n * 0.42), int(n * 0.46), int(n * 0.48), int(n * 0.52)), fill=0)
    elif name == "icon_file":  # лист с загнутым углом + строки
        d.polygon([(int(n * .22), int(n * .12)), (int(n * .66), int(n * .12)),
                   (int(n * .82), int(n * .28)), (int(n * .82), int(n * .88)),
                   (int(n * .22), int(n * .88))], outline=0, width=lw)
        d.line((int(n * .66), int(n * .12), int(n * .66), int(n * .28),
                int(n * .82), int(n * .28)), fill=0, width=lw)
        for k in (0.46, 0.60, 0.74):
            d.line((int(n * .32), int(n * k), int(n * .70), int(n * k)), fill=0, width=max(2, lw // 2))
    elif name in ("icon_sound_on", "icon_sound_off"):  # динамик + дуги/крестик
        d.polygon([(int(n * .14), int(n * .40)), (int(n * .34), int(n * .40)),
                   (int(n * .54), int(n * .22)), (int(n * .54), int(n * .78)),
                   (int(n * .34), int(n * .60)), (int(n * .14), int(n * .60))], fill=0)
        if name == "icon_sound_on":
            d.arc((int(n * .52), int(n * .28), int(n * .86), int(n * .72)),
                  start=-55, end=55, fill=0, width=max(2, lw // 2))
            d.arc((int(n * .46), int(n * .18), int(n * .96), int(n * .82)),
                  start=-55, end=55, fill=0, width=max(2, lw // 2))
        else:
            d.line((int(n * .64), int(n * .36), int(n * .88), int(n * .64)), fill=0, width=lw)
            d.line((int(n * .88), int(n * .36), int(n * .64), int(n * .64)), fill=0, width=lw)
    elif name == "icon_logo":
        # Одна буква «Д» — чистый жирный глиф без рамок и щитов.
        font = None
        for fp in ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/candarab.ttf",
                   "C:/Windows/Fonts/arialbd.ttf", "segoeuib.ttf", "arialbd.ttf"):
            try:
                font = ImageFont.truetype(fp, int(n * 0.90))
                break
            except Exception:
                continue
        if font is not None:
            try:
                d.text((n / 2, n / 2 - n * 0.02), "Д", font=font,
                       fill=0, anchor="mm")
            except Exception:
                font = None
        if font is None:  # геометрический fallback
            d.polygon([(int(n * .16), int(n * .22)), (int(n * .84), int(n * .22)),
                       (int(n * .84), int(n * .38)), (int(n * .72), int(n * .38)),
                       (int(n * .72), int(n * .72)), (int(n * .92), int(n * .72)),
                       (int(n * .92), int(n * .84)), (int(n * .08), int(n * .84)),
                       (int(n * .08), int(n * .72)), (int(n * .28), int(n * .72)),
                       (int(n * .28), int(n * .38)), (int(n * .16), int(n * .38))],
                      fill=0)
    elif name == "icon_settings":   # шестерня (solid)
        d.ellipse((int(n * .30), int(n * .30), int(n * .70), int(n * .70)), fill=0)
        for ang in range(0, 360, 45):
            import math
            cx = cy = n / 2
            x1 = cx + math.cos(math.radians(ang)) * n * .28
            y1 = cy + math.sin(math.radians(ang)) * n * .28
            x2 = cx + math.cos(math.radians(ang)) * n * .46
            y2 = cy + math.sin(math.radians(ang)) * n * .46
            d.line((x1, y1, x2, y2), fill=0, width=max(3, lw))
        d.ellipse((int(n * .40), int(n * .40), int(n * .60), int(n * .60)), fill=255)
    elif name == "icon_plus":
        d.rounded_rectangle((int(n * .42), int(n * .14), int(n * .58), int(n * .86)),
                            radius=int(n * .06), fill=0)
        d.rounded_rectangle((int(n * .14), int(n * .42), int(n * .86), int(n * .58)),
                            radius=int(n * .06), fill=0)
    elif name == "icon_users":
        d.ellipse((int(n * .34), int(n * .14), int(n * .66), int(n * .46)), fill=0)
        d.ellipse((int(n * .12), int(n * .40), int(n * .48), int(n * .72)), fill=0)
        d.ellipse((int(n * .52), int(n * .40), int(n * .88), int(n * .72)), fill=0)
        d.rounded_rectangle((int(n * .16), int(n * .60), int(n * .84), int(n * .90)),
                            radius=int(n * .12), fill=0)
    elif name == "icon_theme":     # палитра
        d.ellipse((int(n * .10), int(n * .10), int(n * .90), int(n * .90)), fill=0)
        for px, py in ((.34, .32), (.62, .30), (.72, .54), (.40, .66)):
            d.ellipse((int(n * (px - .08)), int(n * (py - .08)),
                       int(n * (px + .08)), int(n * (py + .08))), fill=255)
    elif name == "icon_update":    # круговые стрелки
        d.arc((int(n * .16), int(n * .16), int(n * .84), int(n * .84)),
              start=30, end=320, fill=0, width=lw)
        d.polygon([(int(n * .70), int(n * .08)), (int(n * .94), int(n * .20)),
                   (int(n * .68), int(n * .34))], fill=0)
    elif name == "icon_share":     # монитор + луч
        d.rounded_rectangle((int(n * .10), int(n * .18), int(n * .90), int(n * .70)),
                            radius=int(n * .08), fill=0)
        d.rectangle((int(n * .38), int(n * .70), int(n * .62), int(n * .84)), fill=0)
        d.rectangle((int(n * .26), int(n * .84), int(n * .74), int(n * .90)), fill=0)
        d.rectangle((int(n * .20), int(n * .28), int(n * .80), int(n * .60)), fill=255)
    elif name == "icon_lock":
        d.arc((int(n * .28), int(n * .10), int(n * .72), int(n * .56)),
              start=180, end=360, fill=0, width=lw)
        d.rounded_rectangle((int(n * .18), int(n * .44), int(n * .82), int(n * .88)),
                            radius=int(n * .10), fill=0)
        d.ellipse((int(n * .44), int(n * .58), int(n * .56), int(n * .70)), fill=255)
    elif name == "icon_info":
        d.ellipse((int(n * .12), int(n * .12), int(n * .88), int(n * .88)), fill=0)
        d.ellipse((int(n * .44), int(n * .26), int(n * .56), int(n * .38)), fill=255)
        d.rounded_rectangle((int(n * .44), int(n * .44), int(n * .56), int(n * .76)),
                            radius=int(n * .05), fill=255)
    elif name == "icon_save":
        d.rounded_rectangle((int(n * .12), int(n * .12), int(n * .88), int(n * .88)),
                            radius=int(n * .10), fill=0)
        d.rectangle((int(n * .28), int(n * .16), int(n * .72), int(n * .40)), fill=255)
        d.rectangle((int(n * .26), int(n * .56), int(n * .74), int(n * .84)), fill=255)
    elif name == "icon_hash":
        for px in (.36, .64):
            d.rounded_rectangle((int(n * px), int(n * .10),
                                 int(n * (px + .10)), int(n * .90)),
                                radius=int(n * .04), fill=0)
        for py in (.36, .60):
            d.rounded_rectangle((int(n * .10), int(n * py),
                                 int(n * .90), int(n * (py + .10))),
                                radius=int(n * .04), fill=0)
    elif name == "icon_move":
        d.line((int(n * .12), n // 2, int(n * .86), n // 2), fill=0, width=lw)
        d.polygon([(int(n * .66), int(n * .28)), (int(n * .92), n // 2),
                   (int(n * .66), int(n * .72))], fill=0)
        d.polygon([(int(n * .34), int(n * .28)), (int(n * .08), n // 2),
                   (int(n * .34), int(n * .72))], fill=0)
    elif name == "icon_volume":
        d.polygon([(int(n * .10), int(n * .38)), (int(n * .30), int(n * .38)),
                   (int(n * .52), int(n * .18)), (int(n * .52), int(n * .82)),
                   (int(n * .30), int(n * .62)), (int(n * .10), int(n * .62))], fill=0)
        d.arc((int(n * .50), int(n * .24), int(n * .92), int(n * .76)),
              start=-60, end=60, fill=0, width=max(2, lw // 2))
    elif name == "icon_ping":
        d.ellipse((int(n * .40), int(n * .40), int(n * .60), int(n * .60)), fill=0)
        d.arc((int(n * .26), int(n * .26), int(n * .74), int(n * .74)),
              start=-60, end=60, fill=0, width=lw)
        d.arc((int(n * .10), int(n * .10), int(n * .90), int(n * .90)),
              start=-60, end=60, fill=0, width=lw)
    elif name == "icon_close":
        d.line((int(n * .24), int(n * .24), int(n * .76), int(n * .76)),
               fill=0, width=lw)
        d.line((int(n * .76), int(n * .24), int(n * .24), int(n * .76)),
               fill=0, width=lw)
    return im.resize((int(size), int(size)), Image.LANCZOS)


def _icon_pil(name, size, color):
    """Возвращает RGBA-изображение значка (без Tk) — удобно тестировать."""
    n = int(size)
    if not PIL_AVAILABLE:
        return None
    if name in _VIRTUAL_ICONS:
        im = _draw_virtual_icon(name, n)
        mask = im.point(lambda v: max(0, min(255, int(255 - v * 1.15))))
    else:
        # ВАЖНО: у RGBA-иконок чёрный глиф лежит на ПРОЗРАЧНОМ фоне.
        # convert("L") теряет alpha и даёт сплошной чёрный квадрат —
        # значок становился невидимым. Берём именно альфу как маску.
        src = Image.open(os.path.join(ICONS_DIR, name + ".png")).convert("RGBA")
        AA = 3
        big = src.resize((n * AA, n * AA), Image.LANCZOS)
        alpha = big.getchannel("A")
        if alpha.getextrema()[0] < 255:
            mask = alpha.resize((n, n), Image.LANCZOS)
        else:
            # fallback для иконок без прозрачности: тёмный глиф на светлом фоне
            mask = big.convert("L").resize((n, n), Image.LANCZOS).point(
                lambda v: max(0, min(255, int(255 - v * 1.15))))
    rgb = Image.new("RGBA", (n, n), _hex_rgb(color) + (255,))
    rgb.putalpha(mask)
    return rgb


def _load_icon(name, size, color):
    """PNG-иконка из icons/ (или нарисованная), перекрашенная в color."""
    key = (name, int(size), color)
    if key in _icon_cache:
        return _icon_cache[key]
    try:
        im = _icon_pil(name, size, color)
        if im is None:
            return None
        photo = ImageTk.PhotoImage(im)
        _icon_cache[key] = photo
        if len(_icon_cache) > 400:
            _icon_cache.clear()
        return photo
    except Exception:
        return None


def _luminance(h):
    r, g, b = _hex_rgb(h)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def inverted_colors(theme_bg, accent):
    """Цвета «инверсионной» шкалы: белая дорожка на тёмной теме и наоборот."""
    if _luminance(theme_bg) < 128:
        return {"track": "#f0f2f6", "knob": "#171920", "text": "#171920"}
    return {"track": "#171920", "knob": "#f0f2f6", "text": "#f0f2f6"}


class InvertedScale(tk.Canvas):
    """Слайдер с инверсионной дорожкой и AA-рендером, независимый от темы."""

    def __init__(self, parent, variable, from_=0.0, to=1.0, resolution=0.1,
                 track="#f0f2f6", knob="#171920", fill="#5c6ac4",
                 text_color="#171920", width=340, height=30, command=None,
                 label="", inverted=True, owner=None):
        super().__init__(parent, width=width, height=height,
                         bg=parent["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self._var = variable
        self._lo, self._hi = float(from_), float(to)
        self._res = float(resolution) or 0.01
        self._track, self._knob, self._fill = track, knob, fill
        self._text_color = text_color
        self._cw, self._ch = width, height   # НЕ self._w — это путь Tk-виджета
        self._command = command
        self._label = label
        self._inverted = inverted
        self._photo = None
        self._img = self.create_image(0, 0, anchor="nw")
        self._value_text = self.create_text(width - 6, height / 2, anchor="e",
                                            font=(UI_FONT, 8, "bold"), fill=text_color)
        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._set_from_x)
        self.bind("<B1-Motion>", self._set_from_x)
        self.bind("<MouseWheel>", self._on_wheel)
        if owner is not None:
            scales = getattr(owner, "_inverted_scales", None)
            if scales is None:
                scales = owner._inverted_scales = []
            scales.append(self)
        self._render()

    def _on_configure(self, e):
        if e.width > 30 and e.width != self._cw:
            self._cw = e.width
            self._render()

    def _set_from_x(self, e):
        pad = self._ch / 2
        span = max(1, self._cw - 2 * pad)
        t = max(0.0, min(1.0, (e.x - pad) / span))
        v = self._lo + t * (self._hi - self._lo)
        v = round(v / self._res) * self._res
        self._var.set(v)
        self._render()
        if self._command:
            try:
                self._command(v)
            except Exception:
                pass

    def _on_wheel(self, e):
        step = self._res * (1 if (e.delta > 0) else -1) * 5
        v = max(self._lo, min(self._hi, float(self._var.get()) + step))
        self._var.set(round(v / self._res) * self._res)
        self._render()
        if self._command:
            try:
                self._command(v)
            except Exception:
                pass

    def set_colors(self, track=None, knob=None, fill=None, text_color=None):
        if track:
            self._track = track
        if knob:
            self._knob = knob
        if fill:
            self._fill = fill
        if text_color:
            self._text_color = text_color
        self._render()

    def _render(self):
        if not PIL_AVAILABLE:
            return
        try:
            w, h = int(self._cw), int(self._ch)
            S = 3
            W, H = w * S, h * S
            im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            tr = _hex_rgb(self._track) + (255,)
            kn = _hex_rgb(self._knob) + (255,)
            fl = _hex_rgb(self._fill) + (255,)
            d.rounded_rectangle((0, 2 * S, W - 1, H - 1 - 2 * S),
                                radius=(H - 4 * S) / 2, fill=tr)
            try:
                v = float(self._var.get())
            except Exception:
                v = self._lo
            t = max(0.0, min(1.0, (v - self._lo) / max(1e-6, self._hi - self._lo)))
            pad = H / 2
            knx = pad + t * (W - 2 * pad)
            if knx > 4 * S:
                d.rounded_rectangle((0, 2 * S, int(knx), H - 1 - 2 * S),
                                    radius=(H - 4 * S) / 2, fill=fl)
            r = (H - 6 * S) / 2
            d.ellipse((knx - r, 3 * S, knx + r, H - 3 * S), fill=kn)
            im = im.resize((w, h), Image.LANCZOS)
            self._photo = ImageTk.PhotoImage(im)
            self.itemconfig(self._img, image=self._photo)
            txt = f"{self._label}{v:g}" if self._label else f"{v:g}"
            self.itemconfig(self._value_text, text=txt, fill=self._text_color)
            self.coords(self._value_text, w - 8, h / 2)
        except Exception:
            pass


# ------------------------------------------------------------ пользовательские темы
def load_custom_themes():
    try:
        with open(CUSTOM_THEMES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_custom_themes(data):
    try:
        with open(CUSTOM_THEMES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return True
    except Exception:
        return False


def _mix_hex(a, b, t):
    x, y = _hex_rgb(a), _hex_rgb(b)
    return "#%02x%02x%02x" % tuple(int(x[i] + (y[i] - x[i]) * t) for i in range(3))


def _ensure_contrast(fg, bg, target=195):
    """Текст обязан оставаться читаемым, даже если третий цвет «кислотный».

    Третий цвет пользователя — это ОТТЕНОК текста, а не готовый fg:
    на тёмном фоне тянем его к светлому, на светлом — к тёмному,
    сохраняя hue выбранного цвета.
    """
    dark_bg = _luminance(bg) < 128
    want_dark = not dark_bg
    low, high = 0, 100
    best = fg
    for _ in range(24):
        mid = (low + high) / 2
        cand = (_mix_hex(fg, "#0b0c10", mid / 100.0) if want_dark
                else _mix_hex(fg, "#ffffff", mid / 100.0))
        lum = _luminance(cand)
        best = cand
        if want_dark:
            if lum > target:
                low = mid
            else:
                high = mid
        else:
            if lum < target:
                low = mid
            else:
                high = mid
    return best


def theme_from_palette(c1, c2, c3):
    """ТРИ разных цвета, у каждого своя роль:
       c1 — фон/обои/панели, c2 — кнопки/акценты, c3 — оттенок текста.
    """
    base = "#0d0e12" if _luminance(c1) < 110 else "#f7f8fa"
    bg = _mix_hex(c1, base, 0.80)
    panel = _mix_hex(c1, base, 0.68)
    card = _mix_hex(c1, base, 0.58)
    input_ = _mix_hex(c1, base, 0.88)
    fg = _ensure_contrast(c3, bg)
    return {"bg": bg, "panel": panel, "card": card, "fg": fg,
            "accent": c2, "accent_grad": _mix_hex(c2, "#101116", 0.28),
            "input": input_,
            "subtext": _mix_hex(fg, bg, 0.42),
            "line": _mix_hex(c2, bg, 0.60),
            "ok": c2, "warn": _mix_hex(c3, c2, 0.45),
            "bad": _mix_hex(c3, "#b0413e", 0.45),
            "text_color": c3, "button_color": c2, "wall_tint": c1}


def wallpaper_photo(path, w, h, tint, opacity):
    """Фото + полупрозрачная цветная заливка -> PhotoImage для баннера."""
    if not PIL_AVAILABLE or not path or not os.path.exists(path):
        return None
    try:
        im = Image.open(path).convert("RGBA")
        iw, ih = im.size
        scale = max(w / iw, h / ih)
        im = im.resize((max(1, int(iw * scale)), max(1, int(ih * scale))),
                       Image.LANCZOS)
        left = (im.width - w) // 2
        top = (im.height - h) // 2
        im = im.crop((left, top, left + w, top + h))
        overlay = Image.new("RGBA", (w, h),
                            _hex_rgb(tint) + (int(255 * max(0.0, min(1.0, opacity))),))
        im.alpha_composite(overlay)
        return ImageTk.PhotoImage(im.convert("RGB"))
    except Exception:
        return None


def wallpaper_rgba(path, w, h, tint, alpha):
    """Полупрозрачная фотка (RGBA) для слоя поверх интерфейса."""
    if not PIL_AVAILABLE or not path or not os.path.exists(path):
        return None
    try:
        im = Image.open(path).convert("RGBA")
        iw, ih = im.size
        scale = max(w / iw, h / ih)
        im = im.resize((max(1, int(iw * scale)), max(1, int(ih * scale))),
                       Image.LANCZOS)
        left, top = (im.width - w) // 2, (im.height - h) // 2
        im = im.crop((left, top, left + w, top + h))
        im.putalpha(im.getchannel("A").point(
            lambda v: int(v * max(0.0, min(1.0, alpha)))))
        veil = Image.new("RGBA", (w, h),
                         _hex_rgb(tint) + (int(255 * alpha * 0.35),))
        im.alpha_composite(veil)
        return ImageTk.PhotoImage(im)
    except Exception:
        return None


def wallpaper_average(path):
    """Усреднённый цвет фото — им тонируются панели, чтобы фото «проступало»."""
    try:
        im = Image.open(path).convert("RGB").resize((1, 1), Image.BOX)
        r, g, b = im.getpixel((0, 0))
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:
        return "#30323a"


class ToolTip:
    """Лёгкая всплывающая подсказка для иконочных кнопок."""

    def __init__(self, widget, text, bg="#202229", fg="#e8eaed"):
        self.widget, self.text, self.bg, self.fg = widget, text, bg, fg
        self.tip = None
        self.job = None
        if text:
            widget.bind("<Enter>", self._schedule, add="+")
            widget.bind("<Leave>", self._hide, add="+")
            widget.bind("<Button-1>", self._hide, add="+")

    def _schedule(self, e=None):
        self._hide()
        self.job = self.widget.after(450, self._show)

    def _show(self):
        if self.tip or not self.widget.winfo_exists():
            return
        try:
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            w = tk.Toplevel(self.widget)
            w.wm_overrideredirect(True)
            w.wm_geometry(f"+{x}+{y}")
            w.config(bg=self.bg)
            txt = self.text() if callable(self.text) else self.text
            tk.Label(w, text=txt, bg=self.bg, fg=self.fg,
                     font=(UI_FONT, 8), padx=8, pady=4,
                     relief="flat", bd=0).pack()
            self.tip = w
        except Exception:
            self.tip = None

    def _hide(self, e=None):
        if self.job:
            try:
                self.widget.after_cancel(self.job)
            except Exception:
                pass
            self.job = None
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None

# ------------------------------------------------------------ протокол
def pack_control(obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return b"J" + len(body).to_bytes(4, "big") + body

def parse_control(data):
    if len(data) < 5 or data[0:1] != b"J":
        return None
    n = int.from_bytes(data[1:5], "big")
    if n != len(data) - 5 or n > 60000:
        return None
    try:
        msg = json.loads(data[5:].decode("utf-8"))
        return msg if isinstance(msg, dict) else None
    except Exception:
        return None

def pack_audio(seq, pcm_int16):
    return b"A" + seq.to_bytes(2, "big") + pcm_int16.tobytes()


def parse_voice_packet(data):
    # b"a" + nick_len(1) + nick + seq(2) + int16 pcm  (сервер штампует отправителя)
    try:
        if data[:1] != b"a":
            return None
        n = data[1]
        nick = data[2:2 + n].decode("utf-8")
        return nick, data[2 + n + 2:]
    except Exception:
        return None

def pack_img_client(msg_id, ext, idx, total, chunk):
    ext_b = ext.encode("utf-8")
    return b"I" + msg_id + bytes([len(ext_b)]) + ext_b + \
        idx.to_bytes(2, "big") + total.to_bytes(2, "big") + chunk

def parse_img_server(data):
    # b"I" + nick_len(1) + nick + id(4) + ext_len(1) + ext + idx(2) + total(2) + payload
    try:
        nick_len = data[1]
        nick = data[2:2 + nick_len].decode("utf-8")
        p = 2 + nick_len
        msg_id = data[p:p + 4]; p += 4
        ext_len = data[p]; p += 1
        ext = data[p:p + ext_len].decode("utf-8"); p += ext_len
        idx = int.from_bytes(data[p:p + 2], "big")
        total = int.from_bytes(data[p + 2:p + 4], "big")
        payload = data[p + 4:]
        return nick, msg_id, ext, idx, total, payload
    except Exception:
        return None

def pack_file_client(msg_id, name, idx, total, chunk):
    # idx/total = 4 байта: 300 МБ / 1200 Б ≈ 262 144 чанка не влезает в 2 байта
    name_b = str(name).encode("utf-8")[:180]
    return (b"F" + msg_id + bytes([len(name_b)]) + name_b +
            idx.to_bytes(4, "big") + total.to_bytes(4, "big") + chunk)


def parse_file_server(data):
    # b"F" + nick_len(1) + nick + id(4) + name_len(1) + name + idx(4) + total(4) + payload
    try:
        if data[:1] != b"F":
            return None
        nick_len = data[1]
        nick = data[2:2 + nick_len].decode("utf-8")
        p = 2 + nick_len
        msg_id = data[p:p + 4]; p += 4
        name_len = data[p]; p += 1
        name = data[p:p + name_len].decode("utf-8"); p += name_len
        idx = int.from_bytes(data[p:p + 4], "big")
        total = int.from_bytes(data[p + 4:p + 8], "big")
        return nick, msg_id, name, idx, total, data[p + 8:]
    except Exception:
        return None


def pack_video_chunk(fid, total, idx, chunk):
    return b"V" + fid.to_bytes(4, "big") + total.to_bytes(2, "big") + idx.to_bytes(2, "big") + chunk

def pack_stream_audio(seq, pcm_int16):
    return b"S" + seq.to_bytes(4, "big") + pcm_int16.tobytes()


def pack_stream_audio2(session, seq, pcm_int16):
    # session меняется при каждом (пере)запуске захвата: приёмник сбрасывает
    # дедупликатор и не доигрывает «старую волну» поверх новой.
    return (b"s" + session.to_bytes(4, "big") + seq.to_bytes(4, "big")
            + pcm_int16.tobytes())


def parse_stream_audio2(rest):
    try:
        return (int.from_bytes(rest[0:4], "big"),
                int.from_bytes(rest[4:8], "big"), rest[8:])
    except Exception:
        return None

def parse_stream_packet(data):
    # b"V"/b"S" + nick_len(1) + nick + ... -> (tag, nick, rest)
    try:
        nick_len = data[1]
        nick = data[2:2 + nick_len].decode("utf-8")
        return data[0:1], nick, data[2 + nick_len:]
    except Exception:
        return None

def parse_video_chunk(rest):
    # fid(4) + total(2) + idx(2) + payload
    try:
        fid = int.from_bytes(rest[0:4], "big")
        total = int.from_bytes(rest[4:6], "big")
        idx = int.from_bytes(rest[6:8], "big")
        return fid, total, idx, rest[8:]
    except Exception:
        return None

def parse_stream_audio(rest):
    try:
        seq = int.from_bytes(rest[0:4], "big")
        return seq, rest[4:]
    except Exception:
        return None

# -------------------------------------------------- шумоподавление (DSP)
class Resampler:
    """Линейная передискретизация int16 mono между двумя частотами (с накоплением состояния)."""

    def __init__(self, src_rate, dst_rate):
        self.src = float(src_rate)
        self.dst = float(dst_rate)
        self.pos = 0.0   # позиция чтения в исходных сэмплах
        self.buf = np.zeros(0, dtype=np.float32)

    def reset(self):
        self.pos = 0.0
        self.buf = np.zeros(0, dtype=np.float32)

    def process(self, block):
        """block: int16 np-массив при src_rate -> int16 np-массив при dst_rate."""
        if self.src == self.dst:
            return np.asarray(block, dtype=np.int16)
        self.buf = np.concatenate([self.buf, block.astype(np.float32)])
        # Линейная интерполяция с непрерывной фазой. Следующий вызов начинает
        # с позиции ПОСЛЕ последнего сгенерированного сэмпла, а не дублирует его.
        step = self.src / self.dst
        limit = len(self.buf) - 1  # последняя позиция, у которой есть правый сосед
        if limit <= self.pos:
            return np.zeros(0, dtype=np.int16)
        n_out = int(np.ceil((limit - self.pos) / step))
        if n_out <= 0:
            return np.zeros(0, dtype=np.int16)
        idx = self.pos + np.arange(n_out, dtype=np.float64) * step
        keep = idx < limit
        idx = idx[keep]
        if not idx.size:
            return np.zeros(0, dtype=np.int16)
        i0 = idx.astype(np.int64)
        frac = idx - i0
        out = self.buf[i0] * (1.0 - frac) + self.buf[i0 + 1] * frac
        self.pos = float(idx[-1] + step)
        # Сохраняем сэмпл до текущей позиции для интерполяции на границе блока.
        cut = max(0, int(self.pos) - 1)
        if cut > 0:
            self.buf = self.buf[cut:]
            self.pos -= cut
        return np.clip(out, -32768, 32767).astype(np.int16)


class NoiseSuppressor:
    """Мягкое шумоподавление с перекрывающимся спектральным окном.

    Обработка FFT-кадров с 50% overlap-add вместо независимых 20-мс окон
    устраняет щелчки/«пиксельные» артефакты на границах аудиоблоков.
    """

    MODES = ["Выключено", "Базовое (гейт)", "Улучшенное (Чистый голос)", "Максимальное"]

    def __init__(self, rate=SAMPLE_RATE, block=BLOCKSIZE):
        self.rate = rate
        self.block = block
        self.frame_size = block * 2
        # Периодический sqrt-Hann: сумма квадратов двух перекрытых окон = 1.
        hann = np.hanning(self.frame_size + 1)[:-1].astype(np.float32)
        self.window = np.sqrt(np.maximum(hann, 0.0))
        self.ola_norm = np.maximum(
            self.window[:block] ** 2 + self.window[block:] ** 2, 1e-5)
        self.reset()

    def reset(self):
        self.noise_mag = None          # оценка шумового пола по бинам (float)
        self.prev_gain = None          # сглаживание усиления по бинам
        self.gate_gain = 0.0
        self.hold = 0
        self._hp_x1 = 0.0
        self._hp_y1 = 0.0
        self._input_overlap = np.zeros(self.block, dtype=np.float32)
        self._output_overlap = np.zeros(self.block, dtype=np.float32)

    # --- HP-фильтр 1-го порядка
    def _highpass(self, x, cutoff):
        rc = 1.0 / (2 * 3.14159265 * cutoff)
        dt = 1.0 / self.rate
        a = rc / (rc + dt)
        y = np.empty_like(x)
        x1, y1 = self._hp_x1, self._hp_y1
        for i, v in enumerate(x):
            yv = a * (y1 + v - x1)
            y[i] = yv
            x1, y1 = v, yv
        self._hp_x1, self._hp_y1 = x1, y1
        return y

    def process(self, x, mode, rms_threshold):
        """x: int16 np-массив (блок) -> int16 np-массив (обработанный)."""
        if mode == "Выключено":
            return x
        raw = np.asarray(x, dtype=np.int16)
        if raw.size != self.block:
            if raw.size < self.block:
                raw = np.pad(raw, (0, self.block - raw.size))
            else:
                raw = raw[:self.block]
        xf = raw.astype(np.float32)
        rms = float(np.sqrt(np.mean(xf ** 2))) if xf.size else 0.0
        talking = rms >= rms_threshold

        # --- Базовый режим: мягкий gate с hold и плавным release.
        if mode == "Базовое (гейт)":
            if talking:
                self.hold = 8
            if self.hold > 0:
                self.hold -= 1
                target = 1.0
            else:
                target = 0.0
            k = 0.55 if target > self.gate_gain else 0.16
            self.gate_gain += k * (target - self.gate_gain)
            return np.clip(xf * self.gate_gain, -32768, 32767).astype(np.int16)

        # --- Улучшенное / Максимальное: HP + сглаженный спектральный gate.
        cutoff = 90.0 if mode.startswith("Улучшенное") else 120.0
        over = 1.15 if mode.startswith("Улучшенное") else 1.8
        floor_g = 0.18 if mode.startswith("Улучшенное") else 0.08

        xf = self._highpass(xf, cutoff)
        frame = np.concatenate((self._input_overlap, xf))
        self._input_overlap = xf.copy()
        spec = np.fft.rfft(frame * self.window)
        mag = np.abs(spec) + 1e-6
        if self.noise_mag is None or len(self.noise_mag) != len(mag):
            # При старте речи не обучаемся на самой речи; используем консервативный
            # низкий floor и адаптируем его во время пауз.
            self.noise_mag = mag.copy() if not talking else np.maximum(mag * 0.08, 1.0)
            self.prev_gain = np.ones_like(mag)
        if not talking:
            self.noise_mag = 0.88 * self.noise_mag + 0.12 * mag
        else:
            self.noise_mag = 0.998 * self.noise_mag + 0.002 * np.minimum(mag, self.noise_mag * 2.0)

        clean_mag = np.maximum(mag - over * self.noise_mag, floor_g * mag)
        gain = clean_mag / mag
        # Трёхбинное сглаживание без кругового переноса крайних частот.
        padded = np.pad(gain, (1, 1), mode="edge")
        gain_s = (padded[:-2] + padded[1:-1] + padded[2:]) / 3.0
        # Быстро открываемся для речи, медленнее закрываемся — меньше «шепелявости».
        attack = gain_s > self.prev_gain
        alpha = np.where(attack, 0.45, 0.18)
        self.prev_gain += alpha * (gain_s - self.prev_gain)
        processed = np.fft.irfft(spec * self.prev_gain, n=self.frame_size)
        processed *= self.window

        # Выводим предыдущий 20-мс участок и одновременно сохраняем overlap хвост.
        out = (self._output_overlap + processed[:self.block]) / self.ola_norm
        self._output_overlap = processed[self.block:].copy()

        if talking:
            self.hold = 8
            target = 1.0
        elif self.hold > 0:
            self.hold -= 1
            target = 1.0
        else:
            target = 0.0
        k = 0.55 if target > self.gate_gain else 0.16
        self.gate_gain += k * (target - self.gate_gain)

        return np.clip(out * self.gate_gain, -32768, 32767).astype(np.int16)


# -------------------------------------------------- WASAPI loopback (ctypes fallback)
_wasapi_guid_cache = {}

def _make_guid(a, b, c, d):
    key = (a, b, c, tuple(d))
    if key not in _wasapi_guid_cache:
        class G(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]
        _wasapi_guid_cache[key] = G(a, b, c, (ctypes.c_ubyte * 8)(*d))
    return _wasapi_guid_cache[key]

CLSID_MMDeviceEnumerator = _make_guid(0xBCDE0395, 0xE52F, 0x467C, [0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E])
IID_IMMDeviceEnumerator = _make_guid(0xA95664D2, 0x9614, 0x4F35, [0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6])
IID_IAudioClient = _make_guid(0x1CB9AD4C, 0xDBFA, 0x4C32, [0xB1, 0x78, 0xC2, 0xF5, 0x68, 0xA7, 0x03, 0xB2])
IID_IAudioCaptureClient = _make_guid(0xC8ADBD64, 0xE71E, 0x48A0, [0xA4, 0xDE, 0x18, 0x5C, 0x39, 0x5C, 0xD3, 0x17])

def com_method(ptr, index, restype, argtypes):
    """WINFUNCTYPE-обертка метода COM-объекта по индексу vtable."""
    obj = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p)).contents
    fnptr = ctypes.cast(ctypes.c_void_p(obj.value), ctypes.POINTER(ctypes.c_void_p * 24)).contents[index]
    return ctypes.WINFUNCTYPE(restype, *argtypes)(fnptr)


class WasapiLoopback:
    """Захват звука системы напрямую через WASAPI loopback (работает без sounddevice 0.5).
    Выдаёт блоки int16 mono 48000 Hz в очередь."""

    def __init__(self):
        self.blocks = queue.Queue(maxsize=80)
        self.stop_flag = False
        self.error = None
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        time.sleep(0.15)
        return self.error is None

    def stop(self):
        self.stop_flag = True

    def _run(self):
        ole32 = None
        enumerator = device = audio_client = capture = fmt_ptr = None
        com_initialized = False
        stream_started = False
        try:
            from ctypes import POINTER, byref, c_void_p
            ole32 = ctypes.oledll.ole32
            ole32.CoInitializeEx(None, 2)  # отдельный поток COM, STA
            com_initialized = True
            clsctx = 0x17  # CLSCTX_ALL

            def vt(ptr, idx, restype, argtypes):
                return com_method(ptr, idx, restype, argtypes)

            enumerator = c_void_p()
            hr = ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, clsctx,
                                        byref(IID_IMMDeviceEnumerator), byref(enumerator))
            if hr != 0:
                raise OSError(f"CoCreateInstance: {hr & 0xFFFFFFFF:#x}")

            device = c_void_p()
            fn = vt(enumerator, 4, ctypes.HRESULT, [c_void_p, ctypes.c_int, ctypes.c_int, POINTER(c_void_p)])
            hr = fn(enumerator, 0, 0, byref(device))  # eRender, eConsole
            if hr != 0:
                raise OSError(f"GetDefaultAudioEndpoint: {hr & 0xFFFFFFFF:#x}")

            audio_client = c_void_p()
            fn = vt(device, 3, ctypes.HRESULT, [c_void_p, POINTER(type(IID_IAudioClient)), ctypes.c_uint, c_void_p, POINTER(c_void_p)])
            hr = fn(device, byref(IID_IAudioClient), clsctx, None, byref(audio_client))
            if hr != 0:
                raise OSError(f"Activate(IAudioClient): {hr & 0xFFFFFFFF:#x}")

            fmt_ptr = c_void_p()
            fn = vt(audio_client, 8, ctypes.HRESULT, [c_void_p, POINTER(c_void_p)])
            hr = fn(audio_client, byref(fmt_ptr))
            if hr != 0:
                raise OSError(f"GetMixFormat: {hr & 0xFFFFFFFF:#x}")

            class WAVEFORMATEX(ctypes.Structure):
                _fields_ = [("wFormatTag", ctypes.c_ushort), ("nChannels", ctypes.c_ushort),
                            ("nSamplesPerSec", ctypes.c_uint), ("nAvgBytesPerSec", ctypes.c_uint),
                            ("nBlockAlign", ctypes.c_ushort), ("wBitsPerSample", ctypes.c_ushort),
                            ("cbSize", ctypes.c_ushort)]

            fmt = ctypes.cast(fmt_ptr, POINTER(WAVEFORMATEX)).contents
            channels, rate, bits = fmt.nChannels, fmt.nSamplesPerSec, fmt.wBitsPerSample
            block_align = fmt.nBlockAlign
            tag = fmt.wFormatTag
            is_float = tag == 3  # WAVE_FORMAT_IEEE_FLOAT
            if tag == 0xFFFE and fmt.cbSize >= 22:  # WAVE_FORMAT_EXTENSIBLE
                subformat_tag = ctypes.cast(
                    fmt_ptr.value + 24, POINTER(ctypes.c_uint32)).contents.value
                is_float = subformat_tag == 3

            AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
            fn = vt(audio_client, 3, ctypes.HRESULT,
                    [c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_longlong, ctypes.c_longlong, c_void_p, c_void_p])
            hr = fn(audio_client, 0, AUDCLNT_STREAMFLAGS_LOOPBACK, 2_000_000, 0, fmt_ptr, None)
            if hr != 0:
                raise OSError(f"Initialize(loopback): {hr & 0xFFFFFFFF:#x}")

            capture = c_void_p()
            fn = vt(audio_client, 14, ctypes.HRESULT,
                    [c_void_p, POINTER(type(IID_IAudioCaptureClient)), POINTER(c_void_p)])
            hr = fn(audio_client, byref(IID_IAudioCaptureClient), byref(capture))
            if hr != 0:
                raise OSError(f"GetService(IAudioCaptureClient): {hr & 0xFFFFFFFF:#x}")

            fn = vt(audio_client, 10, ctypes.HRESULT, [c_void_p])
            hr = fn(audio_client)
            if hr != 0:
                raise OSError(f"Start: {hr & 0xFFFFFFFF:#x}")
            stream_started = True

            # IAudioCaptureClient: GetBuffer=3, ReleaseBuffer=4, GetNextPacketSize=5.
            get_next = vt(capture, 5, ctypes.HRESULT,
                          [c_void_p, POINTER(ctypes.c_uint)])
            get_buffer = vt(
                capture, 3, ctypes.HRESULT,
                [c_void_p, POINTER(c_void_p), POINTER(ctypes.c_uint), POINTER(ctypes.c_uint),
                 POINTER(ctypes.c_ulonglong), POINTER(ctypes.c_ulonglong)])
            release_buffer = vt(capture, 4, ctypes.HRESULT,
                                [c_void_p, ctypes.c_uint])
            resampler = Resampler(rate, 48000) if rate != 48000 else None
            silent_flag = 0x2  # AUDCLNT_BUFFERFLAGS_SILENT

            while not self.stop_flag:
                packet_frames = ctypes.c_uint(0)
                hr = get_next(capture, byref(packet_frames))
                if hr != 0:
                    raise OSError(f"GetNextPacketSize: {hr & 0xFFFFFFFF:#x}")
                if packet_frames.value == 0:
                    time.sleep(0.003)
                    continue

                while packet_frames.value and not self.stop_flag:
                    data_ptr = c_void_p()
                    frames = ctypes.c_uint(0)
                    flags = ctypes.c_uint(0)
                    device_pos = ctypes.c_ulonglong(0)
                    qpc_pos = ctypes.c_ulonglong(0)
                    hr = get_buffer(capture, byref(data_ptr), byref(frames), byref(flags),
                                    byref(device_pos), byref(qpc_pos))
                    if hr != 0:
                        raise OSError(f"GetBuffer: {hr & 0xFFFFFFFF:#x}")
                    try:
                        if frames.value:
                            if flags.value & silent_flag or not data_ptr.value:
                                pcm = np.zeros(frames.value, dtype=np.int16)
                            else:
                                raw = ctypes.string_at(data_ptr.value, frames.value * block_align)
                                if is_float and bits == 32:
                                    vals = np.frombuffer(raw, dtype="<f4").reshape(-1, channels)
                                    mono = np.nan_to_num(vals.mean(axis=1), nan=0.0, posinf=0.0, neginf=0.0)
                                    pcm = np.clip(mono * 32767.0, -32768, 32767).astype(np.int16)
                                elif bits == 16:
                                    vals = np.frombuffer(raw, dtype="<i2").reshape(-1, channels)
                                    pcm = np.clip(vals.astype(np.int32).sum(axis=1) // channels,
                                                  -32768, 32767).astype(np.int16)
                                elif bits == 24:
                                    vals = np.frombuffer(raw, dtype=np.uint8).reshape(-1, channels, 3).astype(np.int32)
                                    vals = vals[:, :, 0] | (vals[:, :, 1] << 8) | (vals[:, :, 2] << 16)
                                    vals = (vals ^ 0x800000) - 0x800000
                                    pcm = np.clip(np.rint(vals.mean(axis=1) / 256.0),
                                                  -32768, 32767).astype(np.int16)
                                elif bits == 32:
                                    vals = np.frombuffer(raw, dtype="<i4").reshape(-1, channels).astype(np.int64)
                                    pcm = np.clip((vals.sum(axis=1) // channels) >> 16,
                                                  -32768, 32767).astype(np.int16)
                                elif bits == 8:
                                    vals = np.frombuffer(raw, dtype=np.uint8).reshape(-1, channels).astype(np.int16)
                                    pcm = np.clip((vals - 128).sum(axis=1) * 256 // channels,
                                                  -32768, 32767).astype(np.int16)
                                else:
                                    raise RuntimeError(
                                        f"Неподдерживаемый формат WASAPI: tag={tag}, bits={bits}, ch={channels}")

                            if resampler is not None:
                                pcm = resampler.process(pcm)
                            if pcm.size:
                                if self.blocks.full():
                                    try:
                                        self.blocks.get_nowait()
                                    except queue.Empty:
                                        pass
                                self.blocks.put_nowait(pcm)
                    finally:
                        release_buffer(capture, frames.value)

                    packet_frames = ctypes.c_uint(0)
                    hr = get_next(capture, byref(packet_frames))
                    if hr != 0:
                        raise OSError(f"GetNextPacketSize: {hr & 0xFFFFFFFF:#x}")
        except Exception as e:
            self.error = str(e)
        finally:
            if audio_client and audio_client.value and stream_started:
                try:
                    com_method(audio_client, 11, ctypes.HRESULT, [ctypes.c_void_p])(audio_client)
                except Exception:
                    pass
            if fmt_ptr and fmt_ptr.value and ole32:
                try:
                    ole32.CoTaskMemFree(fmt_ptr)
                except Exception:
                    pass
            if ole32:
                for ptr in (capture, audio_client, device, enumerator):
                    if ptr and ptr.value:
                        try:
                            com_method(ptr, 2, ctypes.c_ulong, [ctypes.c_void_p])(ptr)
                        except Exception:
                            pass
                if com_initialized:
                    try:
                        ole32.CoUninitialize()
                    except Exception:
                        pass

# -------------------------------------------------- трансляция через сервер
class _SkipFrame(Exception):
    """Внутренний сигнал: кадр идентичен предыдущему, слать нечего."""


class ScreenStreamer:
    """Захват экрана + звука системы и отправка через сервер (как в Discord)."""

    def __init__(self, app, on_audio_error=None):
        self.app = app
        self.on_audio_error = on_audio_error
        self.stop_flag = False
        self.audio_ok = False
        self.capture_errors = []
        self._jpg_q = STREAM_JPEG_Q
        self._small_frames = 0
        self._fps = STREAM_FPS
        self._slow = 0
        self._fast = 0
        self.actual_fps = 0.0
        self._frames = 0
        self._fps_t = time.time()
        self._fid = 0
        self._aseq = 0
        self._asession = 0
        self._last_sent_audio = None
        self._sent_frames = {}   # fid -> chunks (для досылки по video_nack)
        self._threads = []

    def start(self):
        self._frame_q = queue.Queue(maxsize=2)
        self._raw_q = queue.Queue(maxsize=3)
        # Два независимых GDI-захватчика: BitBlt — узкое место (~34-44 мс),
        # параллель даёт ~43 fps вместо ~30 на этой машине и честные 60 на GPU.
        self._threads = [
            threading.Thread(target=self._grab_loop, daemon=True),
            threading.Thread(target=self._grab_loop, daemon=True),
            threading.Thread(target=self._video_loop, daemon=True),
            threading.Thread(target=self._sender_loop, daemon=True),
            threading.Thread(target=self._audio_loop, daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self):
        self.stop_flag = True

    # ---------- видео
    def resend_video_chunks(self, fid, missing):
        item = self._sent_frames.get(int(fid))
        if not item:
            return
        chunks, parity = item
        total = len(chunks)
        for idx in missing:
            i = int(idx)
            if 0 <= i < total:
                self.app._send_stream_video(int(fid), total, i, chunks[i])
            elif total <= i < total + len(parity):
                self.app._send_stream_video(int(fid), total, i, parity[i - total])

    @staticmethod
    def _xor_parity(grp):
        acc = np.zeros(STREAM_CHUNK, dtype=np.uint8)
        for ch in grp:
            padded = ch.ljust(STREAM_CHUNK, b"\x00")
            np.bitwise_xor(acc, np.frombuffer(padded, dtype=np.uint8), out=acc)
        return acc.tobytes()

    def _sender_loop(self):
        """Отправка кадров + FEC в отдельном потоке, с pacing UDP."""
        while not self.stop_flag:
            try:
                fid, total, chunks, parity = self._frame_q.get(timeout=0.2)
            except queue.Empty:
                continue
            n = 0
            for idx, ch in enumerate(list(chunks) + list(parity)):
                if self.stop_flag:
                    return
                self.app._send_stream_video(fid, total, idx, ch)
                n += 1
                if n % 24 == 0:
                    time.sleep(0.0008)

    def _grab_loop(self):
        """GDI-захват в отдельном потоке (их два)."""
        try:
            sct = mss.MSS()
            monitor = sct.monitors[1]
        except Exception as e:
            print(f"Захват экрана недоступен: {e}")
            return
        while not self.stop_flag:
            if self._raw_q.full():
                time.sleep(0.001)
                continue
            try:
                self._raw_q.put_nowait(np.asarray(sct.grab(monitor)).copy())
            except queue.Full:
                pass
            except Exception as e:
                print(f"Ошибка захвата: {e}")
                time.sleep(0.05)

    def _video_loop(self):
        h = w = 0
        while not self.stop_flag:
            interval = 1.0 / self._fps
            try:
                raw = self._raw_q.get(timeout=0.25)   # уже BGRA, без GDI-ожидания
            except queue.Empty:
                continue
            # Замеряем ТОЛЬКО обработку кадра. Ожидание grabber'а не должно
            # считаться «медленным кадром» и ложно ронять fps до 30.
            t0 = time.perf_counter()
            try:
                h, w = raw.shape[:2]
                # Статичный экран (скриншот/текст) не шлём заново: один
                # 10-байтовый маркер вместо ~110 UDP-пакетов. Приёмник
                # дублирует предыдущий кадр и честно считает fps.
                sig = hashlib.blake2b(raw[::37].tobytes(), digest_size=8).digest()
                if sig == getattr(self, "_last_raw_sig", None):
                    self._fid = (self._fid + 1) & 0xFFFFFFFF
                    self.app._send_stream_video(self._fid, 0, 0, b"X")
                    raise _SkipFrame
                self._last_raw_sig = sig
                # PIL делает BGRA->RGB за один проход в C (11 мс вместо 22 мс
                # у numpy ascontiguousarray). Ресайза нет: 1080p шлём как есть.
                img = Image.frombuffer("RGBA", (w, h), raw.data,
                                       "raw", "BGRA", 0, 1).convert("RGB")
                if w > STREAM_MAX_W:   # только 4K+ мониторы
                    scale = STREAM_MAX_W / w
                    nw = STREAM_MAX_W - (STREAM_MAX_W % 2)
                    nh = max(2, int(h * scale) & ~1)
                    img = img.resize((nw, nh), Image.Resampling.BILINEAR)

                # ОДНО кодирование за кадр. Раньше адаптив перекодировал до 8 раз
                # (≈ 270 мс/кадр -> 3.7 fps и «шакальное» качество).
                data = self._encode(img)
                budget = int(STREAM_MAX_BYTES * STREAM_FPS / max(1, self._fps))
                if len(data) > budget:
                    self._jpg_q = max(STREAM_MIN_Q, self._jpg_q - 2)
                elif len(data) < budget * 0.65 and self._jpg_q < 90:
                    self._jpg_q += 1

                chunks = [data[i:i + STREAM_CHUNK]
                          for i in range(0, len(data), STREAM_CHUNK)]
                total = len(chunks)
                if 0 < total <= 2048:
                    self._fid = (self._fid + 1) & 0xFFFFFFFF
                    if self._frame_q.full():
                        try:
                            self._frame_q.get_nowait()  # не копим задержку
                        except queue.Empty:
                            pass
                    parity = [self._xor_parity(chunks[g:g + STREAM_FEC_GROUP])
                              for g in range(0, total, STREAM_FEC_GROUP)]
                    self._sent_frames[self._fid] = (chunks, parity)
                    if len(self._sent_frames) > 6:
                        self._sent_frames.pop(min(self._sent_frames), None)
                    self._frame_q.put_nowait((self._fid, total, chunks, parity))
            except _SkipFrame:
                pass
            except Exception as e:
                print(f"Ошибка кадра трансляции: {e}")

            self._frames += 1
            now = time.time()
            if now - self._fps_t >= 1.0:
                self.actual_fps = self._frames / (now - self._fps_t)
                self._frames, self._fps_t = 0, now
            # Цель всегда 60 fps. Авто-деградация убрана: она ложно
            # срабатывала на ожидании grabber'а и сама роняла частоту.
            # Реальный fps честно показывается в UI (actual_fps).
            work = time.perf_counter() - t0
            dt = interval - work
            if dt > 0:
                time.sleep(dt)

    def _encode(self, img):
        import io as _io
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=self._jpg_q)
        return buf.getvalue()

    # ---------- звук системы
    def _audio_wanted(self):
        """Тумблер «звук трансляции» — можно выключить/включить на ходу."""
        try:
            return bool(self.app.stream_audio_enabled.get())
        except Exception:
            return True

    def _capture_gain(self):
        try:
            return max(0.0, min(2.0, float(self.app.stream_capture_volume.get())))
        except Exception:
            return 1.0

    def _audio_loop(self):
        # Внешний цикл позволяет включать/выключать звук БЕЗ перезапуска
        # всей трансляции (видео при этом не прерывается).
        while not self.stop_flag:
            if not self._audio_wanted():
                self.audio_ok = False
                time.sleep(0.12)
                continue

            methods = []
            try:
                sig = inspect.signature(sd.WasapiSettings.__init__)
                if "loopback" in sig.parameters:
                    methods.append(("sounddevice loopback", self._capture_sounddevice))
            except (ValueError, TypeError):
                pass
            methods.append(("нативный WASAPI", self._capture_ctypes))
            if self._find_mixer_device() is not None:
                methods.append(("стерео микшер", self._capture_mixer))

            # Каждый (пере)запуск захвата = новая сессия. Приёмник сбрасывает
            # дедупликатор, чтобы старая волна не наложилась на новую.
            self._asession = (self._asession + 1) & 0xFFFFFFFF
            self._last_sent_audio = None
            errors = []
            for name, method in methods:
                if self.stop_flag or not self._audio_wanted():
                    break
                try:
                    for block in method():
                        if self.stop_flag or not self._audio_wanted():
                            break
                        if block is None or len(block) == 0:
                            continue
                        gain = self._capture_gain()
                        if gain != 1.0:
                            block = np.clip(block.astype(np.float32) * gain,
                                            -32768, 32767).astype(np.int16)
                        # Не шлём один и тот же блок снова и снова: в тишине
                        # WASAPI отдаёт идентичные нулевые буферы, и именно они
                        # создавали эффект «записывается и проигрывается раз за разом».
                        prev = self._last_sent_audio
                        if prev is not None and prev.shape == block.shape and np.array_equal(prev, block):
                            continue
                        self._last_sent_audio = block.copy()
                        self._aseq = (self._aseq + 1) & 0xFFFFFFFF
                        self.app._send_stream_audio2(self._asession, self._aseq, block)
                    break  # метод завершился штатно (стоп или тумблер)
                except Exception as e:
                    errors.append(f"{name}: {e}")
                    print(f"Захват звука [{name}] не удался: {e}")

            if self.stop_flag:
                return
            if errors and not self._audio_wanted():
                continue
            if errors:
                self.audio_ok = False
                self.capture_errors = errors
                if self.on_audio_error:
                    self.on_audio_error(errors)
                time.sleep(1.0)
            else:
                time.sleep(0.15)

    @staticmethod
    def _find_mixer_device():
        """Ищет системный loopback-вход ('Стерео микшер', 'Stereo Mix', ...)."""
        try:
            for d in sd.query_devices():
                if d.get("max_input_channels", 0) < 1:
                    continue
                name = str(d.get("name", "")).lower()
                if any(k in name for k in ("стерео микш", "stereo mix", "loopback",
                                           "wave out mix", "what u hear", "микшер")):
                    return d["index"]
        except Exception:
            pass
        return None

    def _capture_mixer(self):
        dev = self._find_mixer_device()
        if dev is None:
            raise RuntimeError("устройство-микшер не найдено")
        rate = 48000
        try:
            rate = int(sd.query_devices(dev)["default_samplerate"]) or 48000
        except Exception:
            pass
        blocks = queue.Queue(maxsize=60)

        def cb(indata, frames, time_info, status):
            try:
                ch = indata.shape[1] if indata.ndim > 1 else 1
                mono = indata[:, :ch].astype(np.float32).mean(axis=1)
                if blocks.full():
                    try:
                        blocks.get_nowait()
                    except queue.Empty:
                        pass
                blocks.put_nowait(np.clip(mono, -32768, 32767).astype(np.int16))
            except Exception:
                pass

        with sd.InputStream(device=dev, samplerate=rate, channels=2, dtype="int16",
                            blocksize=max(1, int(rate * 0.02)), callback=cb):
            self.audio_ok = True
            while not self.stop_flag and self._audio_wanted():
                try:
                    mono = blocks.get(timeout=0.2)
                except queue.Empty:
                    continue
                # Передискретизируем 48k/44.1k -> 24k через общий Resampler.
                r = getattr(self, "_mixer_resampler", None)
                if r is None or r.src != rate:
                    r = self._mixer_resampler = Resampler(rate, STREAM_AUDIO_RATE)
                out = r.process(mono)
                if out.size:
                    yield out

    def _capture_ctypes(self):
        cap = WasapiLoopback()
        if not cap.start():
            raise RuntimeError(cap.error or "не удалось запустить WASAPI")
        try:
            self.audio_ok = True
            while not self.stop_flag and self._audio_wanted():
                if cap.error:
                    raise RuntimeError(cap.error)
                try:
                    mono48 = cap.blocks.get(timeout=0.2)
                except queue.Empty:
                    continue
                block = self._to_24k(mono48)
                if block is not None:
                    yield block
        finally:
            cap.stop()

    def _capture_sounddevice(self):
        # сначала выбранное устройство, потом системное по умолчанию
        spk = self.app.selected_speaker.get() if hasattr(self.app, "selected_speaker") else ""
        candidates = []
        dev_id = self.app.get_device_index(spk, "out") if spk else None
        if dev_id is not None:
            candidates.append(dev_id)
        candidates.append(None)
        last_err = None
        for spk_id in candidates:
            try:
                yield from self._capture_sounddevice_dev(spk_id)
                return
            except Exception as e:
                last_err = e
                print(f"loopback через устройство {spk_id}: {e}")
        raise RuntimeError(str(last_err))

    def _capture_sounddevice_dev(self, spk_id):
        extra = sd.WasapiSettings(loopback=True)
        blocks = queue.Queue(maxsize=60)

        def cb(indata, frames, time_info, status):
            try:
                ch = indata.shape[1] if indata.ndim > 1 else 1
                mono = indata[:, :ch].astype(np.float32).mean(axis=1)
                try:
                    if blocks.full():
                        blocks.get_nowait()
                except queue.Empty:
                    pass
                blocks.put_nowait(np.clip(mono, -32768, 32767).astype(np.int16))
            except Exception:
                pass

        with sd.InputStream(device=spk_id, samplerate=48000, channels=2, dtype="int16",
                            blocksize=960, callback=cb, extra_settings=extra):
            self.audio_ok = True
            while not self.stop_flag:
                try:
                    mono48 = blocks.get(timeout=0.2)
                except queue.Empty:
                    continue
                block = self._to_24k(mono48)
                if block is not None:
                    yield block

    @staticmethod
    def _to_24k(mono48):
        n = (len(mono48) // 2) * 2
        if n == 0:
            return None
        even = mono48[0:n:2].astype(np.int32)
        odd = mono48[1:n:2].astype(np.int32)
        out = ((even + odd) >> 1).astype(np.int16)   # 48k -> 24k с усреднением
        return out


class StreamAudioPlayer:
    """Воспроизведение звука трансляции (24 кГц mono).

    Устраняет «заикание/повтор раз за разом»: дедупликация по seq,
    восстановление порядка, заполнение пропусков, свой буфер джиттера,
    локальное включение/выключение и громкость.
    """

    def __init__(self, app, nick=""):
        self.app = app
        self.nick = nick
        self.q = queue.Queue(maxsize=24)
        self._stream = None
        self._last_seq = None
        self._session = None
        self._last_block = None
        self._enabled = True
        self._volume = 1.0
        self._lock = threading.Lock()

    def set_enabled(self, value):
        with self._lock:
            self._enabled = bool(value)
            if not self._enabled:
                try:
                    while True:
                        self.q.get_nowait()
                except queue.Empty:
                    pass

    def is_enabled(self):
        with self._lock:
            return self._enabled

    def set_volume(self, value):
        with self._lock:
            self._volume = max(0.0, min(2.0, float(value)))

    def get_volume(self):
        with self._lock:
            return self._volume

    def push(self, seq, block, session=0):
        """Кладёт блок: дедуп по seq, по содержимому и сброс при новой сессии."""
        try:
            seq = int(seq) & 0xFFFFFFFF
        except Exception:
            return
        block = np.asarray(block, dtype=np.int16)
        with self._lock:
            if not self._enabled:
                return
            if session != self._session:
                # Новый запуск захвата: не доигрываем старую волну.
                self._session = session
                self._last_seq = None
                self._last_block = None
                try:
                    while True:
                        self.q.get_nowait()
                except queue.Empty:
                    pass
            if self._last_seq is not None:
                delta = (seq - self._last_seq) & 0xFFFFFFFF
                if delta == 0 or delta > 0x80000000:
                    return                       # дубликат / запоздавший
                if delta > 16:
                    self._last_seq = None        # большой разрыв — новая волна
            # Повтор IDENTИЧНОГО блока (туннель/тишина) не проигрываем.
            if self._last_block is not None and self._last_block.shape == block.shape \
                    and np.array_equal(self._last_block, block):
                self._last_seq = seq
                return
            self._last_block = block.copy()
            self._last_seq = seq
            if self.q.full():
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    pass
            self.q.put_nowait(block)

    def start(self):
        def cb(outdata, frames, time_info, status):
            try:
                outdata.fill(0)
                with self._lock:
                    enabled, volume = self._enabled, self._volume
                if not enabled or self.app._audio_params.get("deafened", False):
                    return
                try:
                    block = self.q.get_nowait()
                except queue.Empty:
                    return
                n = min(frames, len(block))
                if volume != 1.0:
                    outdata[:n, 0] = np.clip(
                        block[:n].astype(np.float32) * volume, -32768, 32767).astype(np.int16)
                else:
                    outdata[:n, 0] = block[:n]
            except Exception:
                outdata.fill(0)

        try:
            self._stream = sd.OutputStream(samplerate=STREAM_AUDIO_RATE, channels=1, dtype="int16",
                                           blocksize=STREAM_AUDIO_BLOCK, callback=cb)
            self._stream.start()
        except Exception as e:
            print(f"Ошибка воспроизведения звука трансляции: {e}")
            self._stream = None

    def stop(self):
        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        self._stream = None

# -------------------------------------------------- красивые виджеты
class RoundBtn(tk.Canvas):
    """Кнопка-«пилюля» с настоящим сглаживанием (PIL, ×4 супер-сэмплинг)."""

    def __init__(self, parent, text, bg, hover, command, font=(UI_FONT, 10, "bold"),
                 fg="#ffffff", py=9, px=16, **kw):
        self._font = font
        self._fg = fg
        f = tkFont.Font(font=font)
        tw = f.measure(text)
        th = f.metrics("linespace")
        w = px * 2 + tw + 8
        h = py * 2 + th
        super().__init__(parent, width=w, height=h, bg=parent["bg"],
                         highlightthickness=0, bd=0, cursor="hand2", **kw)
        self._btn_w, self._btn_h = w, h   # НЕ трогаем self._w — это путь Tk-виджета!
        self._bg, self._hover, self._command = bg, hover, command
        self._current_color = bg
        self._bg_photo = None
        self._bg_item = self.create_image(w / 2, h / 2, image=None)
        self.txt = self.create_text(w / 2, h / 2, text=text, font=font, fill=fg)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<ButtonRelease-1>", lambda e: self._set_fill(self._cur_hover_color()))
        self._set_fill(bg)

    def _cur_hover_color(self):
        return self._hover if getattr(self, "_hovering", False) else self._bg

    def _set_fill(self, color):
        self._current_color = color
        photo = _rounded_photo(self._btn_w, self._btn_h, self._btn_h / 2, color)
        if photo:
            self._bg_photo = photo
            self.itemconfig(self._bg_item, image=photo)
        else:
            # fallback без PIL
            if not hasattr(self, "_fallback"):
                self._fallback = self.create_oval(0, 0, self._btn_h, self._btn_h,
                                                  fill=color, outline="")
            self.itemconfig(self._fallback, fill=color)
        self.tag_raise(self.txt)

    def _on_enter(self, e):
        self._hovering = True
        self._set_fill(self._hover)

    def _on_leave(self, e):
        self._hovering = False
        self._set_fill(self._bg)

    def _on_click(self, e):
        self._set_fill(self._accent_dark())
        if self._command:
            self._command()

    def _accent_dark(self):
        try:
            r, g, b = _hex_rgb(self._hover)
            return "#%02x%02x%02x" % (int(r * .78), int(g * .78), int(b * .78))
        except Exception:
            return self._hover

    def set_colors(self, bg=None, hover=None, fg=None):
        if bg is not None:
            self._bg = bg
        if hover is not None:
            self._hover = hover
        if fg is not None:
            self._fg = fg
            self.itemconfig(self.txt, fill=fg)
        self._set_fill(self._cur_hover_color())

    def set_text(self, text):
        self.itemconfig(self.txt, text=text)


class CircleBtn(tk.Canvas):
    """Круглая кнопка (AA через PIL), для текстовых/emoji значков."""

    def __init__(self, parent, text, bg, hover, command, size=38, font=(UI_FONT, 13)):
        super().__init__(parent, width=size, height=size, bg=parent["bg"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self._size = size
        self._bg, self._hover, self._command = bg, hover, command
        self._bg_photo = None
        self._bg_item = self.create_image(size / 2, size / 2, image=None)
        self.txt = self.create_text(size / 2, size / 2, text=text, font=font, fill="#ffffff")
        self.bind("<Enter>", lambda e: self._set_fill(self._hover))
        self.bind("<Leave>", lambda e: self._set_fill(self._bg))
        self.bind("<Button-1>", lambda e: self._command() if self._command else None)
        self._set_fill(bg)

    def _set_fill(self, color):
        photo = _rounded_photo(self._size, self._size, self._size / 2, color)
        if photo:
            self._bg_photo = photo
            self.itemconfig(self._bg_item, image=photo)
        self.tag_raise(self.txt)

    def set_colors(self, bg):
        self._bg = bg
        self._set_fill(bg)


class IconBtn(tk.Canvas):
    """Круглая AA-кнопка с иконкой.

    Анимации: плавная смена цвета, «вырастание» иконки при наведении,
    короткая вспышка при нажатии и квантованный пульс в активном состоянии.
    """

    ANIM_MS = 16
    GROW_STEPS = 6
    PULSE_MS = 90

    def __init__(self, parent, icon, command, size=40, pad=None,
                 bg="#3a3d45", hover="#4a4e58", active_icon=None,
                 active_bg="#b0413e", active_hover="#c25552", tint="#e8eaed",
                 active_tint="#ffffff", tooltip=""):
        super().__init__(parent, width=size, height=size, bg=parent["bg"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self._size = size
        self._command = command
        self._icon_name = icon
        self._active_icon_name = active_icon or icon
        pad = pad if pad is not None else max(8, size // 4)
        self._inner = size - 2 * pad
        self._colors = {"bg": bg, "hover": hover, "active": active_bg, "active_hover": active_hover}
        self._tints = {"norm": tint, "active": active_tint}
        self._active = False
        self._hovering = False
        self._current_color = bg
        self._bg_photo = None
        self._bg_item = self.create_image(size / 2, size / 2, image=None)
        self._ring = self.create_oval(2, 2, size - 2, size - 2, outline="", width=2)
        self._img_item = self.create_image(size / 2, size / 2, image=None)
        self._glyph_item = self.create_text(size / 2, size / 2, text="",
                                            font=("Segoe UI Symbol", max(11, size // 2)),
                                            fill=self._tints["norm"])
        self._photo = None
        self._scale = 1.0
        self._color_job = self._icon_job = self._pulse_job = self._flash_job = None
        self._pulse_step = 0
        self._render_icon()
        self._set_fill(bg)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        if tooltip:
            ToolTip(self, tooltip)

    # --- фон (сглаженный) и иконка
    def _set_fill(self, color):
        self._current_color = color
        photo = _rounded_photo(self._size, self._size, self._size / 2, color)
        if photo:
            self._bg_photo = photo
            self.itemconfig(self._bg_item, image=photo)
        self.tag_raise(self._ring)
        self.tag_raise(self._img_item)
        self.tag_raise(self._glyph_item)

    def _render_icon(self, scale=1.0):
        self._scale = scale
        name = self._active_icon_name if self._active else self._icon_name
        tint = self._tints["active"] if self._active else self._tints["norm"]
        n = max(8, int(self._inner * scale))
        self._photo = _load_icon(name, n, tint)
        if self._photo:
            self.itemconfig(self._img_item, image=self._photo)
            self.itemconfig(self._glyph_item, text="")
        else:
            # Файл иконки отсутствует (старый установщик / сборка) —
            # всё равно показываем узнаваемый значок, а не пустой кружок.
            self.itemconfig(self._img_item, image="")
            self.itemconfig(self._glyph_item, text=_ICON_FALLBACK_GLYPH.get(name, "•"), fill=tint)

    def _animate_icon(self, target_scale):
        if self._icon_job:
            self.after_cancel(self._icon_job)
        start = self._scale
        steps = self.GROW_STEPS

        def step(i):
            if not self.winfo_exists():
                return
            s = start + (target_scale - start) * (i / steps)
            self._render_icon(s)
            if i < steps:
                self._icon_job = self.after(self.ANIM_MS, lambda: step(i + 1))
            else:
                self._icon_job = None
        step(1)

    # --- цвета
    def _cur_color(self):
        if self._active:
            return self._colors["active_hover"] if self._hovering else self._colors["active"]
        return self._colors["hover"] if self._hovering else self._colors["bg"]

    def _on_enter(self, e):
        self._hovering = True
        self._animate_to(self._cur_color())
        self._animate_icon(1.14)

    def _on_leave(self, e):
        self._hovering = False
        self._animate_to(self._cur_color())
        self._animate_icon(1.0)

    def _on_click(self, e):
        self.flash()
        if self._command:
            self._command()

    def flash(self):
        """Короткая световая вспышка + кольцо при нажатии/отправке."""
        if self._flash_job:
            self.after_cancel(self._flash_job)
        base = self._cur_color()
        r, g, b = _hex_rgb(base)
        hot = "#%02x%02x%02x" % (min(255, int(r * 1.45) + 30),
                                 min(255, int(g * 1.45) + 30),
                                 min(255, int(b * 1.45) + 30))
        self._set_fill(hot)
        self.itemconfig(self._ring, outline=hot)
        self._flash_job = self.after(110, lambda: (
            self._set_fill(base) if self.winfo_exists() else None,
            self.itemconfig(self._ring, outline="") if self.winfo_exists() else None))

    def _animate_to(self, target):
        if self._color_job:
            self.after_cancel(self._color_job)
        if self._pulse_job:
            self.after_cancel(self._pulse_job)
            self._pulse_job = None
        c0, c1 = _hex_rgb(self._current_color), _hex_rgb(target)
        steps = 6

        def step(i):
            if not self.winfo_exists():
                return
            t = i / steps
            col = "#%02x%02x%02x" % tuple(int(a + (b - a) * t) for a, b in zip(c0, c1))
            self._set_fill(col)
            if i < steps:
                self._color_job = self.after(self.ANIM_MS, lambda: step(i + 1))
            else:
                self._color_job = None
                if self._active:
                    self._start_pulse()
        step(1)

    def _start_pulse(self):
        if self._pulse_job:
            return
        base = _hex_rgb(self._colors["active"])
        hot = _hex_rgb(self._colors["active_hover"])
        # 8 квантованных оттенков: не создаём сотни PhotoImage в кэше
        palette = ["#%02x%02x%02x" % tuple(
            int(a + (b - a) * (k / 7)) for a, b in zip(base, hot)) for k in range(8)]

        def tick():
            if not self.winfo_exists() or not self._active:
                self._pulse_job = None
                return
            self._pulse_step = (self._pulse_step + 1) % 14
            k = self._pulse_step if self._pulse_step < 8 else 14 - self._pulse_step
            self._set_fill(palette[max(0, min(7, k))])
            self._pulse_job = self.after(self.PULSE_MS, tick)
        tick()

    def set_active(self, active):
        if active == self._active:
            return
        self._active = bool(active)
        self._render_icon(self._scale)
        self._animate_to(self._cur_color())

    def is_active(self):
        return self._active


class AvatarView(tk.Canvas):
    """Круглая аватарка: картинка (если есть) либо первая буква ника."""

    def __init__(self, parent, size=30, bg_circle="#3a3d45", fg="#e8eaed", letter=""):
        super().__init__(parent, width=size, height=size, bg=parent["bg"],
                         highlightthickness=0, bd=0)
        self._size = size
        self._circle_photo = _rounded_photo(size, size, size / 2, bg_circle)
        self._circle = self.create_image(size / 2, size / 2, image=self._circle_photo)
        self._letter = self.create_text(size / 2, size / 2, text=letter,
                                        font=(UI_FONT, max(9, size // 2)), fill=fg)
        self._img_item = self.create_image(size / 2, size / 2, image=None)
        self._photo = None

    def set_photo(self, photo):
        """photo — уже круглый PhotoImage или None."""
        if photo:
            self._photo = photo
            self.itemconfig(self._img_item, image=photo)
            self.itemconfig(self._letter, text="")
        else:
            self._photo = None
            self.itemconfig(self._img_item, image="")
        self.tag_raise(self._img_item)

    def set_letter(self, letter, fg="#e8eaed"):
        if not self._photo:
            self.itemconfig(self._letter, text=(letter or "?").upper(), fill=fg)


def round_photo(source_bytes_or_pil, size):
    """Обрезает картинку в круг с антиалиасингом; кэширует по содержимому."""
    if not PIL_AVAILABLE:
        return None
    try:
        if isinstance(source_bytes_or_pil, (bytes, bytearray)):
            key = ("b", hashlib.md5(bytes(source_bytes_or_pil)).hexdigest(), size)
        else:
            key = ("p", id(source_bytes_or_pil), size)
        cache = getattr(round_photo, "_cache", None)
        if cache is None:
            cache = round_photo._cache = {}
        if key in cache:
            return cache[key]
        im = source_bytes_or_pil
        if isinstance(im, (bytes, bytearray)):
            im = Image.open(io.BytesIO(bytes(im)))
        im = im.convert("RGBA").resize((size * 4, size * 4), Image.LANCZOS)
        mask = Image.new("L", (size * 4, size * 4), 0)
        d = ImageDraw.Draw(mask)
        d.ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
        im.putalpha(mask)
        im = im.resize((size, size), Image.LANCZOS)
        photo = ImageTk.PhotoImage(im)
        cache[key] = photo
        if len(cache) > 200:
            cache.clear()
        return photo
    except Exception:
        return None


class VoiceClientApp:
    def __init__(self, root):
        self.root = root

        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("drunspeak.client.app.1")
        except Exception:
            pass
        force_high_performance_gpu()

        self.root.title(f"друнспик - v{CURRENT_VERSION}")
        ico_path = resource_path("drunspeakicon.ico")
        if not os.path.exists(ico_path):
            ico_path = resource_path("icon.ico")
        try:
            if os.path.exists(ico_path):
                self.root.iconbitmap(ico_path)
        except Exception:
            pass

        self.root.geometry("460x780")
        self.root.minsize(440, 700)

        # --- переменные
        self.current_volume = tk.DoubleVar(value=min(float(cfg["volume"]), 2.0))
        self.rms_threshold = tk.DoubleVar(value=float(cfg["gate"]))
        self.noise_suppression_mode = tk.StringVar(
            value=cfg.get("noise_suppression", DEFAULT_CONFIG["noise_suppression"]))
        if self.noise_suppression_mode.get() not in NoiseSuppressor.MODES:
            self.noise_suppression_mode.set("Улучшенное (Чистый голос)")
        self.loopback_enabled = tk.BooleanVar(value=bool(cfg["loopback"]))
        self.muted = tk.BooleanVar(value=False)
        self.deafened = tk.BooleanVar(value=False)
        self.server_muted = False

        self.server_ip = tk.StringVar(value=cfg["server_ip"])
        self.server_port = tk.StringVar(value=str(cfg["server_port"]))
        self.server_password = tk.StringVar(value=cfg.get("server_password", ""))
        self.account_password = tk.StringVar(value=cfg.get("account_password", ""))
        self.nickname = tk.StringVar(value=cfg["nickname"])
        self.avatar_b64 = str(cfg.get("avatar_b64", "") or "")   # своя аватарка (base64 PNG)
        self.current_theme = tk.StringVar(value=cfg.get("theme", "Discord Modern"))
        # Пользовательские темы (фото + палитра из 3 цветов)
        self.custom_themes = load_custom_themes()
        for _n, _d in self.custom_themes.items():
            if isinstance(_d, dict) and len(_d.get("colors", [])) >= 3:
                THEMES[_n] = theme_from_palette(*_d["colors"][:3])
        self.custom_wall = {}
        self.stream_audio_enabled = tk.BooleanVar(value=bool(cfg.get("stream_audio_enabled", True)))
        self.stream_volume = tk.DoubleVar(value=float(cfg.get("stream_volume", 1.0)))
        self.stream_capture_volume = tk.DoubleVar(value=float(cfg.get("stream_capture_volume", 1.0)))
        self.user_volumes = {}    # nick -> громкость 0..2 (только у меня)
        self.local_mutes = set()  # nick -> локальный мут (только у меня)

        self.current_channel = tk.StringVar(value="друнспик 1")
        self.available_channels_data = {"друнспик 1": {"has_password": False}}
        self._channels_sig = ""
        self.current_ping = 0
        self.remote_users = []

        # --- состояние
        self.client_socket = None
        self.server_address = None
        self.is_connected = False
        self.running_audio = True
        self.is_sharing_screen = False
        self._closing = False

        self.jitter_queue = queue.Queue()   # совместимость (loopback/старый формат)
        self.voice_queues = {}              # nick -> отдельная очередь 20-мс блоков
        self._voice_idle = {}
        self.chat_messages = []
        self.chat_images_cache = {}
        self.remote_streams = {}
        self.active_stream_windows = {}
        self._video_recv = {}       # nick -> {fid: state}
        self._last_shown_fid = {}

        self.img_send_cache = {}
        self.img_recv_state = {}
        self.file_send_cache = {}
        self.file_recv_state = {}
        self._pending_file_save = {}
        self.ping_seq = 0
        self.ping_times = {}
        self.last_pong = 0.0
        self.ping_history = []   # [(time, rtt_ms)] за последнюю минуту
        self.audio_threads = []
        self.streamer = None

        # аватарки участников: nick -> base64 PNG (по сети)
        self.avatars_data = {}
        self._avatar_parts = {}   # сборка чанкованных аватарок: (nick,id) -> state
        self.dsp = NoiseSuppressor()
        self.mic_level = 0.0          # текущий уровень микрофона (для индикатора)
        self._anim_jobs = {}          # key -> after-id (анимации)
        self._inverted_scales = []    # все шкалы, перекрашиваются с темой

        self.ui_queue = queue.Queue()
        self.root.after(40, self._drain_ui)

        try:
            devices = sd.query_devices()
            self.mic_list = [d["name"] for d in devices if d["max_input_channels"] > 0]
            self.speaker_list = [d["name"] for d in devices if d["max_output_channels"] > 0]
        except Exception:
            self.mic_list, self.speaker_list = [], []

        default_mic = cfg["mic_device"] if cfg["mic_device"] in self.mic_list else (self.mic_list[0] if self.mic_list else "")
        default_spk = cfg["speaker_device"] if cfg["speaker_device"] in self.speaker_list else (self.speaker_list[0] if self.speaker_list else "")
        self.selected_mic = tk.StringVar(value=default_mic)
        self.selected_speaker = tk.StringVar(value=default_spk)

        self._audio_params = {
            "mic": default_mic, "speaker": default_spk,
            "gate": float(self.rms_threshold.get()),
            "mode": self.noise_suppression_mode.get(),
            "volume": float(self.current_volume.get()),
            "muted": False, "deafened": False,
            "loopback": bool(self.loopback_enabled.get()),
        }
        self.root.after(200, self._sync_audio_params)

        self.apply_theme()
        self.create_login_frame()

        self.start_audio_streams()
        threading.Thread(target=self.ping_loop, daemon=True).start()
        # Тихая проверка обновлений при старте (не блокирует UI).
        threading.Thread(target=lambda: self.check_for_updates(silent=True),
                         daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------ ui helper
    def ui(self, fn):
        self.ui_queue.put(fn)

    def _drain_ui(self):
        try:
            while True:
                fn = self.ui_queue.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.root.after(40, self._drain_ui)

    def apply_theme(self):
        t = dict(THEMES.get(self.current_theme.get(), THEMES["Discord Modern"]))
        self.custom_wall = getattr(self, "custom_themes", {}).get(
            self.current_theme.get(), {})
        # Цвета панелей впитывают усреднённый оттенок фотки: изображение
        # читается через весь интерфейс, а не только в пустых зазорах.
        wall = self.custom_wall or {}
        wp = wall.get("wallpaper")
        if wp and os.path.exists(wp):
            avg = wallpaper_average(wp)
            for key in ("bg", "panel", "card", "input", "line"):
                t[key] = _mix_hex(t[key], avg, 0.30)
        for key in ("bg", "panel", "card", "fg", "accent", "accent_grad", "input", "subtext", "line", "ok", "warn", "bad"):
            setattr(self, "th_" + key, t[key])
        self.root.config(bg=self.th_bg)
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure("Vertical.TScrollbar", gripcount=0,
                             background=self.th_panel, darkcolor=self.th_panel,
                             lightcolor=self.th_panel, troughcolor=self.th_card,
                             bordercolor=self.th_card, arrowcolor=self.th_subtext)
        self.style.map("Vertical.TScrollbar",
                       background=[("active", self.th_accent), ("pressed", self.th_accent_grad)])
        # Все инверсионные шкалы (в т.ч. в уже открытом окне настроек)
        # обязаны перекраситься при смене темы / после входа на сервер.
        inv = inverted_colors(self.th_bg, self.th_accent)
        alive = []
        for s in getattr(self, "_inverted_scales", []):
            try:
                if s.winfo_exists():
                    s.set_colors(track=inv["track"], knob=inv["knob"],
                                 text_color=inv["text"], fill=self.th_accent)
                    alive.append(s)
            except Exception:
                pass
        self._inverted_scales = alive

    def bind_entry_shortcuts(self, entry_widget):
        def select_all(event):
            entry_widget.select_range(0, tk.END)
            return "break"
        entry_widget.bind("<Control-a>", select_all)
        entry_widget.bind("<Control-A>", select_all)

    def clear_window(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def save_config(self, *args):
        try:
            config = {
                "volume": self.current_volume.get(),
                "gate": self.rms_threshold.get(),
                "noise_suppression": self.noise_suppression_mode.get(),
                "loopback": self.loopback_enabled.get(),
                "server_ip": self.server_ip.get(),
                "server_port": self.server_port.get(),
                "server_password": self.server_password.get(),
                "account_password": self.account_password.get(),
                "nickname": self.nickname.get(),
                "avatar_b64": self.avatar_b64,
                "mic_device": self.selected_mic.get(),
                "speaker_device": self.selected_speaker.get(),
                "theme": self.current_theme.get(),
                "stream_audio_enabled": bool(self.stream_audio_enabled.get()),
                "stream_volume": float(self.stream_volume.get()),
                "stream_capture_volume": float(self.stream_capture_volume.get()),
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(e)

    # ------------------------------------------------------------ login
    def _styled_entry(self, parent, var, show=""):
        wrap = tk.Frame(parent, bg=self.th_input, highlightthickness=1,
                        highlightbackground=self.th_line)
        e = tk.Entry(wrap, textvariable=var, bg=self.th_input, fg=self.th_fg,
                     insertbackground=self.th_fg, font=(UI_FONT, 11),
                     relief="flat", bd=0, show=show)
        e.pack(fill="x", padx=10, ipady=7)
        self.bind_entry_shortcuts(e)
        e.bind("<FocusOut>", lambda ev: self.save_config())
        return wrap

    def _field_label(self, parent, text):
        return tk.Label(parent, text=text, bg=self.th_card, fg=self.th_subtext,
                        font=(UI_FONT, 8, "bold"), anchor="w")

    def create_login_frame(self):
        self.clear_window()
        self.apply_theme()
        self.root.geometry("460x780")

        outer = tk.Frame(self.root, bg=self.th_bg)
        outer.pack(fill="both", expand=True)

        card = tk.Frame(outer, bg=self.th_card, highlightthickness=1,
                        highlightbackground=self.th_line)
        card.place(relx=0.5, rely=0.5, anchor="center", width=400)

        banner = gradient_bar(card, 398, 4, self.th_accent_grad, self.th_accent)
        banner.pack(fill="x", pady=(0, 18))
        brand = tk.Frame(card, bg=self.th_card)
        brand.pack(pady=(2, 0))
        logo = _load_icon("icon_logo", 42, self.th_accent)
        if logo:
            logo_lbl = tk.Label(brand, image=logo, bg=self.th_card)
            logo_lbl.image = logo
            logo_lbl.pack(side="left", padx=(0, 10))
        tk.Label(brand, text="ДРУНСПИК", bg=self.th_card, fg=self.th_fg,
                 font=(UI_FONT, 21, "bold")).pack(side="left")
        tk.Label(card, text="голос · демки · сообщество", bg=self.th_card,
                 fg=self.th_accent, font=(UI_FONT, 8, "bold")).pack(pady=(2, 0))
        tk.Label(card, text="голосовой чат · трансляции · сообщество", bg=self.th_card,
                 fg=self.th_subtext, font=(UI_FONT, 8)).pack(pady=(0, 16))

        line = tk.Frame(card, bg=self.th_line, height=1)
        line.pack(fill="x", padx=28, pady=(0, 14))

        self._field_label(card, "НИКНЕЙМ").pack(fill="x", padx=30)
        self._styled_entry(card, self.nickname).pack(fill="x", padx=30, pady=(3, 10))

        self._field_label(card, "ПАРОЛЬ АККАУНТА").pack(fill="x", padx=30)
        self._styled_entry(card, self.account_password, show="•").pack(fill="x", padx=30, pady=(3, 10))
        tk.Label(card, text="новый ник + пароль = регистрация на сервере", bg=self.th_card,
                 fg=self.th_subtext, font=(UI_FONT, 7)).pack(fill="x", padx=32, pady=(0, 10))

        self._field_label(card, "IP / ДОМЕН СЕРВЕРА").pack(fill="x", padx=30)
        ip_row = tk.Frame(card, bg=self.th_card)
        ip_row.pack(fill="x", padx=30, pady=(3, 10))
        self._styled_entry(ip_row, self.server_ip).pack(side="left", fill="x", expand=True)
        port_wrap = self._styled_entry(ip_row, self.server_port)
        port_wrap.pack(side="left", padx=(6, 0))
        for ch in port_wrap.winfo_children():
            if isinstance(ch, tk.Entry):
                ch.configure(width=5)

        self._field_label(card, "ПАРОЛЬ СЕРВЕРА (ЕСЛИ ЕСТЬ)").pack(fill="x", padx=30)
        self._styled_entry(card, self.server_password, show="•").pack(fill="x", padx=30, pady=(3, 10))

        # --- аватарка: круглый превью + загрузка своей картинки
        av_row = tk.Frame(card, bg=self.th_card)
        av_row.pack(fill="x", padx=30, pady=(2, 14))
        self.login_avatar = AvatarView(av_row, size=52,
                                       bg_circle=self.th_input, fg=self.th_fg)
        self.login_avatar.pack(side="left")
        self._refresh_login_avatar()
        info = tk.Frame(av_row, bg=self.th_card)
        info.pack(side="left", fill="x", expand=True, padx=(12, 0))
        tk.Label(info, text="Аватарка", bg=self.th_card, fg=self.th_fg,
                 font=(UI_FONT, 9, "bold"), anchor="w").pack(fill="x")
        tk.Label(info, text="кликните, чтобы загрузить свою картинку", bg=self.th_card,
                 fg=self.th_subtext, font=(UI_FONT, 7), anchor="w").pack(fill="x")
        info.bind("<Button-1>", lambda e: self.upload_avatar())
        self.login_avatar.bind("<Button-1>", lambda e: self.upload_avatar())

        self.connect_btn = RoundBtn(card, "🚀  Подключиться", self.th_accent, self.th_accent_grad,
                                    self.start_connecting, font=(UI_FONT, 11, "bold"))
        self.connect_btn.pack(padx=30, fill="x")

        self.status_label = tk.Label(card, text=f"Отключено · v{CURRENT_VERSION}",
                                     bg=self.th_card, fg=self.th_subtext, font=(UI_FONT, 8, "bold"))
        self.status_label.pack(pady=12)

        for e in card.winfo_children():
            if isinstance(e, tk.Entry):
                e.bind("<Return>", lambda ev: self.start_connecting())
        self._apply_wallpaper_layer()

    def _apply_wallpaper_layer(self):
        """Фото на ВСЁ окно под всеми слоями, но видимое — БЕЗ дыр в окне.

        -transparentcolor делал окно «дырявым»: сквозь края были видны
        другие окна/рабочий стол. От него отказались. Вместо этого:
          1) непрозрачная подложка с фото на весь размер окна (нижний слой);
          2) цвета панелей тонируются усреднённым цветом фото — фото
             «проступает» через весь интерфейс;
          3) полупрозрачный RGBA-слой поверх виджетов с click-through
             (WS_EX_TRANSPARENT), чтобы фото было видно и поверх панелей.
        """
        try:
            if getattr(self, "_wp_layer", None) and self._wp_layer.winfo_exists():
                self._wp_layer.destroy()
        except Exception:
            pass
        try:
            if getattr(self, "_wp_overlay", None) and self._wp_overlay.winfo_exists():
                self._wp_overlay.destroy()
        except Exception:
            pass
        self._wp_layer = self._wp_overlay = None
        try:
            self.root.attributes("-transparentcolor", "#ff00ff")
        except Exception:
            pass
        wall = self.custom_wall or {}
        path = wall.get("wallpaper")
        if not path or not os.path.exists(path):
            return
        try:
            w = max(self.root.winfo_width(), 440)
            h = max(self.root.winfo_height(), 700)
            tint = wall.get("colors", [self.th_accent])[0]
            opacity = float(wall.get("opacity", 0.45))
            photo = wallpaper_photo(path, w, h, tint, opacity)
            if not photo:
                return
            layer = tk.Label(self.root, image=photo, bd=0,
                             highlightthickness=0, bg="#000001")
            layer.image = photo
            layer.place(x=0, y=0, width=w, height=h)
            layer.lower()
            self._wp_layer = layer

            # Тонкая полупрозрачная фотка ПОВЕРХ интерфейса.
            top_img = wallpaper_rgba(path, w, h, tint,
                                     min(0.30, max(0.08, opacity * 0.42)))
            if top_img:
                ov = tk.Canvas(self.root, width=w, height=h, bd=0,
                               highlightthickness=0)
                ov._img = top_img
                ov.create_image(0, 0, image=top_img, anchor="nw")
                ov.place(x=0, y=0, width=w, height=h)
                # У Canvas и lift(), и tkraise() переопределены в tag_raise,
                # поэтому поднимаем виджет прямой Tcl-командой.
                ov.tk.call("raise", str(ov._w))
                if self._make_click_through(ov):
                    self._wp_overlay = ov
                else:
                    ov.destroy()

            if not getattr(self, "_wp_resize_bound", False):
                self._wp_resize_bound = True
                self.root.bind("<Configure>", self._on_root_resize, add="+")
        except Exception as e:
            print(f"Фон темы: {e}")

    @staticmethod
    def _make_click_through(widget):
        """WS_EX_TRANSPARENT: слой показывает фото, но не перехватывает клики."""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            hwnd = int(widget.winfo_id())
            GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
            user32.GetWindowLongW.restype = wintypes.LONG
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                  ex | WS_EX_LAYERED | WS_EX_TRANSPARENT)
            return True
        except Exception:
            return False

    def _on_root_resize(self, event):
        if not (self.custom_wall or {}).get("wallpaper"):
            return
        size = (event.width, event.height)
        if size == getattr(self, "_wp_last_size", None):
            return
        self._wp_last_size = size
        job = getattr(self, "_wp_resize_job", None)
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._wp_resize_job = self.root.after(180, self._apply_wallpaper_layer)

    def _refresh_login_avatar(self):
        photo = None
        if self.avatar_b64:
            try:
                photo = round_photo(base64.b64decode(self.avatar_b64), 52)
            except Exception:
                photo = None
        if not photo and PIL_AVAILABLE:
            try:
                letter = (self.nickname.get() or "U")[:1]
                img = Image.new("RGBA", (52, 52), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                d.ellipse((0, 0, 51, 51), fill=self.th_input)
                try:
                    font = ImageFont.truetype("segoeui.ttf", 24)
                except Exception:
                    font = ImageFont.load_default()
                d.text((26, 26), letter.upper(), anchor="mm", fill=self.th_fg, font=font)
                photo = round_photo(img, 52)
            except Exception:
                photo = None
        self.login_avatar.set_photo(photo)
        if not photo:
            self.login_avatar.set_letter((self.nickname.get() or "U")[:1], self.th_fg)

    def upload_avatar(self):
        path = filedialog.askopenfilename(
            title="Выберите картинку аватарки",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.gif *.bmp *.webp")])
        if not path:
            return
        try:
            im = Image.open(path).convert("RGBA")
            w, h = im.size
            s = min(w, h)
            im = im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
            im = im.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            if len(b64) > AVATAR_B64_LIMIT:
                messagebox.showwarning("Аватарка", "Картинка слишком большая, попробуйте другую.")
                return
            self.avatar_b64 = b64
            self.save_config()
            self._refresh_login_avatar()
            # сразу сообщим серверу, если подключены
            if self.is_connected:
                self._send_avatar(b64)
        except Exception as e:
            messagebox.showerror("Аватарка", f"Не удалось загрузить картинку:\n{e}")

    # ---- аватарки: чанкованная передача (одним 12-КБ пакетом через playit не доходит)
    def _send_avatar(self, b64):
        if not self.is_connected:
            return
        b64 = str(b64 or "")[:AVATAR_B64_LIMIT]
        if not b64:
            self._send_control({"t": "avatar_clear"})
            return
        aid = os.urandom(4).hex()
        parts = [b64[i:i + AVATAR_PART_SIZE] for i in range(0, len(b64), AVATAR_PART_SIZE)]
        if not self._send_control({"t": "set_avatar_start", "id": aid,
                                   "total": len(parts), "len": len(b64)}):
            return
        for i, part in enumerate(parts):
            if not self._send_control({"t": "set_avatar_part", "id": aid,
                                       "i": i, "d": part}):
                return
            if i and i % 6 == 0:
                time.sleep(0.003)

    def _handle_avatar_start(self, msg):
        nick = str(msg.get("nick", ""))
        aid = str(msg.get("id", ""))[:16]
        try:
            total = int(msg.get("total", 0))
            length = int(msg.get("len", 0))
        except (TypeError, ValueError):
            return
        if not nick or not aid or total < 1 or total > 64 or length > AVATAR_B64_LIMIT:
            return
        self._avatar_parts[(nick, aid)] = {"total": total, "len": length,
                                          "parts": {}, "t": time.time()}

    def _handle_avatar_part(self, msg):
        nick = str(msg.get("nick", ""))
        aid = str(msg.get("id", ""))[:16]
        key = (nick, aid)
        st = self._avatar_parts.get(key)
        if not st:
            return
        try:
            idx = int(msg.get("i", -1))
        except (TypeError, ValueError):
            return
        part = str(msg.get("d", ""))
        if idx < 0 or idx >= st["total"] or len(part) > 1000:
            return
        st["parts"][idx] = part
        st["t"] = time.time()
        if len(st["parts"]) == st["total"]:
            b64 = "".join(st["parts"][i] for i in range(st["total"]))
            self._avatar_parts.pop(key, None)
            if len(b64) == st["len"] and len(b64) <= AVATAR_B64_LIMIT:
                self.avatars_data[nick] = b64
                if nick == (self.nickname.get() or "User"):
                    self.avatar_b64 = b64
                self.ui(self.refresh_user_cards)

    def set_status(self, text, color):
        self.ui(lambda: self.status_label.config(text=text, fg=color)
                if hasattr(self, "status_label") and self.status_label.winfo_exists() else None)

    # ------------------------------------------------------------ room
    def create_room_frame(self):
        self.clear_window()
        self.apply_theme()
        self._cards_sig = None   # карточки будут построены заново
        self.root.geometry("1060x680")

        layout = tk.Frame(self.root, bg=self.th_bg)
        layout.pack(fill="both", expand=True)

        # ---------- левый сайдбар
        left = tk.Frame(layout, bg=self.th_panel, width=270)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        gradient_bar(left, 270, 3, self.th_accent_grad, self.th_accent).pack(fill="x")
        head = tk.Frame(left, bg=self.th_panel)
        head.pack(fill="x", padx=12, pady=(10, 6))
        head_logo = _load_icon("icon_logo", 20, self.th_accent)
        if head_logo:
            hl = tk.Label(head, image=head_logo, bg=self.th_panel)
            hl.image = head_logo
            hl.pack(side="left", padx=(0, 6))
        tk.Label(head, text="друнспик", bg=self.th_panel, fg=self.th_fg,
                 font=(UI_FONT, 12, "bold")).pack(side="left")
        settings_btn = IconBtn(head, "icon_settings", self.open_settings_window,
                               size=30, bg=self.th_card, hover=self.th_line,
                               tint=self.th_fg, tooltip="Настройки")
        settings_btn.pack(side="right")

        tk.Frame(left, bg=self.th_line, height=1).pack(fill="x", padx=12, pady=(2, 4))

        self.chan_scroll_frame = tk.Frame(left, bg=self.th_panel)
        self.chan_scroll_frame.pack(fill="both", expand=True, padx=8, pady=4)
        self.rebuild_channel_list_ui()

        # ---------- правый сайдбар
        right = tk.Frame(layout, bg=self.th_panel, width=240)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        tk.Label(right, text="УЧАСТНИКИ", bg=self.th_panel, fg=self.th_subtext,
                 font=(UI_FONT, 8, "bold"), anchor="w").pack(fill="x", padx=14, pady=(12, 6))
        self.cards_container = tk.Frame(right, bg=self.th_panel)
        self.cards_container.pack(fill="both", expand=True, padx=8)

        # ---------- центр
        center = tk.Frame(layout, bg=self.th_bg)
        center.pack(side="left", fill="both", expand=True)

        top = tk.Frame(center, bg=self.th_bg, height=52)
        top.pack(fill="x", padx=14, pady=(8, 0))
        top.pack_propagate(False)
        self.header_title = tk.Label(top, text=f"# {self.current_channel.get()}",
                                     bg=self.th_bg, fg=self.th_fg, font=(UI_FONT, 13, "bold"))
        self.header_title.pack(side="left")
        self.screen_btn = RoundBtn(top, "📺  Трансляция экрана", self.th_accent, self.th_accent_grad,
                                   self.prompt_screen_share, font=(UI_FONT, 9, "bold"), py=7, px=12)
        self.screen_btn.pack(side="right", padx=4)
        ToolTip(self.screen_btn, "Показать экран (цель 60 fps)")
        self.stream_audio_btn = IconBtn(
            top, "icon_sound_on", self._toggle_stream_audio, size=34,
            active_icon="icon_sound_off", bg=self.th_card, hover=self.th_line,
            tint=self.th_fg, active_bg=self.th_bad, active_hover="#c25552",
            active_tint="#ffffff", tooltip="Звук при трансляции вкл/выкл")
        self.stream_audio_btn.set_active(not self.stream_audio_enabled.get())
        self.stream_audio_btn.pack(side="right", padx=(0, 4))

        chat_wrap = tk.Frame(center, bg=self.th_panel, highlightthickness=1, highlightbackground=self.th_line)
        chat_wrap.pack(fill="both", expand=True, padx=14, pady=6)
        self.chat_display = scrolledtext.ScrolledText(
            chat_wrap, bg=self.th_input, fg=self.th_fg, font=(UI_FONT, 10),
            state="disabled", bd=0, highlightthickness=0, padx=14, pady=10, wrap="word")
        self.chat_display.pack(side="left", fill="both", expand=True)
        chat_scroll = ttk.Scrollbar(chat_wrap, orient="vertical", command=self.chat_display.yview,
                                    style="Vertical.TScrollbar")
        chat_scroll.pack(side="right", fill="y")
        self.chat_display.config(yscrollcommand=chat_scroll.set)
        for tag, opts in (("sender", {"foreground": self.th_accent, "font": (UI_FONT, 10, "bold")}),
                          ("time", {"foreground": self.th_subtext, "font": (UI_FONT, 7)}),
                          ("body", {"foreground": self.th_fg, "font": (UI_FONT, 10), "spacing3": 8})):
            self.chat_display.tag_configure(tag, **opts)
        self.redraw_chat_history()

        input_row = tk.Frame(center, bg=self.th_bg, height=54)
        input_row.pack(fill="x", padx=14, pady=(2, 10))
        input_row.pack_propagate(False)
        self.file_btn = IconBtn(input_row, "icon_file", self.send_file_dialog, size=38,
                                bg=self.th_card, hover=self.th_line,
                                tint=self.th_fg, tooltip="📎 Отправить файл")
        self.file_btn.pack(side="left", padx=(0, 6))
        self.img_btn = IconBtn(input_row, "icon_image", self.send_image_dialog, size=38,
                               bg=self.th_card, hover=self.th_line,
                               tint=self.th_fg, tooltip="📷 Отправить картинку")
        self.img_btn.pack(side="left", padx=(0, 8))
        entry_wrap = tk.Frame(input_row, bg=self.th_input, highlightthickness=1, highlightbackground=self.th_line)
        entry_wrap.pack(side="left", fill="both", expand=True)
        self.chat_entry = tk.Entry(entry_wrap, bg=self.th_input, fg=self.th_fg,
                                   insertbackground=self.th_fg, font=(UI_FONT, 10),
                                   relief="flat", bd=0)
        self.chat_entry.pack(fill="both", expand=True, padx=10)
        self.bind_entry_shortcuts(self.chat_entry)
        self.chat_entry.bind("<Return>", self.send_chat_message)
        self.send_chat_btn = IconBtn(input_row, "icon_send", self.send_chat_message, size=38,
                                     bg=self.th_accent, hover=self.th_accent_grad,
                                     tint="#ffffff", pad=10,
                                     tooltip="✈ Отправить сообщение")
        self.send_chat_btn.pack(side="right", padx=(8, 0))

        self._build_voice_bar()
        self.refresh_user_cards()
        self._apply_wallpaper_layer()

    def _build_voice_bar(self):
        bar = tk.Frame(self.root, bg=self.th_panel, height=64)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Frame(bar, bg=self.th_line, height=1).pack(fill="x", side="top")

        self.voice_bar = bar
        info = tk.Frame(bar, bg=self.th_panel)
        info.pack(side="left", padx=12, fill="y", expand=True)
        self.ping_indicator_lbl = tk.Label(info, text="● качество соединения: —",
                                           bg=self.th_panel, fg=self.th_ok,
                                           font=(UI_FONT, 8, "bold"), anchor="w",
                                           cursor="hand2")
        self.ping_indicator_lbl.pack(anchor="w", pady=(6, 0))
        self.ping_indicator_lbl.bind("<Button-1>", lambda e: self._show_ping_stats())
        ToolTip(self.ping_indicator_lbl, self._ping_tooltip_text)
        self.current_chan_lbl = tk.Label(info, text=f"🔊 {self.current_channel.get()}",
                                         bg=self.th_panel, fg=self.th_subtext,
                                         font=(UI_FONT, 8), anchor="w")
        self.current_chan_lbl.pack(anchor="w")

        self.disconnect_btn = IconBtn(bar, "icon_power", self.disconnect_server, size=40,
                                      bg=self.th_bad, hover="#c25552",
                                      tint="#ffffff", active_tint="#ffffff",
                                      tooltip="Отключиться от сервера")
        self.disconnect_btn.pack(side="right", padx=10)
        self.deafen_btn = IconBtn(bar, "icon_headphones", self.toggle_deafen, size=40,
                                  bg=self.th_card, hover=self.th_line, tint=self.th_fg,
                                  active_bg=self.th_warn, active_hover="#d1a552",
                                  active_tint="#ffffff", tooltip="Отключить звук")
        self.deafen_btn.pack(side="right", padx=3)
        self.mute_btn = IconBtn(bar, "icon_mic", self.toggle_mute, size=40,
                                bg=self.th_card, hover=self.th_line, tint=self.th_fg,
                                active_bg=self.th_bad, active_hover="#c25552",
                                active_tint="#ffffff", tooltip="Микрофон вкл/выкл")
        self.mute_btn.pack(side="right", padx=3)
        ToolTip(self.deafen_btn, "Выключить звук")
        ToolTip(self.mute_btn, "Микрофон вкл/выкл")

        # индикатор уровня микрофона (анимированная полоска)
        self.mic_meter = tk.Canvas(bar, width=90, height=8, bg=self.th_panel, highlightthickness=0, bd=0)
        self.mic_meter.pack(side="right", padx=(3, 8))
        self._meter_bar = self.mic_meter.create_rectangle(0, 0, 0, 8, fill=self.th_ok, width=0)
        self._animate_meter()

    def rebuild_channel_list_ui(self):
        if not hasattr(self, "chan_scroll_frame"):
            return
        for w in self.chan_scroll_frame.winfo_children():
            w.destroy()
        chan_head = tk.Frame(self.chan_scroll_frame, bg=self.th_panel)
        chan_head.pack(fill="x", padx=6, pady=(6, 3))
        tk.Label(chan_head, text="ГОЛОСОВЫЕ КАНАЛЫ", bg=self.th_panel, fg=self.th_subtext,
                 font=(UI_FONT, 8, "bold"), anchor="w").pack(side="left")
        add_btn = IconBtn(chan_head, "icon_plus", self.create_channel_dialog,
                          size=22, bg=self.th_card, hover=self.th_line,
                          tint=self.th_fg, tooltip="Создать свой канал (можно с паролем)")
        add_btn.pack(side="right")
        counts = {}
        for u in self.remote_users:
            counts[u.get("channel", "друнспик 1")] = counts.get(u.get("channel", "друнспик 1"), 0) + 1
        for cname, cdata in self.available_channels_data.items():
            lock = " 🔒" if cdata.get("has_password") else ""
            cnt = counts.get(cname, 0)
            cnt_txt = f"  · {cnt}" if cnt else ""
            is_current = (self.current_channel.get() == cname)
            row = tk.Frame(self.chan_scroll_frame, bg=self.th_card if is_current else self.th_panel)
            row.pack(fill="x", pady=1)
            btn = tk.Label(row, text=f"🔊 {cname}{lock}{cnt_txt}",
                           bg=row["bg"], fg=self.th_fg if is_current else self.th_subtext,
                           font=(UI_FONT, 9, "bold" if is_current else "normal"),
                           anchor="w", padx=10, pady=7, cursor="hand2")
            btn.pack(fill="x")
            btn.bind("<Enter>", lambda e, b=btn, r=row: (b.config(bg=self.th_card), r.config(bg=self.th_card)))
            btn.bind("<Leave>", lambda e, b=btn, r=row, cur=is_current: (
                b.config(bg=self.th_card if cur else self.th_panel),
                r.config(bg=self.th_card if cur else self.th_panel)))
            btn.bind("<Button-1>", lambda e, cn=cname: self.try_switch_channel(cn))
            if is_current:
                tk.Frame(row, bg=self.th_accent, width=3).pack(side="left", fill="y")

    def create_channel_dialog(self):
        if not self.is_connected:
            return
        name = simpledialog.askstring(
            "Новый канал", "Название канала:", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()[:32]
        pw = simpledialog.askstring(
            "Пароль канала",
            f"Пароль для '{name}'\n(пусто — канал без пароля):",
            show="•", parent=self.root) or ""
        if not self._send_control({"t": "channel_create", "chan": name, "pass": pw[:64]}):
            return
        self.append_chat_message("⚙ система",
                                 f"Запрос на создание канала '{name}' отправлен.")
        if pw:
            self.append_chat_message("⚙ система",
                                     f"Пароль канала '{name}': {pw} "
                                     "(его видно в логах и админ-панели сервера).")

    def try_switch_channel(self, channel_name):
        if channel_name == self.current_channel.get() or not self.is_connected:
            return
        password = ""
        if self.available_channels_data.get(channel_name, {}).get("has_password", False):
            password = simpledialog.askstring("Пароль канала",
                                              f"Канал '{channel_name}' защищен паролем.\nВведите пароль:",
                                              show="•", parent=self.root) or ""
        self._send_control({"t": "switch", "chan": channel_name, "pass": password})

    # ------------------------------------------------------------ chat
    def _chat_render(self, msg, insert=True):
        d = self.chat_display
        t = time.strftime("%H:%M", time.localtime(msg.get("ts", time.time())))
        if msg["type"] == "text":
            d.insert(tk.END, f"{t} ", "time")
            d.insert(tk.END, msg["sender"], "sender")
            d.insert(tk.END, f"   {msg['content']}\n", "body")
        elif msg["type"] == "image":
            d.insert(tk.END, f"{t} ", "time")
            d.insert(tk.END, msg["sender"], "sender")
            d.insert(tk.END, "  отправил(а) изображение\n", "body")
            try:
                image = Image.open(io.BytesIO(msg["bytes"]))
                image.thumbnail((280, 280))
                photo = ImageTk.PhotoImage(image)
                self.chat_images_cache[id(photo)] = photo
                d.image_create(tk.END, image=photo)
                d.insert(tk.END, "\n", "body")
            except Exception:
                d.insert(tk.END, "[Не удалось отобразить картинку]\n", "body")
        else:  # file / image_file
            name = msg.get("name", "file.bin")
            size = msg.get("size", 0)
            d.insert(tk.END, f"{t} ", "time")
            d.insert(tk.END, msg["sender"], "sender")
            if msg["type"] == "image_file":
                d.insert(tk.END, "  отправил(а) изображение\n", "body")
                try:
                    image = Image.open(io.BytesIO(self._file_payload(msg)))
                    image.thumbnail((320, 320), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(image)
                    self.chat_images_cache[id(photo)] = photo
                    d.image_create(tk.END, image=photo)
                    d.insert(tk.END, "\n", "body")
                except Exception:
                    d.insert(tk.END, "[Не удалось отобразить картинку]\n", "body")
            d.insert(tk.END, f"  📎 {name}  ({size / 1024:.0f} КБ)\n", "body")
            key = id(msg)
            self._pending_file_save[key] = msg
            btn = tk.Button(d, text="💾  Сохранить как…", bg=self.th_card, fg=self.th_fg,
                            font=(UI_FONT, 8, "bold"), relief="flat", bd=0,
                            cursor="hand2", activebackground=self.th_accent,
                            command=lambda k=key, n=name: self._save_chat_file(k, n))
            d.window_create(tk.END, window=btn)
            d.insert(tk.END, "\n", "body")

    def redraw_chat_history(self):
        if not hasattr(self, "chat_display") or not self.chat_display.winfo_exists():
            return
        d = self.chat_display
        d.config(state="normal")
        d.delete("1.0", tk.END)
        cur = self.current_channel.get()
        for msg in self.chat_messages:
            # Отдельный чат у каждого канала. Сообщения без метки (старые)
            # считаем относящимися к текущему каналу.
            ch = msg.get("channel", cur)
            if ch and ch != cur:
                continue
            self._chat_render(msg)
        d.config(state="disabled")
        d.see(tk.END)

    def _visible_now(self, channel):
        cur = self.current_channel.get()
        return not channel or channel == cur

    def append_chat_message(self, sender, text, play_sound=False, channel=None):
        self.chat_messages.append({"type": "text", "sender": sender, "content": text,
                                   "ts": time.time(),
                                   "channel": channel or self.current_channel.get()})
        if not self._visible_now(channel):
            return   # сообщение другого канала — только в историю
        if len(self.chat_messages) > 300:
            self.chat_messages = self.chat_messages[-200:]
        if play_sound:
            try:
                self.root.bell()
            except Exception:
                pass
        if hasattr(self, "chat_display") and self.chat_display.winfo_exists():
            d = self.chat_display
            d.config(state="normal")
            self._chat_render(self.chat_messages[-1])
            d.config(state="disabled")
            d.see(tk.END)

    def append_chat_file(self, sender, name, data, as_image=False):
        msg = {"type": "image_file" if as_image else "file",
               "sender": sender, "name": name, "size": len(data),
               "ts": time.time(), "channel": self.current_channel.get()}
        msg.update(self._store_file_payload(name, data))
        self.chat_messages.append(msg)
        if not self._visible_now(msg["channel"]):
            return
        if len(self.chat_messages) > 300:
            self.chat_messages = self.chat_messages[-200:]
        live = {id(m) for m in self.chat_messages}
        for k in list(self._pending_file_save.keys()):
            if k not in live:
                self._pending_file_save.pop(k, None)
        if hasattr(self, "chat_display") and self.chat_display.winfo_exists():
            d = self.chat_display
            d.config(state="normal")
            self._chat_render(self.chat_messages[-1])
            d.config(state="disabled")
            d.see(tk.END)

    def _save_chat_file(self, key, suggested_name):
        msg = self._pending_file_save.get(key)
        data = self._file_payload(msg) if msg else b""
        if not data:
            messagebox.showerror("Файл", "Данные файла недоступны.")
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить файл", initialfile=os.path.basename(suggested_name or "file.bin"))
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(data)
            messagebox.showinfo("Файл", f"Сохранено:\n{path}")
        except Exception as e:
            messagebox.showerror("Файл", f"Не удалось сохранить:\n{e}")

    def append_chat_image(self, sender, img_bytes, channel=None):
        self.chat_messages.append({"type": "image", "sender": sender, "bytes": img_bytes,
                                   "ts": time.time(),
                                   "channel": channel or self.current_channel.get()})
        if not self._visible_now(channel):
            return
        if len(self.chat_messages) > 300:
            self.chat_messages = self.chat_messages[-200:]
        if hasattr(self, "chat_display") and self.chat_display.winfo_exists():
            d = self.chat_display
            d.config(state="normal")
            self._chat_render(self.chat_messages[-1])
            d.config(state="disabled")
            d.see(tk.END)

    def send_chat_message(self, event=None):
        if not hasattr(self, "chat_entry"):
            return
        text = self.chat_entry.get().strip()
        if text and self.is_connected:
            if self._send_control({"t": "chat", "text": text[:1000]}):
                if hasattr(self, "send_chat_btn"):
                    self.send_chat_btn.flash()
                self.append_chat_message(self.nickname.get() or "User", text)
                self.chat_entry.delete(0, tk.END)

    # ---- картинки: надёжная передача с окнами и NACK-перезапросом
    IMG_WINDOW = 30        # чанков за один рывок
    IMG_WINDOW_PAUSE = 0.03  # пауза между рывками, сек

    def send_image_dialog(self):
        if not self.is_connected:
            return
        if hasattr(self, "img_btn"):
            self.img_btn.flash()
        file_path = filedialog.askopenfilename(
            title="Выберите картинку для чата",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.gif *.bmp *.webp")])
        if not file_path:
            return
        try:
            with open(file_path, "rb") as f:
                img_bytes = f.read()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось прочитать файл:\n{e}")
            return
        if len(img_bytes) > 2000 * 1024:
            messagebox.showwarning("Ошибка", "Картинка слишком большая! До 2 МБ.")
            return
        # большие фото сжимаем в JPEG — меньше пакетов, надёжнее доставка
        ext = os.path.splitext(file_path)[1].lower() or ".png"
        if ext in (".jpg", ".jpeg") or len(img_bytes) > 400 * 1024:
            try:
                im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                if max(im.size) > 1600:
                    k = 1600 / max(im.size)
                    im = im.resize((int(im.size[0] * k), int(im.size[1] * k)), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=82)
                if len(buf.getvalue()) < len(img_bytes):
                    img_bytes = buf.getvalue()
                    ext = ".jpg"
            except Exception:
                pass
        msg_id = os.urandom(4)
        chunks = [img_bytes[i:i + 1200] for i in range(0, len(img_bytes), 1200)]
        now = time.time()
        self.img_send_cache[msg_id] = {"chunks": chunks, "ext": ext, "created": now,
                                       "sent": now, "acked": False, "nacks": 0,
                                       "resends": 0, "inflight": 0}
        # Своя картинка видна сразу: даже при потере части пакетов в туннеле
        # пользователь понимает, что отправка состоялась.
        self.append_chat_image(self.nickname.get() or "User", img_bytes)
        self._start_image_send(msg_id, list(range(len(chunks))))

    def _start_image_send(self, msg_id, indices=None):
        """Отправка чанков в фоновом потоке (не блокирует GUI и приём сети)."""
        entry = self.img_send_cache.get(msg_id)
        if not entry:
            return
        # Разрешаем параллельные переотправки (NACK может прийти, пока идёт
        # первая волна), но ведём счётчик, чтобы не запускать лишний resend.
        entry["inflight"] = entry.get("inflight", 0) + 1
        threading.Thread(target=self._send_image_chunks,
                         args=(msg_id, indices), daemon=True).start()

    def _send_image_chunks(self, msg_id, indices=None):
        entry = self.img_send_cache.get(msg_id)
        if not entry or not self.is_connected:
            if entry:
                entry["inflight"] = max(0, entry.get("inflight", 1) - 1)
            return
        try:
            total = len(entry["chunks"])
            ext = entry["ext"]
            if indices is None:
                indices = range(total)
            sent_in_window = 0
            for idx in indices:
                if not self.is_connected:
                    break
                try:
                    self.client_socket.sendto(
                        pack_img_client(msg_id, ext, idx, total, entry["chunks"][idx]),
                        self.server_address)
                except Exception as e:
                    print(f"Ошибка отправки картинки: {e}")
                    break
                sent_in_window += 1
                if sent_in_window >= self.IMG_WINDOW:
                    sent_in_window = 0
                    time.sleep(self.IMG_WINDOW_PAUSE)  # не заливаем UDP-буферы туннеля
            entry["sent"] = time.time()
        finally:
            if entry:
                entry["inflight"] = max(0, entry.get("inflight", 1) - 1)

    def _handle_img_chunk(self, data):
        parsed = parse_img_server(data)
        if not parsed:
            return
        nick, msg_id, ext, idx, total, payload = parsed
        if total < 1 or total > 2048 or idx >= total or len(payload) > 1400:
            return
        state = self.img_recv_state.get(msg_id)
        if state is None or state["total"] != total:
            state = {"nick": nick, "ext": ext, "total": total, "chunks": {},
                     "nacks": 0, "last": time.time(), "done": False}
            self.img_recv_state[msg_id] = state
        if state["done"]:
            # Повторный хвост после сборки — подтверждаем ещё раз, чтобы
            # отправитель точно выкинул картинку из кэша.
            self._send_control({"t": "img_ack", "id": msg_id.hex()})
            return
        state["last"] = time.time()
        state["chunks"][idx] = payload
        if len(state["chunks"]) == state["total"]:
            try:
                full = b"".join(state["chunks"][i] for i in range(state["total"]))
            except KeyError:
                return
            state["done"] = True
            self._send_control({"t": "img_ack", "id": msg_id.hex()})
            self.ui(lambda s=nick, b=full: self.append_chat_image(s, b))
            self.ui(lambda: self._purge_done_image(msg_id))

    def _purge_done_image(self, msg_id):
        self.img_recv_state.pop(msg_id, None)

    def _handle_img_feedback(self, msg):
        t = msg.get("t")
        try:
            msg_id = bytes.fromhex(str(msg.get("id", "")))
        except Exception:
            return
        if t == "img_ack":
            self.img_send_cache.pop(msg_id, None)
        elif t == "img_nack":
            entry = self.img_send_cache.get(msg_id)
            if not entry:
                return
            missing = msg.get("missing", [])
            total = len(entry["chunks"])
            indices = [int(i) for i in missing if isinstance(i, int) and 0 <= i < total]
            if indices:
                entry["nacks"] += 1
                # ВАЖНО: не шлём из приёмного потока (там sleep блокировал
                # приём голоса/видео). Переотправка всегда в фоне.
                self._start_image_send(msg_id, indices)

    # ---- файлы: та же надёжная схема «окна + NACK», что и у картинок
    FILE_CHUNK = 1200
    FILE_MAX_SEND = FILE_MAX_SEND
    FILE_WINDOW = 40
    FILE_WINDOW_PAUSE = 0.004
    IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

    def _store_file_payload(self, name, data):
        """Маленькие файлы — в памяти, большие — во временном файле."""
        if len(data) >= FILE_BIG_THRESHOLD:
            d = os.path.join(tempfile.gettempdir(), "drunspeak_files")
            os.makedirs(d, exist_ok=True)
            safe = os.path.basename(str(name))[:120] or "file.bin"
            p = os.path.join(d, os.urandom(6).hex() + "_" + safe)
            with open(p, "wb") as f:
                f.write(data)
            return {"path": p}
        return {"bytes": data}

    @staticmethod
    def _file_payload(msg):
        if "bytes" in msg:
            return msg["bytes"]
        try:
            with open(msg["path"], "rb") as f:
                return f.read()
        except Exception:
            return b""

    def send_file_dialog(self):
        if not self.is_connected:
            return
        if hasattr(self, "file_btn"):
            self.file_btn.flash()
        path = filedialog.askopenfilename(title="Выберите файл для отправки")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            messagebox.showerror("Файл", f"Не удалось прочитать файл:\n{e}")
            return
        if len(data) > self.FILE_MAX_SEND:
            messagebox.showwarning("Файл", "Слишком большой файл! До 300 МБ.")
            return
        name = os.path.basename(path)
        is_image = os.path.splitext(name)[1].lower() in self.IMAGE_EXTS
        msg_id = os.urandom(4)
        chunks = [data[i:i + self.FILE_CHUNK]
                  for i in range(0, len(data), self.FILE_CHUNK)]
        now = time.time()
        self.file_send_cache[msg_id] = {
            "chunks": chunks, "name": name, "created": now, "sent": now,
            "acked": False, "resends": 0, "inflight": 0}
        self.append_chat_file(self.nickname.get() or "User", name, data,
                              as_image=is_image)
        self._start_file_send(msg_id, list(range(len(chunks))))

    def _start_file_send(self, msg_id, indices=None):
        entry = self.file_send_cache.get(msg_id)
        if not entry:
            return
        entry["inflight"] = entry.get("inflight", 0) + 1
        threading.Thread(target=self._send_file_chunks,
                         args=(msg_id, indices), daemon=True).start()

    def _send_file_chunks(self, msg_id, indices=None):
        entry = self.file_send_cache.get(msg_id)
        if not entry or not self.is_connected:
            if entry:
                entry["inflight"] = max(0, entry.get("inflight", 1) - 1)
            return
        try:
            total, name = len(entry["chunks"]), entry["name"]
            if indices is None:
                indices = range(total)
            n = 0
            for idx in indices:
                if not self.is_connected:
                    break
                try:
                    self.client_socket.sendto(
                        pack_file_client(msg_id, name, idx, total, entry["chunks"][idx]),
                        self.server_address)
                except Exception as e:
                    print(f"Ошибка отправки файла: {e}")
                    break
                n += 1
                if n >= self.FILE_WINDOW:
                    n = 0
                    time.sleep(self.FILE_WINDOW_PAUSE)
            entry["sent"] = time.time()
        finally:
            if entry:
                entry["inflight"] = max(0, entry.get("inflight", 1) - 1)

    def _handle_file_chunk(self, data):
        parsed = parse_file_server(data)
        if not parsed:
            return
        nick, msg_id, name, idx, total, payload = parsed
        if total < 1 or total > 300000 or idx >= total or len(payload) > 1300:
            return
        st = self.file_recv_state.get(msg_id)
        if st is None or st["total"] != total:
            st = {"nick": nick, "name": name, "total": total, "chunks": {},
                  "nacks": 0, "last": time.time(), "done": False}
            self.file_recv_state[msg_id] = st
        if st["done"]:
            self._send_control({"t": "file_ack", "id": msg_id.hex()})
            return
        st["last"] = time.time()
        st["chunks"][idx] = payload
        if len(st["chunks"]) == st["total"]:
            try:
                full = b"".join(st["chunks"][i] for i in range(st["total"]))
            except KeyError:
                return
            st["done"] = True
            self._send_control({"t": "file_ack", "id": msg_id.hex()})
            as_image = os.path.splitext(name)[1].lower() in self.IMAGE_EXTS
            self.ui(lambda s=nick, n=name, b=full, a=as_image:
                    self.append_chat_file(s, n, b, as_image=a))
            self.ui(lambda: self.file_recv_state.pop(msg_id, None))

    def _handle_file_feedback(self, msg):
        t = msg.get("t")
        try:
            msg_id = bytes.fromhex(str(msg.get("id", "")))
        except Exception:
            return
        if t == "file_ack":
            self.file_send_cache.pop(msg_id, None)
        elif t == "file_nack":
            entry = self.file_send_cache.get(msg_id)
            if not entry:
                return
            total = len(entry["chunks"])
            indices = [int(i) for i in msg.get("missing", [])
                       if isinstance(i, int) and 0 <= i < total]
            if indices:
                self._start_file_send(msg_id, indices)

    def video_nack_loop(self):
        """Быстрый (12 мс) перезапрос чанков кадра — вместо 1-секундного монитора."""
        while self.is_connected:
            time.sleep(0.012)
            now = time.time()
            for nick, buf in list(self._video_recv.items()):
                for fid, st in list(buf.items()):
                    age = now - st.get("t", 0)
                    if age < 0.030 or st.get("nacks", 0) >= 3:
                        continue
                    missing = [i for i in range(st["total"])
                               if i not in st["chunks"]]
                    if not missing or len(missing) > 512:
                        continue
                    st["nacks"] = st.get("nacks", 0) + 1
                    st["t"] = now
                    self._send_control({"t": "video_nack", "nick": nick,
                                        "fid": fid, "missing": missing})

    def img_monitor_loop(self):
        while self.is_connected:
            time.sleep(1.0)
            now = time.time()
            for msg_id in list(self.img_send_cache.keys()):
                entry = self.img_send_cache.get(msg_id)
                if not entry:
                    continue
                if entry["acked"] or now - entry["created"] > 120.0:
                    self.img_send_cache.pop(msg_id, None)
                    continue
                # Если ACK не пришёл (потерялся сам control-пакет) — повторяем
                # всю картинку целиком до 3 раз.
                if (entry.get("inflight", 0) == 0 and now - entry["sent"] > 8.0
                        and entry.get("resends", 0) < 3):
                    entry["resends"] += 1
                    entry["sent"] = now
                    self._start_image_send(msg_id, list(range(len(entry["chunks"]))))
            for msg_id, st in list(self.img_recv_state.items()):
                if st["done"]:
                    continue
                if now - st["last"] > 120.0:
                    self.img_recv_state.pop(msg_id, None)
                    continue
                if now - st["last"] > 0.7 and st["nacks"] < 20:
                    missing = [i for i in range(st["total"]) if i not in st["chunks"]]
                    if not missing:
                        continue
                    st["nacks"] += 1
                    st["last"] = now
                    self._send_control({"t": "img_nack", "id": msg_id.hex(), "missing": missing})
            # ---- файлы: NACK + повтор целиком, если ACK потерялся
            for msg_id in list(self.file_send_cache.keys()):
                e = self.file_send_cache.get(msg_id)
                if not e:
                    continue
                if e["acked"] or now - e["created"] > 300.0:
                    self.file_send_cache.pop(msg_id, None)
                    continue
                if (e.get("inflight", 0) == 0 and now - e["sent"] > 10.0
                        and e.get("resends", 0) < 3):
                    e["resends"] += 1
                    e["sent"] = now
                    self._start_file_send(msg_id, list(range(len(e["chunks"]))))
            for msg_id, st in list(self.file_recv_state.items()):
                if st["done"]:
                    continue
                if now - st["last"] > 300.0:
                    self.file_recv_state.pop(msg_id, None)
                    continue
                if now - st["last"] > 1.0 and st["nacks"] < 25:
                    missing = [i for i in range(st["total"]) if i not in st["chunks"]]
                    if missing:
                        st["nacks"] += 1
                        st["last"] = now
                        self._send_control({"t": "file_nack", "id": msg_id.hex(),
                                            "missing": missing[:4000]})
            # забыть недособранные аватарки старше минуты
            for key, st in list(self._avatar_parts.items()):
                if now - st.get("t", 0) > 60.0:
                    self._avatar_parts.pop(key, None)
            # Только уборка просроченных fid; NACK вынесен в быстрый поток
            # (здесь период 1 с — для 60 fps это слишком поздно).
            for nick, buf in list(self._video_recv.items()):
                for fid, st in list(buf.items()):
                    if now - st.get("t", 0) > 0.35:
                        buf.pop(fid, None)
                if not buf:
                    self._video_recv.pop(nick, None)

    # ------------------------------------------------------------ участники
    def refresh_user_cards(self):
        if not hasattr(self, "cards_container") or not self.cards_container.winfo_exists():
            return
        try:
            sig = json.dumps(
                [self.remote_users, self.current_channel.get(),
                 self.muted.get(), self.deafened.get(), self.is_sharing_screen,
                 sorted(self.local_mutes), self.user_volumes,
                 self.nickname.get(), self.current_theme.get(),
                 {k: hashlib.md5(str(v).encode()).hexdigest()[:8]
                  for k, v in self.avatars_data.items()}],
                ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            sig = None
        if sig is not None and sig == getattr(self, "_cards_sig", None):
            return   # пользовательский список не изменился — не дёргаем UI
        self._cards_sig = sig
        self._stop_card_anims()
        for w in self.cards_container.winfo_children():
            w.destroy()

        users = list(self.remote_users)
        my_nick = self.nickname.get() or "User"
        my_found = any(u.get("name") == my_nick for u in users)
        if not my_found:
            users.append({
                "name": my_nick, "muted": False, "mic_muted": self.muted.get(),
                "deafened": self.deafened.get(), "sharing": self.is_sharing_screen,
                "talking": False, "channel": self.current_channel.get(),
            })
        else:
            for u in users:
                if u.get("name") == my_nick:
                    u["sharing"] = self.is_sharing_screen

        channels_map = {c: [] for c in self.available_channels_data.keys()}
        for u in users:
            chan = u.get("channel", "друнспик 1")
            channels_map.setdefault(chan, []).append(u)

        for chan_name, chan_users in channels_map.items():
            tk.Label(self.cards_container, text=f"🔊 {chan_name} ({len(chan_users)})",
                     bg=self.th_panel, fg=self.th_subtext, font=(UI_FONT, 8, "bold"),
                     anchor="w").pack(fill="x", pady=(8, 2))
            for u in chan_users:
                self._build_user_card(u, my_nick)

    def _stop_card_anims(self):
        for job in list(self._anim_jobs.values()):
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._anim_jobs = {}

    def _avatar_photo(self, nick, size=30):
        b64 = self.avatars_data.get(nick, "")
        if not b64 and nick == (self.nickname.get() or "User"):
            b64 = self.avatar_b64
        if b64:
            try:
                return round_photo(base64.b64decode(b64), size)
            except Exception:
                return None
        return None

    def _build_user_card(self, u, my_nick):
        name = u.get("name")
        is_me = (name == my_nick)
        is_in_my_channel = (u.get("channel") == self.current_channel.get())
        is_talking = u.get("talking", False) if is_in_my_channel else False

        if not is_in_my_channel:
            status_sub, stat_color = f"В [{u.get('channel')}]", self.th_subtext
        elif u.get("muted", False):
            status_sub, stat_color = "мут от админа", self.th_bad
        elif u.get("mic_muted", False):
            status_sub, stat_color = "микрофон выкл", self.th_warn
        elif u.get("deafened", False):
            status_sub, stat_color = "звук выключен", self.th_warn
        elif is_talking:
            status_sub, stat_color = "говорит", self.th_ok
        else:
            status_sub, stat_color = "в сети", self.th_subtext

        border = self.th_ok if is_talking else self.th_line
        card = tk.Frame(self.cards_container, bg=self.th_card, padx=8, pady=6,
                        highlightthickness=2, highlightbackground=border)
        card.pack(fill="x", pady=2)
        av = AvatarView(card, size=30, bg_circle=self.th_input, fg=self.th_fg,
                        letter=(name or "?")[:1])
        photo = self._avatar_photo(name)
        av.set_photo(photo)
        if not photo:
            av.set_letter((name or "?")[:1], self.th_fg)
        av.pack(side="left", padx=(0, 8))
        info_f = tk.Frame(card, bg=self.th_card)
        info_f.pack(side="left", fill="both", expand=True)
        tk.Label(info_f, text=f"{name} (Вы)" if is_me else name, bg=self.th_card, fg=self.th_fg,
                 font=(UI_FONT, 9, "bold"), anchor="w").pack(fill="x")
        tk.Label(info_f, text=status_sub, bg=self.th_card, fg=stat_color,
                 font=(UI_FONT, 7), anchor="w").pack(fill="x")
        if is_in_my_channel and u.get("sharing", False) and not is_me:
            share_btn = IconBtn(card, "icon_share",
                                lambda n=name: self.open_remote_stream_window(n),
                                size=24, bg=self.th_accent, hover=self.th_accent_grad,
                                tint="#ffffff", tooltip="Смотреть трансляцию")
            share_btn.pack(side="right", padx=2)
        if not is_me:
            muted = name in self.local_mutes
            vol_btn = IconBtn(card, "icon_sound_off" if muted else "icon_volume",
                              lambda n=name: self._open_user_volume_popup(n),
                              size=24, bg=self.th_input, hover=self.th_line,
                              tint=self.th_fg,
                              tooltip="Громкость игрока и мут только для себя")
            vol_btn.pack(side="right", padx=2)
        if is_talking:
            self._start_talking_pulse(card)

    def _open_user_volume_popup(self, nick):
        """Ползунок громкости + локальный мут конкретного игрока."""
        win = tk.Toplevel(self.root)
        win.title(f"Звук — {nick}")
        win.geometry("320x245")
        win.config(bg=self.th_bg)
        win.resizable(False, False)
        try:
            win.geometry(f"+{self.root.winfo_pointerx() + 8}+{self.root.winfo_pointery() + 8}")
        except Exception:
            pass
        tk.Label(win, text=nick, bg=self.th_bg, fg=self.th_fg,
                 font=(UI_FONT, 12, "bold")).pack(pady=(14, 2))
        tk.Label(win, text="настройки видны только вам", bg=self.th_bg, fg=self.th_subtext,
                 font=(UI_FONT, 8)).pack(pady=(0, 12))

        inv = inverted_colors(self.th_bg, self.th_accent)
        vol = tk.DoubleVar(value=float(self.user_volumes.get(nick, 1.0)))
        muted = tk.BooleanVar(value=nick in self.local_mutes)

        def apply(*_):
            # Применяем на лету без перерисовки списка (иначе карточки мигают
            # при каждом движении ползунка).
            self.user_volumes[nick] = float(vol.get())

        tk.Label(win, text="Громкость", bg=self.th_bg, fg=inv["text"],
                 font=(UI_FONT, 9, "bold"), anchor="w").pack(fill="x", padx=22)
        InvertedScale(win, vol, from_=0.0, to=2.0, resolution=0.05,
                      width=276, command=apply, owner=self,
                      track=inv["track"], knob=inv["knob"], text_color=inv["text"],
                      fill=self.th_accent, label="×").pack(padx=22, pady=(2, 12))

        def toggle_mute():
            if muted.get():
                self.local_mutes.add(nick)
            else:
                self.local_mutes.discard(nick)
            self.refresh_user_cards()

        tk.Checkbutton(win, text="Выключить звук только у меня", variable=muted,
                       command=toggle_mute, bg=self.th_bg, fg=self.th_fg,
                       selectcolor=self.th_input, activebackground=self.th_bg,
                       activeforeground=self.th_fg, font=(UI_FONT, 9, "bold")).pack(
                           anchor="w", padx=22, pady=(0, 10))

        def reset():
            vol.set(1.0)
            muted.set(False)
            self.user_volumes.pop(nick, None)
            self.local_mutes.discard(nick)
            self.refresh_user_cards()
            win.destroy()

        RoundBtn(win, "↺  Сбросить", self.th_card, self.th_line, reset,
                 font=(UI_FONT, 9, "bold"), py=6, px=12).pack(fill="x", padx=22, pady=(0, 5))
        RoundBtn(win, "Готово", self.th_accent, self.th_accent_grad, win.destroy,
                 font=(UI_FONT, 9, "bold"), py=6, px=12).pack(fill="x", padx=22, pady=(0, 12))

    def _start_talking_pulse(self, card):
        """Пульсирующая зелёная рамка у карточки говорящего."""
        key = "card:" + str(id(card))
        c0, c1 = self._hex_rgb(self.th_ok), self._hex_rgb("#1d4227")

        def tick(t=[0.0]):
            if not card.winfo_exists():
                self._anim_jobs.pop(key, None)
                return
            t[0] += 0.35
            k = 0.5 + 0.5 * np.sin(t[0] * 2)
            col = "#%02x%02x%02x" % tuple(int(a + (b - a) * k) for a, b in zip(c0, c1))
            try:
                card.config(highlightbackground=col)
            except Exception:
                return
            self._anim_jobs[key] = self.root.after(90, tick)
        tick()

    # ------------------------------------------------------------ статусы и пинг
    def send_my_status(self):
        self._send_control({
            "t": "status",
            "mic_muted": self.muted.get(),
            "deafened": self.deafened.get(),
            "sharing": self.is_sharing_screen,
        })

    def toggle_deafen(self):
        if self.deafened.get():
            self.deafened.set(False)
            self.deafen_btn.set_active(False)
        else:
            self.deafened.set(True)
            self.deafen_btn.set_active(True)
            for q in list(self.voice_queues.values()):
                try:
                    while True:
                        q.get_nowait()
                except queue.Empty:
                    pass
            for info in list(self.active_stream_windows.values()):
                if info.get("player"):
                    try:
                        while True:
                            info["player"].q.get_nowait()
                    except queue.Empty:
                        pass
        self.send_my_status()
        self.refresh_user_cards()

    def toggle_mute(self):
        if self.muted.get():
            self.muted.set(False)
            self.mute_btn.set_active(False)
        else:
            self.muted.set(True)
            self.mute_btn.set_active(True)
        self.send_my_status()
        self.refresh_user_cards()

    def _animate_meter(self):
        """Анимированный индикатор уровня микрофона в голосовой панели."""
        if not hasattr(self, "mic_meter") or not self.mic_meter.winfo_exists():
            return
        level = 0.0 if (self.muted.get() or self.server_muted) else min(1.0, self.mic_level / 3000.0)
        self._meter_level = getattr(self, "_meter_level", 0.0) + 0.3 * (level - getattr(self, "_meter_level", 0.0))
        w = max(1, int(self._meter_level * 90))
        color = self.th_ok if self._meter_level < 0.7 else (self.th_warn if self._meter_level < 0.9 else self.th_bad)
        self.mic_meter.itemconfig(self._meter_bar, width=90 if w < 1 else w, fill=color)
        self.mic_meter.coords(self._meter_bar, 0, 0, w, 8)
        self.root.after(80, self._animate_meter)

    def _pulse_screen_btn(self):
        """Мягкий пульс кнопки трансляции, пока идёт стрим."""
        if not self.is_sharing_screen:
            self._screen_pulse_job = None
            return
        if not (hasattr(self, "screen_btn") and self.screen_btn.winfo_exists()):
            self._screen_pulse_job = None
            return
        # Квантованный пульс (8 оттенков): не создаём сотни уникальных
        # PhotoImage и не раздуваем кэш сглаженных форм.
        self._screen_pulse_i = (getattr(self, "_screen_pulse_i", 0) + 1) % 14
        k = min(self._screen_pulse_i, 14 - self._screen_pulse_i) / 7.0
        c0, c1 = self._hex_rgb(self.th_bad), self._hex_rgb("#7e2f2f")
        col = "#%02x%02x%02x" % tuple(int(a + (b - a) * k) for a, b in zip(c0, c1))
        try:
            self.screen_btn._set_fill(col)
            if self.streamer:
                self.screen_btn.set_text(
                    f"🔴 LIVE · {int(self.streamer.actual_fps)} fps · Остановить")
        except Exception:
            pass
        self._screen_pulse_job = self.root.after(120, self._pulse_screen_btn)

    @staticmethod
    def _hex_rgb(h):
        h = h.lstrip("#")
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)]

    def _ping_stats(self):
        now = time.time()
        self.ping_history = [(t, r) for t, r in self.ping_history if now - t <= 60.0]
        vals = [r for _, r in self.ping_history]
        if not vals:
            return None
        jit = (sum(abs(vals[i] - vals[i - 1]) for i in range(1, len(vals)))
               / max(1, len(vals) - 1))
        return {"min": min(vals), "max": max(vals),
                "avg": sum(vals) / len(vals), "jitter": jit, "n": len(vals)}

    def _ping_tooltip_text(self):
        s = self._ping_stats()
        if not s:
            return "Качество соединения: нет измерений"
        return (f"Пинг: {int(s['avg'])} мс · джиттер {s['jitter']:.1f} мс\n"
                f"Нажмите — минимум/максимум за минуту")

    def _show_ping_stats(self):
        s = self._ping_stats()
        if not s:
            messagebox.showinfo("Качество соединения", "Пока нет измерений.")
            return
        messagebox.showinfo(
            "Качество соединения (последняя минута)",
            f"Минимальный пинг : {s['min']} мс\n"
            f"Максимальный пинг: {s['max']} мс\n"
            f"Средний пинг     : {s['avg']:.1f} мс\n"
            f"Джиттер          : {s['jitter']:.1f} мс\n"
            f"Измерений        : {s['n']}")

    def update_ping_ui(self, ping_val):
        self.ping_history.append((time.time(), ping_val))
        if len(self.ping_history) > 400:
            self.ping_history = self.ping_history[-200:]
        if not hasattr(self, "ping_indicator_lbl") or not self.ping_indicator_lbl.winfo_exists():
            return
        if ping_val <= 150:
            label, color = "отличная", self.th_ok
        elif ping_val <= 450:
            label, color = "средняя", self.th_warn
        else:
            label, color = "плохая", self.th_bad
        self.ping_indicator_lbl.config(
            text=f"● качество соединения: {ping_val} мс · {label}", fg=color)

    def ping_loop(self):
        seq = 0
        while self.running_audio:
            if self.is_connected and self.client_socket:
                try:
                    self.ping_seq = seq
                    self.ping_times[seq] = time.time()
                    self.client_socket.sendto(pack_control({"t": "ping", "seq": seq}), self.server_address)
                except Exception:
                    pass
                if self.last_pong and time.time() - self.last_pong > 7.0:
                    self.ui(lambda: self.update_ping_ui(999))
            seq = (seq + 1) % 65536
            time.sleep(2.0)

    # ------------------------------------------------------------ аудио (голос)
    def start_audio_streams(self):
        self.audio_gen = getattr(self, "audio_gen", 0) + 1
        gen = self.audio_gen
        t_in = threading.Thread(target=self.audio_input_loop, args=(gen,), daemon=True)
        t_out = threading.Thread(target=self.audio_output_loop, args=(gen,), daemon=True)
        self.audio_threads = [t_in, t_out]
        t_in.start()
        t_out.start()

    def restart_audio_streams(self):
        """Мягкий рестарт: НЕ ставим running_audio=False и НЕ join'им.

        stream.read() — блокирующий вызов; старый поток мог зависнуть на
        устройстве, не увидеть флаг и остаться держать микрофон. Вместо этого
        просто меняем поколение: старые потоки выходят сами при gen != audio_gen,
        а новый открывает устройство заново без гонки за него.
        """
        self.start_audio_streams()

    def _sync_audio_params(self):
        """Зеркало Tk-переменных в обычном словаре: аудио-потоки читают его,
        а не Tkinter-переменные (Tk не потокобезопасен)."""
        try:
            self._audio_params.update({
                "mic": self.selected_mic.get(),
                "speaker": self.selected_speaker.get(),
                "gate": float(self.rms_threshold.get()),
                "mode": self.noise_suppression_mode.get(),
                "volume": float(self.current_volume.get()),
                "muted": bool(self.muted.get()),
                "deafened": bool(self.deafened.get()),
                "loopback": bool(self.loopback_enabled.get()),
            })
        except Exception:
            pass
        if not self._closing:
            self.root.after(200, self._sync_audio_params)

    def _audio_notice(self, title, detail=""):
        """Видимое сообщение о проблеме аудио (в чат + консоль), вместо молчаливого сбоя."""
        text = title + (f": {detail}" if detail else "")
        print(f"[аудио] {text}")

        def show():
            if hasattr(self, "chat_display") and self.chat_display.winfo_exists():
                self.append_chat_message("⚙ система", text)
        self.ui(show)

    @staticmethod
    def _device_is_virtual(name):
        n = str(name).lower()
        return any(k in n for k in ("steam streaming", "wdm-ks", "cable",
                                    "virtual", "vb-audio", "sound mapper"))

    def _resolve_device(self, name, kind):
        """Точное совпадение; при пропаже устройства НЕ берём первое попавшееся."""
        idx = self.get_device_index(name, kind) if name else None
        if idx is not None:
            return idx
        if not name:
            return None
        try:
            devices = sd.query_devices()
            key = "max_input_channels" if kind == "in" else "max_output_channels"
            # Сначала ищем то же устройство среди НЕ-виртуальных (имена иногда
            # меняются после переподключения гарнитуры).
            for i, d in enumerate(devices):
                if d[key] < 1 or self._device_is_virtual(d["name"]):
                    continue
                if name.lower() in d["name"].lower() or d["name"].lower() in name.lower():
                    return i
        except Exception:
            pass
        now = time.time()
        if now - getattr(self, "_last_dev_notice", 0) > 15.0:
            self._last_dev_notice = now
            self._audio_notice(
                "Устройство audio недоступно",
                f"'{name}' не найдено, используется системное по умолчанию. "
                "Проверьте выбор в настройках.")
        return None

    def _device_attempts(self, kind):
        p = self._audio_params
        name = p["mic"] if kind == "in" else p["speaker"]
        dev = self._resolve_device(name, kind)
        attempts = []
        if dev is not None:
            attempts.append({"device": dev, "rate": SAMPLE_RATE, "channels": 1})
            try:
                native = int(sd.query_devices(dev)["default_samplerate"])
                if native and native != SAMPLE_RATE:
                    attempts.append({"device": dev, "rate": native, "channels": 1})
            except Exception:
                pass
        # Системный дефолт — только крайний fallback, не «рандомный Realtek».
        attempts.append({"device": None, "rate": SAMPLE_RATE, "channels": 1})
        uniq = []
        for a in attempts:
            if a not in uniq:
                uniq.append(a)
        return uniq

    def audio_input_loop(self, gen=0):
        fifo = np.zeros(0, dtype=np.float32)
        last_talking = [None]
        seq = [0]
        err_shown = False
        last_err = ""
        while self.running_audio and gen == self.audio_gen:
            attempts = self._device_attempts("in")
            stream, used = None, None
            for a in attempts:
                try:
                    stream = sd.InputStream(device=a["device"], samplerate=a["rate"],
                                            channels=a["channels"], dtype="int16",
                                            blocksize=0)
                    stream.start()
                    used = a
                    break
                except Exception as e:
                    last_err = str(e)
                    print(f"Микрофон не открылся ({a['device']}, {a['rate']} Hz): {e}")
            if stream is None:
                if not err_shown:
                    err_shown = True
                    self._audio_notice("Микрофон не смог открыться", last_err)
                time.sleep(3.0)
                continue
            err_shown = False
            resamp = Resampler(used["rate"], SAMPLE_RATE) if used["rate"] != SAMPLE_RATE else None
            frames_to_read = max(1, int(used["rate"] * 0.02))
            used_name = self._audio_params.get("mic")
            try:
                while self.running_audio and gen == self.audio_gen:
                    # Пользователь сменил микрофон — переоткрываем без рестарта потока.
                    if self._audio_params.get("mic") != used_name:
                        break
                    data, ovf = stream.read(frames_to_read)
                    if ovf:
                        pass
                    if data.size == 0:
                        continue
                    ch = data.shape[1] if data.ndim > 1 else 1
                    mono = data[:, :ch].astype(np.float32).mean(axis=1)
                    if resamp is not None:
                        mono = resamp.process(
                            np.clip(mono, -32768, 32767).astype(np.int16)).astype(np.float32)
                    if mono.size:
                        fifo = np.concatenate([fifo, mono])
                    while len(fifo) >= BLOCKSIZE:
                        block = fifo[:BLOCKSIZE]
                        fifo = fifo[BLOCKSIZE:]
                        self._process_mic_block(block, seq, last_talking)
            except Exception as e:
                print(f"Микрофон: сбой потока: {e}")
            finally:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            time.sleep(0.2)

    def _process_mic_block(self, block_f, seq, last_talking):
        p = self._audio_params
        rms = float(np.sqrt(np.mean(block_f ** 2))) if block_f.size else 0.0
        self.mic_level = rms
        thr = max(1.0, p["gate"])
        mode = p["mode"]
        talking = rms >= thr
        if talking != last_talking[0]:
            last_talking[0] = talking
            self._send_control({"t": "talking", "on": talking})

        clean = self.dsp.process(np.clip(block_f, -32768, 32767).astype(np.int16), mode, thr)
        if p["muted"] or self.server_muted:
            return
        # полностью тишина (гейт закрыт) — не грузим сеть
        if not talking and int(np.max(np.abs(clean.astype(np.int32)))) < 8:
            return
        clean = np.clip(clean.astype(np.float32) * p["volume"], -32768, 32767).astype(np.int16)
        if self.is_connected and self.client_socket:
            packet = pack_audio(seq[0], clean)
            seq[0] = (seq[0] + 1) % 65536
            try:
                self.client_socket.sendto(packet, self.server_address)
            except Exception:
                pass
        if p["loopback"]:
            self._push_voice("__me__", clean)

    def audio_output_loop(self, gen=0):
        while self.running_audio and gen == self.audio_gen:
            attempts = self._device_attempts("out")
            stream, used = None, None
            last_err = ""
            for a in attempts:
                try:
                    stream = sd.OutputStream(device=a["device"], samplerate=a["rate"],
                                             channels=1, dtype="int16",
                                             blocksize=max(1, int(a["rate"] * 0.02)))
                    stream.start()
                    used = a
                    break
                except Exception as e:
                    last_err = str(e)
                    print(f"Динамики не открылись ({a['device']}, {a['rate']} Hz): {e}")
            if stream is None:
                self._audio_notice("Динамики не смогли открыться", last_err)
                time.sleep(3.0)
                continue
            resamp = Resampler(SAMPLE_RATE, used["rate"]) if used["rate"] != SAMPLE_RATE else None
            dev_block_size = max(1, int(used["rate"] * 0.02))
            used_name = self._audio_params.get("speaker")
            try:
                while self.running_audio and gen == self.audio_gen:
                    if self._audio_params.get("speaker") != used_name:
                        break
                    acc = self._mix_jitter()
                    if acc is None:
                        dev_block = np.zeros(dev_block_size, dtype=np.int16)
                    elif resamp is not None:
                        dev_block = resamp.process(np.clip(acc, -32768, 32767).astype(np.int16))
                        if len(dev_block) < dev_block_size:
                            dev_block = np.concatenate(
                                [dev_block, np.zeros(dev_block_size - len(dev_block), dtype=np.int16)])
                        elif len(dev_block) > dev_block_size:
                            dev_block = dev_block[:dev_block_size]
                    else:
                        dev_block = np.clip(acc, -32768, 32767).astype(np.int16)
                    try:
                        stream.write(dev_block)
                    except Exception as e:
                        print(f"Динамики: сбой записи: {e}")
                        break
            finally:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            time.sleep(0.2)

    def _push_voice(self, nick, pcm):
        """Кладёт блок в очередь КОНКРЕТНОГО говорящего."""
        q = self.voice_queues.get(nick)
        if q is None:
            q = self.voice_queues[nick] = queue.Queue(maxsize=8)
        if q.full():
            try:
                q.get_nowait()   # выбрасываем самый старый, не растём в задержке
            except queue.Empty:
                pass
        try:
            q.put_nowait(pcm)
        except queue.Full:
            pass

    def _mix_jitter(self):
        """Микширует по ОДНОМУ 20-мс блоку от каждого говорящего за такт.

        Раздельные очереди решают две проблемы: последовательные пакеты
        одного игрока больше не складываются (не ускоряют речь), а громкость
        и локальный мут применяются персонально.
        """
        qs = self.voice_queues
        if self._audio_params["deafened"]:
            for q in qs.values():
                try:
                    while True:
                        q.get_nowait()
                except queue.Empty:
                    pass
            self._jitter_primed = False
            return None

        if not getattr(self, "_jitter_primed", False):
            if not any(q.qsize() >= 2 for q in qs.values()):
                return None
            self._jitter_primed = True

        acc = np.zeros((BLOCKSIZE,), dtype=np.int32)
        got_any = False
        stale = []
        for nick, q in list(qs.items()):
            try:
                f = q.get_nowait()
            except queue.Empty:
                self._voice_idle[nick] = self._voice_idle.get(nick, 0) + 1
                if self._voice_idle[nick] > 50:
                    stale.append(nick)
                continue
            self._voice_idle[nick] = 0
            got_any = True
            if nick in self.local_mutes:
                continue
            vol = float(self.user_volumes.get(nick, 1.0))
            if vol <= 0.0:
                continue
            f = np.asarray(f, dtype=np.int16)
            if len(f) < BLOCKSIZE:
                f = np.pad(f, (0, BLOCKSIZE - len(f)))
            acc += np.clip(f[:BLOCKSIZE].astype(np.float32) * vol,
                           -32768, 32767).astype(np.int32)
        for nick in stale:
            qs.pop(nick, None)
            self._voice_idle.pop(nick, None)

        if not got_any:
            self._jitter_primed = False
            return None
        return np.clip(acc, -32768, 32767).astype(np.int16)

    def get_device_index(self, name, kind):
        try:
            devices = sd.query_devices()
            for i, d in enumerate(devices):
                if d["name"] == name:
                    if kind == "in" and d["max_input_channels"] > 0:
                        return i
                    if kind == "out" and d["max_output_channels"] > 0:
                        return i
        except Exception:
            pass
        return None

    def on_device_changed(self):
        self.save_config()
        try:
            self._audio_params["mic"] = self.selected_mic.get()
            self._audio_params["speaker"] = self.selected_speaker.get()
        except Exception:
            pass
        self.restart_audio_streams()

    # ------------------------------------------------------------ подключение
    def start_connecting(self):
        if self.is_connected:
            return
        nick = (self.nickname.get() or "").strip()[:32] or "User"
        ip = (self.server_ip.get() or "").strip()
        try:
            port = int(self.server_port.get())
        except ValueError:
            messagebox.showerror("Ошибка", "Порт должен быть числом!")
            return
        self.save_config()
        self.connect_btn.set_colors(self.th_subtext)
        self.set_status("Подключение...", self.th_warn)
        threading.Thread(target=self._connect_worker,
                         args=(nick, ip, port, self.account_password.get(),
                               self.server_password.get()), daemon=True).start()

    def _prompt_async(self, title, prompt, show="•"):
        ev = threading.Event()
        box = {"value": None}

        def ask():
            box["value"] = simpledialog.askstring(title, prompt, show=show, parent=self.root)
            ev.set()

        self.ui(ask)
        ev.wait(120)
        return box["value"]

    def _connect_worker(self, nick, ip, port, acc_pass, srv_pass):
        chan_pass = ""
        for attempt in range(6):
            s = None
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.bind(("", 0))
                s.settimeout(2.0)
                s.sendto(pack_control({"t": "auth", "nick": nick, "acc_pass": acc_pass,
                                       "chan": "друнспик 1", "pass": chan_pass,
                                       "server_pass": srv_pass}), (ip, port))
                result = None
                deadline = time.time() + 3.0
                while time.time() < deadline and result is None:
                    try:
                        data, _ = s.recvfrom(65535)
                    except socket.timeout:
                        break
                    msg = parse_control(data)
                    if not msg:
                        continue
                    t = msg.get("t")
                    if t in ("ack", "wrong_pass", "bad_server_pass", "nick_taken", "banned",
                             "need_account_pass", "wrong_account_pass", "account_banned", "auth_error"):
                        result = (t, msg)
                if result is None:
                    raise TimeoutError("сервер не отвечает")
                t, msg = result
                if t == "ack":
                    s.settimeout(None)
                    self.ui(lambda sk=s: self._finish_connect(sk, ip, port))
                    return
                if t in ("banned", "account_banned"):
                    text = "Вы забанены на этом сервере." if t == "banned" else "Ваш аккаунт забанен на сервере."
                    self._connect_fail(text)
                    s.close()
                    return
                if t == "auth_error":
                    self._connect_fail(str(msg.get("reason", "Отклонено сервером.")))
                    s.close()
                    return
                if t == "wrong_pass":
                    chan_pass = self._prompt_async("Пароль канала", "Введите пароль канала:") or ""
                elif t == "bad_server_pass":
                    srv_pass = self._prompt_async("Пароль сервера", "Введите пароль сервера:") or ""
                    self.ui(lambda p=srv_pass: (self.server_password.set(p), self.save_config()))
                elif t == "need_account_pass":
                    acc_pass = self._prompt_async(
                        "Регистрация",
                        f"Аккаунта '{nick}' нет.\nПридумайте пароль (мин. 4 символа), чтобы создать его:") or ""
                    self.ui(lambda p=acc_pass: (self.account_password.set(p), self.save_config()))
                elif t == "wrong_account_pass":
                    acc_pass = self._prompt_async(
                        "Пароль аккаунта", f"Неверный пароль аккаунта '{nick}'.\nВведите пароль:") or ""
                    self.ui(lambda p=acc_pass: (self.account_password.set(p), self.save_config()))
                elif t == "nick_taken":
                    self._connect_fail("Этот аккаунт уже в сети с другого устройства.")
                    s.close()
                    return
                s.close()
            except Exception as e:
                if s:
                    try:
                        s.close()
                    except Exception:
                        pass
                self._connect_fail(f"Не удалось подключиться:\n{e}")
                return
        self._connect_fail("Сервер не отвечает или данные неверны.")

    def _connect_fail(self, text):
        def fail():
            self.set_status("Ошибка подключения", self.th_bad)
            self.connect_btn.set_colors(self.th_accent)
            messagebox.showerror("Ошибка", text)
        self.ui(fail)

    def _finish_connect(self, sock, ip, port):
        self.client_socket = sock
        self.server_address = (ip, port)
        self.is_connected = True
        self.server_muted = False
        self.current_channel.set("друнспик 1")
        self.remote_users = []
        self.avatars_data = {}
        self._avatar_parts = {}
        self.file_send_cache = {}
        self.file_recv_state = {}
        self.dsp.reset()
        self._jitter_primed = False
        self.voice_queues = {}
        self._voice_idle = {}
        try:
            while True:
                self.jitter_queue.get_nowait()
        except queue.Empty:
            pass
        self.create_room_frame()
        self.send_my_status()
        if self.avatar_b64:
            self._send_avatar(self.avatar_b64)
        threading.Thread(target=self.receive_network_loop, daemon=True).start()
        threading.Thread(target=self.img_monitor_loop, daemon=True).start()
        threading.Thread(target=self.video_nack_loop, daemon=True).start()

    def disconnect_server(self):
        try:
            if hasattr(self, "disconnect_btn") and self.disconnect_btn.winfo_exists():
                self.disconnect_btn.flash()
        except Exception:
            pass
        if self.is_connected:
            self._send_control({"t": "disconnect"})
        self.is_connected = False
        self.server_muted = False
        self._stop_sharing_internal()
        self.remote_streams.clear()
        self._video_recv.clear()
        self._last_shown_fid.clear()
        self.file_send_cache = {}
        self.file_recv_state = {}
        self.voice_queues = {}
        self._voice_idle = {}
        self.ui(self._close_all_stream_windows)
        self.ui(self._back_to_login)

    def _back_to_login(self):
        if not self.is_connected:
            self.create_login_frame()
            self.connect_btn.set_colors(self.th_accent)

    def _close_all_stream_windows(self):
        for nick in list(self.active_stream_windows.keys()):
            self.close_remote_stream(nick)

    def _on_kicked(self, reason):
        self.ui(lambda r=reason: (self.disconnect_server(),
                                  messagebox.showwarning("Внимание", r or "Вас отключили от сервера.")))

    # ------------------------------------------------------------ приём
    def _send_control(self, obj):
        if self.is_connected and self.client_socket:
            try:
                self.client_socket.sendto(pack_control(obj), self.server_address)
                return True
            except Exception as e:
                print(f"Ошибка отправки: {e}")
        return False

    def _send_stream_video(self, fid, total, idx, chunk):
        if self.is_connected and self.client_socket:
            try:
                self.client_socket.sendto(pack_video_chunk(fid, total, idx, chunk), self.server_address)
            except Exception:
                pass

    def _send_stream_audio(self, seq, pcm):
        if self.is_connected and self.client_socket:
            try:
                self.client_socket.sendto(pack_stream_audio(seq, pcm), self.server_address)
            except Exception:
                pass

    def _send_stream_audio2(self, session, seq, pcm):
        if self.is_connected and self.client_socket:
            try:
                self.client_socket.sendto(pack_stream_audio2(session, seq, pcm),
                                          self.server_address)
            except Exception:
                pass

    def receive_network_loop(self):
        while self.is_connected:
            try:
                sock = self.client_socket
                if not sock:
                    break
                data, _ = sock.recvfrom(65535)
                if not data:
                    continue
                tag = data[0:1]
                if tag == b"J":
                    msg = parse_control(data)
                    if msg:
                        self.handle_server_control(msg)
                elif tag == b"a":
                    p = parse_voice_packet(data)
                    if p:
                        nick, payload = p
                        if len(payload) >= 2 and len(payload) % 2 == 0:
                            self._push_voice(nick, np.frombuffer(payload, dtype=np.int16))
                elif tag == b"A":
                    payload = data[3:]
                    if len(payload) >= 2 and len(payload) % 2 == 0:
                        self._push_voice("", np.frombuffer(payload, dtype=np.int16))
                elif tag == b"I":
                    self._handle_img_chunk(data)
                elif tag == b"F":
                    self._handle_file_chunk(data)
                elif tag == b"V":
                    self._handle_stream_video(data)
                elif tag == b"S":
                    self._handle_stream_audio(data)
            except OSError:
                break
            except Exception as e:
                if not self.is_connected:
                    break
                print(f"Ошибка приёма: {e}")
                time.sleep(0.05)

    def _handle_stream_video(self, data):
        parsed = parse_stream_packet(data)
        if not parsed:
            return
        tag, nick, rest = parsed
        vc = parse_video_chunk(rest)
        if not vc:
            return
        fid, total, idx, payload = vc
        # Маркер «кадр не изменился»: дублируем предыдущий без трафика.
        if total == 0:
            if nick in self.remote_streams:
                self._last_shown_fid[nick] = fid
                winfo = self.active_stream_windows.get(nick)
                if winfo:
                    winfo["frames"] = winfo.get("frames", 0) + 1
            return
        if total < 1 or total > 2048 or len(payload) > STREAM_CHUNK:
            return
        now = time.time()
        # Буфер по fid: чанк соседнего кадра больше не сбрасывает сборщик.
        buf = self._video_recv.get(nick)
        if buf is None:
            buf = self._video_recv[nick] = {}
        st = buf.get(fid)
        if st is None:
            if len(buf) >= 5:
                for old in sorted(buf, key=lambda f: buf[f]["t"])[:len(buf) - 4]:
                    buf.pop(old, None)
            st = buf[fid] = {"total": total, "chunks": {}, "parity": {},
                             "t": now, "nacks": 0}
        if st["total"] != total:
            return
        st["t"] = now
        if idx >= total:
            g = idx - total              # XOR-паритет группы g
            st["parity"][g] = payload
            self._recover_fec(st, g)
        else:
            st["chunks"][idx] = payload
            self._recover_fec(st, idx // STREAM_FEC_GROUP)
        if len(st["chunks"]) == total:
            buf.pop(fid, None)
            try:
                img = Image.open(io.BytesIO(
                    b"".join(st["chunks"][i] for i in range(total))))
                arr = np.asarray(img.convert("RGB"))
                if fid >= self._last_shown_fid.get(nick, -1):
                    self._last_shown_fid[nick] = fid
                    self.remote_streams[nick] = arr
                    winfo = self.active_stream_windows.get(nick)
                    if winfo:
                        winfo["frames"] = winfo.get("frames", 0) + 1
            except Exception:
                pass

    @staticmethod
    def _recover_fec(st, g):
        """Восстанавливает один потерянный чанк группы без запроса к серверу."""
        p = st["parity"].get(g)
        chunks, total = st["chunks"], st["total"]
        if not p:
            return
        lo = g * STREAM_FEC_GROUP
        hi = min(total, lo + STREAM_FEC_GROUP)
        missing = [i for i in range(lo, hi) if i not in chunks]
        if len(missing) != 1:
            return
        acc = np.frombuffer(p.ljust(STREAM_CHUNK, b"\x00"),
                            dtype=np.uint8).copy()
        for i in range(lo, hi):
            if i in chunks:
                np.bitwise_xor(
                    acc,
                    np.frombuffer(chunks[i].ljust(STREAM_CHUNK, b"\x00"),
                                  dtype=np.uint8), out=acc)
        chunks[missing[0]] = acc.tobytes()

    def _handle_stream_audio(self, data):
        parsed = parse_stream_packet(data)
        if not parsed:
            return
        tag, nick, rest = parsed
        if tag == b"s":
            sa2 = parse_stream_audio2(rest)
            if not sa2:
                return
            session, seq, payload = sa2
        else:
            sa = parse_stream_audio(rest)
            if not sa:
                return
            session, seq, payload = 0, sa[0], sa[1]
        if len(payload) < 2 or len(payload) % 2 != 0:
            return
        pcm = np.frombuffer(payload, dtype=np.int16)
        info = self.active_stream_windows.get(nick)
        if info and info.get("player"):
            info["player"].push(seq, pcm, session=session)

    def handle_server_control(self, msg):
        t = msg.get("t")
        if t == "userlist":
            self.remote_users = msg.get("users", [])
            channels = msg.get("channels", {"друнспик 1": {"has_password": False}})
            self.available_channels_data = channels
            my_nick = self.nickname.get() or "User"
            for u in self.remote_users:
                if u.get("name") == my_nick:
                    s_chan = u.get("channel", "друнспик 1")
                    if s_chan != self.current_channel.get():
                        self.current_channel.set(s_chan)
                        self.ui(self.redraw_chat_history)
            self.ui(self.refresh_user_cards)
            sig = json.dumps(channels, ensure_ascii=False) + "|" + self.current_channel.get()
            if sig != self._channels_sig:
                self._channels_sig = sig
                self.ui(self._refresh_channel_ui_safe)
        elif t == "chat":
            sender = str(msg.get("from", "?"))
            if sender != (self.nickname.get() or "User"):
                text = str(msg.get("text", ""))
                chan = str(msg.get("channel", "") or "")
                self.ui(lambda s=sender, x=text, ch=chan:
                        self.append_chat_message(s, x, play_sound=True, channel=ch))
        elif t == "channel_ok":
            self.ui(lambda n=msg.get("chan", ""): self.append_chat_message(
                "⚙ система", f"Канал '{n}' создан."))
        elif t == "channel_error":
            self.ui(lambda r=msg.get("reason", ""): messagebox.showwarning(
                "Канал", r or "Не удалось создать канал"))
        elif t == "chan_ok":
            self.ui(self.redraw_chat_history)
        elif t == "video_nack":
            if self.streamer:
                self.streamer.resend_video_chunks(
                    msg.get("fid", 0), msg.get("missing", []))
        elif t == "moved":
            chan = str(msg.get("chan", ""))
            if chan:
                self.ui(lambda ch=chan: (
                    self.current_channel.set(ch), self.redraw_chat_history(),
                    self.refresh_user_cards()))
        elif t == "kicked":
            self._on_kicked(str(msg.get("reason", "Вас кикнули с сервера!")))
        elif t == "banned":
            self._on_kicked("Вы забанены на сервере.")
        elif t == "account_banned":
            self._on_kicked("Ваш аккаунт забанен на сервере.")
        elif t == "wrong_pass":
            self.ui(lambda c=msg.get("chan", ""): messagebox.showerror(
                "Ошибка", f"Неверный пароль канала '{c}'!"))
        elif t == "mute":
            self.server_muted = True
            self.send_my_status()
            self.ui(self.refresh_user_cards)
        elif t == "unmute":
            self.server_muted = False
            self.send_my_status()
            self.ui(self.refresh_user_cards)
        elif t == "pong":
            seq = msg.get("seq")
            sent = self.ping_times.pop(seq, None)
            if sent is not None:
                rtt = max(1, int((time.time() - sent) * 1000))
                self.current_ping = rtt
                self.last_pong = time.time()
                self.ui(lambda r=rtt: self.update_ping_ui(r))
        elif t == "stream_end":
            self._on_stream_end(str(msg.get("from", "")))
        elif t == "avatars":
            # стартовый пакет аватарок: {nick: b64}
            for nick, b64 in dict(msg.get("avatars", {})).items():
                if isinstance(b64, str) and len(b64) <= AVATAR_B64_LIMIT:
                    self.avatars_data[nick] = b64
            self.ui(self.refresh_user_cards)
        elif t == "avatar":
            nick = str(msg.get("nick", ""))
            b64 = str(msg.get("b64", ""))
            if nick and len(b64) <= AVATAR_B64_LIMIT:
                if b64:
                    self.avatars_data[nick] = b64
                else:
                    self.avatars_data.pop(nick, None)
                self.ui(self.refresh_user_cards)
        elif t == "avatar_start":
            self._handle_avatar_start(msg)
        elif t == "avatar_part":
            self._handle_avatar_part(msg)
        elif t == "avatar_clear":
            nick = str(msg.get("nick", ""))
            if nick:
                self.avatars_data.pop(nick, None)
                if nick == (self.nickname.get() or "User"):
                    self.avatar_b64 = ""
                self.ui(self.refresh_user_cards)
        elif t in ("img_ack", "img_nack"):
            self._handle_img_feedback(msg)
        elif t in ("file_ack", "file_nack"):
            self._handle_file_feedback(msg)
        elif t in ("ack", "ping", "disconnect", "auth_error", "chan_removed",
                   "need_account_pass", "wrong_account_pass", "nick_taken"):
            pass

    # ------------------------------------------------------------ трансляция экрана
    def _refresh_channel_ui_safe(self):
        if hasattr(self, "chan_scroll_frame") and self.chan_scroll_frame.winfo_exists():
            if hasattr(self, "header_title") and self.header_title.winfo_exists():
                self.header_title.config(text=f"# {self.current_channel.get()}")
            if hasattr(self, "current_chan_lbl") and self.current_chan_lbl.winfo_exists():
                self.current_chan_lbl.config(text=f"🔊 {self.current_channel.get()}")
            self.rebuild_channel_list_ui()

    def _audio_error_dialog(self, errors=None):
        def ask():
            detail = "\n".join(f"• {e}" for e in (errors or [])) or "• нет данных"
            messagebox.showwarning(
                "Звук трансляции",
                "Не удалось включить захват звука системы. Видео трансляции "
                "продолжает работать.\n\n"
                f"Проверенные способы:\n{detail}\n\n"
                "Что можно сделать:\n"
                "1) В Windows: «Параметры → Звук → Устройства ввода» включите "
                "«Стерео микшер».\n"
                "2) Убедитесь, что на компьютере сейчас что-то воспроизводится "
                "(WASAPI loopback не даёт данные в полной тишине).\n"
                "3) Остановите и снова включите трансляцию — захват "
                "перезапустится.\n\n"
                "Обновление sounddevice здесь обычно не требуется: захват идёт "
                "напрямую через WASAPI.")
        self.ui(ask)

    def _upgrade_sounddevice(self):
        """Обновляет sounddevice синхронно и показывает результат (не молча)."""
        win = tk.Toplevel(self.root)
        win.title("Обновление sounddevice")
        win.geometry("520x300")
        win.config(bg=self.th_bg)
        win.grab_set()
        tk.Label(win, text="Выполняется: pip install -U sounddevice", bg=self.th_bg,
                 fg=self.th_fg, font=(UI_FONT, 10, "bold")).pack(pady=(12, 4))
        log = scrolledtext.ScrolledText(win, bg=self.th_input, fg=self.th_fg,
                                        font=("Consolas", 8), bd=0)
        log.pack(fill="both", expand=True, padx=12, pady=6)

        def worker():
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-U", "sounddevice"],
                    capture_output=True, text=True, timeout=240,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                out = (proc.stdout or "") + "\n" + (proc.stderr or "")

                def show():
                    log.insert(tk.END, out.strip() or "(пустой вывод pip)")
                    log.see(tk.END)
                    if proc.returncode == 0:
                        tk.Label(win, text="Готово! Перезапустить приложение?",
                                 bg=self.th_bg, fg=self.th_ok,
                                 font=(UI_FONT, 10, "bold")).pack(pady=4)
                        if messagebox.askyesno("Обновление", "sounddevice обновлён.\nПерезапустить приложение сейчас?"):
                            self.on_close()
                    else:
                        tk.Label(win, text=f"Не удалось (код {proc.returncode}).",
                                 bg=self.th_bg, fg=self.th_bad,
                                 font=(UI_FONT, 10, "bold")).pack(pady=4)
                self.ui(show)
            except Exception as e:
                self.ui(lambda: messagebox.showerror(
                    "Ошибка", f"Не удалось запустить установку:\n{e}\n\n"
                              f"Выполните вручную:\n{sys.executable} -m pip install -U sounddevice"))
        threading.Thread(target=worker, daemon=True).start()

    def _toggle_stream_audio(self):
        """Тумблер захвата звука системы — работает и во время трансляции."""
        new = not self.stream_audio_enabled.get()
        self.stream_audio_enabled.set(new)
        try:
            self.stream_audio_btn.set_active(not new)
        except Exception:
            pass
        self.save_config()

    def _apply_stream_volume(self):
        """Меняет громкость во всех открытых окнах просмотра."""
        self.save_config()
        try:
            base = float(self.stream_volume.get())
        except Exception:
            base = 1.0
        for info in list(self.active_stream_windows.values()):
            p = info.get("player")
            if p:
                p.set_volume(base * float(info.get("local_volume", 1.0)))

    def prompt_screen_share(self):
        if self.is_sharing_screen:
            self._stop_sharing()
            self.is_sharing_screen = False
            self.ui(lambda: self.screen_btn.set_text("📺  Трансляция экрана"))
            self.send_my_status()
            self.refresh_user_cards()
            return
        if not SCREENSHARE_AVAILABLE:
            missing = []
            if not MSS_AVAILABLE:
                missing.append("mss")
            if not PIL_AVAILABLE:
                missing.append("pillow")
            messagebox.showerror("Ошибка",
                                 "Для трансляции экрана нужны библиотеки: " + ", ".join(missing) +
                                 "\nУстановите: pip install " + " ".join(missing))
            return
        self.is_sharing_screen = True
        self.ui(lambda: self.screen_btn.set_text("🔴 LIVE · Остановить"))
        self.send_my_status()
        self.refresh_user_cards()
        self.streamer = ScreenStreamer(self, on_audio_error=self._audio_error_dialog)
        self.streamer.start()
        self._pulse_screen_btn()

    def _stop_sharing_internal(self):
        self.is_sharing_screen = False
        if self.streamer:
            self.streamer.stop()
            self.streamer = None

    def _stop_sharing(self):
        self._stop_sharing_internal()
        self._send_control({"t": "stream_end"})

    def _on_stream_end(self, nick):
        self.remote_streams.pop(nick, None)
        self._video_recv.pop(nick, None)
        self._last_shown_fid.pop(nick, None)
        self.ui(lambda n=nick: self.close_remote_stream(n))

    def open_remote_stream_window(self, nick):
        info = self.active_stream_windows.get(nick)
        if info and info["win"].winfo_exists():
            info["win"].lift()
            return
        win = tk.Toplevel(self.root)
        win.title(f"Трансляция: {nick}")
        win.geometry("960x620")
        win.config(bg="#0a0a0a")
        top = tk.Frame(win, bg="#111217", height=46)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text=f"🔴 {nick} — трансляция экрана", bg="#111217", fg="#ffffff",
                 font=(UI_FONT, 9, "bold")).pack(side="left", padx=10)
        fps_lbl = tk.Label(top, text="— fps", bg="#111217", fg="#8f959e",
                           font=(UI_FONT, 8, "bold"))
        fps_lbl.pack(side="right", padx=10)

        player = StreamAudioPlayer(self, nick)
        vol_var = tk.DoubleVar(value=float(self.stream_volume.get()))
        audio_var = tk.BooleanVar(value=True)

        def on_volume(v=None):
            try:
                player.set_volume(float(vol_var.get()))
            except Exception:
                pass

        def on_audio_toggle():
            player.set_enabled(audio_var.get())
            snd_btn.set_active(not audio_var.get())

        snd_btn = IconBtn(top, "icon_sound_on", on_audio_toggle, size=30,
                          active_icon="icon_sound_off", bg="#2a2b31", hover="#3a3d45",
                          tint="#ffffff", active_bg="#b0413e", active_hover="#c25552",
                          active_tint="#ffffff", tooltip="Звук трансляции вкл/выкл")
        snd_btn.pack(side="right", padx=(6, 0))
        tk.Scale(top, from_=0.0, to=2.0, resolution=0.05, orient="horizontal",
                 variable=vol_var, length=130, width=12, sliderlength=14,
                 bg="#111217", fg="#ffffff", troughcolor="#2a2b31", bd=0,
                 highlightthickness=0, activebackground=self.th_accent,
                 command=on_volume).pack(side="right", padx=(6, 0))
        tk.Label(top, text="громкость", bg="#111217", fg="#8f959e",
                 font=(UI_FONT, 7)).pack(side="right")

        player.set_enabled(audio_var.get())
        player.set_volume(vol_var.get())
        player.start()
        label = tk.Label(win, bg="#0a0a0a")
        label.pack(fill="both", expand=True)
        info = {"win": win, "label": label, "player": player, "fps_lbl": fps_lbl,
                "frames": 0, "fps_last": 0, "fps_t": time.time(),
                "local_volume": 1.0}
        self.active_stream_windows[nick] = info

        def on_close():
            player.stop()
            self.active_stream_windows.pop(nick, None)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        self._blit_stream(nick, label)

    def close_remote_stream(self, nick):
        info = self.active_stream_windows.pop(nick, None)
        if info:
            try:
                if info["player"]:
                    info["player"].stop()
                info["win"].destroy()
            except Exception:
                pass

    def _blit_stream(self, nick, label):
        info = self.active_stream_windows.get(nick)
        if not info or not info["win"].winfo_exists():
            return
        now = time.time()
        if now - info.get("fps_t", 0) >= 1.0:
            f = info.get("frames", 0)
            try:
                info["fps_lbl"].config(
                    text=f"{max(0, f - info.get('fps_last', 0))} fps")
            except Exception:
                pass
            info["fps_last"], info["fps_t"] = f, now
        # Перерисовываем ТОЛЬКО когда пришёл новый кадр. Раньше 60 раз/с
        # делали resize+PhotoImage даже на статичном изображении.
        if info.get("frames", 0) == info.get("last_blit_frame", -1):
            label.after(16, lambda: self._blit_stream(nick, label))
            return
        info["last_blit_frame"] = info.get("frames", 0)
        frame = self.remote_streams.get(nick)
        if frame is not None and frame.size > 0:
            try:
                w = max(label.winfo_width(), 50)
                h = max(label.winfo_height(), 50)
                img = Image.fromarray(frame).resize((w, h), Image.BILINEAR)
                photo = ImageTk.PhotoImage(img)
                label.config(image=photo)
                label.image = photo
            except Exception:
                pass
        label.after(16, lambda: self._blit_stream(nick, label))

    # ------------------------------------------------------------ настройки
    def play_test_sound(self):
        """Короткий сигнал через выбранный динамик — проверка, что звук работает."""
        def worker():
            try:
                spk = self._audio_params.get("speaker", "")
                dev = self.get_device_index(spk, "out") if spk else None
                t = np.linspace(0, 0.7, int(16000 * 0.7), endpoint=False)
                tone = (np.sin(2 * np.pi * 660 * t) * 6000).astype(np.int16)
                fade = (t / 0.7)
                tone = (tone * fade).astype(np.int16)
                if dev is not None:
                    try:
                        stream = sd.OutputStream(device=dev, samplerate=16000, channels=1, dtype="int16")
                    except Exception:
                        stream = sd.OutputStream(samplerate=16000, channels=1, dtype="int16")
                else:
                    stream = sd.OutputStream(samplerate=16000, channels=1, dtype="int16")
                stream.start()
                stream.write(tone)
                stream.stop()
                stream.close()
            except Exception as e:
                self._audio_notice("Тест звука не удался", str(e))
        threading.Thread(target=worker, daemon=True).start()

    def change_theme_live(self):
        self.save_config()
        if self.is_connected:
            self.create_room_frame()
        else:
            self.create_login_frame()

    def open_settings_window(self):
        win = tk.Toplevel(self.root)
        win.title("Настройки друнспика")
        win.geometry("440x780")
        win.config(bg=self.th_bg)
        win.resizable(False, False)
        win.grab_set()

        inner = tk.Frame(win, bg=self.th_bg)
        inner.pack(fill="both", expand=True)
        tk.Label(inner, text="НАСТРОЙКИ", bg=self.th_bg, fg=self.th_fg,
                 font=(UI_FONT, 13, "bold")).pack(pady=(15, 10))

        tk.Label(inner, text="Тема оформления", bg=self.th_bg, fg=self.th_subtext,
                 font=(UI_FONT, 9, "bold"), anchor="w").pack(fill="x", padx=25)
        theme_combo = ttk.Combobox(inner, textvariable=self.current_theme, values=list(THEMES.keys()),
                                   state="readonly", width=40)
        theme_combo.pack(padx=25, pady=(4, 6))
        theme_combo.bind("<<ComboboxSelected>>", lambda e: self.change_theme_live())
        theme_row = tk.Frame(inner, bg=self.th_card, cursor="hand2")
        theme_row.pack(fill="x", padx=25, pady=(0, 10))
        theme_ic = IconBtn(theme_row, "icon_theme", self.create_custom_theme_dialog,
                           size=26, bg=self.th_card, hover=self.th_accent,
                           tint=self.th_fg)
        theme_ic.pack(side="left", padx=(6, 4), pady=4)
        theme_lbl = tk.Label(theme_row,
                             text="Своя тема: фото + 3 цвета + предпросмотр",
                             bg=self.th_card, fg=self.th_fg,
                             font=(UI_FONT, 8, "bold"), cursor="hand2")
        theme_lbl.pack(side="left", fill="x", expand=True)
        theme_lbl.bind("<Button-1>", lambda e: self.create_custom_theme_dialog())

        tk.Label(inner, text="Система шумоподавления", bg=self.th_bg, fg=self.th_subtext,
                 font=(UI_FONT, 9, "bold"), anchor="w").pack(fill="x", padx=25)
        ns_combo = ttk.Combobox(inner, textvariable=self.noise_suppression_mode,
                                values=NoiseSuppressor.MODES,
                                state="readonly", width=40)
        ns_combo.pack(padx=25, pady=(4, 10))
        ns_combo.bind("<<ComboboxSelected>>", lambda e: (self.dsp.reset(), self.save_config()))

        # проверка звука: тест динамика + живой уровень микрофона
        check_row = tk.Frame(inner, bg=self.th_bg)
        check_row.pack(fill="x", padx=25, pady=(0, 10))
        RoundBtn(check_row, "🔊  Тест звука", self.th_card, self.th_accent,
                 self.play_test_sound, font=(UI_FONT, 9, "bold"), py=6, px=12).pack(side="left")
        meter = tk.Canvas(check_row, width=120, height=12, bg=self.th_input, highlightthickness=1,
                          highlightbackground=self.th_line)
        meter.pack(side="left", padx=(10, 0))
        meter_bar = meter.create_rectangle(0, 1, 0, 11, fill=self.th_ok, width=0)
        tk.Label(inner, text="Уровень микрофона (говорите — полоска двигается)",
                 bg=self.th_bg, fg=self.th_subtext, font=(UI_FONT, 7), anchor="w").pack(fill="x", padx=25)

        def tick_meter():
            if not meter.winfo_exists():
                return
            lvl = min(1.0, self.mic_level / 3000.0)
            color = self.th_ok if lvl < 0.7 else (self.th_warn if lvl < 0.9 else self.th_bad)
            meter.itemconfig(meter_bar, fill=color, width=max(1, int(lvl * 118)))
            meter.coords(meter_bar, 1, 1, 1 + lvl * 118, 11)
            win.after(90, tick_meter)
        tick_meter()

        tk.Label(inner, text="Микрофон", bg=self.th_bg, fg=self.th_subtext,
                 font=(UI_FONT, 9, "bold"), anchor="w").pack(fill="x", padx=25)
        ttk.Combobox(inner, textvariable=self.selected_mic, values=self.mic_list,
                     state="readonly", width=40).pack(padx=25, pady=(4, 10))

        tk.Label(inner, text="Динамики / Наушники", bg=self.th_bg, fg=self.th_subtext,
                 font=(UI_FONT, 9, "bold"), anchor="w").pack(fill="x", padx=25)
        ttk.Combobox(inner, textvariable=self.selected_speaker, values=self.speaker_list,
                     state="readonly", width=40).pack(padx=25, pady=(4, 10))

        tk.Checkbutton(inner, text="Прослушивать свой микрофон (Loopback)", variable=self.loopback_enabled,
                       bg=self.th_bg, fg=self.th_fg, selectcolor=self.th_input,
                       activebackground=self.th_bg, activeforeground=self.th_fg,
                       font=(UI_FONT, 9, "bold"), command=self.save_config).pack(anchor="w", padx=25, pady=(0, 10))

        inv = inverted_colors(self.th_bg, self.th_accent)
        scale_kw = dict(track=inv["track"], knob=inv["knob"],
                        text_color=inv["text"], fill=self.th_accent)

        tk.Label(inner, text="Порог чувствительности (Gate)", bg=self.th_bg, fg=inv["text"],
                 font=(UI_FONT, 9, "bold"), anchor="w").pack(fill="x", padx=25)
        InvertedScale(inner, self.rms_threshold, from_=1.0, to=200.0, resolution=1.0,
                      command=lambda v: self.save_config(), label="", owner=self, **scale_kw).pack(
                          fill="x", padx=25, pady=(2, 10))

        tk.Label(inner, text="Усиление микрофона", bg=self.th_bg, fg=inv["text"],
                 font=(UI_FONT, 9, "bold"), anchor="w").pack(fill="x", padx=25)
        InvertedScale(inner, self.current_volume, from_=0.1, to=2.0, resolution=0.1,
                      command=lambda v: self.save_config(), label="×", owner=self, **scale_kw).pack(
                          fill="x", padx=25, pady=(2, 10))

        # ---------- звук трансляции
        tk.Label(inner, text="ЗВУК ТРАНСЛЯЦИИ / ДЕМКИ", bg=self.th_bg, fg=self.th_accent,
                 font=(UI_FONT, 9, "bold"), anchor="w").pack(fill="x", padx=25, pady=(6, 4))
        tk.Checkbutton(inner, text="Захватывать звук системы при трансляции",
                       variable=self.stream_audio_enabled,
                       bg=self.th_bg, fg=self.th_fg, selectcolor=self.th_input,
                       activebackground=self.th_bg, activeforeground=self.th_fg,
                       font=(UI_FONT, 9), command=self.save_config).pack(anchor="w", padx=25)
        tk.Label(inner, text="Громкость демки при просмотре", bg=self.th_bg, fg=inv["text"],
                 font=(UI_FONT, 8, "bold"), anchor="w").pack(fill="x", padx=25, pady=(6, 0))
        InvertedScale(inner, self.stream_volume, from_=0.0, to=2.0, resolution=0.05,
                      command=lambda v: self._apply_stream_volume(), label="", owner=self, **scale_kw).pack(
                          fill="x", padx=25, pady=(2, 8))
        tk.Label(inner, text="Громкость захвата (при отправке)", bg=self.th_bg, fg=inv["text"],
                 font=(UI_FONT, 8, "bold"), anchor="w").pack(fill="x", padx=25)
        InvertedScale(inner, self.stream_capture_volume, from_=0.0, to=2.0, resolution=0.05,
                      command=lambda v: self.save_config(), label="", owner=self, **scale_kw).pack(
                          fill="x", padx=25, pady=(2, 10))

        RoundBtn(inner, "🔄  Проверить обновления", self.th_accent, self.th_accent_grad,
                 self.check_for_updates_interactive, font=(UI_FONT, 10, "bold")).pack(fill="x", padx=25, pady=(5, 5))
        RoundBtn(inner, "Готово", self.th_panel, self.th_accent, win.destroy,
                 font=(UI_FONT, 10, "bold")).pack(fill="x", padx=25, pady=6)

    def create_custom_theme_dialog(self):
        """Своя тема: полупрозрачное фото + палитра из 3 цветов + предпросмотр."""
        win = tk.Toplevel(self.root)
        win.title("🎨 Своя тема")
        win.geometry("560x640")
        win.config(bg=self.th_bg)
        win.resizable(False, False)
        win.grab_set()

        st = {"path": "", "colors": ["#5c6ac4", "#3ba55c", "#c9973c"],
              "opacity": 0.45, "name": "Моя тема"}

        tk.Label(win, text="СОЗДАТЬ ТЕМУ", bg=self.th_bg, fg=self.th_fg,
                 font=(UI_FONT, 13, "bold")).pack(pady=(14, 10))

        name_row = tk.Frame(win, bg=self.th_bg)
        name_row.pack(fill="x", padx=22)
        tk.Label(name_row, text="Название", bg=self.th_bg, fg=self.th_subtext,
                 font=(UI_FONT, 9, "bold")).pack(side="left")
        name_var = tk.StringVar(value=st["name"])
        tk.Entry(name_row, textvariable=name_var, bg=self.th_input, fg=self.th_fg,
                 insertbackground=self.th_fg, relief="flat", bd=0,
                 font=(UI_FONT, 10)).pack(side="left", fill="x", expand=True, padx=10)

        tk.Button(win, text="🖼 Выбрать фото (полупрозрачный фон)",
                  bg=self.th_card, fg=self.th_fg, font=(UI_FONT, 9, "bold"),
                  relief="flat", bd=0, cursor="hand2",
                  command=lambda: pick_photo()).pack(fill="x", padx=22, pady=(10, 4))

        colors_row = tk.Frame(win, bg=self.th_bg)
        colors_row.pack(fill="x", padx=22, pady=4)
        swatches = []
        for i, label in enumerate(("Цвет 1 · акцент", "Цвет 2 · успех", "Цвет 3 · предупр.")):
            f = tk.Frame(colors_row, bg=self.th_bg)
            f.pack(side="left", expand=True, fill="x", padx=2)
            tk.Label(f, text=label, bg=self.th_bg, fg=self.th_subtext,
                     font=(UI_FONT, 7)).pack()
            b = tk.Button(f, text="   ", bg=st["colors"][i], relief="flat", bd=0,
                          cursor="hand2", height=1,
                          command=lambda idx=i: pick_color(idx))
            b.pack(fill="x")
            swatches.append(b)

        op_var = tk.DoubleVar(value=st["opacity"])
        tk.Label(win, text="Прозрачность наложения фото", bg=self.th_bg,
                 fg=self.th_subtext, font=(UI_FONT, 9, "bold"),
                 anchor="w").pack(fill="x", padx=22, pady=(10, 0))
        inv = inverted_colors(self.th_bg, self.th_accent)
        InvertedScale(win, op_var, from_=0.0, to=0.9, resolution=0.05,
                      width=516, command=lambda v: (
                          st.__setitem__("opacity", float(v)), render()),
                      track=inv["track"], knob=inv["knob"], text_color=inv["text"],
                      fill=self.th_accent, owner=self).pack(padx=22, pady=(2, 10))

        preview = tk.Canvas(win, width=516, height=250, bg=self.th_bg,
                            highlightthickness=1, highlightbackground=self.th_line)
        preview.pack(padx=22)
        preview_photo = [None]

        def render():
            th = theme_from_palette(*st["colors"])
            pv = preview
            pv.delete("all")
            photo = wallpaper_photo(st["path"], 516, 250,
                                    st["colors"][0], st["opacity"])
            if photo:
                preview_photo[0] = photo
                pv.create_image(0, 0, image=photo, anchor="nw")
            else:
                pv.create_rectangle(0, 0, 516, 250, fill=th["bg"], outline="")
            # пример интерфейса
            pv.create_rectangle(0, 0, 140, 250, fill=th["panel"], outline="")
            pv.create_text(12, 16, text="◈ друнспик", fill=th["fg"],
                           font=(UI_FONT, 11, "bold"), anchor="nw")
            for k in range(3):
                pv.create_rectangle(10, 48 + k * 30, 130, 72 + k * 30,
                                    fill=th["card"], outline="")
                pv.create_text(18, 53 + k * 30, text=f"# канал {k + 1}",
                               fill=th["subtext"], font=(UI_FONT, 8), anchor="nw")
            pv.create_text(158, 18, text="# общий чат", fill=th["fg"],
                           font=(UI_FONT, 12, "bold"), anchor="nw")
            pv.create_rectangle(158, 48, 500, 190, fill=th["input"], outline="")
            pv.create_text(170, 62, text="Пример сообщения в вашей теме",
                           fill=th["fg"], font=(UI_FONT, 9), anchor="nw")
            pv.create_text(170, 88, text="Цвета палитры:",
                           fill=th["subtext"], font=(UI_FONT, 8), anchor="nw")
            for i, c in enumerate(st["colors"]):
                pv.create_rectangle(170 + i * 46, 104, 206 + i * 46, 130,
                                    fill=c, outline="")
            pill = _rounded_photo(120, 32, 16, th["accent"])
            if pill:
                preview_photo.append(pill)
                pv.create_image(230, 214, image=pill)
            pv.create_text(230, 214, text="Кнопка",
                           fill="#ffffff", font=(UI_FONT, 9, "bold"))

        def pick_photo():
            p = filedialog.askopenfilename(
                title="Фото для темы",
                filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp *.webp")])
            if p:
                st["path"] = p
                render()

        def pick_color(idx):
            _, hexc = colorchooser.askcolor(color=st["colors"][idx], parent=win)
            if hexc:
                st["colors"][idx] = hexc
                swatches[idx].config(bg=hexc)
                render()

        def save():
            name = name_var.get().strip()[:40] or "Моя тема"
            st["name"] = name
            THEMES[name] = theme_from_palette(*st["colors"])
            self.custom_themes[name] = {
                "colors": list(st["colors"]),
                "wallpaper": st["path"],
                "opacity": float(st["opacity"]),
            }
            if not save_custom_themes(self.custom_themes):
                messagebox.showerror("Тема", "Не удалось сохранить тему.")
                return
            self.current_theme.set(name)
            win.destroy()
            self.change_theme_live()

        render()
        RoundBtn(win, "👁  Обновить предпросмотр", self.th_card, self.th_line, render,
                 font=(UI_FONT, 9, "bold"), py=7, px=12).pack(
                     fill="x", padx=22, pady=(12, 4))
        RoundBtn(win, "💾  Сохранить и применить", self.th_accent,
                 self.th_accent_grad, save,
                 font=(UI_FONT, 10, "bold"), py=8, px=12).pack(
                     fill="x", padx=22, pady=(2, 12))

    # ------------------------------------------------------------ обновления
    def version_tuple(self, s):
        try:
            return tuple(int(x) for x in re.findall(r"\d+", str(s))[:3]) or (0,)
        except Exception:
            return (0,)

    def _fetch_version_info(self):
        last = None
        for url in UPDATE_CHECK_URLS:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "DrunspeakUpdater/3.7",
                                  "Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=6) as response:
                    data = json.loads(response.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("version"):
                    return data
            except Exception as e:
                last = e
        raise RuntimeError(str(last) if last else "все источники недоступны")

    @staticmethod
    def _pick_update_url(data):
        """Для собранного exe берём exe_url, для скрипта — url (client.pyw)."""
        frozen = getattr(sys, "frozen", False)
        return (data.get("exe_url") if frozen else None) or data.get("url")

    def check_for_updates(self, silent=False):
        """Тихая проверка при старте + явная по кнопке в настройках."""
        try:
            data = self._fetch_version_info()
        except Exception as e:
            if not silent:
                self.ui(lambda: messagebox.showerror(
                    "Обновление", f"Не удалось проверить обновления:\n{e}"))
            return
        latest = data.get("version")
        if self.version_tuple(latest) <= self.version_tuple(CURRENT_VERSION):
            if not silent:
                self.ui(lambda: messagebox.showinfo(
                    "Обновление", "У вас установлена самая последняя версия!"))
            return
        url = self._pick_update_url(data)
        sha = (data.get("exe_sha256") if getattr(sys, "frozen", False) else None) \
            or data.get("sha256")
        note = str(data.get("note", "") or "")[:400]

        def ask():
            msg = (f"Доступна новая версия: {latest} (у вас {CURRENT_VERSION}).")
            if note:
                msg += f"\n\n{note}"
            if messagebox.askyesno("Обновление", msg + "\n\nОбновиться сейчас?"):
                self.download_and_apply_update(url, sha)
        self.ui(ask)

    def check_for_updates_interactive(self):
        self.check_for_updates(silent=False)

    def download_and_apply_update(self, url, sha=None):
        try:
            if not url:
                messagebox.showerror("Ошибка", "В version.json не указан адрес загрузки (url).")
                return
            is_exe = getattr(sys, "frozen", False)
            if is_exe and not str(url).lower().split("?")[0].endswith(".exe"):
                # Для собранного exe нужен именно exe-ассет. Без него
                # не подменяем бинарник скриптом — просто открываем релизы.
                webbrowser.open(f"https://github.com/{UPDATE_REPO}/releases")
                messagebox.showinfo(
                    "Обновление",
                    "Для этой сборки нужен exe из раздела Releases.\n"
                    "Открыл страницу загрузки в браузере.")
                return
            suffix = "client.exe" if is_exe else "client.pyw"
            new_file = os.path.join(tempfile.gettempdir(), "drunspeak_update_" + suffix)
            # GitHub/CDN иногда отдают 403 без User-Agent — качаем через opener.
            opener = urllib.request.build_opener()
            opener.addheaders = [("User-Agent", "DrunspeakUpdater/3.7"),
                                 ("Cache-Control", "no-cache")]
            with opener.open(url, timeout=60) as resp, open(new_file, "wb") as out:
                while True:
                    block = resp.read(1 << 20)
                    if not block:
                        break
                    out.write(block)

            size = os.path.getsize(new_file)
            if sha:
                h = hashlib.sha256()
                with open(new_file, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                if h.hexdigest().lower() != str(sha).lower():
                    messagebox.showerror("Ошибка", "Файл обновления поврежден (не совпал SHA256).")
                    return
            if is_exe:
                if size < 1024 * 1024:
                    raise ValueError("подозрительно маленький файл")
                with open(new_file, "rb") as f:
                    if f.read(2) != b"MZ":
                        raise ValueError("это не exe-файл")
            else:
                if size < 10 * 1024:
                    raise ValueError("подозрительно маленький файл")

            bat_path = os.path.join(tempfile.gettempdir(), "drunspeak_updater.bat")
            if is_exe:
                target = os.path.abspath(sys.executable)
                script = f"""@echo off
timeout /t 2 /nobreak > nul
move /y "{target}" "{target}.old" > nul
move /y "{new_file}" "{target}" > nul
start "" "{target}"
del /f /q "{target}.old" > nul
del "%~f0" > nul
"""
            else:
                target = os.path.abspath(__file__)
                runner = "pythonw.exe" if target.lower().endswith(".pyw") else "python.exe"
                script = f"""@echo off
chcp 65001 > nul
timeout /t 2 /nobreak > nul
del /f /q "{target}" > nul
move /y "{new_file}" "{target}" > nul
start "" "{runner}" "{target}"
del "%~f0" > nul
"""
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(script)
            subprocess.Popen([bat_path], shell=True)
            self.on_close()
        except Exception as e:
            messagebox.showerror("Ошибка обновления", f"Не удалось применить обновление:\n{e}")

    # ------------------------------------------------------------ завершение
    def on_close(self):
        if self._closing:
            return
        self._closing = True
        self.running_audio = False
        self._stop_sharing_internal()
        if self.is_connected:
            self._send_control({"t": "disconnect"})
        self.is_connected = False
        try:
            if self.client_socket:
                self.client_socket.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    root = tk.Tk()
    app = VoiceClientApp(root)
    root.mainloop()






