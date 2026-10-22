# ====================================================
# TT11TT — Discord боты (v55 - Fix _install_signal_handlers)
# ====================================================
import os
import sys
import asyncio
import logging
import random
import time
import string
import signal
import traceback
from collections import deque
from datetime import datetime, timezone

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

# ТОКЕНЫ
TOKEN_EGOIST = get_env_str("DISCORD_TOKEN_1")
TOKEN_EGORIK = get_env_str("DISCORD_TOKEN_2")
TOKEN_MAIN = get_env_str("DISCORD_TOKEN_3")
TOKEN_SECOND = get_env_str("DISCORD_TOKEN_4")
TOKEN_THIRD = get_env_str("DISCORD_TOKEN_5")
TOKEN_RESERVE = get_env_str("DISCORD_TOKEN_6")

MAIN_CHANNEL_ID = get_env_int("VOICE_CHANNEL_ID")
GUILD_ID = get_env_int("GUILD_ID")
SPAM_CHANNEL_ID = get_env_int("SPAM_CHANNEL_ID", 1270672504582832150)

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

# ДОСТУП К КОМАНДАМ
ALLOWED_COMMAND_USERS = {1113899288192356402, 1416125087324831815}
COMMAND_ALLOWED_CHANNEL_IDS = {1241013075151425650}

# КОНСТАНТЫ
STATUS_INTERVAL = 3600
CONNECT_TIMEOUT = 60.0
WATCHDOG_BASE_INTERVAL = 30.0
RECONNECT_MIN_DELAY = 10.0
DEAD_SOCKET_LATENCY_MS = 30000
ALL_OFFLINE_RESTART_SEC = 120
RECONNECT_GUARD_SEC = 20.0
CHANNEL_NOT_FOUND_BACKOFF_MIN = 10.0
CHANNEL_NOT_FOUND_BACKOFF_MAX = 60.0

TEXT_STATUSES = ["Commback", "TALENT 24/7", "На страже", "Сижу в войсе"]
WATCHING_STATUSES = ["как вы играете", "на ваши сообщения", "за порядком"]

# ГЕНЕРАТОР ТЕКСТА
_LOWER = string.ascii_lowercase
_UPPER = string.ascii_uppercase
_DIGITS = string.digits
_ALL = _LOWER + _UPPER + _DIGITS

def _rand_letters(min_len=3, max_len=12):
    return ''.join(random.choices(_LOWER, k=random.randint(min_len, max_len)))

def _rand_letters_mixed(min_len=3, max_len=12):
    pool = _LOWER + _LOWER + _UPPER
    return ''.join(random.choices(pool, k=random.randint(min_len, max_len)))

def _rand_digits(min_len=1, max_len=8):
    return ''.join(random.choices(_DIGITS, k=random.randint(min_len, max_len)))

def _rand_mixed(min_len=5, max_len=15):
    return ''.join(random.choices(_ALL, k=random.randint(min_len, max_len)))

def _rand_gibberish_phrase():
    n = random.randint(2, 4)
    return " ".join(''.join(random.choices(_LOWER, k=random.randint(3, 7))) for _ in range(n))

def _generate_live_text_safe() -> str:
    try:
        r = random.random()
        if r < 0.5:
            return _rand_letters_mixed()
        if r < 0.75:
            return _rand_digits()
        if r < 0.95:
            return _rand_mixed()
        return _rand_gibberish_phrase()
    except Exception:
        return _rand_letters()

# СОСТОЯНИЕ
watcher_egoist = None
watcher_egorik = None
start_time = time.time()

ALL_KEYS = ("main", "second", "third", "reserve", "egoist", "egorik")
reconnect_stats = {k: 0 for k in ALL_KEYS}
_voice_last_ok = {k: time.time() for k in ALL_KEYS}
_reconnect_guard = {k: 0.0 for k in ALL_KEYS}
_watchdog_intervals = {k: WATCHDOG_BASE_INTERVAL for k in ALL_KEYS}
_client_ready = {k: False for k in ALL_KEYS}
_channel_not_found = {k: 0 for k in ALL_KEYS}
_channel_backoff = {k: CHANNEL_NOT_FOUND_BACKOFF_MIN for k in ALL_KEYS}
_consecutive_failures = {k: 0 for k in ALL_KEYS}
_drops_by_hour = {h: 0 for h in range(24)}
_drops_day = datetime.now(timezone.utc).date()

total_messages_sent = 0
total_commands_handled = 0
total_voice_drops = 0
event_log = deque(maxlen=150)

