# ====================================================
# TT11TT — Discord боты (v3, полная версия без Intents)
# ====================================================
import os
import sys
import asyncio
import logging
import random
import time
import html
import signal
import aiohttp
import discord
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import json
import traceback

# ====================================================
# ЛОГИРОВАНИЕ
# ====================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
    force=True
)
log = logging.getLogger(__name__)

def exception_handler(loop, context):
    log.error("Необработанная ошибка: %s", context.get("exception", context["message"]))
    exc = context.get("exception")
    if exc and hasattr(exc, "__traceback__"):
        log.error("".join(traceback.format_tb(exc.__traceback__)))

# ====================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# ====================================================
def get_env_int(name, default=0):
    val = os.getenv(name)
    if not val:
        return default
    val = str(val).strip().strip('"').strip("'")
    try:
        return int(val)
    except ValueError:
        log.error("ОШИБКА: переменная %s должна быть числом, а не '%s'!", name, val)
        return default

def get_env_str(name):
    val = os.getenv(name)
    if not val:
        return None
    return val.strip().strip('"').strip("'")

def get_env_bool(name, default=False):
    val = get_env_str(name)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "on")

TOKEN_MAIN = get_env_str("DISCORD_TOKEN_1")
TOKEN_SECOND = get_env_str("DISCORD_TOKEN_4")
TOKEN_THIRD = get_env_str("DISCORD_TOKEN_5")  # УПРАВЛЯЮЩИЙ

MAIN_CHANNEL_ID = get_env_int("VOICE_CHANNEL_ID")
GUILD_ID = get_env_int("GUILD_ID")

TOKEN_EGOIST = get_env_str("DISCORD_TOKEN_2")
USER_ID_EGOIST = get_env_int("USER_ID_1")

TOKEN_EGORIK = get_env_str("DISCORD_TOKEN_3")
USER_ID_EGORIK = get_env_int("USER_ID_2")

