# ====================================================
# TT11TT — Discord боты (v50 - Рабочий ответ и мут)
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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
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

RENDER_URL = get_env_str("RENDER_URL") or "https://tt11tt-1.onrender.com"
STATUS_ROTATION = get_env_bool("STATUS_ROTATION", False)
SPAM_CHANNEL_ID = 1270672504582832150
REPORT_CHANNEL_ID = 1241013075151425650

# ====================================================
# КОНСТАНТЫ
# ====================================================
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

# ====================================================
# ГЕНЕРАТОР БЕССМЫСЛЕННОГО ТЕКСТА
# ====================================================
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

# ====================================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ
# ====================================================
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

# ====================================================
# КЛИЕНТЫ
# ====================================================
main_client = discord.Client(chunk_guilds_at_startup=False)
second_client = discord.Client(chunk_guilds_at_startup=False)
third_client = discord.Client(chunk_guilds_at_startup=False)
reserve_client = discord.Client(chunk_guilds_at_startup=False) if TOKEN_RESERVE else None

_CLIENTS = {"main": main_client, "second": second_client, "third": third_client}
if reserve_client is not None: _CLIENTS["reserve"] = reserve_client

_LOCKS = {"main": _main_lock, "second": _second_lock, "third": _third_lock, "reserve": _reserve_lock}

# ====================================================
# УТИЛИТЫ И ОТЧЕТЫ
# ====================================================
def log_event(text: str):
    ts = time.strftime("%H:%M:%S")
    event_log.append(f"[{ts}] {text}")
    log.info(text)

def log_reconnect(name: str):
    reconnect_stats[name] = reconnect_stats.get(name, 0) + 1
    last_event_time[name] = time.time()
    log_event(f"реконнект {name} #{reconnect_stats[name]}")

async def uptime_pinger():
    await asyncio.sleep(60)
    while not _shutdown_event.is_set():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RENDER_URL, timeout=20): pass
        except Exception: pass
        try: await asyncio.wait_for(_shutdown_event.wait(), timeout=14 * 60)
        except asyncio.TimeoutError: pass

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
                try:
                    channels = await guild.fetch_channels()
                    for c in channels:
                        if c.id == channel_id: return c
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

# ====================================================
# МОНИТОРИНГ
# ====================================================
def _register_voice_ok(name: str):
    now = time.time()
    last = _voice_last_ok.get(name, now)
    _voice_uptime[name] = _voice_uptime.get(name, 0.0) + max(0.0, now - last)
    _voice_last_ok[name] = now
    _watchdog_intervals[name] = max(WATCHDOG_MIN_INTERVAL, _watchdog_intervals.get(name, WATCHDOG_BASE_INTERVAL) * 0.95)
    _consecutive_failures[name] = 0
    client = _get_client(name)
    if client and client.is_ready():
        try:
            lat = client.latency * 1000
            if lat == lat: _latency_history[name].append(lat)
        except Exception: pass

def _register_voice_drop(name: str, reason: str = ""):
    global total_voice_drops, _drops_day
    today = datetime.now(timezone.utc).date()
    if today != _drops_day:
        for h in _drops_by_hour: _drops_by_hour[h] = 0
        _drops_day = today
    total_voice_drops += 1
    _voice_last_ok[name] = time.time()
    _watchdog_intervals[name] = min(WATCHDOG_MAX_INTERVAL, _watchdog_intervals.get(name, WATCHDOG_BASE_INTERVAL) * 2.0)
    _consecutive_failures[name] = _consecutive_failures.get(name, 0) + 1
    _drops_by_hour[datetime.now().hour] += 1
    log_event(f"⚠️ {name} вылетел из войса ({reason or 'watchdog'})")

async def _log_voice_uptime_periodic():
    while not _shutdown_event.is_set():
        await asyncio.sleep(VOICE_UPTIME_LOG_INTERVAL)
        for name in ALL_KEYS:
            client = _get_client(name)
            if client and _is_in_voice(client, MAIN_CHANNEL_ID):
                _register_voice_ok(name)
                log.info("[%s] в войсе, uptime=%s, реконнектов=%d", name.upper(), fmt_duration(_voice_uptime.get(name, 0)), reconnect_stats.get(name, 0))