_shutdown_event = asyncio.Event()
_all_offline_since = None

# КЛИЕНТЫ
main_client = discord.Client(chunk_guilds_at_startup=False)
second_client = discord.Client(chunk_guilds_at_startup=False)
third_client = discord.Client(chunk_guilds_at_startup=False)
reserve_client = discord.Client(chunk_guilds_at_startup=False) if TOKEN_RESERVE else None

_CLIENTS = {"main": main_client, "second": second_client, "third": third_client}
if reserve_client:
    _CLIENTS["reserve"] = reserve_client

# УТИЛИТЫ
def log_event(text: str):
    ts = time.strftime("%H:%M:%S")
    event_log.append(f"[{ts}] {text}")
    log.info(text)

def log_reconnect(name: str):
    reconnect_stats[name] = reconnect_stats.get(name, 0) + 1
    log_event(f"реконнект {name} #{reconnect_stats[name]}")

def fmt_uptime() -> str:
    u = int(time.time() - start_time)
    return f"{u // 3600}ч {(u % 3600) // 60}м {u % 60}с"

def _voice_channel_id(client) -> int:
    try:
        if client.voice_clients:
            vc = client.voice_clients[0]
            if vc and vc.is_connected() and vc.channel:
                return vc.channel.id
    except Exception:
        pass
    return 0

def _is_in_voice(client, channel_id: int) -> bool:
    return bool(channel_id) and _voice_channel_id(client) == channel_id

async def _safe_get_channel(client, channel_id):
    if not channel_id:
        return None
    ch = client.get_channel(channel_id)
    if ch:
        return ch
    try:
        ch = await client.fetch_channel(channel_id)
        if ch:
            return ch
    except Exception:
        pass
    if GUILD_ID:
        try:
            guild = client.get_guild(GUILD_ID)
            if not guild:
                try:
                    guild = await client.fetch_guild(GUILD_ID)
                except Exception:
                    guild = None
            if guild:
                ch = guild.get_channel(channel_id)
                if ch:
                    return ch
                try:
                    ch = await guild.fetch_channel(channel_id)
                    if ch:
                        return ch
                except Exception:
                    pass
        except Exception:
            pass
    return None

def _is_dead_socket(client) -> bool:
    if not client.is_ready():
        return True
    try:
        lat = client.latency
        if lat != lat or lat * 1000 > DEAD_SOCKET_LATENCY_MS:
            return True
    except Exception:
        return True
    return False

def _is_watcher_client(client) -> bool:
    for w in (watcher_egoist, watcher_egorik):
        if w is not None and w.client is client:
            return True
    return False

async def _warmup_guild(key: str):
    try:
        _client_ready[key] = True
        _voice_last_ok[key] = time.time()
    except Exception:
        pass

def _register_voice_ok(key: str):
    try:
        _voice_last_ok[key] = time.time()
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
            await asyncio.wait_for(_shutdown_event.wait(), timeout=840)
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

async def status_changer(client, name):
    if not STATUS_ROTATION:
        return
    while not _shutdown_event.is_set():
        try:
            if client.is_ready():
                await client.change_presence(activity=None)
                await asyncio.sleep(2)
                if random.random() < 0.5:
                    act = discord.Activity(type=discord.ActivityType.playing, name=random.choice(TEXT_STATUSES))
                else:
                    act = discord.Activity(type=discord.ActivityType.watching, name=random.choice(WATCHING_STATUSES))
                await client.change_presence(activity=act)
        except Exception:
            pass
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=STATUS_INTERVAL)
        except asyncio.TimeoutError:
            pass