MAIN_BOT_USER_ID = get_env_int("MAIN_BOT_USER_ID", 0)
RENDER_URL = get_env_str("RENDER_URL") or "https://tt11tt.onrender.com"
TELEGRAM_TOKEN = get_env_str("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = get_env_str("TELEGRAM_CHAT_ID")
STATUS_ROTATION = get_env_bool("STATUS_ROTATION", False)

log.info("=" * 50)
log.info("Конфиг: канал=%s, гильдия=%s, статус-ротация=%s",
         MAIN_CHANNEL_ID, GUILD_ID or "все", "ON" if STATUS_ROTATION else "OFF")
if GUILD_ID == 0:
    log.warning("GUILD_ID не задан — боты реагируют на события во всех гильдиях!")
log.info("=" * 50)

# ====================================================
# КОНСТАНТЫ
# ====================================================
STATUS_INTERVAL = 3600
DEAD_SOCKET_TIMEOUT = 900
DEAD_SOCKET_CRITICAL = 3600
CONNECT_TIMEOUT = 60.0
RECONNECT_MIN_DELAY = 2.0
RECONNECT_MAX_DELAY = 8.0
MESSAGE_DELETE_DELAY = 15 * 60 # 15 минут

GAMES_POOL = [
    {"name": "Counter-Strike 2", "app_id": 730},
    {"name": "Dota 2", "app_id": 570},
    {"name": "PUBG: BATTLEGROUNDS", "app_id": 578080},
    {"name": "Apex Legends", "app_id": 1172470},
    {"name": "Grand Theft Auto V", "app_id": 271590},
    {"name": "Rust", "app_id": 252490},
]
TEXT_STATUSES = ["Commback", "TALENT 24/7", "На страже", "Сижу в войсе"]
WATCHING_STATUSES = ["как вы играете", "на ваши сообщения", "за порядком"]

# ====================================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ
# ====================================================
watcher_egoist = None
watcher_egorik = None
start_time = time.time()

reconnect_stats = {"main": 0, "second": 0, "third": 0, "egoist": 0, "egorik": 0}
reconnect_history = {k: deque(maxlen=200) for k in reconnect_stats}
last_event_time = {k: time.time() for k in reconnect_stats}
total_messages_sent = 0
total_telegram_sent = 0
event_log = deque(maxlen=30)

_watchdogs_started = set()
_user_cache = {}
_dm_notifications_enabled = True

_main_lock = asyncio.Lock()
_second_lock = asyncio.Lock()
_third_lock = asyncio.Lock()

_shutdown_event = asyncio.Event()
_clients_ready = asyncio.Event()

# ====================================================
# КЛИЕНТЫ
# ====================================================
main_client = discord.Client(chunk_guilds_at_startup=False)
second_client = discord.Client(chunk_guilds_at_startup=False)
third_client = discord.Client(chunk_guilds_at_startup=False)

# ====================================================
# ВЕБ-СЕРВЕР
# ====================================================
class HealthHandler(BaseHTTPRequestHandler):
    def _respond(self, code, body, ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/status"):
            uptime = int(time.time() - start_time)
            payload = {
                "ok": True,
                "uptime_sec": uptime,
                "reconnects": dict(reconnect_stats),
                "messages_sent": total_messages_sent,
                "telegram_sent": total_telegram_sent,
                "dm_enabled": _dm_notifications_enabled,
                "clients": {
                    "main": main_client.is_ready() if TOKEN_MAIN else None,
                    "second": second_client.is_ready() if TOKEN_SECOND else None,
                    "third": third_client.is_ready() if TOKEN_THIRD else None,
                    "egoist": watcher_egoist.client.is_ready() if watcher_egoist and TOKEN_EGOIST else None,
                    "egorik": watcher_egorik.client.is_ready() if watcher_egorik and TOKEN_EGORIK else None,
                },
            }
            self._respond(200, json.dumps(payload, ensure_ascii=False).encode(), "application/json")
        else:
            self._respond(200, b"Bot is running")

    def log_message(self, *args):
        pass

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    log.info("Веб-сервер запущен на порту %s (endpoint /status)", port)
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ====================================================
# УТИЛИТЫ
# ====================================================
def log_event(text: str):
    ts = time.strftime("%H:%M:%S")
    event_log.append(f"[{ts}] {text}")
    log.info(text)

def log_reconnect(name: str):
    reconnect_stats[name] += 1
    reconnect_history[name].append(time.time())
    log_event(f"реконнект {name} #{reconnect_stats[name]}")

async def get_cached_user(client, user_id):
    if not user_id:
        return None
    user = _user_cache.get(user_id)
    if user is None:
        try:
            user = client.get_user(user_id) or await client.fetch_user(user_id)
            if user is not None:
                _user_cache[user_id] = user
        except Exception:
            return None
    return user

async def send_dm_notification(user_id, text):
    global total_messages_sent, _dm_notifications_enabled
    if not _dm_notifications_enabled or not user_id:
        return
    if not third_client or not third_client.is_ready():
        return
    try:
        user = await get_cached_user(third_client, user_id)
        if user:
            await user.send(text)
            total_messages_sent += 1
    except discord.Forbidden as e:
        code = getattr(e, "code", None)
        status = getattr(e, "status", 0) or 0
        if code == 40001 or status == 401:
            _dm_notifications_enabled = False
            log.critical("DM: 40001/401 — токен не авторизован (возможен бан/отзыв). ЛС отключены.")
        else:
            log.warning("DM %s: ЛС закрыты — пропускаю", user_id)
    except discord.NotFound:
        log.warning("DM %s: пользователь не найден", user_id)
    except Exception as e:
        log.warning("DM: ошибка отправки: %s", e)

def tg_escape(s: str) -> str:
    return html.escape(str(s), quote=False)

async def send_telegram(text: str):
    global total_telegram_sent
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            async with session.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=10,
            ) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:200]
                    log.warning("TG: ошибка %s: %s", resp.status, body)
                else:
                    total_telegram_sent += 1
    except Exception as e:
        log.warning("TG: ошибка отправки: %s", e)

async def uptime_pinger():
    await asyncio.sleep(60)
    while not _shutdown_event.is_set():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RENDER_URL, timeout=20) as resp:
                    log.info("UPTIME: пинг %s -> %s", RENDER_URL, resp.status)
        except Exception as e:
            log.warning("UPTIME: ошибка пинга: %s", e)
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=14 * 60)
        except asyncio.TimeoutError:
            pass

async def auto_delete_message(msg, delay=MESSAGE_DELETE_DELAY):
    try:
        await asyncio.sleep(delay)
        await msg.delete()
    except discord.NotFound:
        pass
    except Exception as e:
        log.warning(f"[AUTO-DELETE] Ошибка удаления: {e}")