async def _all_offline_watchdog():
    global _all_offline_since
    while not _shutdown_event.is_set():
        await asyncio.sleep(30)
        if not _CLIENTS: continue
        ready_count = sum(1 for k in _CLIENTS if _CLIENTS[k].is_ready())
        if ready_count == 0:
            if _all_offline_since is None:
                _all_offline_since = time.time()
                log_event("⚠️ ВСЕ клиенты offline — пробую полный рестарт клиентов")
            elif time.time() - _all_offline_since > ALL_OFFLINE_RESTART_SEC:
                log.error("🚨 ВСЕ offline > %dс — перезапуск процесса", ALL_OFFLINE_RESTART_SEC)
                os._exit(1)
        else:
            if _all_offline_since is not None: _all_offline_since = None

# ====================================================
# СТАТУС
# ====================================================
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

# ====================================================
# WARMUP
# ====================================================
async def _warmup_guild(client_name: str) -> bool:
    client = _get_client(client_name)
    if client is None or not GUILD_ID:
        _client_ready[client_name] = True
        _warmup_done[client_name] = True
        return True
    deadline = time.time() + GUILD_READY_TIMEOUT
    while time.time() < deadline:
        if not client.is_ready():
            await asyncio.sleep(1.0)
            continue
        guild = client.get_guild(GUILD_ID)
        if guild is None:
            try: guild = await client.fetch_guild(GUILD_ID)
            except Exception: guild = None
        if guild is not None:
            channel = guild.get_channel(MAIN_CHANNEL_ID)
            if channel is None:
                try: channel = await guild.fetch_channel(MAIN_CHANNEL_ID)
                except Exception: channel = None
            if channel is None:
                try:
                    channels = await guild.fetch_channels()
                    for c in channels:
                        if c.id == MAIN_CHANNEL_ID: channel = c; break
                except Exception: pass
            if channel is not None:
                _client_ready[client_name] = True
                _warmup_done[client_name] = True
                log.info("[%s] гильдия и канал в кэше", client_name.upper())
                return True
        await asyncio.sleep(2.0)
    log.warning("[%s] warmup не удался за %.0fс — включаю фоновый retry", client_name.upper(), GUILD_READY_TIMEOUT)
    _client_ready[client_name] = True
    _warmup_done[client_name] = False
    _start_warmup_retry(client_name)
    return False

def _start_warmup_retry(client_name: str):
    if _warmup_task.get(client_name) and not _warmup_task[client_name].done():
        return
    async def _retry():
        while not _shutdown_event.is_set() and not _warmup_done.get(client_name, False):
            await asyncio.sleep(WARMUP_RETRY_INTERVAL)
            client = _get_client(client_name)
            if client and client.is_ready():
                try:
                    guild = client.get_guild(GUILD_ID) or await client.fetch_guild(GUILD_ID)
                    if guild:
                        try: await guild.fetch_channels()
                        except Exception: pass
                        if guild.get_channel(MAIN_CHANNEL_ID) or await _safe_get_channel(client, MAIN_CHANNEL_ID):
                            _warmup_done[client_name] = True
                            log.info("[%s] фоновый warmup успешен", client_name.upper())
                except Exception: pass
    _warmup_task[client_name] = asyncio.ensure_future(_retry())

