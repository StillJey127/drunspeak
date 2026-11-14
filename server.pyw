# -*- coding: utf-8 -*-
"""
Друнспик — сервер (UDP-релея), протокол v3.
  контроль:  b"J" + len(4, big-endian) + JSON (в JSON поле "t" — тип пакета)
  аудио:     b"A" + seq(2) + int16 mono 16 kHz
  картинки:  C->S: b"I" + id(4) + ext_len(1) + ext + idx(2) + total(2) + данные
             S->C: b"I" + nick_len(1) + nick + id(4) + ext_len(1) + ext + idx(2) + total(2) + данные
"""
import socket
import threading
import time
import datetime
import os
import sys
import json
import queue
import hashlib
import hmac
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
CONFIG_FILE = os.path.join(BASE_DIR, "server_config.json")
BANS_FILE = os.path.join(BASE_DIR, "banned.json")
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")

MAIN_CHANNEL = "друнспик 1"
MAX_NICK_LEN = 32
MAX_CHAT_LEN = 1000
CLIENT_TIMEOUT = 10.0
USERLIST_INTERVAL = 1.0
IMG_SRC_LIMIT = 256
IMG_SRC_TTL = 120.0
FILE_SRC_LIMIT = 128
FILE_SRC_TTL = 300.0
FILE_MAX_PAYLOAD = 1300
FILE_MAX_NAME = 180
AVATAR_B64_LIMIT = 12000
AVATAR_PART_LIMIT = 900
GARBAGE_STRIKES = 40
AUDIO_MAX_PAYLOAD = 2048
IMG_MAX_PAYLOAD = 1400
VIDEO_MAX_CHUNK = 1500
STREAM_VIDEO_BYTES_CAP = 24 * 1024 * 1024  # байт/сек на клиента (60 fps, 1080p q82)
FILE_MAX_TOTAL = 300000
FILE_MAX_SIZE = 300 * 1024 * 1024
PBKDF2_ITER = 120000


def default_config():
    return {"port": 12345, "server_password": "", "registration": True,
            "channels": {MAIN_CHANNEL: ""}}


def hash_password(password, salt_hex=None):
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITER)
    return salt.hex(), h.hex()


def verify_password(password, salt_hex, hash_hex):
    try:
        _, h = hash_password(password, salt_hex)
        return hmac.compare_digest(h, hash_hex)
    except Exception:
        return False


def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    default.update(data)
                    return default
    except Exception as e:
        print(f"Ошибка чтения {path}: {e}")
    return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Ошибка записи {path}: {e}")