async def _all_offline_watchdog():
    global _all_offline_since
    while not _shutdown_event.is_set():
        await asyncio.sleep(30)
        expected = []
        for name, c in _CLIENTS.items():
            token = {"main": TOKEN_MAIN, "second": TOKEN_SECOND, "third": TOKEN_THIRD, "reserve": TOKEN_RESERVE}.get(name)
            if token:
                expected.append(c)
        if watcher_egoist and watcher_egoist.token:
            expected.append(watcher_egoist.client)
        if watcher_egorik and watcher_egorik.token:
            expected.append(watcher_egorik.client)
        if not expected:
            continue
        ready = sum(1 for c in expected if c.is_ready())
        if ready == 0:
            if _all_offline_since is None:
                _all_offline_since = time.time()
                log_event("⚠️ Все клиенты оффлайн")
            elif time.time() - _all_offline_since > ALL_OFFLINE_RESTART_SEC:
                log.error("Все оффлайн > %dс — перезапуск процесса", ALL_OFFLINE_RESTART_SEC)
                os._exit(1)
        else:
            _all_offline_since = None

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
        async def on_resumed():
            log.info("[%s] resumed", self.name)
            if not _client_ready.get(self.key):
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
                if not g or g.id != GUILD_ID:
                    return
            me = self.client.user
            if not me or member.id != me.id:
                return
            if not self.manual_mode or self.disabled:
                return
            if before.channel and after.channel and before.channel.id == after.channel.id:
                return
            if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
                log_event(f"[{self.name}] Потеря связи с войсом, переподключение...")
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
        while not _shutdown_event.is_set() and not _client_ready.get(self.key):
            await asyncio.sleep(1)
        while not _shutdown_event.is_set():
            await asyncio.sleep(WATCHDOG_BASE_INTERVAL)
            if self.disabled or not self.manual_mode:
                continue
            if not self.client.is_ready():
                continue
            if _is_dead_socket(self.client):
                log_event(f"[{self.name}] dead socket")
                _register_voice_drop(self.key, "dead-socket")
                log_reconnect(self.key)
                await self._force_reconnect()
                continue
            if not _is_in_voice(self.client, MAIN_CHANNEL_ID):
                now = time.time()
                if now - _reconnect_guard.get(self.key, 0) < RECONNECT_GUARD_SEC:
                    continue
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
            except Exception:
                pass
            await asyncio.sleep(RECONNECT_MIN_DELAY)
        await self.connect(MAIN_CHANNEL_ID)

    async def connect(self, channel_id):
        async with self._connect_lock:
            if self.disabled or not channel_id:
                return False, "Отключен"
            try:
                ch = await _safe_get_channel(self.client, channel_id)
                if not ch:
                    _channel_not_found[self.key] = _channel_not_found.get(self.key, 0) + 1
                    _channel_backoff[self.key] = min(CHANNEL_NOT_FOUND_BACKOFF_MAX, _channel_backoff.get(self.key, CHANNEL_NOT_FOUND_BACKOFF_MIN) * 1.5)
                    return False, "Канал не найден"
                _channel_not_found[self.key] = 0
                _channel_backoff[self.key] = CHANNEL_NOT_FOUND_BACKOFF_MIN
                if _is_in_voice(self.client, channel_id):
                    _register_voice_ok(self.key)
                    return True, "Уже в канале"
                if self.client.voice_clients:
                    try:
                        await self.client.voice_clients[0].disconnect(force=True)
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                await ch.connect(timeout=CONNECT_TIMEOUT, self_deaf=False, self_mute=False)
                log.info("[%s] подключен к %s (БЕЗ МУТА)", self.name, getattr(ch, "name", channel_id))
                _register_voice_ok(self.key)
                return True, "Успешно"
            except Exception as e:
                _register_voice_drop(self.key, "connect-fail")
                return False, str(e)

    async def disconnect(self):
        try:
            if self.client.voice_clients:
                await self.client.voice_clients[0].disconnect(force=True)
        except Exception:
            pass

    async def start(self):
        delay = 30
        while not _shutdown_event.is_set():
            try:
                await self.client.start(self.token)
                log.warning("[%s] клиент закрылся, рестарт через %dс", self.name, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 600)
            except discord.LoginFailure as e:
                log.error("[%s] Неверный токен (%s). Ждём 300с", self.name, e)
                await asyncio.sleep(300)
                delay = 300
            except Exception as e:
                log.error("[%s] ошибка старта: %s, рестарт через %dс", self.name, e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 600)

    async def spam_messages(self, channel_id, count):
        global total_messages_sent
        try:
            ch = await _safe_get_channel(self.client, channel_id)
            if not ch:
                return False
            for _ in range(count):
                txt = _generate_live_text_safe()
                try:
                    await ch.send(txt)
                    total_messages_sent += 1
                except discord.HTTPException as he:
                    if he.status == 429:
                        r = None
                        try:
                            if he.response:
                                r = he.response.headers.get("Retry-After")
                        except Exception:
                            pass
                        await asyncio.sleep(float(r or 5))
                        continue
                    raise
                await asyncio.sleep(random.uniform(1.2, 3.0))
            log.info("[%s] отправил %d сообщений", self.name, count)
            return True
        except Exception as e:
            log.error("[%s] ошибка спама: %s", self.name, e)
            return False

# КОМАНДЫ
def _is_allowed_user(message) -> bool:
    return getattr(message.author, "id", 0) in ALLOWED_COMMAND_USERS