# ====================================================
# WATCHER (ДЕЖУРНЫЕ - БЕЗ МУТА)
# ====================================================
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
                self._status_task = self.client.loop.create_task(status_changer(self.client, self.name))
            self._start_watchdog()
        
        @self.client.event
        async def on_disconnect():
            if self.manual_mode and not self.disabled: log_event(f"[{self.name}] on_disconnect")
        
        @self.client.event
        async def on_resumed():
            log.info("[%s] resumed", self.name)
            if not _client_ready.get(self.key, False):
                await _warmup_guild(self.key)
            if self.manual_mode and not self.disabled:
                await self.connect(MAIN_CHANNEL_ID)
        
        @self.client.event
        async def on_voice_state_update(member, before, after):
            if GUILD_ID and member.guild.id != GUILD_ID: return
            me = self.client.user
            if me is None or member.id != me.id: return
            if not self.manual_mode or self.disabled: return
            
            if before.channel and after.channel and before.channel.id == after.channel.id:
                return
                
            if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
                log_event(f"[{self.name}] Принудительный выход или кик, переподключение...")
                await asyncio.sleep(5)
                if self.manual_mode and not self.disabled:
                    await self.connect(MAIN_CHANNEL_ID)

    def _start_watchdog(self):
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = self.client.loop.create_task(self._watchdog())

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
                if _is_in_voice(self.client, channel_id): return True, "Уже в канале"
                if self.client.voice_clients:
                    try: await self.client.voice_clients[0].disconnect(force=True)
                    except Exception: pass
                    await asyncio.sleep(2)

                await channel.connect(timeout=CONNECT_TIMEOUT, self_deaf=False, self_mute=False)
                log.info("[%s] подключен к %s (БЕЗ МУТА)", self.name, channel.name)
                _register_voice_ok(self.key)
                return True, "Успешно"
            except Exception as e:
                return False, str(e)

    async def disconnect(self):
        if self.client.voice_clients:
            try: await self.client.voice_clients[0].disconnect(force=True)
            except Exception: pass

    async def start(self):
        delay = 30
        while not _shutdown_event.is_set():
            try:
                await self.client.start(self.token)
                delay = 30
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
                        retry = he.response.headers.get("Retry-After") if he.response else None
                        await asyncio.sleep(float(retry or 5))
                        continue
                    raise
                await asyncio.sleep(random.uniform(1.2, 3.5))
            log.info("[%s] Отправил %d бессмысленных сообщений", self.name, count)
            return True
        except Exception as e:
            log.error("[%s] Ошибка спама: %s", self.name, e)
            return False

# ====================================================
# 24/7 + RESERVE (ЭТИ БОТЫ ОСТАЮТСЯ В МУТЕ)
# ====================================================
async def _ensure_connected(client_name: str):
    client = _get_client(client_name)
    if client is None or MAIN_CHANNEL_ID == 0: return
    if not _client_ready.get(client_name, False): return
    
    if _is_in_voice(client, MAIN_CHANNEL_ID):
        _channel_not_found[client_name] = 0
        _channel_backoff[client_name] = CHANNEL_NOT_FOUND_BACKOFF_MIN
        return
        
    lock = _LOCKS.get(client_name)
    if lock is None: return
    async with lock:
        if _is_in_voice(client, MAIN_CHANNEL_ID): return
        channel = await _safe_get_channel(client, MAIN_CHANNEL_ID)
        if not channel:
            _channel_not_found[client_name] = _channel_not_found.get(client_name, 0) + 1
            _channel_backoff[client_name] = min(CHANNEL_NOT_FOUND_BACKOFF_MAX, _channel_backoff.get(client_name, CHANNEL_NOT_FOUND_BACKOFF_MIN) * 1.5)
            return
        _channel_not_found[client_name] = 0
        _channel_backoff[client_name] = CHANNEL_NOT_FOUND_BACKOFF_MIN
        
        if client.voice_clients:
            try: await client.voice_clients[0].disconnect(force=True)
            except Exception: pass
            await asyncio.sleep(3)
            
        try:
            await channel.connect(timeout=CONNECT_TIMEOUT, self_deaf=True, self_mute=True)
            log.info("[%s] подключен к %s (В МУТЕ)", client_name.upper(), channel.name)
            _register_voice_ok(client_name)
        except Exception as e:
            log.warning("[%s] ошибка подключения: %s", client_name.upper(), e)
            _register_voice_drop(client_name, f"connect: {e}")

async def _watchdog_loop(client_name: str):
    client = _get_client(client_name)
    if client is None: return
    while not _shutdown_event.is_set() and not _client_ready.get(client_name, False):
        await asyncio.sleep(1.0)
    while not _shutdown_event.is_set():
        interval = _watchdog_intervals.get(client_name, WATCHDOG_BASE_INTERVAL)
        if _channel_not_found.get(client_name, 0) > 0:
            interval = max(interval, _channel_backoff.get(client_name, interval))
        await asyncio.sleep(interval)
        if _paused.get(client_name, False): continue
        if not _client_ready.get(client_name, False): continue
        if client_name == "reserve" and not reserve_manual_enabled: continue
        if _is_dead_socket(client):
            log_event(f"[{client_name}] dead socket")
            _register_voice_drop(client_name, "dead-socket")
            log_reconnect(client_name)
            await _ensure_connected(client_name)
            continue
        if not _is_in_voice(client, MAIN_CHANNEL_ID):
            now = time.time()
            if now - _reconnect_guard.get(client_name, 0) < RECONNECT_GUARD_SEC: continue
            _reconnect_guard[client_name] = now
            if _channel_not_found.get(client_name, 0) > 0:
                await _ensure_connected(client_name)
                continue
            _register_voice_drop(client_name, "not-in-voice")
            log_reconnect(client_name)
            await _ensure_connected(client_name)
            continue
        _register_voice_ok(client_name)