def build_reconnect_chart(name: str, hours: int = 24) -> str:
    if hours <= 0:
        return ""
    now = time.time()
    cutoff = now - hours * 3600
    buckets = [0] * hours
    for ts in reconnect_history[name]:
        if ts < cutoff:
            continue
        idx = int((ts - cutoff) / 3600)
        if 0 <= idx < hours:
            buckets[idx] += 1
    max_val = max(buckets) if buckets else 0
    if max_val == 0:
        return "▁" * hours
    chars = "▁▂▃▄▅▆▇█"
    return "".join(chars[min(int(b / max_val * (len(chars) - 1)), len(chars) - 1)] for b in buckets)

def get_latency_info() -> str:
    info = []
    pairs = [
        ("main", main_client), ("second", second_client if TOKEN_SECOND else None),
        ("third", third_client if TOKEN_THIRD else None),
        ("egoist", watcher_egoist.client if watcher_egoist else None),
        ("egorik", watcher_egorik.client if watcher_egorik else None),
    ]
    for name, client in pairs:
        if client and client.latency:
            ms = int(client.latency * 1000)
            q = "🟢" if ms < 100 else "🟡" if ms < 200 else "🟠" if ms < 400 else "🔴"
            info.append(f"`{name:8}` {ms}ms {q}")
    return "\n".join(info) if info else "нет данных"

def fmt_uptime() -> str:
    u = int(time.time() - start_time)
    return f"{u // 3600}ч {(u % 3600) // 60}м {u % 60}с"

# ====================================================
# СМЕНА СТАТУСА
# ====================================================
async def status_changer(client, name):
    if not STATUS_ROTATION:
        log.info("[%s] ротация статусов выключена (STATUS_ROTATION=off)", name)
        return
    while not _shutdown_event.is_set():
        try:
            if not getattr(client, "is_afk", False) and client.is_ready():
                r = random.random()
                if r < 0.4:
                    game = random.choice(GAMES_POOL)
                    activity = discord.Activity(
                        type=discord.ActivityType.playing, name=game["name"],
                        application_id=game["app_id"],
                        timestamps={"start": int(time.time() * 1000)},
                    )
                elif r < 0.7:
                    activity = discord.Activity(type=discord.ActivityType.watching,
                                                name=random.choice(WATCHING_STATUSES))
                else:
                    activity = discord.Activity(type=discord.ActivityType.playing,
                                                name=random.choice(TEXT_STATUSES))
                await client.change_presence(activity=activity)
        except Exception as e:
            log.warning("[%s] ошибка статуса: %s", name, e)
        try:
            await asyncio.wait_for(
                _shutdown_event.wait(),
                timeout=STATUS_INTERVAL + random.uniform(0, 300),
            )
        except asyncio.TimeoutError:
            pass

