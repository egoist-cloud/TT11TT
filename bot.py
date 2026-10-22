# ====================================================
# TT11TT — Discord боты (v53 - Полный фикс под bot.py)
# ====================================================
import os
import sys
import asyncio
import logging
import random
import time
import html
import signal
import string
import json
import io
import traceback
from collections import deque
from datetime import datetime, timezone, timedelta

import aiohttp
import discord

# ====================================================
# ЛОГИРОВАНИЕ
# ====================================================
def _build_log_handlers():
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.insert(0, logging.FileHandler('bot.log', encoding='utf-8'))
    except Exception:
        pass
    return handlers

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=_build_log_handlers(),
    force=True,
)
log = logging.getLogger(__name__)

def exception_handler(loop, context):
    log.error("Необработанная ошибка в цикле: %s", context.get("exception", context.get("message")))
    exc = context.get("exception")
    if exc and hasattr(exc, "__traceback__"):
        log.error("".join(traceback.format_tb(exc.__traceback__)))

# ====================================================
# ENV И НАСТРОЙКИ
# ====================================================
def get_env_int(name, default=0):
    val = os.getenv(name)
    if not val: return default
    val = str(val).strip().strip('"').strip("'")
    try: return int(val)
    except ValueError:
        log.error("ОШИБКА: переменная %s должна быть числом, а не '%s'!", name, val)
        return default

def get_env_str(name):
    val = os.getenv(name)
    if not val: return None
    return val.strip().strip('"').strip("'")

def get_env_bool(name, default=False):
    val = get_env_str(name)
    if val is None: return default
    return val.lower() in ("1", "true", "yes", "on")

# РАСПРЕДЕЛЕНИЕ ТОКЕНОВ
TOKEN_EGOIST = get_env_str("DISCORD_TOKEN_1")
TOKEN_EGORIK = get_env_str("DISCORD_TOKEN_2")
TOKEN_MAIN = get_env_str("DISCORD_TOKEN_3")
TOKEN_SECOND = get_env_str("DISCORD_TOKEN_4")
TOKEN_THIRD = get_env_str("DISCORD_TOKEN_5")
TOKEN_RESERVE = get_env_str("DISCORD_TOKEN_6")

MAIN_CHANNEL_ID = get_env_int("VOICE_CHANNEL_ID")
GUILD_ID = get_env_int("GUILD_ID")

def _detect_render_url() -> str:
    for key in ("RENDER_URL", "RENDER_EXTERNAL_URL"):
        value = (os.getenv(key) or "").strip().rstrip("/")
        if value:
            return value
    host = (os.getenv("RENDER_EXTERNAL_HOSTNAME") or "").strip().strip("/")
    if host:
        return "https://" + host
    return ""

RENDER_URL = _detect_render_url()
STATUS_ROTATION = get_env_bool("STATUS_ROTATION", False)
SPAM_CHANNEL_ID = get_env_int("SPAM_CHANNEL_ID", 1270672504582832150)

# ДОСТУП К КОМАНДАМ
ALLOWED_COMMAND_USERS = {1113899288192356402, 1416125087324831815}
COMMAND_ALLOWED_CHANNEL_IDS = {1241013075151425650}

# КОНСТАНТЫ
STATUS_INTERVAL = 3600
CONNECT_TIMEOUT = 60.0
WATCHDOG_MIN_INTERVAL = 15.0
WATCHDOG_BASE_INTERVAL = 30.0
WATCHDOG_MAX_INTERVAL = 60.0
RECONNECT_MIN_DELAY = 10.0
DEAD_SOCKET_LATENCY_MS = 30_000
ALL_OFFLINE_RESTART_SEC = 120
RECONNECT_GUARD_SEC = 20.0
VOICE_UPTIME_LOG_INTERVAL = 1800
CHANNEL_NOT_FOUND_BACKOFF_MIN = 10.0
CHANNEL_NOT_FOUND_BACKOFF_MAX = 60.0
GUILD_READY_TIMEOUT = 60.0
LATENCY_HISTORY_SIZE = 60
WARMUP_RETRY_INTERVAL = 15.0

TEXT_STATUSES = ["Commback", "TALENT 24/7", "На страже", "Сижу в войсе"]
WATCHING_STATUSES = ["как вы играете", "на ваши сообщения", "за порядком"]
WRITE_COOLDOWN = 15.0
MAX_WRITE_COUNT = 100