def _bind_client_events(name, client):
    @client.event
    async def on_ready():
        log.info("[%s] авторизован как %s", name.upper(), client.user)
        await _warmup_guild(name)
        await _ensure_connected(name)
        if name not in _watchdogs_started:
            _watchdogs_started.add(name)
            client.loop.create_task(_watchdog_loop(name))
        if name not in _status_started:
            _status_started.add(name)
            client.loop.create_task(status_changer(client, name.upper()))
    
    @client.event
    async def on_disconnect():
        log.warning("[%s] on_disconnect", name.upper())
    
    @client.event
    async def on_resumed():
        log.info("[%s] resumed", name.upper())
        if not _client_ready.get(name, False):
            await _warmup_guild(name)
        await _ensure_connected(name)
    
    @client.event
    async def on_voice_state_update(member, before, after):
        if GUILD_ID and member.guild.id != GUILD_ID: return
        me = client.user
        if me is None or member.id != me.id: return
        
        if before.channel and after.channel and before.channel.id == after.channel.id:
            return
            
        if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
            log_event(f"[{name.upper()}] Вылетел из канала, переподключение...")
            await asyncio.sleep(5)
            await _ensure_connected(name)

if TOKEN_MAIN: _bind_client_events("main", main_client)
if TOKEN_SECOND: _bind_client_events("second", second_client)
if TOKEN_THIRD: _bind_client_events("third", third_client)
if TOKEN_RESERVE and reserve_client is not None: _bind_client_events("reserve", reserve_client)

# ====================================================
# TIMELY
# ====================================================
async def timely_scheduler():
    global _last_timely_key
    MSK = timezone(timedelta(hours=3))
    while not _shutdown_event.is_set():
        now = datetime.now(MSK)
        key = (now.date(), now.hour, now.minute)
        if now.hour == 4 and now.minute in (5, 10):
            if _last_timely_key != key:
                _last_timely_key = key
                target = watcher_egoist if now.minute == 5 else watcher_egorik
                if target and target.manual_mode and target.client.is_ready():
                    try:
                        channel = await _safe_get_channel(target.client, SPAM_CHANNEL_ID)
                        if channel:
                            await channel.send("/timely")
                            log_event(f"[{target.name}] /timely отправлен")
                    except Exception as e: log.error("[%s] timely error: %s", target.name, e)
            await asyncio.sleep(61)
        await asyncio.sleep(30)

# ====================================================
# ПАНЕЛИ
# ====================================================
def _w_state(w) -> str:
    if not w: return "⚪ не настроен"
    if w.disabled: return "🔴 выкл"
    if w.manual_mode: return "🟢 вкл"
    return "🟡 ожидание"

def _build_panel() -> str:
    lines = ["🎛 **ПАНЕЛЬ TT11TT**", f"⏱ Uptime: **{fmt_uptime()}**", f"🔻 Всего вылетов: **{total_voice_drops}**"]
    nf = sum(_channel_not_found.values())
    if nf: lines.append(f"⚠️ Канал не найден: **{nf}**")
    lines += ["", "**Дежурные (БЕЗ МУТА):**", f"  • Egoist: {_w_state(watcher_egoist)}", f"  • Egorik: {_w_state(watcher_egorik)}", "", "**24/7 боты (В МУТЕ):**"]
    for n in ("main", "second", "third"):
        c = _CLIENTS[n]
        ch = _voice_channel_id(c)
        paused = "⏸ " if _paused.get(n) else ""
        if ch == MAIN_CHANNEL_ID: status = f"🟢 `{ch}` ({fmt_duration(_voice_uptime.get(n, 0))})"
        elif not ch: status = "🔴 не в голосе"
        else: status = f"🟡 другой `{ch}`"
        lines.append(f"  • {n.upper()}: {paused}{status}")
    if reserve_client is not None:
        ch = _voice_channel_id(reserve_client)
        paused = "⏸ " if _paused.get("reserve") else ""
        if not reserve_manual_enabled: rstat = "🔴 отключён"
        elif ch == MAIN_CHANNEL_ID: rstat = f"🟢 `{ch}` ({fmt_duration(_voice_uptime.get('reserve', 0))})"
        elif not ch: rstat = "🔴 не в голосе"
        else: rstat = f"🟡 другой `{ch}`"
        lines.append(f"  • **RESERVE**: {paused}{rstat}")
    else: lines.append("  • **RESERVE**: ⚪ нет токена")
    lines += ["", f"**Сообщений:** {total_messages_sent} | **Команд:** {total_commands_handled}", "", "**Управление:** `!on egoist` / `!on egorik` (включить)", "`!off egoist` / `!off egorik` (выключить)", "`!panel` `!ping` `!stats` `!info` `!help`"]
    return "\n".join(lines)