# ====================================================
# НАБЛЮДАТЕЛИ
# ====================================================
class Watcher:
    def __init__(self, token, user_id, name):
        self.token = token
        self.user_id = user_id
        self.name = name
        self.client = discord.Client(chunk_guilds_at_startup=False)
        self.client.is_afk = False
        self.is_reconnecting = False
        self.manual_mode = False
        self.disabled = False
        self._started = set()
        self._connect_lock = None
        self._register_events()

    def _task_once(self, key, coro):
        if key not in self._started:
            self._started.add(key)
            self.client.loop.create_task(coro)

    def _register_events(self):
        @self.client.event
        async def on_ready():
            log.info("[%s] авторизован как %s", self.name, self.client.user)
            self._task_once("watchdog", self.watchdog())
            self._task_once("dead", self.dead_socket_watchdog())
            self._task_once("status", status_changer(self.client, self.name))

        @self.client.event
        async def on_socket_event_type(event_type):
            last_event_time[self.name.lower()] = time.time()

        @self.client.event
        async def on_presence_update(before, after):
            last_event_time[self.name.lower()] = time.time()
            if not self.client.is_ready():
                return
            guild = getattr(after, "guild", None)
            if GUILD_ID and guild is not None and guild.id != GUILD_ID:
                return
            if self.disabled or self.manual_mode or after.id != self.user_id:
                return
            try:
                if after.status == discord.Status.offline:
                    log_event(f"[{self.name}] владелец офлайн, захожу через 30с")
                    await asyncio.sleep(30)
                    if not self.disabled and not self.manual_mode:
                        user = await get_cached_user(self.client, self.user_id)
                        if user and user.status == discord.Status.offline:
                            await self.connect(MAIN_CHANNEL_ID)
                else:
                    log_event(f"[{self.name}] владелец вернулся, выхожу")
                    await self.disconnect()
            except Exception as e:
                log.warning("[%s] on_presence_update: %s", self.name, e)

        @self.client.event
        async def on_voice_state_update(member, before, after):
            last_event_time[self.name.lower()] = time.time()
            if GUILD_ID and member.guild.id != GUILD_ID:
                return
            me = self.client.user
            if me is None:
                return
            if member.id == me.id:
                if self.disabled or not self.manual_mode:
                    return
                if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
                    log.warning("[%s] бот вылетел, возвращаю...", self.name)
                    log_reconnect(self.name.lower())
                    await asyncio.sleep(random.uniform(RECONNECT_MIN_DELAY, RECONNECT_MAX_DELAY))
                    if self.manual_mode and not self.disabled:
                        await self.connect(MAIN_CHANNEL_ID)
            elif member.id == self.user_id:
                if not (self.manual_mode and not self.disabled):
                    return
                if after.channel:
                    if (not self.client.voice_clients
                            or self.client.voice_clients[0].channel.id != after.channel.id):
                        await self.connect(after.channel.id)
                else:
                    if (self.client.voice_clients
                            and self.client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
                        await self.connect(MAIN_CHANNEL_ID)

    async def toggle_afk(self):
        vc = self.client.voice_clients[0] if self.client.voice_clients else None
        if not vc or not vc.is_connected():
            return False, "бот не в войсе"
        self.client.is_afk = not self.client.is_afk
        try:
            if self.client.is_afk:
                await vc.guild.change_voice_state(channel=vc.channel, self_mute=True, self_deaf=True)
                await self.client.change_presence(
                    activity=discord.Activity(type=discord.ActivityType.watching, name="AFK"))
                return True, "включен"
            await vc.guild.change_voice_state(channel=vc.channel, self_mute=False, self_deaf=False)
            return True, "выключен"
        except Exception as e:
            return False, str(e) or "неизвестная ошибка"

    async def watchdog(self):
        while not _shutdown_event.is_set():
            await asyncio.sleep(random.uniform(3, 7))
            if self.disabled or not self.manual_mode:
                continue
            owner_in_vc = False
            if self.user_id:
                try:
                    user = await get_cached_user(self.client, self.user_id)
                    if user and user.voice and user.voice.channel:
                        owner_in_vc = True
                except Exception:
                    pass
            if not owner_in_vc:
                if (not self.client.voice_clients
                        or not self.client.voice_clients[0].is_connected()
                        or self.client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
                    log_reconnect(self.name.lower())
                    await self.connect(MAIN_CHANNEL_ID)

    async def dead_socket_watchdog(self):
        while not _shutdown_event.is_set():
            await asyncio.sleep(60)
            silence = time.time() - last_event_time.get(self.name.lower(), time.time())
            if silence > DEAD_SOCKET_CRITICAL:
                log.critical(
                    "[%s] молчание %dс (>%dс) — возможно, токен мёртв. "
                    "Реконнекты приостановлены до восстановления событий.",
                    self.name, int(silence), DEAD_SOCKET_CRITICAL,
                )
                continue
            if silence > DEAD_SOCKET_TIMEOUT:
                log.warning("[%s] мёртвый сокет (%dс), реконнект", self.name, int(silence))
                log_reconnect(self.name.lower())
                if self.client.voice_clients:
                    try:
                        await self.client.voice_clients[0].disconnect(force=True)
                    except Exception:
                        pass
                await asyncio.sleep(random.uniform(RECONNECT_MIN_DELAY, RECONNECT_MAX_DELAY))
                if self.manual_mode and not self.disabled:
                    await self.connect(MAIN_CHANNEL_ID)

    async def connect(self, channel_id):
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            if self.disabled or channel_id == 0:
                return False, "Отключен"
            try:
                channel = self.client.get_channel(channel_id)
                if not channel:
                    try:
                        channel = await self.client.fetch_channel(channel_id)
                    except Exception:
                        log.warning("[%s] канал %s не найден", self.name, channel_id)
                        return False, "Канал не найден"
                if (self.client.voice_clients
                        and self.client.voice_clients[0].is_connected()
                        and self.client.voice_clients[0].channel.id == channel_id):
                    return True, "Уже в канале"
                if self.client.voice_clients:
                    try:
                        await self.client.voice_clients[0].disconnect(force=True)
                    except Exception:
                        pass
                    await asyncio.sleep(1)
                await channel.connect(timeout=CONNECT_TIMEOUT, self_deaf=False, self_mute=False)
                log.info("[%s] подключен к %s", self.name, channel.name)
                return True, "Успешно"
            except asyncio.TimeoutError:
                log.warning("[%s] таймаут подключения к %s", self.name, channel_id)
                return False, "Таймаут"
            except Exception as e:
                log.warning("[%s] ошибка подключения: %s", self.name, e)
                return False, str(e)

    async def disconnect(self):
        if self.client.voice_clients:
            try:
                await self.client.voice_clients[0].disconnect(force=True)
                self.client.is_afk = False
            except Exception:
                pass

    async def start(self):
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        delay = 30
        while not _shutdown_event.is_set():
            try:
                await self.client.start(self.token)
                delay = 30
            except discord.HTTPException as e:
                if e.status == 429:
                    log.error("[%s] 429, жду %dс", self.name, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 600)
                    continue
                log.error("[%s] HTTP %s, рестарт через %dс", self.name, e.status, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 600)
            except Exception as e:
                log.error("[%s] ошибка: %s, рестарт через %dс", self.name, e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 600)

# ====================================================
# MAIN / SECOND / THIRD
# ====================================================
_LOCKS = {"main": _main_lock, "second": _second_lock, "third": _third_lock}
_CLIENTS = {"main": main_client, "second": second_client, "third": third_client}

async def _ensure_connected(client_name):
    client = _CLIENTS[client_name]
    if MAIN_CHANNEL_ID == 0:
        return
    if (client.voice_clients
            and client.voice_clients[0].is_connected()
            and client.voice_clients[0].channel.id == MAIN_CHANNEL_ID):
        return
    async with _LOCKS[client_name]:
        if (client.voice_clients
                and client.voice_clients[0].is_connected()
                and client.voice_clients[0].channel.id == MAIN_CHANNEL_ID):
            return
        channel = client.get_channel(MAIN_CHANNEL_ID)
        if not channel:
            try:
                channel = await client.fetch_channel(MAIN_CHANNEL_ID)
            except Exception:
                log.warning("[%s] канал %s не найден", client_name.upper(), MAIN_CHANNEL_ID)
                return
        if client.voice_clients:
            try:
                await client.voice_clients[0].disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(1)
        try:
            await channel.connect(timeout=CONNECT_TIMEOUT, self_deaf=False, self_mute=False)
            log.info("[%s] подключен к %s", client_name.upper(), channel.name)
        except asyncio.TimeoutError:
            log.warning("[%s] таймаут подключения", client_name.upper())
        except Exception as e:
            log.warning("[%s] ошибка подключения: %s", client_name.upper(), e)

async def main_watchdog():
    while not _shutdown_event.is_set():
        await asyncio.sleep(random.uniform(5, 10))
        if (not main_client.voice_clients
                or not main_client.voice_clients[0].is_connected()
                or main_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log_reconnect("main")
            await _ensure_connected("main")

async def second_watchdog():
    while not _shutdown_event.is_set():
        await asyncio.sleep(random.uniform(5, 10))
        if (not second_client.voice_clients
                or not second_client.voice_clients[0].is_connected()
                or second_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log_reconnect("second")
            await _ensure_connected("second")

async def third_watchdog():
    while not _shutdown_event.is_set():
        await asyncio.sleep(random.uniform(5, 10))
        if (not third_client.voice_clients
                or not third_client.voice_clients[0].is_connected()
                or third_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log_reconnect("third")
            await _ensure_connected("third")

def _bind_client_events(name, client, watchdog_coro, ready_extra=None):
    @client.event
    async def on_ready():
        log.info("[%s] авторизован как %s", name.upper(), client.user)
        try:
            await _ensure_connected(name)
        except Exception as e:
            log.warning("[%s] _ensure_connected в on_ready: %s", name.upper(), e)
        if name not in _watchdogs_started:
            _watchdogs_started.add(name)
            client.loop.create_task(watchdog_coro)
            client.loop.create_task(status_changer(client, name.upper()))
        if ready_extra:
            try:
                await ready_extra()
            except Exception as e:
                log.warning("[%s] ready_extra: %s", name.upper(), e)

    @client.event
    async def on_socket_event_type(event_type):
        last_event_time[name] = time.time()

    @client.event
    async def on_voice_state_update(member, before, after):
        last_event_time[name] = time.time()
        if GUILD_ID and member.guild.id != GUILD_ID:
            return
        me = client.user
        if me is None:
            return
        if member.id != me.id:
            return
        if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
            log_reconnect(name)
            await asyncio.sleep(random.uniform(RECONNECT_MIN_DELAY, RECONNECT_MAX_DELAY))
            await _ensure_connected(name)

async def _third_ready_extra():
    try:
        await asyncio.wait_for(_clients_ready.wait(), timeout=10)
    except asyncio.TimeoutError:
        pass
    await send_dm_notification(USER_ID_EGOIST, "Управляющий бот (THIRD) запущен.")
    await send_telegram(
        f"<b>TT11TT (THIRD) запущен</b>\nАптайм: <code>{tg_escape(fmt_uptime())}</code>"
    )

if TOKEN_MAIN:
    _bind_client_events("main", main_client, main_watchdog())
if TOKEN_SECOND:
    _bind_client_events("second", second_client, second_watchdog())
if TOKEN_THIRD:
    _bind_client_events("third", third_client, third_watchdog(), ready_extra=_third_ready_extra)

# ====================================================
# КОМАНДЫ THIRD (ЛС)
# ====================================================
@third_client.event
async def on_message(message):
    global total_messages_sent
    if third_client.user is None:
        return
    if message.author.id == third_client.user.id:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return
    if message.author.id not in (USER_ID_EGOIST, USER_ID_EGORIK, MAIN_BOT_USER_ID, third_client.user.id):
        return

    content = message.content.strip().lower()

    if content in ("!status", "!panel", "!menu"):
        def w_state(w):
            if not w:
                return "❌ нет"
            if w.disabled:
                return "🔴 отключён"
            afk = " 💤[AFK]" if getattr(w.client, "is_afk", False) else ""
            return f"🟢 ручной{afk}" if w.manual_mode else f"⚙️ авто{afk}"

        third_vc = third_client.voice_clients[0] if third_client.voice_clients else None
        third_in = "🟢 в войсе" if (third_vc and third_vc.is_connected()) else "🔴 нет"
        panel = (
            f"🎛 **ПАНЕЛЬ УПРАВЛЕНИЯ (THIRD)**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Фон:**\n"
            f"• Канал: <#{MAIN_CHANNEL_ID}>\n"
            f"• THIRD-бот: {third_in}\n"
            f"• Реконнектов THIRD: `{reconnect_stats['third']}`\n\n"
            f"**egoist:** {w_state(watcher_egoist)} (реконнектов: {reconnect_stats['egoist']})\n"
            f"**egorik:** {w_state(watcher_egorik)} (реконнектов: {reconnect_stats['egorik']})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"`!on egoist` / `!off egoist`\n"
            f"`!on egorik` / `!off egorik`\n"
            f"`!all on` / `!all off`\n"
            f"`!afk` / `!ping` / `!stats` / `!chart`\n"
            f"`!log` / `!who` / `!tgtest` / `!uptime`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Uptime: `{fmt_uptime()}`\n\n"
            f"**📡 Пинг:**\n{get_latency_info()}"
        )
        msg = await message.channel.send(panel)
        third_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        total_messages_sent += 1
        return

    if content == "!uptime":
        msg = await message.channel.send(f"Uptime: `{fmt_uptime()}`")
        third_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        total_messages_sent += 1
        return

    if content == "!ping":
        start_ping = time.time()
        msg = await message.channel.send("🏓 Пингую узлы...")
        total_messages_sent += 1
        api_ping, api_status = -1, "ошибка"
        async with aiohttp.ClientSession() as session:
            for url in ("https://discord.com/api/v10/gateway",
                        "https://discord.com/api/v9/gateway"):
                try:
                    t0 = time.time()
                    async with session.get(url, timeout=5) as resp:
                        api_ping = int((time.time() - t0) * 1000)
                        api_status = "OK" if resp.status == 200 else str(resp.status)
                        break
                except Exception:
                    continue
            web_ping, web_status = -1, "ошибка"
            try:
                t0 = time.time()
                async with session.get(RENDER_URL, timeout=10) as resp:
                    web_ping = int((time.time() - t0) * 1000)
                    web_status = "OK" if resp.status == 200 else str(resp.status)
            except Exception:
                pass
        ws_ping = int(third_client.latency * 1000)
        await msg.edit(content=(
            f"🏓 **Умный пинг**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔌 WebSocket (THIRD): `{ws_ping}ms`\n"
            f"🌐 Discord API: `{api_ping}ms` ({api_status})\n"
            f"🖥 Render Web: `{web_ping}ms` ({web_status})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Задержка сообщения: `{int((time.time() - start_ping) * 1000)}ms`"
        ))
        third_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    if content == "!stats":
        stats_text = (
            f"📊 **СТАТИСТИКА**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 Uptime: `{fmt_uptime()}`\n"
            f"💬 Discord DM: `{total_messages_sent}`\n"
            f"📨 Telegram: `{total_telegram_sent}`\n"
            f"🔇 DM-уведомления: {'вкл' if _dm_notifications_enabled else 'выкл'}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔄 Реконнекты:\n"
            f"• MAIN: `{reconnect_stats['main']}`\n"
            f"• SECOND: `{reconnect_stats['second']}`\n"
            f"• THIRD: `{reconnect_stats['third']}`\n"
            f"• Egoist: `{reconnect_stats['egoist']}`\n"
            f"• Egorik: `{reconnect_stats['egorik']}`\n"
        )
        msg = await message.channel.send(stats_text)
        third_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        total_messages_sent += 1
        return

    if content == "!log":
        logs = "\n".join(list(event_log)[-15:]) if event_log else "пусто"
        msg = await message.channel.send(f"**📜 Лог:**\n```\n{logs}\n```")
        third_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        total_messages_sent += 1
        return

    if content == "!chart":
        lines = ["**📈 Реконнекты за 24ч**\n"]
        for n in reconnect_stats:
            lines.append(f"`{n:8}` {build_reconnect_chart(n, 24)}  всего: {reconnect_stats[n]}")
        msg = await message.channel.send("\n".join(lines))
        third_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        total_messages_sent += 1
        return

    if content == "!who":
        channel = third_client.get_channel(MAIN_CHANNEL_ID)
        if not channel:
            try:
                channel = await third_client.fetch_channel(MAIN_CHANNEL_ID)
            except Exception:
                channel = None
        if not channel:
            msg = await message.channel.send("⚠️ Канал не найден.")
        else:
            members = list(getattr(channel, "members", []) or [])
            if not members:
                guild = getattr(channel, "guild", None)
                if guild is not None:
                    try:
                        async for m in guild.fetch_members(limit=200):
                            if m.voice and m.voice.channel and m.voice.channel.id == channel.id:
                                members.append(m)
                    except Exception:
                        pass
            if members:
                names = [tg_escape(m.display_name) for m in members]
                msg = await message.channel.send(f"**🎙 В войсе ({len(names)}):**\n" + "\n".join(f"• {n}" for n in names))
            else:
                msg = await message.channel.send("🎙 В войсе никого.")
        third_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        total_messages_sent += 1
        return

    if content == "!tgtest":
        await send_telegram("🧪 Тестовое сообщение из TT11TT")
        msg = await message.channel.send("Отправлено в Telegram.")
        third_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        total_messages_sent += 1
        return

    if content == "!afk":
        target = (watcher_egoist if message.author.id == USER_ID_EGOIST
                  else watcher_egorik if message.author.id == USER_ID_EGORIK else None)
        if target:
            success, state = await target.toggle_afk()
            msg = f"💤 Режим AFK {state}." if success else f"⚠️ Ошибка: {state}"
            msg_obj = await message.channel.send(msg)
            third_client.loop.create_task(auto_delete_message(msg_obj, MESSAGE_DELETE_DELAY))
            total_messages_sent += 1
        return

    if content == "!all on":
        for w in (watcher_egoist, watcher_egorik):
            if w:
                w.disabled = False
                w.manual_mode = True
                w.client.is_afk = False
                await w.connect(MAIN_CHANNEL_ID)
        msg = await message.channel.send("✅ Все включены.")
        third_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        total_messages_sent += 1
        return

    if content == "!all off":
        for w in (watcher_egoist, watcher_egorik):
            if w:
                w.manual_mode = False
                w.disabled = True
                await w.disconnect()
        msg = await message.channel.send("⛔ Все выключены.")
        third_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        total_messages_sent += 1
        return

    parts = content.split()
    if len(parts) >= 2:
        cmd, target_name = parts[0], parts[1]
        w = (watcher_egoist if target_name == "egoist"
             else watcher_egorik if target_name == "egorik" else None)
        if w:
            if cmd == "!on":
                w.disabled = False
                w.manual_mode = True
                w.client.is_afk = False
                await w.connect(MAIN_CHANNEL_ID)
                msg = await message.channel.send(f"✅ {target_name} в ручном режиме.")
                third_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
                total_messages_sent += 1
            elif cmd == "!off":
                w.manual_mode = False
                w.disabled = False
                await w.disconnect()
                msg = await message.channel.send(f"⚙️ {target_name} в авто-режиме.")
                third_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
                total_messages_sent += 1

# ====================================================
# ЕЖЕДНЕВНЫЙ ОТЧЁТ
# ====================================================
async def daily_report_task():
    try:
        await asyncio.wait_for(_shutdown_event.wait(), timeout=60)
        return
    except asyncio.TimeoutError:
        pass

    while not _shutdown_event.is_set():
        try:
            now = time.time()
            cutoff = now - 24 * 3600
            day_reconnects = sum(1 for ts in reconnect_history["third"] if ts >= cutoff)
            vc = third_client.voice_clients[0] if third_client.voice_clients else None
            in_vc = vc and vc.is_connected()
            ping = int(third_client.latency * 1000) if third_client else 0
            await send_dm_notification(
                USER_ID_EGOIST,
                f"📅 **Ежедневный отчёт (THIRD)**\n"
                f"🕐 Uptime: `{fmt_uptime()}`\n"
                f"🔄 Реконнектов за сутки: `{day_reconnects}`\n"
                f"⚙️ В войсе: `{'да' if in_vc else 'нет'}`\n"
                f"📡 Пинг: `{ping}ms`"
            )
            await send_telegram(
                f"📅 <b>Отчёт THIRD</b>\n"
                f"🕐 Uptime: <code>{tg_escape(fmt_uptime())}</code>\n"
                f"🔄 Реконнектов: <code>{day_reconnects}</code>\n"
                f"⚙️ В войсе: <code>{'да' if in_vc else 'нет'}</code>\n"
                f"📡 Пинг: <code>{ping}ms</code>"
            )
            log_event("ежедневный отчёт отправлен")
        except Exception as e:
            log.error("REPORT: ошибка: %s", e)
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=24 * 3600)
        except asyncio.TimeoutError:
            pass

# ====================================================
# БЕЗОПАСНЫЙ ЗАПУСК
# ====================================================
async def safe_start(client, token, name):
    delay = 30
    while not _shutdown_event.is_set():
        try:
            await client.start(token)
            delay = 30
        except discord.HTTPException as e:
            if e.status == 429:
                log.error("[%s] 429, жду %dс", name, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 600)
                continue
            log.error("[%s] HTTP %s, рестарт через %dс", name, e.status, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 600)
        except Exception as e:
            log.error("[%s] ошибка: %s, рестарт через %dс", name, e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 600)

# ====================================================
# MAIN
# ====================================================
async def main():
    global watcher_egoist, watcher_egorik
    log.info("-" * 40)
    log.info("   TT11TT v3 — старт")
    log.info("-" * 40)

    watcher_egoist = Watcher(TOKEN_EGOIST, USER_ID_EGOIST, "Egoist") if TOKEN_EGOIST else None
    watcher_egorik = Watcher(TOKEN_EGORIK, USER_ID_EGORIK, "Egorik") if TOKEN_EGORIK else None
    _clients_ready.set()

    tasks = []
    if TOKEN_MAIN:
        tasks.append(safe_start(main_client, TOKEN_MAIN, "MAIN"))
        await asyncio.sleep(2)
    if TOKEN_SECOND:
        tasks.append(safe_start(second_client, TOKEN_SECOND, "SECOND"))
        await asyncio.sleep(2)
    if TOKEN_THIRD:
        tasks.append(safe_start(third_client, TOKEN_THIRD, "THIRD"))
        await asyncio.sleep(2)
    if watcher_egoist:
        tasks.append(watcher_egoist.start())
        await asyncio.sleep(2)
    if watcher_egorik:
        tasks.append(watcher_egorik.start())

    if not tasks:
        log.error("Ни один токен не задан! Проверь Environment Variables.")
        return

    tasks.append(uptime_pinger())
    tasks.append(daily_report_task())

    log.info("Запуск всех ботов...")
    await asyncio.gather(*tasks, return_exceptions=True)

def _install_signal_handlers(loop):
    def _stop():
        log.info("Получен сигнал остановки, завершаю...")
        _shutdown_event.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

if __name__ == "__main__":
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _install_signal_handlers(loop)
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        log.info("Остановлено пользователем.")
    except Exception as e:
        log.error("КРИТИЧЕСКАЯ ОШИБКА: %s", e)
        traceback.print_exc()
    finally:
        try:
            loop.close()
        except Exception:
            pass