# ГЕНЕРАТОР ТЕКСТА
_LOWER = string.ascii_lowercase
_UPPER = string.ascii_uppercase
_DIGITS = string.digits
_ALL = _LOWER + _UPPER + _DIGITS

def _rand_letters(min_len=3, max_len=12): return ''.join(random.choices(_LOWER, k=random.randint(min_len, max_len)))
def _rand_letters_mixed(min_len=3, max_len=12):
    pool = _LOWER + _LOWER + _LOWER + _UPPER
    return ''.join(random.choices(pool, k=random.randint(min_len, max_len)))
def _rand_digits(min_len=1, max_len=8): return ''.join(random.choices(_DIGITS, k=random.randint(min_len, max_len)))
def _rand_mixed(min_len=5, max_len=15): return ''.join(random.choices(_ALL, k=random.randint(min_len, max_len)))
def _rand_gibberish_word(): return _rand_letters(random.randint(3, 9))
def _rand_gibberish_phrase():
    n = random.randint(2, 5)
    return " ".join(_rand_gibberish_word() for _ in range(n))

def _generate_live_text() -> str:
    r = random.random()
    if r < 0.50: return _rand_letters_mixed()
    if r < 0.75: return _rand_digits()
    if r < 0.95: return _rand_mixed()
    return _rand_gibberish_phrase()

def _generate_live_text_safe() -> str:
    try:
        t = _generate_live_text().strip()
        return t if t else _rand_letters()
    except Exception: return _rand_letters()

# СОСТОЯНИЕ
watcher_egoist = None
watcher_egorik = None
start_time = time.time()
ALL_KEYS = ("main", "second", "third", "reserve", "egoist", "egorik")

reconnect_stats = {k: 0 for k in ALL_KEYS}
last_event_time = {k: time.time() for k in ALL_KEYS}
_voice_uptime = {k: 0.0 for k in ALL_KEYS}
_voice_last_ok = {k: time.time() for k in ALL_KEYS}
_reconnect_guard = {k: 0.0 for k in ALL_KEYS}
_reconnect_locks = {k: asyncio.Lock() for k in ALL_KEYS}
_watchdog_intervals = {k: WATCHDOG_BASE_INTERVAL for k in ALL_KEYS}

_client_ready = {k: False for k in ALL_KEYS}
_warmup_done = {k: False for k in ALL_KEYS}
_warmup_task = {k: None for k in ALL_KEYS}
_channel_not_found = {k: 0 for k in ALL_KEYS}
_channel_backoff = {k: CHANNEL_NOT_FOUND_BACKOFF_MIN for k in ALL_KEYS}

_paused = {k: False for k in ALL_KEYS}
_consecutive_failures = {k: 0 for k in ALL_KEYS}
_latency_history = {k: deque(maxlen=LATENCY_HISTORY_SIZE) for k in ALL_KEYS}
_drops_by_hour = {h: 0 for h in range(24)}
_drops_day = datetime.now(timezone.utc).date()
_blacklist = set()

reserve_manual_enabled = True
total_messages_sent = 0
total_commands_handled = 0
total_voice_drops = 0
event_log = deque(maxlen=200)

_watchdogs_started = set()
_status_started = set()
_main_lock = asyncio.Lock()
_second_lock = asyncio.Lock()
_third_lock = asyncio.Lock()
_reserve_lock = asyncio.Lock()

_shutdown_event = asyncio.Event()
_last_write_at = 0.0
_last_timely_key = None
_all_offline_since = None

# КЛИЕНТЫ
main_client = discord.Client(chunk_guilds_at_startup=False)
second_client = discord.Client(chunk_guilds_at_startup=False)
third_client = discord.Client(chunk_guilds_at_startup=False)
reserve_client = discord.Client(chunk_guilds_at_startup=False) if TOKEN_RESERVE else None

_CLIENTS = {"main": main_client, "second": second_client, "third": third_client}
if reserve_client is not None: _CLIENTS["reserve"] = reserve_client

_LOCKS = {"main": _main_lock, "second": _second_lock, "third": _third_lock, "reserve": _reserve_lock}

# ВОЙС-ВСПОМОГАТЕЛЬНЫЕ
async def _warmup_guild(key: str):
    try:
        _client_ready[key] = True
        _warmup_done[key] = True
        _voice_last_ok[key] = time.time()
        if _voice_uptime.get(key, 0.0) == 0.0:
            _voice_uptime[key] = time.time()
        log.debug("[%s] warmup done", key)
    except Exception as e:
        log.debug("[%s] warmup error: %s", key, e)