def _build_ping(client_list) -> str:
    lines = ["🏓 **PONG**", ""]
    for name, client in client_list:
        if client.is_ready():
            try:
                latency = client.latency * 1000
                flag = "💀" if latency > DEAD_SOCKET_LATENCY_MS else ""
                lines.append(f"• **{name}**: `{latency:.0f} ms` {flag}")
            except Exception: lines.append(f"• **{name}**: `n/a`")
        else: lines.append(f"• **{name}**: ❌ не готов")
    lines.append(""); lines.append(f"⏱ Uptime: **{fmt_uptime()}**")
    return "\n".join(lines)

def _build_latency() -> str:
    lines = ["📶 **LATENCY (за 5 мин)**", ""]
    for k in ALL_KEYS:
        hist = _latency_history.get(k, deque())
        if not hist: continue
        vals = list(hist)
        lines.append(f"• **{k.upper()}**: min `{min(vals):.0f}` / avg `{sum(vals)/len(vals):.0f}` / max `{max(vals):.0f}` ms ({len(vals)} точек)")
    if len(lines) == 2: lines.append("— нет данных")
    return "\n".join(lines)

def _build_stats_by_hour() -> str:
    lines = ["🕐 **ВЫЛЕТЫ ПО ЧАСАМ** (всего " + str(total_voice_drops) + ")", ""]
    max_v = max(_drops_by_hour.values()) or 1
    for h in range(24):
        v = _drops_by_hour.get(h, 0)
        bar = "█" * int((v / max_v) * 20) if v else ""
        lines.append(f"`{h:02d}:00` {bar} {v}")
    return "\n".join(lines)

def _build_stats() -> str:
    lines = ["📊 **СТАТИСТИКА**", "", "**Реконнекты:**"]
    for n in ALL_KEYS:
        if _get_client(n) is None: continue
        v = reconnect_stats.get(n, 0)
        last = last_event_time.get(n)
        upt = fmt_duration(_voice_uptime.get(n, 0))
        lines.append(f"  • {n}: `{v}` (uptime {upt}, {fmt_ago(last)})")
    lines += ["", f"**Вылетов:** {total_voice_drops}", f"**Канал не найден:** {sum(_channel_not_found.values())}", f"**Сообщений:** {total_messages_sent}", f"**Команд:** {total_commands_handled}", "", "**Последние события:**"]
    for e in list(event_log)[-15:]: lines.append(f"  {e}")
    return "\n".join(lines)

def _build_logs(n: int) -> str:
    items = list(event_log)[-n:]
    if not items: return "📭 Лог пуст."
    return f"📜 **Логи (последние {len(items)})**\n```\n" + "\n".join(items) + "\n```"

def _build_info() -> str:
    lines = ["ℹ️ **ИНФО**", "", f"Гильдия: `{GUILD_ID or 'все'}`", f"Канал: `{MAIN_CHANNEL_ID}`", f"Спам: `{SPAM_CHANNEL_ID}`", f"Rotate: `{STATUS_ROTATION}` | Reserve: `{reserve_manual_enabled}`", f"Blacklist: `{len(_blacklist)}`", ""]
    for name in ("main", "second", "third", "reserve"):
        client = _CLIENTS.get(name)
        if client is None: continue
        u = client.user
        lines.append(f"  • {name.upper()}: `{u or 'n/a'}` ready={client.is_ready()}")
    for w in (watcher_egoist, watcher_egorik):
        if w: lines.append(f"  • {w.name}: `{w.client.user or 'n/a'}` manual={w.manual_mode} disabled={w.disabled}")
    return "\n".join(lines)