def pack_control(obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if len(body) > 60000:
        raise ValueError("control packet too large")
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


class VoiceServerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Друнспик — Панель управления сервером")
        self.root.geometry("760x780")
        self.root.config(bg="#1a1c23")
        self.root.resizable(False, False)

        self.cfg = load_json(CONFIG_FILE, default_config())
        self.port = int(self.cfg.get("port", 12345) or 12345)
        self.server_password = str(self.cfg.get("server_password", "") or "")
        self.channels = {str(k): str(v) for k, v in (self.cfg.get("channels") or {}).items()}
        self.channels.setdefault(MAIN_CHANNEL, "")

        self.banned = set(load_json(BANS_FILE, {"banned": []}).get("banned", []))
        self.accounts = load_json(ACCOUNTS_FILE, {"accounts": {}}).get("accounts", {})
        # Пароли аккаунтов ТОЛЬКО в оперативной памяти (на диск не пишем).
        self.account_passwords = {}
        self.registration = bool(self.cfg.get("registration", True))

        self.clients = {}
        self.clients_lock = threading.RLock()
        self.ui_queue = queue.Queue()
        self.img_src = {}
        self.file_src = {}
        self.video_src = {}   # fid -> [addr, expires]
        self.chat_times = {}
        self.strikes = {}
        self.stream_usage = {}
        # Чанкованная загрузка аватарок: addr -> {id, total, parts, nick, t}
        self.avatar_uploads = {}
        self.addr_map = []
        self.ban_notice = {}
        self.server_socket = None
        self.is_running = False

        self.create_widgets()
        self._drain_ui()
        self._schedule_list_refresh()
        self.start_server()

    # ------------------------------------------------------------ UI helpers
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

    def log(self, message):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {message}")
        self.ui(lambda m=f"[{ts}] {message}": self._append_log(m))

    def _append_log(self, msg):
        if hasattr(self, "log_text") and self.log_text.winfo_exists():
            self.log_text.config(state="normal")
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state="disabled")

    def update_info_label(self):
        self.playit_label.config(
            text=f"Порт: {self.port} | Каналов: {len(self.channels)} | Клиентов: {len(self.clients)} | Статус: 🟢 Работает"
        )

    # ------------------------------------------------------------ widgets
    def create_widgets(self):
        header = tk.Frame(self.root, bg="#1a1c23")
        header.pack(fill="x", padx=20, pady=12)
        tk.Label(header, text="ПАНЕЛЬ УПРАВЛЕНИЯ СЕРВЕРОМ", bg="#1a1c23", fg="#00ffcc",
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        self.status_indicator = tk.Label(header, text="● СЕРВЕР АКТИВЕН", bg="#1a1c23", fg="#00ffcc",
                                         font=("Segoe UI", 10, "bold"))
        self.status_indicator.pack(side="right")

        info_frame = tk.Frame(self.root, bg="#242730", padx=12, pady=8)
        info_frame.pack(fill="x", padx=20, pady=5)
        self.playit_label = tk.Label(info_frame, text="", bg="#242730", fg="#b0b0b8", font=("Segoe UI", 9))
        self.playit_label.pack(anchor="w")

        list_frame = tk.LabelFrame(self.root, text=" Подключенные пользователи ", bg="#1a1c23", fg="#00ffcc",
                                   font=("Segoe UI", 10, "bold"))
        list_frame.pack(fill="both", expand=True, padx=20, pady=5)
        self.client_listbox = tk.Listbox(list_frame, bg="#121318", fg="#00ffcc", selectbackground="#5865f2",
                                         font=("Consolas", 10), bd=0, highlightthickness=0)
        self.client_listbox.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.client_listbox.yview)
        sb.pack(side="right", fill="y", pady=8)
        self.client_listbox.config(yscrollcommand=sb.set)

        control_frame = tk.Frame(self.root, bg="#1a1c23")
        control_frame.pack(fill="x", padx=20, pady=5)

        chan_frame = tk.Frame(control_frame, bg="#1a1c23")
        chan_frame.pack(fill="x", pady=(0, 6))
        tk.Button(chan_frame, text="➕ Создать войс", bg="#5865f2", fg="white", font=("Segoe UI", 9, "bold"),
                  relief="flat", command=self.create_channel_prompt).pack(side="left", expand=True, fill="x", padx=(0, 4))
        tk.Button(chan_frame, text="🔑 Пароль канала", bg="#8e44ad", fg="white", font=("Segoe UI", 9, "bold"),
                  relief="flat", command=self.change_channel_password).pack(side="left", expand=True, fill="x", padx=4)
        tk.Button(chan_frame, text="🗑️ Удалить войс", bg="#c0392b", fg="white", font=("Segoe UI", 9, "bold"),
                  relief="flat", command=self.delete_channel_prompt).pack(side="left", expand=True, fill="x", padx=(4, 0))

        pw_frame = tk.Frame(control_frame, bg="#1a1c23")
        pw_frame.pack(fill="x", pady=(0, 6))
        tk.Button(pw_frame, text="🔑 Показать пароли каналов", bg="#2d3436", fg="#f1c40f",
                  font=("Segoe UI", 9, "bold"), relief="flat",
                  command=self.show_channel_passwords).pack(side="left", expand=True, fill="x", padx=(0, 4))
        tk.Button(pw_frame, text="👤 Пароли аккаунтов (RAM)", bg="#2d3436", fg="#f1c40f",
                  font=("Segoe UI", 9, "bold"), relief="flat",
                  command=self.show_account_passwords).pack(side="left", expand=True, fill="x", padx=(4, 0))

        row2 = tk.Frame(control_frame, bg="#1a1c23")
        row2.pack(fill="x", pady=(0, 6))
        tk.Button(row2, text="🔇 Мут / Размутить", bg="#e67e22", fg="white", font=("Segoe UI", 9, "bold"),
                  relief="flat", command=self.toggle_mute_client).pack(side="left", expand=True, fill="x", padx=(0, 4))
        tk.Button(row2, text="Бан IP", bg="#8b0000", fg="white", font=("Segoe UI", 9, "bold"),
                  relief="flat", command=self.ban_selected_client).pack(side="left", expand=True, fill="x", padx=4)
        tk.Button(row2, text="Разбанить IP", bg="#27ae60", fg="white", font=("Segoe UI", 9, "bold"),
                  relief="flat", command=self.unban_prompt).pack(side="left", expand=True, fill="x", padx=4)
        tk.Button(row2, text="Кикнуть", bg="#e74c3c", fg="white", font=("Segoe UI", 9, "bold"),
                  relief="flat", command=self.kick_client).pack(side="left", expand=True, fill="x", padx=4)

        row3 = tk.Frame(control_frame, bg="#1a1c23")
        row3.pack(fill="x")
        tk.Button(row3, text="👤 Аккаунты", bg="#16a085", fg="white", font=("Segoe UI", 9, "bold"),
                  relief="flat", command=self.open_accounts_dialog).pack(side="left", expand=True, fill="x", padx=(0, 4))
        tk.Button(row3, text="⚙ Настройки сервера", bg="#242730", fg="#b0b0b8", font=("Segoe UI", 9, "bold"),
                  relief="flat", command=self.open_settings).pack(side="left", expand=True, fill="x", padx=4)
        tk.Button(row3, text="🔄 Перезапустить сеть", bg="#242730", fg="#b0b0b8", font=("Segoe UI", 9, "bold"),
                  relief="flat", command=self.restart_network).pack(side="left", expand=True, fill="x", padx=(4, 0))
        tk.Button(row3, text="↔ Перенести в войс", bg="#2980b9", fg="white",
                  font=("Segoe UI", 9, "bold"), relief="flat",
                  command=self.move_client_prompt).pack(side="left", expand=True, fill="x", padx=(4, 0))

        logs_frame = tk.LabelFrame(self.root, text=" Логи сервера в реальном времени ", bg="#1a1c23", fg="#00ffcc",
                                   font=("Segoe UI", 10, "bold"))
        logs_frame.pack(fill="both", expand=True, padx=20, pady=10)
        self.log_text = tk.Text(logs_frame, bg="#0d0e12", fg="#00ffcc", font=("Consolas", 9), bd=0,
                                highlightthickness=0, state="disabled", height=8)
        self.log_text.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        log_scroll = ttk.Scrollbar(logs_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y", pady=8)
        self.log_text.config(yscrollcommand=log_scroll.set)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------ persistence
    def save_config(self):
        self.cfg["port"] = self.port
        self.cfg["server_password"] = self.server_password
        self.cfg["registration"] = self.registration
        self.cfg["channels"] = self.channels
        save_json(CONFIG_FILE, self.cfg)

    def save_bans(self):
        save_json(BANS_FILE, {"banned": sorted(self.banned)})

    def save_accounts(self):
        save_json(ACCOUNTS_FILE, {"accounts": self.accounts})

    # ------------------------------------------------------------ server start
    def start_server(self):
        if self._bind():
            self.is_running = True
            self.log(f"Сервер запущен на порту {self.port}")
            threading.Thread(target=self.network_listener, daemon=True).start()
            threading.Thread(target=self.cleanup_clients_loop, daemon=True).start()
            threading.Thread(target=self.broadcast_user_list, daemon=True).start()
            self.status_indicator.config(text="● СЕРВЕР АКТИВЕН", fg="#00ffcc")
        self.update_info_label()

    def _bind(self):
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
            self.server_socket.bind(("0.0.0.0", self.port))
            return True
        except Exception as e:
            self.log(f"Не удалось занять порт {self.port}: {e}")
            self.status_indicator.config(text="● ОШИБКА ПОРТА", fg="#e74c3c")
            return False

    def restart_network(self):
        self.log("Перезапуск сетевого слоя...")
        try:
            if self.server_socket:
                self.server_socket.close()
        except Exception:
            pass
        time.sleep(0.3)
        if self._bind():
            threading.Thread(target=self.network_listener, daemon=True).start()
            self.log(f"Сеть перезапущена на порту {self.port}")
            self.status_indicator.config(text="● СЕРВЕР АКТИВЕН", fg="#00ffcc")
        self.update_info_label()

    # ------------------------------------------------------------ anti-flood
    def strike(self, addr, reason):
        with self.clients_lock:
            cnt, until = self.strikes.get(addr, (0, 0.0))
            if until > time.time():
                return
            cnt += 1
            if cnt >= GARBAGE_STRIKES:
                self.strikes[addr] = (0, time.time() + 60.0)
                self.log(f"Флуд от {addr[0]}:{addr[1]} — игнор на 60 сек ({reason})")
            else:
                self.strikes[addr] = (cnt, until)

    def is_ignored(self, addr):
        with self.clients_lock:
            _, until = self.strikes.get(addr, (0, 0.0))
            return until > time.time()

    # ------------------------------------------------------------ listener
    def network_listener(self):
        while self.is_running:
            try:
                data, addr = self.server_socket.recvfrom(65535)
            except OSError:
                break
            if not self.is_running:
                break
            try:
                self.handle_packet(data, addr)
            except Exception as e:
                print(f"Ошибка пакета от {addr}: {e}")

    def handle_packet(self, data, addr):
        ip = addr[0]
        if ip in self.banned:
            self._notify_banned(addr)
            return
        if self.is_ignored(addr):
            return
        tag = data[0:1]
        if tag == b"J":
            msg = parse_control(data)
            if msg is None:
                self.strike(addr, "битый контроль-пакет")
                return
            self.handle_control(addr, msg)
        elif tag == b"A":
            self.handle_audio(addr, data)
        elif tag == b"I":
            self.handle_image_chunk(addr, data)
        elif tag == b"F":
            self.handle_file_chunk(addr, data)
        elif tag == b"V":
            self.handle_stream_video(addr, data)
        elif tag in (b"S", b"s"):
            self.handle_stream_audio(addr, data)
        else:
            self.strike(addr, "неизвестный пакет")

    def _notify_banned(self, addr):
        now = time.time()
        last = self.ban_notice.get(addr[0], 0.0)
        if now - last > 2.0:
            self.ban_notice[addr[0]] = now
            try:
                self.server_socket.sendto(pack_control({"t": "banned"}), addr)
            except Exception:
                pass

    # ------------------------------------------------------------ control
    def handle_control(self, addr, msg):
        t = msg.get("t")
        with self.clients_lock:
            client = self.clients.get(addr)
            if client:
                client["time"] = time.time()

        if t == "auth":
            self.handle_auth(addr, msg)
            return
        if t == "disconnect":
            with self.clients_lock:
                client = self.clients.pop(addr, None)
            if client:
                self.log(f"Клиент отключился: {client['name']} ({addr[0]})")
                if client.get("sharing"):
                    self.broadcast_stream_end(addr, client["channel"], client["name"])
            return
        if client is None:
            self.strike(addr, "пакет без авторизации")
            return

        if t == "switch":
            self.handle_switch(addr, msg)
        elif t == "status":
            with self.clients_lock:
                client["mic_muted"] = bool(msg.get("mic_muted", False))
                client["deafened"] = bool(msg.get("deafened", False))
                client["sharing"] = bool(msg.get("sharing", False))
        elif t == "talking":
            with self.clients_lock:
                client["talking"] = bool(msg.get("on", False))
        elif t == "set_avatar":
            # Старый клиент: один пакет (совместимость), но всё равно
            # рассылаем чанками, чтобы не фрагментировать UDP.
            self._commit_avatar(addr, client["name"], str(msg.get("b64", ""))[:AVATAR_B64_LIMIT])
        elif t == "set_avatar_start":
            aid = str(msg.get("id", ""))[:16]
            try:
                total = int(msg.get("total", 0))
            except (TypeError, ValueError):
                return
            if not aid or total < 1 or total > 64:
                return
            with self.clients_lock:
                client["time"] = time.time()
                self.avatar_uploads[addr] = {"id": aid, "total": total, "parts": {},
                                             "nick": client["name"], "t": time.time()}
        elif t == "set_avatar_part":
            aid = str(msg.get("id", ""))[:16]
            try:
                idx = int(msg.get("i", -1))
            except (TypeError, ValueError):
                return
            part = str(msg.get("d", ""))
            if not aid or idx < 0 or len(part) > AVATAR_PART_LIMIT:
                return
            with self.clients_lock:
                entry = self.avatar_uploads.get(addr)
                if not entry or entry["id"] != aid or idx >= entry["total"]:
                    return
                client["time"] = time.time()
                entry["parts"][idx] = part
                entry["t"] = time.time()
                done = len(entry["parts"]) == entry["total"]
                if done:
                    self.avatar_uploads.pop(addr, None)
                    b64 = "".join(entry["parts"][i] for i in range(entry["total"]))
                    nick = entry["nick"]
            if done:
                self._commit_avatar(addr, nick, b64[:AVATAR_B64_LIMIT])
        elif t == "avatar_clear":
            self._commit_avatar(addr, client["name"], "")
        elif t == "chat":
            self.handle_chat(addr, msg)
        elif t == "channel_create":
            self.handle_channel_create(addr, msg)
        elif t == "ping":
            try:
                self.server_socket.sendto(pack_control({"t": "pong", "seq": msg.get("seq", 0)}), addr)
            except Exception:
                pass
        elif t in ("webrtc_offer", "webrtc_answer", "webrtc_ice"):
            self.relay_webrtc(addr, t, msg)
        elif t == "stream_end":
            with self.clients_lock:
                client["sharing"] = False
            self.broadcast_stream_end(addr, client["channel"], client["name"])
        elif t in ("img_ack", "img_nack"):
            self.route_image_feedback(addr, t, msg)
        elif t in ("file_ack", "file_nack"):
            self.route_file_feedback(addr, t, msg)
        elif t == "video_nack":
            self.route_video_feedback(msg)
        elif t == "admin_move":
            self.handle_admin_move(addr, msg)
        else:
            self.strike(addr, "неизвестный тип пакета")

    def handle_auth(self, addr, msg):
        nick = str(msg.get("nick", "")).strip()[:MAX_NICK_LEN]
        acc_pass = str(msg.get("acc_pass", ""))
        chan = str(msg.get("chan", "")).strip()
        chan_pass = str(msg.get("pass", ""))
        srv_pass = str(msg.get("server_pass", ""))

        if not nick:
            self._send(addr, {"t": "auth_error", "reason": "Пустой никнейм"})
            return
        if self.server_password and srv_pass != self.server_password:
            self._send(addr, {"t": "bad_server_pass"})
            return

        # --- аккаунты: вход или регистрация
        with self.clients_lock:
            acct = self.accounts.get(nick)
        if acct:
            if acct.get("banned"):
                self._send(addr, {"t": "account_banned"})
                self.log(f"Отклонен забаненный аккаунт: {nick} ({addr[0]})")
                return
            if not verify_password(acc_pass, acct.get("salt", ""), acct.get("hash", "")):
                self._send(addr, {"t": "wrong_account_pass"})
                return
            self.account_passwords[nick] = acc_pass
        else:
            if not self.registration:
                self._send(addr, {"t": "auth_error", "reason": "Регистрация на сервере закрыта."})
                return
            if len(acc_pass) < 4:
                self._send(addr, {"t": "need_account_pass"})
                return
            salt, h = hash_password(acc_pass)
            with self.clients_lock:
                if nick not in self.accounts:
                    self.accounts[nick] = {"salt": salt, "hash": h, "banned": False,
                                           "created": time.time()}
                    self.save_accounts()
                self.account_passwords[nick] = acc_pass
            self.log(f"Зарегистрирован новый аккаунт: {nick} | пароль: '{acc_pass}' ({addr[0]})")

        # --- одна активная сессия на аккаунт
        with self.clients_lock:
            for a, c in self.clients.items():
                if a != addr and c["name"] == nick:
                    self._send(addr, {"t": "nick_taken"})
                    return

        with self.clients_lock:
            if chan not in self.channels:
                chan = MAIN_CHANNEL
            if self.channels.get(chan, "") and chan_pass != self.channels.get(chan, ""):
                self._send(addr, {"t": "wrong_pass", "chan": chan})
                return
            self.strikes.pop(addr, None)
            av_b64 = str(msg.get("avatar_b64", "") or "")[:AVATAR_B64_LIMIT]
            # Аватарка лежит в аккаунте — подхватываем, даже если клиент
            # не прислал её в пакете auth (старые/лёгкие клиенты).
            if not av_b64 and acct:
                av_b64 = str(acct.get("avatar_b64", "") or "")[:AVATAR_B64_LIMIT]
            with self.clients_lock:
                if acct is not None:
                    acct["avatar_b64"] = av_b64
                elif nick in self.accounts:
                    self.accounts[nick]["avatar_b64"] = av_b64
                self.save_accounts()
            self.clients[addr] = {
                "time": time.time(), "name": nick, "channel": chan,
                "muted": False, "mic_muted": False, "deafened": False,
                "sharing": False, "talking": False, "avatar_b64": av_b64,
            }
        self.log(f"Вход: {nick} -> канал '{chan}' ({addr[0]}:{addr[1]})")
        self._send(addr, {"t": "ack"})
        # Стартовые аватарки — каждому чанками (< MTU туннеля),
        # а не одним giant-пакетом, который режется/теряется.
        try:
            with self.clients_lock:
                avatars = {c["name"]: c.get("avatar_b64", "")
                           for a, c in self.clients.items()
                           if a != addr and c.get("avatar_b64")}
                targets = [a for a in self.clients.keys() if a != addr]
            for other, b64 in avatars.items():
                self._send_avatar_to([addr], other, b64)
            if av_b64:
                self._send_avatar_to(targets, nick, av_b64)
        except Exception as e:
            print(f"Рассылка аватарок: {e}")
        self.ui(self.update_info_label)

    # ------------------------------------------------ avatar transfer
    def _commit_avatar(self, addr, nick, b64):
        """Сохраняет аватарку и чанками рассылает её всем остальным."""
        b64 = str(b64 or "")[:AVATAR_B64_LIMIT]
        with self.clients_lock:
            c = self.clients.get(addr)
            if c:
                c["avatar_b64"] = b64
            if nick in self.accounts:
                self.accounts[nick]["avatar_b64"] = b64
            self.save_accounts()
            targets = [a for a in self.clients.keys() if a != addr]
        self._send_avatar_to(targets, nick, b64)

    def _send_avatar_to(self, targets, nick, b64):
        """Чанкованная рассылка: один control-пакет < ~1 КБ (без UDP-фрагментации)."""
        if not targets:
            return
        if not b64:
            out = pack_control({"t": "avatar_clear", "nick": nick})
            for a in targets:
                try:
                    self.server_socket.sendto(out, a)
                except Exception:
                    pass
            return
        aid = os.urandom(4).hex()
        parts = [b64[i:i + AVATAR_PART_LIMIT] for i in range(0, len(b64), AVATAR_PART_LIMIT)]
        head = pack_control({"t": "avatar_start", "nick": nick, "id": aid,
                             "total": len(parts), "len": len(b64)})
        for a in targets:
            try:
                self.server_socket.sendto(head, a)
            except Exception:
                pass
        for i, part in enumerate(parts):
            out = pack_control({"t": "avatar_part", "nick": nick, "id": aid,
                                "i": i, "d": part})
            for a in targets:
                try:
                    self.server_socket.sendto(out, a)
                except Exception:
                    pass
            if i and i % 8 == 0:
                time.sleep(0.002)

    def handle_channel_create(self, addr, msg):
        """Клиент создаёт свой канал (с паролем или без)."""
        name = str(msg.get("chan", "")).strip()[:MAX_NICK_LEN]
        pw = str(msg.get("pass", "") or "")[:64]
        if not name:
            self._send(addr, {"t": "channel_error", "reason": "Пустое название"})
            return
        with self.clients_lock:
            if name in self.channels:
                self._send(addr, {"t": "channel_error", "reason": "Канал уже существует"})
                return
            self.channels[name] = pw
            self.save_config()
        # Пароль виден и в логах, и в админ-панели (по требованию).
        self.log(f"Создан канал '{name}' | пароль: '{pw or '(нет)'}' | "
                 f"инициатор: {self.clients.get(addr, {}).get('name', '?')}")
        self._send(addr, {"t": "channel_ok", "chan": name})
        self.ui(self.update_info_label)

    def handle_switch(self, addr, msg):
        chan = str(msg.get("chan", "")).strip()
        chan_pass = str(msg.get("pass", ""))
        with self.clients_lock:
            client = self.clients.get(addr)
            if not client:
                return
            if chan not in self.channels:
                return
            if self.channels.get(chan, "") and chan_pass != self.channels.get(chan, ""):
                self._send(addr, {"t": "wrong_pass", "chan": chan})
                return
            old_chan = client["channel"]
            if chan == old_chan:
                return
            client["channel"] = chan
            was_sharing = client.get("sharing", False)
        self.log(f"{client['name']} перешел в канал '{chan}'")
        self._send(addr, {"t": "chan_ok"})
        if was_sharing:
            self.broadcast_stream_end(addr, old_chan, client["name"])

    def handle_chat(self, addr, msg):
        text = str(msg.get("text", ""))[:MAX_CHAT_LEN].strip()
        if not text:
            return
        now = time.time()
        with self.clients_lock:
            client = self.clients.get(addr)
            if not client:
                return
            dq = self.chat_times.setdefault(addr, [])
            while dq and now - dq[0] > 3.0:
                dq.pop(0)
            if len(dq) >= 8:
                return
            dq.append(now)
            my_chan = client["channel"]
            recipients = [a for a, c in self.clients.items() if c["channel"] == my_chan and a != addr]
        packet = pack_control({"t": "chat", "from": client["name"],
                               "text": text, "channel": my_chan})
        for a in recipients:
            try:
                self.server_socket.sendto(packet, a)
            except Exception:
                pass

    def relay_webrtc(self, addr, t, msg):
        with self.clients_lock:
            client = self.clients.get(addr)
            if not client:
                return
            to_nick = str(msg.get("to", ""))[:MAX_NICK_LEN]
            target = None
            for a, c in self.clients.items():
                if c["name"] == to_nick and c["channel"] == client["channel"]:
                    target = a
                    break
        if target is None:
            return
        out = {"t": t, "from": client["name"]}
        for key in ("sdp", "cand"):
            if key in msg:
                out[key] = msg[key]
        try:
            self.server_socket.sendto(pack_control(out), target)
        except Exception:
            pass

    def broadcast_stream_end(self, addr, chan, nick):
        packet = pack_control({"t": "stream_end", "from": nick})
        with self.clients_lock:
            recipients = [a for a, c in self.clients.items() if c["channel"] == chan and a != addr]
        for a in recipients:
            try:
                self.server_socket.sendto(packet, a)
            except Exception:
                pass

    def _send(self, addr, obj):
        try:
            self.server_socket.sendto(pack_control(obj), addr)
        except Exception:
            pass

    # ------------------------------------------------------------ audio relay
    def handle_audio(self, addr, data):
        if len(data) > AUDIO_MAX_PAYLOAD + 3:
            self.strike(addr, "аудио слишком большое")
            return
        with self.clients_lock:
            client = self.clients.get(addr)
            if not client:
                return
            client["time"] = time.time()
            if client["muted"] or client["mic_muted"]:
                return
            my_chan = client["channel"]
            recipients = [a for a, c in self.clients.items()
                          if c["channel"] == my_chan and a != addr and not c["deafened"]]
            nick_b = client["name"].encode("utf-8")[:255]
        # Штампуем отправителя: клиент может регулировать громкость
        # и локально мутить КАЖДОГО игрока по отдельности.
        stamped = b"a" + bytes([len(nick_b)]) + nick_b + data[1:]
        for a in recipients:
            try:
                self.server_socket.sendto(stamped, a)
            except Exception:
                pass

    # ------------------------------------------------------------ image relay
    def handle_image_chunk(self, addr, data):
        # C->S: b"I" + id(4) + ext_len(1) + ext + idx(2) + total(2) + payload
        if len(data) < 11:
            self.strike(addr, "битый чанк картинки")
            return
        with self.clients_lock:
            client = self.clients.get(addr)
            if not client:
                return
            client["time"] = time.time()
            nick = client["name"]
            my_chan = client["channel"]
            recipients = [a for a, c in self.clients.items() if c["channel"] == my_chan and a != addr]

        msg_id = data[1:5]
        ext_len = data[5]
        p = 6 + ext_len
        if ext_len > 8 or p + 4 > len(data):
            self.strike(addr, "битый чанк картинки")
            return
        idx = int.from_bytes(data[p:p + 2], "big")
        total = int.from_bytes(data[p + 2:p + 4], "big")
        payload = data[p + 4:]
        if total < 1 or total > 2048 or idx >= total or len(payload) > IMG_MAX_PAYLOAD:
            self.strike(addr, "битый чанк картинки")
            return

        nick_b = nick.encode("utf-8")
        if len(nick_b) > 255:
            return
        out = b"I" + bytes([len(nick_b)]) + nick_b + msg_id + data[5:p + 4] + payload
        now = time.time()
        with self.clients_lock:
            if msg_id not in self.img_src:
                if len(self.img_src) >= IMG_SRC_LIMIT:
                    for k in sorted(self.img_src, key=lambda k: self.img_src[k][1])[:len(self.img_src) // 2]:
                        self.img_src.pop(k, None)
                self.img_src[msg_id] = [addr, now + IMG_SRC_TTL]
            else:
                self.img_src[msg_id][1] = now + IMG_SRC_TTL
        for a in recipients:
            try:
                self.server_socket.sendto(out, a)
            except Exception:
                pass

    # ------------------------------------------------------------ file relay
    def handle_file_chunk(self, addr, data):
        # C->S: b"F" + id(4) + name_len(1) + name + idx(4) + total(4) + payload
        if len(data) < 14:
            self.strike(addr, "битый чанк файла")
            return
        with self.clients_lock:
            client = self.clients.get(addr)
            if not client:
                return
            client["time"] = time.time()
            nick = client["name"]
            my_chan = client["channel"]
            recipients = [a for a, c in self.clients.items()
                          if c["channel"] == my_chan and a != addr]

        msg_id = data[1:5]
        name_len = data[5]
        p = 6 + name_len
        if name_len < 1 or name_len > FILE_MAX_NAME or p + 8 > len(data):
            self.strike(addr, "битый чанк файла")
            return
        idx = int.from_bytes(data[p:p + 4], "big")
        total = int.from_bytes(data[p + 4:p + 8], "big")
        payload = data[p + 8:]
        if (total < 1 or total > FILE_MAX_TOTAL or idx >= total
                or len(payload) > FILE_MAX_PAYLOAD
                or total * FILE_MAX_PAYLOAD > FILE_MAX_SIZE):
            self.strike(addr, "битый чанк файла")
            return

        nick_b = nick.encode("utf-8")
        if len(nick_b) > 255:
            return
        out = (b"F" + bytes([len(nick_b)]) + nick_b + msg_id +
               data[5:p + 8] + payload)
        now = time.time()
        with self.clients_lock:
            if msg_id not in self.file_src:
                if len(self.file_src) >= FILE_SRC_LIMIT:
                    for k in sorted(self.file_src,
                                    key=lambda k: self.file_src[k][1])[:len(self.file_src) // 2]:
                        self.file_src.pop(k, None)
                self.file_src[msg_id] = [addr, now + FILE_SRC_TTL]
            else:
                self.file_src[msg_id][1] = now + FILE_SRC_TTL
        for a in recipients:
            try:
                self.server_socket.sendto(out, a)
            except Exception:
                pass

    def route_file_feedback(self, addr, t, msg):
        try:
            msg_id = bytes.fromhex(str(msg.get("id", "")))
        except Exception:
            return
        if len(msg_id) != 4:
            return
        with self.clients_lock:
            entry = self.file_src.get(msg_id)
            if not entry or entry[1] < time.time():
                return
            src = entry[0]
        out = {"t": t, "id": msg_id.hex()}
        if t == "file_nack":
            missing = msg.get("missing", [])
            if isinstance(missing, list) and 0 < len(missing) <= 4000:
                out["missing"] = [int(i) for i in missing if isinstance(i, int)]
            else:
                return
        try:
            self.server_socket.sendto(pack_control(out), src)
        except Exception:
            pass

    def _stream_relay(self, addr, out, nbytes):
        """Штампует ник отправителя и рассылает пакет трансляции по каналу с лимитом скорости."""
        now = time.time()
        with self.clients_lock:
            client = self.clients.get(addr)
            if not client:
                return False
            client["time"] = now
            nick = client["name"]
            my_chan = client["channel"]
            recipients = [a for a, c in self.clients.items()
                          if c["channel"] == my_chan and a != addr]
            # лимит трафика видео на клиента
            sec, total = self.stream_usage.get(addr, (int(now), 0))
            if int(now) != sec:
                sec, total = int(now), 0
            total += nbytes
            self.stream_usage[addr] = (sec, total)
            if total > STREAM_VIDEO_BYTES_CAP:
                return False
        nick_b = nick.encode("utf-8")
        if len(nick_b) > 255:
            return False
        stamped = out[0:1] + bytes([len(nick_b)]) + nick_b + out[1:]
        for a in recipients:
            try:
                self.server_socket.sendto(stamped, a)
            except Exception:
                pass
        return True

    def handle_stream_video(self, addr, data):
        # C->S: b"V" + fid(4) + total(2) + idx(2) + payload
        if len(data) < 10 or len(data) - 9 > VIDEO_MAX_CHUNK:
            self.strike(addr, "битый кадр трансляции")
            return
        fid = int.from_bytes(data[1:5], "big")
        with self.clients_lock:
            self.video_src[fid] = [addr, time.time() + 2.0]
            if len(self.video_src) > 256:
                for k in sorted(self.video_src,
                                key=lambda k: self.video_src[k][1])[:128]:
                    self.video_src.pop(k, None)
        self._stream_relay(addr, data, len(data))

    def route_video_feedback(self, msg):
        try:
            fid = int(msg.get("fid", 0))
        except (TypeError, ValueError):
            return
        with self.clients_lock:
            entry = self.video_src.get(fid)
            if not entry or entry[1] < time.time():
                return
            src = entry[0]
        missing = msg.get("missing", [])
        if not isinstance(missing, list) or not 0 < len(missing) <= 512:
            return
        out = {"t": "video_nack", "fid": fid,
               "missing": [int(i) for i in missing if isinstance(i, int)]}
        try:
            self.server_socket.sendto(pack_control(out), src)
        except Exception:
            pass

    def handle_admin_move(self, addr, msg):
        """Админ (локальная панель) переносит игрока между войсами."""
        target = str(msg.get("nick", ""))[:MAX_NICK_LEN]
        chan = str(msg.get("chan", "")).strip()
        with self.clients_lock:
            if chan not in self.channels:
                return
            hit = None
            for a, c in self.clients.items():
                if c["name"] == target:
                    hit = (a, c)
                    break
            if not hit:
                return
            a, c = hit
            old = c["channel"]
            c["channel"] = chan
            was_sharing = c.get("sharing", False)
            self._send(a, {"t": "moved", "chan": chan})
        if was_sharing:
            self.broadcast_stream_end(a, old, target)
        self.log(f"Админ переместил '{target}': '{old}' -> '{chan}'")
        self.ui(self.update_info_label)

    def handle_stream_audio(self, addr, data):
        # C->S: b"S" + seq(4) + payload
        if len(data) < 6 or len(data) - 5 > 2048:
            self.strike(addr, "битый звук трансляции")
            return
        self._stream_relay(addr, data, len(data))

    def route_image_feedback(self, addr, t, msg):
        try:
            msg_id = bytes.fromhex(str(msg.get("id", "")))
        except Exception:
            return
        if len(msg_id) != 4:
            return
        with self.clients_lock:
            entry = self.img_src.get(msg_id)
            if not entry or entry[1] < time.time():
                return
            src = entry[0]
        out = {"t": t, "id": msg_id.hex()}
        if t == "img_nack":
            missing = msg.get("missing", [])
            if isinstance(missing, list) and 0 < len(missing) <= 2048:
                out["missing"] = [int(i) for i in missing if isinstance(i, int)]
            else:
                return
        try:
            self.server_socket.sendto(pack_control(out), src)
        except Exception:
            pass

    # ------------------------------------------------------------ background loops
    def broadcast_user_list(self):
        while self.is_running:
            time.sleep(USERLIST_INTERVAL)
            with self.clients_lock:
                users = [{
                    "name": c["name"], "muted": c["muted"], "mic_muted": c["mic_muted"],
                    "deafened": c["deafened"], "sharing": c["sharing"], "talking": c["talking"],
                    "channel": c["channel"],
                } for c in self.clients.values()]
                channels = {name: {"has_password": bool(pw)} for name, pw in self.channels.items()}
            packet = pack_control({"t": "userlist", "users": users, "channels": channels})
            with self.clients_lock:
                targets = list(self.clients.keys())
            for a in targets:
                try:
                    self.server_socket.sendto(packet, a)
                except Exception:
                    pass

    def cleanup_clients_loop(self):
        while self.is_running:
            time.sleep(2)
            now = time.time()
            with self.clients_lock:
                dead = [a for a, c in self.clients.items() if now - c["time"] > CLIENT_TIMEOUT]
                dead_info = [(a, self.clients[a]["name"], self.clients[a]["channel"],
                              self.clients[a].get("sharing", False)) for a in dead]
                for a in dead:
                    del self.clients[a]
                expired_img = [k for k, v in self.img_src.items() if v[1] < now]
                for k in expired_img:
                    self.img_src.pop(k, None)
                for k, v in list(self.file_src.items()):
                    if v[1] < now:
                        self.file_src.pop(k, None)
                for a, up in list(self.avatar_uploads.items()):
                    if now - up.get("t", 0) > 60.0:
                        self.avatar_uploads.pop(a, None)
                stale_usage = [a for a, (sec, _) in self.stream_usage.items() if sec != int(now)]
                for a in stale_usage:
                    self.stream_usage.pop(a, None)
            for a, name, chan, sharing in dead_info:
                self.log(f"Таймаут соединения: {name} ({a[0]}:{a[1]})")
                if sharing:
                    self.broadcast_stream_end(a, chan, name)
            self.ui(self.update_info_label)

    # ------------------------------------------------------------ user list GUI
    def _schedule_list_refresh(self):
        self.update_client_listbox()
        self.root.after(1000, self._schedule_list_refresh)

    def update_client_listbox(self):
        selected_text = None
        sel = self.client_listbox.curselection()
        if sel:
            selected_text = self.client_listbox.get(sel[0])

        self.client_listbox.delete(0, tk.END)
        with self.clients_lock:
            entries = sorted(self.clients.items(), key=lambda kv: kv[1]["name"])
        self.addr_map = [a for a, _ in entries]
        for i, (addr, c) in enumerate(entries):
            if c["muted"]:
                status = "[ МУТ АДМИНА ]"
            elif c["mic_muted"]:
                status = "[ МИКРО ВЫКЛ ]"
            elif c["talking"]:
                status = "[ ГОВОРИТ ]"
            else:
                status = "[ ОНЛАЙН ]"
            line = f"👤 {c['name']} [{c['channel']}] ({addr[0]}:{addr[1]}) — {status}"
            self.client_listbox.insert(tk.END, line)
            if selected_text and line == selected_text:
                self.client_listbox.selection_set(i)

    def _selected_addr(self):
        sel = self.client_listbox.curselection()
        if not sel:
            messagebox.showwarning("Внимание", "Выберите пользователя в списке!")
            return None
        idx = sel[0]
        if idx >= len(self.addr_map):
            return None
        return self.addr_map[idx]

    # ------------------------------------------------------------ admin actions
    def toggle_mute_client(self):
        addr = self._selected_addr()
        if addr is None:
            return
        with self.clients_lock:
            client = self.clients.get(addr)
            if not client:
                return
            new_mute = not client["muted"]
            client["muted"] = new_mute
            name = client["name"]
        self._send(addr, {"t": "mute" if new_mute else "unmute"})
        self.log(f"{'Мут' if new_mute else 'Размут'} админом: {name} ({addr[0]}:{addr[1]})")
        self.update_client_listbox()

    def kick_addr(self, addr, reason):
        with self.clients_lock:
            client = self.clients.pop(addr, None)
        if not client:
            return
        self._send(addr, {"t": "kicked", "reason": reason})
        self.log(f"Кикнут: {client['name']} ({addr[0]}:{addr[1]}) — {reason}")
        if client.get("sharing"):
            self.broadcast_stream_end(addr, client["channel"], client["name"])
        self.ui(self.update_info_label)

    def ban_account_nick(self, nick):
        """Забанить аккаунт по нику: ставит флаг, сохраняет и кикает активную сессию."""
        with self.clients_lock:
            self.accounts.setdefault(nick, {"salt": "", "hash": "", "banned": True})["banned"] = True
            targets = [a for a, c in self.clients.items() if c["name"] == nick]
        self.save_accounts()
        self.log(f"Аккаунт {nick} забанен")
        for a in targets:
            self.kick_addr(a, "Ваш аккаунт забанен на сервере.")

    def kick_client(self):
        addr = self._selected_addr()
        if addr is not None:
            self.kick_addr(addr, "Кикнут администратором")

    def ban_selected_client(self):
        addr = self._selected_addr()
        if addr is None:
            return
        ip = addr[0]
        self.banned.add(ip)
        self.save_bans()
        self.log(f"IP {ip} забанен")
        with self.clients_lock:
            targets = [a for a in self.clients if a[0] == ip]
        for a in targets:
            self.kick_addr(a, "Вы забанены на сервере")

    def unban_prompt(self):
        if not self.banned:
            messagebox.showinfo("Информация", "Список забаненных IP пуст.")
            return
        target = simpledialog.askstring(
            "Разбан IP",
            "Забаненные IP:\n" + ", ".join(sorted(self.banned)) + "\n\nВведите IP для разбана:")
        if target and target.strip() in self.banned:
            self.banned.discard(target.strip())
            self.save_bans()
            self.log(f"IP {target.strip()} разбанен")
            messagebox.showinfo("Успех", f"IP {target.strip()} разбанен.")

    def move_client_prompt(self):
        """Выбрать игрока в списке и перенести в другой голосовой канал."""
        sel = self.client_listbox.curselection()
        if not sel:
            messagebox.showinfo("Перенос", "Сначала выберите игрока в списке.")
            return
        with self.clients_lock:
            addrs = list(self.clients.keys())
            names = [self.clients[a]["name"] for a in addrs]
        if sel[0] >= len(names):
            return
        target = names[sel[0]]
        win = tk.Toplevel(self.root)
        win.title(f"Перенос: {target}")
        win.geometry("320x190")
        win.config(bg="#1a1c23")
        win.grab_set()
        tk.Label(win, text=f"Куда перенести '{target}'?", bg="#1a1c23",
                 fg="#ffffff", font=("Segoe UI", 10, "bold")).pack(pady=(14, 8))
        var = tk.StringVar(value=next(iter(self.channels), MAIN_CHANNEL))
        ttk.Combobox(win, textvariable=var, values=list(self.channels.keys()),
                     state="readonly", width=28).pack(pady=4)

        def do_move():
            self.handle_admin_move(None, {"nick": target, "chan": var.get()})
            win.destroy()

        tk.Button(win, text="Перенести", bg="#2980b9", fg="white",
                  font=("Segoe UI", 9, "bold"), relief="flat",
                  command=do_move).pack(fill="x", padx=24, pady=(14, 12))

    # ------------------------------------------------------------ channels
    def show_channel_passwords(self):
        lines = [f"{n} : {p or '(без пароля)'}" for n, p in self.channels.items()]
        messagebox.showinfo("Пароли каналов",
                            "\n".join(lines) if lines else "Каналов нет")

    def show_account_passwords(self):
        lines = [f"{n} : {p}" for n, p in self.account_passwords.items()]
        messagebox.showinfo(
            "Пароли аккаунтов (только в памяти)",
            ("⚠ Пароли хранятся только в ОЗУ и исчезают при перезапуске сервера.\n"
             "На диск не записываются.\n\n" +
             ("\n".join(lines) if lines else "Пока никто не входил/не регистрировался.")))

    def create_channel_prompt(self):
        name = simpledialog.askstring("Создание войса", "Введите название нового голосового канала:")
        if not name or not name.strip():
            return
        name = name.strip()[:MAX_NICK_LEN]
        if name in self.channels:
            messagebox.showerror("Ошибка", f"Канал '{name}' уже существует!")
            return
        pw = simpledialog.askstring("Пароль", f"Пароль для канала '{name}'\n(пусто — без пароля):", show="*") or ""
        with self.clients_lock:
            self.channels[name] = pw
        self.save_config()
        self.log(f"Создан канал: '{name}' | пароль: '{pw or '(нет)'}'")
        self.update_info_label()

    def change_channel_password(self):
        name = simpledialog.askstring("Пароль канала", "Название канала:\n" + ", ".join(self.channels) + "\n\nКанал:")
        if not name or not name.strip():
            return
        name = name.strip()
        if name not in self.channels:
            messagebox.showerror("Ошибка", f"Канал '{name}' не найден!")
            return
        pw = simpledialog.askstring("Пароль", f"Новый пароль для '{name}'\n(пусто — убрать пароль):", show="*") or ""
        with self.clients_lock:
            self.channels[name] = pw
        self.save_config()
        self.log(f"Пароль канала '{name}' изменен | новый пароль: '{pw or '(нет)'}'")
        self.update_info_label()

    def delete_channel_prompt(self):
        name = simpledialog.askstring("Удаление войса", "Название канала для удаления:")
        if not name or not name.strip():
            return
        name = name.strip()
        if name == MAIN_CHANNEL:
            messagebox.showwarning("Ошибка", f"Нельзя удалить основной канал '{MAIN_CHANNEL}'!")
            return
        if name not in self.channels:
            messagebox.showerror("Ошибка", f"Канал '{name}' не найден!")
            return
        with self.clients_lock:
            del self.channels[name]
            moved = []
            for a, c in self.clients.items():
                if c["channel"] == name:
                    c["channel"] = MAIN_CHANNEL
                    moved.append(a)
            self._send_to_locked(MAIN_CHANNEL, {"t": "chan_removed", "chan": name})
        self.save_config()
        self.log(f"Канал '{name}' удален, пользователи перенесены в '{MAIN_CHANNEL}'")
        self.update_info_label()

    def _send_to_locked(self, chan, obj):
        # вызывать, удерживая clients_lock
        try:
            packet = pack_control(obj)
            targets = [a for a, c in self.clients.items() if c["channel"] == chan]
        except Exception:
            return
        for a in targets:
            try:
                self.server_socket.sendto(packet, a)
            except Exception:
                pass

    # ------------------------------------------------------------ аккаунты
    def open_accounts_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Аккаунты")
        win.geometry("420x420")
        win.config(bg="#1a1c23")
        win.grab_set()

        tk.Label(win, text="АККАУНТЫ", bg="#1a1c23", fg="#00ffcc",
                 font=("Segoe UI", 12, "bold")).pack(pady=(12, 6))
        lst = tk.Listbox(win, bg="#121318", fg="#00ffcc", font=("Consolas", 10),
                         bd=0, highlightthickness=0)
        lst.pack(fill="both", expand=True, padx=14)
        with self.clients_lock:
            names = sorted(self.accounts.keys())
            online = {c["name"] for c in self.clients.values()}
        for n in names:
            a = self.accounts[n]
            tag = "🚫 ЗАБАНЕН" if a.get("banned") else ("🟢 в сети" if n in online else "ок")
            pw = self.account_passwords.get(n, "— (нет в RAM)")
            lst.insert(tk.END, f"{n:18s} pw:{pw:14s} {tag}")

        def selected():
            sel = lst.curselection()
            if not sel:
                return None
            return names[sel[0]]

        def ban_account():
            n = selected()
            if not n:
                return
            with self.clients_lock:
                self.accounts.setdefault(n, {})["banned"] = True
                targets = [a for a, c in self.clients.items() if c["name"] == n]
            self.save_accounts()
            self.log(f"Аккаунт {n} забанен")
            for a in targets:
                self.kick_addr(a, "Ваш аккаунт забанен на сервере.")
            win.destroy()
            self.open_accounts_dialog()

        def unban_account():
            n = selected()
            if not n:
                return
            with self.clients_lock:
                if n in self.accounts:
                    self.accounts[n]["banned"] = False
            self.save_accounts()
            self.log(f"Аккаунт {n} разбанен")
            win.destroy()
            self.open_accounts_dialog()

        def delete_account():
            n = selected()
            if not n:
                return
            if not messagebox.askyesno("Удаление", f"Удалить аккаунт '{n}'?"):
                return
            with self.clients_lock:
                self.accounts.pop(n, None)
                targets = [a for a, c in self.clients.items() if c["name"] == n]
            self.save_accounts()
            self.log(f"Аккаунт {n} удален")
            for a in targets:
                self.kick_addr(a, "Ваш аккаунт был удален с сервера.")
            win.destroy()
            self.open_accounts_dialog()

        btns = tk.Frame(win, bg="#1a1c23")
        btns.pack(fill="x", padx=14, pady=10)
        tk.Button(btns, text="🚫 Забанить", bg="#8b0000", fg="white", relief="flat",
                  font=("Segoe UI", 9, "bold"), command=ban_account).pack(side="left", expand=True, fill="x", padx=(0, 3))
        tk.Button(btns, text="✅ Разбанить", bg="#27ae60", fg="white", relief="flat",
                  font=("Segoe UI", 9, "bold"), command=unban_account).pack(side="left", expand=True, fill="x", padx=3)
        tk.Button(btns, text="🗑 Удалить", bg="#e74c3c", fg="white", relief="flat",
                  font=("Segoe UI", 9, "bold"), command=delete_account).pack(side="left", expand=True, fill="x", padx=(3, 0))

    # ------------------------------------------------------------ settings
    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Настройки сервера")
        win.geometry("380x280")
        win.config(bg="#1a1c23")
        win.resizable(False, False)
        win.grab_set()

        tk.Label(win, text="Порт сервера (нужен перезапуск сети)", bg="#1a1c23", fg="#00ffcc",
                 font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=20, pady=(15, 2))
        port_entry = tk.Entry(win, font=("Segoe UI", 11))
        port_entry.insert(0, str(self.port))
        port_entry.pack(fill="x", padx=20, ipady=4)

        tk.Label(win, text="Пароль сервера (пусто — вход без пароля)", bg="#1a1c23", fg="#00ffcc",
                 font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=20, pady=(10, 2))
        pass_entry = tk.Entry(win, font=("Segoe UI", 11), show="*")
        pass_entry.insert(0, self.server_password)
        pass_entry.pack(fill="x", padx=20, ipady=4)

        reg_var = tk.BooleanVar(value=self.registration)
        tk.Checkbutton(win, text="Открытая регистрация аккаунтов", variable=reg_var,
                       bg="#1a1c23", fg="#b0b0b8", selectcolor="#121318",
                       activebackground="#1a1c23", activeforeground="#00ffcc",
                       font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=20, pady=(10, 0))

        def apply():
            try:
                new_port = int(port_entry.get().strip())
                if not (1 <= new_port <= 65535):
                    raise ValueError
            except ValueError:
                messagebox.showerror("Ошибка", "Порт должен быть числом 1-65535!", parent=win)
                return
            self.server_password = pass_entry.get().strip()
            self.registration = bool(reg_var.get())
            self.port = new_port
            self.save_config()
            self.log("Настройки сервера сохранены")
            win.destroy()
            self.restart_network()

        tk.Button(win, text="Сохранить и перезапустить сеть", bg="#5865f2", fg="white",
                  font=("Segoe UI", 10, "bold"), relief="flat", command=apply).pack(fill="x", padx=20, pady=(15, 5))
        tk.Button(win, text="Закрыть", bg="#242730", fg="#b0b0b8", font=("Segoe UI", 9),
                  relief="flat", command=win.destroy).pack(fill="x", padx=20, pady=5)

    # ------------------------------------------------------------ shutdown
    def on_close(self):
        self.is_running = False
        try:
            if self.server_socket:
                self.server_socket.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    root = tk.Tk()
    app = VoiceServerApp(root)
    root.mainloop()