def _register_voice_ok(key: str):
    try:
        _voice_last_ok[key] = time.time()
        if _voice_uptime.get(key, 0.0) == 0.0:
            _voice_uptime[key] = time.time()
        _consecutive_failures[key] = 0
    except Exception:
        pass

def _register_voice_drop(key: str, reason: str = ""):
    global total_voice_drops
    try:
        total_voice_drops += 1
        now = datetime.now(timezone.utc)
        global _drops_day
        if now.date() != _drops_day:
            _drops_day = now.date()
            for h in range(24):
                _drops_by_hour[h] = 0
        _drops_by_hour[now.hour] += 1
        _consecutive_failures[key] = _consecutive_failures.get(key, 0) + 1
        log_event(f"[{key}] voice drop ({reason})")
    except Exception:
        pass

# УТИЛИТЫ
def log_event(text: str):
    ts = time.strftime("%H:%M:%S")
    event_log.append(f"[{ts}] {text}")
    log.info(text)

def log_reconnect(name: str):
    reconnect_stats[name] = reconnect_stats.get(name, 0) + 1
    last_event_time[name] = time.time()
    log_event(f"реконнект {name} #{reconnect_stats[name]}")

async def uptime_pinger():
    if not RENDER_URL:
        return
    await asyncio.sleep(60)
    while not _shutdown_event.is_set():
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(RENDER_URL) as resp:
                    await resp.read()
        except Exception:
            pass
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=14 * 60)
        except asyncio.TimeoutError:
            pass

async def _heartbeat_loop():
    try:
        import staypresent
    except Exception:
        return
    while not _shutdown_event.is_set():
        try:
            staypresent.heartbeat()
        except Exception:
            pass
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass

def fmt_uptime() -> str:
    u = int(time.time() - start_time)
    return f"{u // 3600}ч {(u % 3600) // 60}м {u % 60}с"

def fmt_ago(ts: float) -> str:
    if not ts: return "никогда"
    delta = int(time.time() - ts)
    if delta < 60: return f"{delta}с назад"
    if delta < 3600: return f"{delta // 60}м назад"
    return f"{delta // 3600}ч {(delta % 3600) // 60}м назад"

def fmt_duration(sec: float) -> str:
    sec = int(sec)
    if sec < 60: return f"{sec}с"
    if sec < 3600: return f"{sec // 60}м {sec % 60}с"
    return f"{sec // 3600}ч {(sec % 3600) // 60}м"

def _voice_channel_id(client) -> int:
    try:
        if client.voice_clients:
            vc = client.voice_clients[0]
            if vc and vc.is_connected() and vc.channel: return vc.channel.id
    except Exception: pass
    return 0

def _is_in_voice(client, channel_id: int) -> bool:
    return bool(channel_id) and _voice_channel_id(client) == channel_id

async def _safe_get_channel(client, channel_id):
    if not channel_id: return None
    ch = client.get_channel(channel_id)
    if ch is not None: return ch
    try:
        ch = await client.fetch_channel(channel_id)
        if ch is not None: return ch
    except Exception: pass
    if GUILD_ID:
        try:
            guild = client.get_guild(GUILD_ID)
            if guild is None:
                try: guild = await client.fetch_guild(GUILD_ID)
                except Exception: guild = None
            if guild is not None:
                ch = guild.get_channel(channel_id)
                if ch is not None: return ch
                try:
                    ch = await guild.fetch_channel(channel_id)
                    if ch is not None: return ch
                except Exception: pass
        except Exception: pass
    return None

def _is_dead_socket(client) -> bool:
    if not client.is_ready(): return True
    try:
        latency = client.latency
        if latency != latency or latency * 1000 > DEAD_SOCKET_LATENCY_MS: return True
    except Exception: return True
    return False

def _get_client(name: str):
    if name in _CLIENTS: return _CLIENTS[name]
    if name == "egoist" and watcher_egoist: return watcher_egoist.client
    if name == "egorik" and watcher_egorik: return watcher_egorik.client
    return None

def _get_token(name: str):
    return {"main": TOKEN_MAIN, "second": TOKEN_SECOND, "third": TOKEN_THIRD, "reserve": TOKEN_RESERVE, "egoist": TOKEN_EGOIST, "egorik": TOKEN_EGORIK}.get(name)