def _build_help() -> str:
    return "📖 **HELP**\n**Дежурные:** `!on egoist` / `!on egorik`, `!off egoist` / `!off egorik`\n**Инфо:** `!panel` `!ping` `!stats` `!info` `!latency`\n**Диагностика:** `!logs <n>` `!screenshot` `!stats_by_hour`\n**Резерв:** `!reserve on|off`\n**Пауза:** `!pause <client>` `!resume <client>`"

# ====================================================
# КОМАНДЫ
# ====================================================
def _make_on_message(bound_client):
    async def handler(message):
        global total_commands_handled, _last_write_at, reserve_manual_enabled
        
        if bound_client.user is None or message.author.id == bound_client.user.id: 
            return
            
        # РАЗРЕШАЕМ ВСЕМ (чтобы вы могли управлять с любого аккаунта)
        if message.author.id in _blacklist:
            return
            
        content = message.content.strip()
        parts = content.split()
        if not parts: return
        
        # РАЗРЕШАЕМ КОМАНДЫ В КАНАЛЕ #РЕПОРТЫ И В ЛС
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_report_channel = (message.channel.id == 1241013075151425650)
        
        if not is_dm and not is_report_channel:
            return

        total_commands_handled += 1
        low = content.lower()

        # --- УПРАВЛЕНИЕ ДЕЖУРНЫМИ ---
        if len(parts) >= 2:
            cmd, t = parts[0].lower(), parts[1].lower()
            if t in ("egoist", "egorik") and cmd in ("!on", "!off", "!вкл", "!выкл"):
                w = watcher_egoist if t == "egoist" else watcher_egorik
                if not w:
                    await message.channel.send(f"⚠️ Дежурный `{t}` не настроен.")
                    return
                
                if cmd in ("!on", "!вкл"):
                    w.disabled = False
                    w.manual_mode = True
                    ok, msg = await w.connect(MAIN_CHANNEL_ID)
                    if ok:
                        await message.channel.send(f"✅ {t} вкл (Успешно).")
                    else:
                        await message.channel.send(f"❌ Ошибка при подключении {t}: {msg}")
                else:
                    w.manual_mode = False
                    w.disabled = True
                    await w.disconnect()
                    await message.channel.send(f"⚙️ {t} выкл.")
                return

        # --- ОСТАЛЬНЫЕ КОМАНДЫ ---
        if low in ("!help", "!commands"): await message.channel.send(_build_help()); return
        if low in ("!status", "!panel", "!menu"): await message.channel.send(_build_panel()); return
        if low == "!ping":
            clients = [("MAIN", main_client), ("SECOND", second_client), ("THIRD", third_client)]
            if reserve_client is not None: clients.append(("RESERVE", reserve_client))
            if watcher_egoist: clients.append(("Egoist", watcher_egoist.client))
            if watcher_egorik: clients.append(("Egorik", watcher_egorik.client))
            await message.channel.send(_build_ping(clients)); return
        if low == "!info": await message.channel.send(_build_info()); return
        if low == "!stats": await message.channel.send(_build_stats()); return
        if low == "!latency": await message.channel.send(_build_latency()); return
        if low == "!stats_by_hour": await message.channel.send(_build_stats_by_hour()); return
        if low.startswith("!logs"):
            try:
                n = int(parts[1]) if len(parts) > 1 else 20
                n = max(1, min(n, 100))
            except ValueError: n = 20
            await message.channel.send(_build_logs(n)); return

        if parts[0].lower() in ("!write", "!спам"):
            if len(parts) < 2 or not parts[1].isdigit():
                await message.channel.send("⚠️ `!write <число>`"); return
            count = int(parts[1])
            if count < 1 or count > MAX_WRITE_COUNT:
                await message.channel.send(f"⚠️ Диапазон: 1–{MAX_WRITE_COUNT}"); return
            now = time.time()
            if now - _last_write_at < WRITE_COOLDOWN:
                await message.channel.send(f"⏳ Подождите {int(WRITE_COOLDOWN - (now - _last_write_at))}с"); return
            active = None
            if watcher_egoist and watcher_egoist.manual_mode: active = watcher_egoist
            elif watcher_egorik and watcher_egorik.manual_mode: active = watcher_egorik
            if not active:
                await message.channel.send("⚠️ Дежурный не включён."); return
            _last_write_at = now
            await message.channel.send(f"⏳ {active.name} спамит ({count})...")
            ok = await active.spam_messages(SPAM_CHANNEL_ID, count)
            await message.channel.send(f"✅ Готово." if ok else "❌ Ошибка."); return

        if parts[0].lower() == "!reserve" and len(parts) >= 2:
            if reserve_client is None:
                await message.channel.send("⚠️ RESERVE не настроен."); return
            if parts[1].lower() == "on":
                reserve_manual_enabled = True
                await _ensure_connected("reserve")
                await message.channel.send("✅ RESERVE включён.")
            elif parts[1].lower() == "off":
                reserve_manual_enabled = False
                try:
                    if reserve_client.voice_clients:
                        await reserve_client.voice_clients[0].disconnect(force=True)
                except Exception: pass
                await message.channel.send("⛔ RESERVE отключён.")
            else:
                await message.channel.send("⚠️ `!reserve on|off`")
            return

        if parts[0].lower() in ("!pause", "!resume") and len(parts) >= 2:
            target = parts[1].lower()
            if target not in ALL_KEYS or _get_client(target) is None:
                await message.channel.send(f"⚠️ Неизвестный клиент: `{target}`"); return
            if parts[0].lower() == "!pause":
                _paused[target] = True
                await message.channel.send(f"⏸ {target} на паузе.")
            else:
                _paused[target] = False
                if target in _CLIENTS:
                    await _ensure_connected(target)
                await message.channel.send(f"▶️ {target} возобновлён.")
            return

        if low == "!all on":
            for w in (watcher_egoist, watcher_egorik):
                if w:
                    w.disabled, w.manual_mode = False, True
                    await w.connect(MAIN_CHANNEL_ID)
            await message.channel.send("✅ Все включены."); return
        if low == "!all off":
            for w in (watcher_egoist, watcher_egorik):
                if w:
                    w.manual_mode, w.disabled = False, True
                    await w.disconnect()
            await message.channel.send("⛔ Все выключены."); return
            
    return handler