def _is_command_channel(message) -> bool:
    if getattr(message, "guild", None) is None:
        return True
    if isinstance(message.channel, (discord.DMChannel, discord.PartialMessageable)):
        return True
    return getattr(message.channel, "id", 0) in COMMAND_ALLOWED_CHANNEL_IDS

def _should_defer_to_watcher(bound_client) -> bool:
    if _is_watcher_client(bound_client):
        return False
    for w in (watcher_egoist, watcher_egorik):
        if w and w.client.is_ready():
            return True
    return False

_HANDLED = set()

def _mark(message_id: int) -> bool:
    if message_id in _HANDLED:
        return True
    if len(_HANDLED) > 2000:
        _HANDLED.clear()
    _HANDLED.add(message_id)
    return False

def _help() -> str:
    return (
        "**TT11TT**\n"
        "`!panel` / `!ping` / `!help`\n"
        "`!on` / `!off` egoist|egorik\n"
        "`!connect` / `!disconnect` / `!reconnect`\n"
        "`!spam <число>`"
    )

def _ping(clients) -> str:
    out = ["**Статус**"]
    for name, c in clients:
        try:
            if not c:
                out.append(f"- {name}: нет")
            elif not c.is_ready():
                out.append(f"- {name}: оффлайн")
            else:
                out.append(f"- {name}: онлайн | {int(c.latency * 1000)}мс")
        except Exception:
            out.append(f"- {name}: ошибка")
    return "\n".join(out)

def _panel() -> str:
    try:
        w1 = "ON" if watcher_egoist and not watcher_egoist.disabled and watcher_egoist.manual_mode else "OFF"
        w2 = "ON" if watcher_egorik and not watcher_egorik.disabled and watcher_egorik.manual_mode else "OFF"
        m1 = "ON" if main_client.is_ready() else "OFF"
        m2 = "ON" if second_client.is_ready() else "OFF"
        m3 = "ON" if third_client.is_ready() else "OFF"
        m4 = "ON" if reserve_client and reserve_client.is_ready() else "OFF"
    except Exception:
        w1 = w2 = m1 = m2 = m3 = m4 = "?"
    return (
        f"**Панель** | Uptime: {fmt_uptime()}\n"
        f"Egoist:{w1} Egorik:{w2} | MAIN:{m1} SECOND:{m2} THIRD:{m3} RES:{m4}\n"
        f"Сообщений:{total_messages_sent} Команд:{total_commands_handled} Drops:{total_voice_drops}"
    )

def _on_msg(c):
    async def handler(m):
        try:
            await _handle(c, m)
        except Exception as e:
            log.error("cmd err: %s", e)
    return handler

async def _handle(bound, message):
    global total_commands_handled, total_messages_sent
    if not bound.user or message.author.id == bound.user.id:
        return
    if not _is_allowed_user(message):
        return
    content = (message.content or "").strip()
    if not content.startswith("!"):
        return
    if not _is_command_channel(message):
        return
    if _should_defer_to_watcher(bound):
        return
    if _mark(message.id):
        return
    total_commands_handled += 1
    parts = content.split()
    low = content.lower()

    if len(parts) == 2:
        cmd = parts[0].lower()
        t = parts[1].lower()
        if t in ("egoist", "egorik") and cmd in ("!on", "!вкл"):
            w = watcher_egoist if t == "egoist" else watcher_egorik
            if not w:
                await message.channel.send("Не настроен")
                return
            w.disabled = False
            w.manual_mode = True
            ok, res = await w.connect(MAIN_CHANNEL_ID)
            await message.channel.send("OK" if ok else f"ERR: {res}")
            return
        if t in ("egoist", "egorik") and cmd in ("!off", "!выкл"):
            w = watcher_egoist if t == "egoist" else watcher_egorik
            if not w:
                await message.channel.send("Не настроен")
                return
            w.manual_mode = False
            w.disabled = True
            await w.disconnect()
            await message.channel.send("OFF")
            return

    if low in ("!panel", "!status"):
        await message.channel.send(_panel())
        return
    if low == "!help":
        await message.channel.send(_help())
        return
    if low == "!ping":
        lst = [("MAIN", main_client), ("SECOND", second_client), ("THIRD", third_client)]
        if reserve_client:
            lst.append(("RES", reserve_client))
        if watcher_egoist:
            lst.append(("Egoist", watcher_egoist.client))
        if watcher_egorik:
            lst.append(("Egorik", watcher_egorik.client))
        await message.channel.send(_ping(lst))
        return
    if low == "!connect" and bound == main_client:
        await message.channel.send("Используй !on egoist|egorik")
        return
    if low == "!disconnect" and bound == main_client:
        await message.channel.send("Используй !off egoist|egorik")
        return
    if low == "!reconnect" and bound == main_client:
        for w in (watcher_egoist, watcher_egorik):
            if w and not w.disabled and w.manual_mode:
                try:
                    await w._force_reconnect()
                except Exception:
                    pass
        await message.channel.send("Реконнект запущен")
        return
    if low.startswith("!spam") and bound == main_client:
        try:
            cnt = int(parts[1]) if len(parts) > 1 else 5
            cnt = max(1, min(cnt, 40))
            if watcher_egoist and not watcher_egoist.disabled and watcher_egoist.manual_mode:
                await watcher_egoist.spam_messages(SPAM_CHANNEL_ID or MAIN_CHANNEL_ID, cnt)
                await message.channel.send(f"Спам {cnt} (Egoist)")
                return
            elif watcher_egorik and not watcher_egorik.disabled and watcher_egorik.manual_mode:
                await watcher_egorik.spam_messages(SPAM_CHANNEL_ID or MAIN_CHANNEL_ID, cnt)
                await message.channel.send(f"Спам {cnt} (Egorik)")
                return
            else:
                await message.channel.send("Включи !on egoist или !on egorik")
                return
        except Exception as e:
            await message.channel.send(f"Err {e}")
            return