def _is_watcher_client(client) -> bool:
    for w in (watcher_egoist, watcher_egorik):
        if w is not None and w.client is client:
            return True
    return False

async def _all_offline_watchdog():
    global _all_offline_since
    while not _shutdown_event.is_set():
        await asyncio.sleep(30)
        expected = []
        for name, client in _CLIENTS.items():
            if _get_token(name): expected.append(client)
        if watcher_egoist and watcher_egoist.token: expected.append(watcher_egoist.client)
        if watcher_egorik and watcher_egorik.token: expected.append(watcher_egorik.client)
        if not expected: continue
        ready_count = sum(1 for c in expected if c.is_ready())
        if ready_count == 0:
            if _all_offline_since is None:
                _all_offline_since = time.time()
                log_event("⚠️ ВСЕ клиенты offline — пробую полный рестарт")
            elif time.time() - _all_offline_since > ALL_OFFLINE_RESTART_SEC:
                log.error("🚨 ВСЕ offline > %dс — перезапуск процесса", ALL_OFFLINE_RESTART_SEC)
                os._exit(1)
        else:
            if _all_offline_since is not None: _all_offline_since = None

async def status_changer(client, name):
    if not STATUS_ROTATION: return
    while not _shutdown_event.is_set():
        try:
            if client.is_ready():
                await client.change_presence(activity=None)
                await asyncio.sleep(2)
                if random.random() < 0.5:
                    activity = discord.Activity(type=discord.ActivityType.playing, name=random.choice(TEXT_STATUSES))
                else:
                    activity = discord.Activity(type=discord.ActivityType.watching, name=random.choice(WATCHING_STATUSES))
                await client.change_presence(activity=activity)
        except Exception: pass
        try: await asyncio.wait_for(_shutdown_event.wait(), timeout=STATUS_INTERVAL)
        except asyncio.TimeoutError: pass