for _name, _client in _CLIENTS.items():
    _client.event(_make_on_message(_client))

# ====================================================
# ЗАПУСК
# ====================================================
async def safe_start(client, token, name):
    delay = 30
    while not _shutdown_event.is_set():
        try:
            await client.start(token)
            delay = 30
        except Exception as e:
            log.error("[%s] ошибка: %s, рестарт через %dс", name, e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 600)

async def main():
    global watcher_egoist, watcher_egorik
    log.info("Инициализация...")
    watcher_egoist = Watcher(TOKEN_EGOIST, get_env_int("USER_ID_1"), "Egoist") if TOKEN_EGOIST else None
    watcher_egorik = Watcher(TOKEN_EGORIK, get_env_int("USER_ID_2"), "Egorik") if TOKEN_EGORIK else None
    if watcher_egoist:
        watcher_egoist.client.event(_make_on_message(watcher_egoist.client))
    if watcher_egorik:
        watcher_egorik.client.event(_make_on_message(watcher_egorik.client))

    tasks = []
    if TOKEN_MAIN: tasks.append(safe_start(main_client, TOKEN_MAIN, "MAIN"))
    if TOKEN_SECOND: tasks.append(safe_start(second_client, TOKEN_SECOND, "SECOND"))
    if TOKEN_THIRD: tasks.append(safe_start(third_client, TOKEN_THIRD, "THIRD"))
    if TOKEN_RESERVE and reserve_client is not None: tasks.append(safe_start(reserve_client, TOKEN_RESERVE, "RESERVE"))
    if watcher_egoist: tasks.append(watcher_egoist.start())
    if watcher_egorik: tasks.append(watcher_egorik.start())
    tasks.append(uptime_pinger())
    tasks.append(timely_scheduler())
    tasks.append(_log_voice_uptime_periodic())
    tasks.append(_all_offline_watchdog())

    log.info("Запуск всех ботов...")
    await asyncio.gather(*tasks, return_exceptions=True)

def _install_signal_handlers(loop):
    def _stop():
        log.info("Получен сигнал завершения...")
        _shutdown_event.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, _stop)
        except NotImplementedError: pass

if __name__ == "__main__":
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(exception_handler)
        _install_signal_handlers(loop)
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