def _install_signal_handlers(loop):
    def _stop():
        log.info("Получен сигнал завершения...")
        _shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

async def _start(c, t, n):
    d = 30
    while not _shutdown_event.is_set():
        try:
            await c.start(t)
            log.warning("[%s] закрыт, рестарт через %dс", n, d)
            await asyncio.sleep(d)
            d = min(d * 2, 600)
        except discord.LoginFailure as e:
            log.error("[%s] неверный токен: %s", n, e)
            await asyncio.sleep(300)
            d = 300
        except Exception as e:
            log.error("[%s] ошибка: %s, рестарт через %dс", n, e, d)
            await asyncio.sleep(d)
            d = min(d * 2, 600)

async def main():
    global watcher_egoist, watcher_egorik
    log.info("Инициализация...")
    log.info("--------------------------------------------------")
    log.info("VOICE_CHANNEL_ID = %s", MAIN_CHANNEL_ID)
    log.info("GUILD_ID         = %s", GUILD_ID)
    log.info("SPAM_CHANNEL_ID  = %s", SPAM_CHANNEL_ID)
    log.info("Токены: 1=%s 2=%s 3=%s 4=%s 5=%s 6=%s", bool(TOKEN_EGOIST), bool(TOKEN_EGORIK), bool(TOKEN_MAIN), bool(TOKEN_SECOND), bool(TOKEN_THIRD), bool(TOKEN_RESERVE))
    log.info("--------------------------------------------------")

    watcher_egoist = Watcher(TOKEN_EGOIST, get_env_int("USER_ID_1"), "Egoist") if TOKEN_EGOIST else None
    watcher_egorik = Watcher(TOKEN_EGORIK, get_env_int("USER_ID_2"), "Egorik") if TOKEN_EGORIK else None

    if watcher_egoist:
        watcher_egoist.client.event(_on_msg(watcher_egoist.client))
    if watcher_egorik:
        watcher_egorik.client.event(_on_msg(watcher_egorik.client))
    main_client.event(_on_msg(main_client))
    second_client.event(_on_msg(second_client))
    third_client.event(_on_msg(third_client))
    if reserve_client:
        reserve_client.event(_on_msg(reserve_client))

    tasks = []
    if TOKEN_MAIN:
        tasks.append(_start(main_client, TOKEN_MAIN, "MAIN"))
    if TOKEN_SECOND:
        tasks.append(_start(second_client, TOKEN_SECOND, "SECOND"))
    if TOKEN_THIRD:
        tasks.append(_start(third_client, TOKEN_THIRD, "THIRD"))
    if TOKEN_RESERVE and reserve_client:
        tasks.append(_start(reserve_client, TOKEN_RESERVE, "RESERVE"))
    if watcher_egoist:
        tasks.append(watcher_egoist.start())
    if watcher_egorik:
        tasks.append(watcher_egorik.start())
    tasks.append(uptime_pinger())
    tasks.append(_heartbeat_loop())
    tasks.append(_all_offline_watchdog())

    log.info("Запуск всех ботов...")
    await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(exception_handler)
        _install_signal_handlers(loop)
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