# WATCHER
class Watcher:
    def __init__(self, token, user_id, name):
        self.token = token
        self.user_id = user_id
        self.name = name
        self.key = name.lower()
        self.client = discord.Client(chunk_guilds_at_startup=False)
        self.manual_mode = False
        self.disabled = True
        self._connect_lock = asyncio.Lock()
        self._watchdog_task = None
        self._status_task = None
        self._register_events()

    def _register_events(self):
        @self.client.event
        async def on_ready():
            log.info("[%s] авторизован как %s", self.name, self.client.user)
            await _warmup_guild(self.key)
            if self._status_task is None or self._status_task.done():
                try:
                    self._status_task = self.client.loop.create_task(status_changer(self.client, self.name))
                except Exception:
                    pass
            self._start_watchdog()

        @self.client.event
        async def on_disconnect():
            if self.manual_mode and not self.disabled:
                log_event(f"[{self.name}] on_disconnect")

        @self.client.event
        async def on_resumed():
            log.info("[%s] resumed", self.name)
            if not _client_ready.get(self.key, False):
                await _warmup_guild(self.key)
            if self.manual_mode and not self.disabled:
                try:
                    await self.connect(MAIN_CHANNEL_ID)
                except Exception:
                    pass

        @self.client.event
        async def on_voice_state_update(member, before, after):
            if GUILD_ID:
                g = getattr(member, "guild", None)
                if g is None or g.id != GUILD_ID: return
            me = self.client.user
            if me is None or member.id != me.id: return
            if not self.manual_mode or self.disabled: return
            if before.channel and after.channel and before.channel.id == after.channel.id: return
            if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
                log_event(f"[{self.name}] Потеря соединения с войсом, переподключаю...")
                await asyncio.sleep(4)
                if self.manual_mode and not self.disabled:
                    try:
                        await self.connect(MAIN_CHANNEL_ID)
                    except Exception:
                        pass

    def _start_watchdog(self):
        if self._watchdog_task is None or self._watchdog_task.done():
            try:
                self._watchdog_task = self.client.loop.create_task(self._watchdog())
            except Exception:
                pass

    async def _watchdog(self):
        while not _shutdown_event.is_set() and not _client_ready.get(self.key, False):
            await asyncio.sleep(1.0)
        while not _shutdown_event.is_set():
            interval = _watchdog_intervals.get(self.key, WATCHDOG_BASE_INTERVAL)
            if _channel_not_found.get(self.key, 0) > 0:
                interval = max(interval, _channel_backoff.get(self.key, interval))
            await asyncio.sleep(interval)
            if _paused.get(self.key, False): continue
            if self.disabled or not self.manual_mode: continue
            if not self.client.is_ready(): continue
            if _is_dead_socket(self.client):
                log_event(f"[{self.name}] dead socket")
                _register_voice_drop(self.key, "dead-socket")
                log_reconnect(self.key)
                await self._force_reconnect()
                continue
            if not _is_in_voice(self.client, MAIN_CHANNEL_ID):
                now = time.time()
                if now - _reconnect_guard.get(self.key, 0) < RECONNECT_GUARD_SEC: continue
                _reconnect_guard[self.key] = now
                if _channel_not_found.get(self.key, 0) > 0:
                    await self.connect(MAIN_CHANNEL_ID)
                    continue
                _register_voice_drop(self.key, "not-in-voice")
                log_reconnect(self.key)
                await self._force_reconnect()
                continue
            _register_voice_ok(self.key)

    async def _force_reconnect(self):
        async with self._connect_lock:
            try:
                if self.client.voice_clients:
                    await self.client.voice_clients[0].disconnect(force=True)
            except Exception: pass
            await asyncio.sleep(RECONNECT_MIN_DELAY)
        await self.connect(MAIN_CHANNEL_ID)

    async def connect(self, channel_id):
        async with self._connect_lock:
            if self.disabled or channel_id == 0: return False, "Отключен"
            try:
                channel = await _safe_get_channel(self.client, channel_id)
                if not channel:
                    _channel_not_found[self.key] = _channel_not_found.get(self.key, 0) + 1
                    _channel_backoff[self.key] = min(CHANNEL_NOT_FOUND_BACKOFF_MAX, _channel_backoff.get(self.key, CHANNEL_NOT_FOUND_BACKOFF_MIN) * 1.5)
                    return False, "Канал не найден"
                _channel_not_found[self.key] = 0
                _channel_backoff[self.key] = CHANNEL_NOT_FOUND_BACKOFF_MIN
                if _is_in_voice(self.client, channel_id): 
                    _register_voice_ok(self.key)
                    return True, "Уже в канале"
                if self.client.voice_clients:
                    try: await self.client.voice_clients[0].disconnect(force=True)
                    except Exception: pass
                    await asyncio.sleep(2)
                await channel.connect(timeout=CONNECT_TIMEOUT, self_deaf=False, self_mute=False)
                log.info("[%s] подключен к %s (БЕЗ МУТА)", self.name, getattr(channel, 'name', channel_id))
                _register_voice_ok(self.key)
                return True, "Успешно"
            except Exception as e:
                _register_voice_drop(self.key, "connect-fail")
                return False, str(e)

    async def disconnect(self):
        try:
            if self.client.voice_clients:
                await self.client.voice_clients[0].disconnect(force=True)
        except Exception: pass

    async def start(self):
        delay = 30
        while not _shutdown_event.is_set():
            try:
                await self.client.start(self.token)
                log.warning("[%s] клиент закрылся, переподключение через %dс", self.name, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 600)
            except discord.LoginFailure as e:
                log.error("[%s] НЕВЕРНЫЙ ТОКЕН (%s). Ждём 300с перед повтором.", self.name, e)
                await asyncio.sleep(300)
                delay = 300
            except Exception as e:
                log.error("[%s] ошибка старта: %s, рестарт через %dс", self.name, e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 600)

    async def spam_messages(self, channel_id, count):
        global total_messages_sent
        try:
            channel = await _safe_get_channel(self.client, channel_id)
            if not channel: return False
            for _ in range(count):
                text = _generate_live_text_safe()
                try:
                    await channel.send(text)
                    total_messages_sent += 1
                except discord.HTTPException as he:
                    if he.status == 429:
                        retry = None
                        try:
                            if he.response: retry = he.response.headers.get("Retry-After")
                        except Exception: pass
                        await asyncio.sleep(float(retry or 5))
                        continue
                    raise
                await asyncio.sleep(random.uniform(1.2, 3.5))
            log.info("[%s] Отправил %d бессмысленных сообщений", self.name, count)
            return True
        except Exception as e:
            log.error("[%s] Ошибка спама: %s", self.name, e)
            return False

# ОБРАБОТКА КОМАНД
def _is_allowed_user(message) -> bool:
    author_id = getattr(message.author, "id", None)
    return author_id in ALLOWED_COMMAND_USERS

def _is_command_channel(message) -> bool:
    if
