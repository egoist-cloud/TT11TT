# ====================================================
# TT11TT — Discord боты (v27, Максимальная защита войса)
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
import traceback
import threading
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta

import aiohttp
import discord

# ====================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
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
# ENV
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
TOKEN_THIRD = get_env_str("DISCORD_TOKEN_5")
TOKEN_EGOIST = get_env_str("DISCORD_TOKEN_2")
TOKEN_EGORIK = get_env_str("DISCORD_TOKEN_3")

MAIN_CHANNEL_ID = get_env_int("VOICE_CHANNEL_ID")
GUILD_ID = get_env_int("GUILD_ID")
USER_ID_EGOIST = get_env_int("USER_ID_1")
USER_ID_EGORIK = get_env_int("USER_ID_2")
MAIN_BOT_USER_ID = get_env_int("MAIN_BOT_USER_ID", 0)

RENDER_URL = get_env_str("RENDER_URL") or "https://tt11tt-1.onrender.com"
TELEGRAM_TOKEN = get_env_str("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = get_env_str("TELEGRAM_CHAT_ID")
STATUS_ROTATION = get_env_bool("STATUS_ROTATION", False)
SPAM_CHANNEL_ID = 1270672504582832150

log.info("=" * 50)
log.info("Загрузка конфигурации...")
log.info("Канал: %s | Гильдия: %s", MAIN_CHANNEL_ID, GUILD_ID or "все")
log.info("=" * 50)


# ====================================================
# КОНСТАНТЫ (защита войса)
# ====================================================
STATUS_INTERVAL = 3600
CONNECT_TIMEOUT = 60.0

# Watchdog
WATCHDOG_MIN_INTERVAL = 2.0
WATCHDOG_BASE_INTERVAL = 3.0
WATCHDOG_MAX_INTERVAL = 10.0

# Backoff при реконнектах
RECONNECT_MIN_DELAY = 2.0
RECONNECT_MAX_DELAY = 8.0

# Dead socket
DEAD_SOCKET_LATENCY_MS = 30_000  # latency > 30с = мёртвый
DEAD_SOCKET_TIMEOUT = 900        # 15 мин
DEAD_SOCKET_CRITICAL = 3600      # 1 час

# Анти-дребезг
RECONNECT_GUARD_SEC = 5.0

# Мониторинг стабильности
STABILITY_WINDOW_SEC = 3600      # окно 1 час
STABILITY_MAX_DROPS = 5          # > 5 вылетов/час = алерт
VOICE_UPTIME_LOG_INTERVAL = 1800 # лог каждые 30 мин

TEXT_STATUSES = ["Commback", "TALENT 24/7", "На страже", "Сижу в войсе"]
WATCHING_STATUSES = ["как вы играете", "на ваши сообщения", "за порядком"]

WRITE_COOLDOWN = 15.0
MAX_WRITE_COUNT = 100

# ====================================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ
# ====================================================
watcher_egoist = None
watcher_egorik = None
start_time = time.time()

reconnect_stats = {"main": 0, "second": 0, "third": 0, "egoist": 0, "egorik": 0}
reconnect_history = {k: deque(maxlen=500) for k in reconnect_stats}
last_event_time = {k: time.time() for k in reconnect_stats}

# --- Защита от вылетов ---
_voice_uptime = {k: 0.0 for k in reconnect_stats}        # сек в войсе
_voice_last_ok = {k: time.time() for k in reconnect_stats}
_reconnect_guard = {k: 0.0 for k in reconnect_stats}     # ts последнего реконнекта
_reconnect_locks = {k: asyncio.Lock() for k in reconnect_stats}
_watchdog_intervals = {k: WATCHDOG_BASE_INTERVAL for k in reconnect_stats}
_last_uptime_log = {k: 0.0 for k in reconnect_stats}

total_messages_sent = 0
total_telegram_sent = 0
total_commands_handled = 0
total_voice_drops = 0
event_log = deque(maxlen=80)

_watchdogs_started = set()
_user_cache = {}
_main_lock = asyncio.Lock()
_second_lock = asyncio.Lock()
_third_lock = asyncio.Lock()

_shutdown_event = asyncio.Event()
_last_write_at = 0.0
_last_timely_key = None

# ====================================================
# КЛИЕНТЫ
# ====================================================
main_client = discord.Client(chunk_guilds_at_startup=False)
second_client = discord.Client(chunk_guilds_at_startup=False)
third_client = discord.Client(chunk_guilds_at_startup=False)

_CLIENTS = {"main": main_client, "second": second_client, "third": third_client}
_LOCKS = {"main": _main_lock, "second": _second_lock, "third": _third_lock}


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
            payload = {
                "ok": True,
                "uptime_sec": int(time.time() - start_time),
                "reconnects": reconnect_stats,
                "voice_uptime": {k: int(v) for k, v in _voice_uptime.items()},
                "messages_sent": total_messages_sent,
                "commands": total_commands_handled,
                "voice_drops": total_voice_drops,
            }
            self._respond(200, json.dumps(payload).encode(), "application/json")
        elif self.path.startswith("/events"):
            self._respond(200, json.dumps(list(event_log)).encode(), "application/json")
        elif self.path.startswith("/health"):
            self._respond(200, b"OK")
        else:
            self._respond(200, b"Bot is running")

    def log_message(self, *args):
        pass


def run_web_server():
    port = int(os.getenv("PORT", 10000))
    try:
        HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()
    except Exception as e:
        log.error("Web server error: %s", e)


threading.Thread(target=run_web_server, daemon=True).start()


# ====================================================
# УТИЛИТЫ
# ====================================================
def log_event(text: str):
    ts = time.strftime("%H:%M:%S")
    event_log.append(f"[{ts}] {text}")
    log.info(text)


def log_reconnect(name: str):
    reconnect_stats[name] = reconnect_stats.get(name, 0) + 1
    reconnect_history[name].append(time.time())
    last_event_time[name] = time.time()
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
                if resp.status == 200:
                    total_telegram_sent += 1
    except Exception:
        pass


async def uptime_pinger():
    await asyncio.sleep(60)
    while not _shutdown_event.is_set():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RENDER_URL, timeout=20):
                    pass
        except Exception:
            pass
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=14 * 60)
        except asyncio.TimeoutError:
            pass


def fmt_uptime() -> str:
    u = int(time.time() - start_time)
    return f"{u // 3600}ч {(u % 3600) // 60}м {u % 60}с"


def fmt_ago(ts: float) -> str:
    if not ts:
        return "никогда"
    delta = int(time.time() - ts)
    if delta < 60:
        return f"{delta}с назад"
    if delta < 3600:
        return f"{delta // 60}м назад"
    return f"{delta // 3600}ч {(delta % 3600) // 60}м назад"


def fmt_duration(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}с"
    if sec < 3600:
        return f"{sec // 60}м {sec % 60}с"
    return f"{sec // 3600}ч {(sec % 3600) // 60}м"


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
    if ch is not None:
        return ch
    try:
        return await client.fetch_channel(channel_id)
    except Exception:
        return None


async def _safe_get_guild(client, guild_id):
    if not guild_id:
        return None
    g = client.get_guild(guild_id)
    if g is not None:
        return g
    try:
        return await client.fetch_guild(guild_id)
    except Exception:
        return None


# ====================================================
# МОНИТОРИНГ СТАБИЛЬНОСТИ
# ====================================================
def _register_voice_ok(name: str):
    """Фиксирует успешное нахождение в войсе."""
    now = time.time()
    last = _voice_last_ok.get(name, now)
    _voice_uptime[name] = _voice_uptime.get(name, 0.0) + max(0.0, now - last)
    _voice_last_ok[name] = now
    _watchdog_intervals[name] = max(
        WATCHDOG_MIN_INTERVAL,
        _watchdog_intervals.get(name, WATCHDOG_BASE_INTERVAL) * 0.9,
    )


def _register_voice_drop(name: str, reason: str = ""):
    """Фиксирует вылет из войса."""
    global total_voice_drops
    total_voice_drops += 1
    _voice_last_ok[name] = time.time()
    _watchdog_intervals[name] = min(
        WATCHDOG_MAX_INTERVAL,
        _watchdog_intervals.get(name, WATCHDOG_BASE_INTERVAL) * 1.5 + 0.5,
    )
    log_event(f"⚠️ {name} вылетел из войса ({reason or 'watchdog'})")
    _check_stability(name)


def _check_stability(name: str):
    """Проверяет частоту вылетов за окно и алертит."""
    now = time.time()
    hist = reconnect_history[name]
    recent = [t for t in hist if now - t <= STABILITY_WINDOW_SEC]
    if len(recent) > STABILITY_MAX_DROPS:
        asyncio.ensure_future(send_telegram(
            f"🚨 <b>{name}</b>: {len(recent)} вылетов за час! "
            f"uptime={fmt_duration(_voice_uptime.get(name, 0))}"
        ))


async def _log_voice_uptime_periodic():
    """Периодически логирует uptime голосовых соединений."""
    while not _shutdown_event.is_set():
        await asyncio.sleep(VOICE_UPTIME_LOG_INTERVAL)
        for name in ("main", "second", "third"):
            client = _CLIENTS[name]
            if _is_in_voice(client, MAIN_CHANNEL_ID):
                _register_voice_ok(name)
                log.info(
                    "[%s] в войсе, суммарный uptime=%s, реконнектов=%d",
                    name.upper(),
                    fmt_duration(_voice_uptime.get(name, 0)),
                    reconnect_stats.get(name, 0),
                )


# ====================================================
# ПРОВЕРКА «МЁРТВОГО» СОКЕТА
# ====================================================
def _is_dead_socket(client) -> bool:
    """True, если клиент считается «мёртвым» по latency."""
    if not client.is_ready():
        return True
    try:
        latency = client.latency
        if latency != latency:  # NaN
            return True
        if latency * 1000 > DEAD_SOCKET_LATENCY_MS:
            return True
    except Exception:
        return True
    return False


# ====================================================
# СМЕНА СТАТУСА
# ====================================================
async def status_changer(client, name):
    if not STATUS_ROTATION:
        return
    while not _shutdown_event.is_set():
        try:
            if client.is_ready():
                if random.random() < 0.5:
                    activity = discord.Activity(
                        type=discord.ActivityType.playing,
                        name=random.choice(TEXT_STATUSES),
                    )
                else:
                    activity = discord.Activity(
                        type=discord.ActivityType.watching,
                        name=random.choice(WATCHING_STATUSES),
                    )
                await client.change_presence(activity=activity)
        except Exception as e:
            log.debug("[%s] status_changer: %s", name, e)
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=STATUS_INTERVAL)
        except asyncio.TimeoutError:
            pass


# ====================================================
# WATCHER (Egoist / Egorik)
# ====================================================
class Watcher:
    def __init__(self, token, user_id, name):
        self.token = token
        self.user_id = user_id
        self.name = name
        self.client = discord.Client(chunk_guilds_at_startup=False)
        self.manual_mode = False
        self.disabled = True
        self._connect_lock = asyncio.Lock()
        self._watchdog_task = None
        self._register_events()

    def _register_events(self):
        @self.client.event
        async def on_ready():
            log.info("[%s] авторизован как %s", self.name, self.client.user)
            self.client.loop.create_task(status_changer(self.client, self.name))
            self._start_watchdog()

        @self.client.event
        async def on_disconnect():
            if self.manual_mode and not self.disabled:
                log_event(f"[{self.name}] on_disconnect → реконнект")

        @self.client.event
        async def on_voice_state_update(member, before, after):
            if GUILD_ID and member.guild.id != GUILD_ID:
                return
            me = self.client.user
            if me is None or member.id != me.id:
                return
            if not self.manual_mode or self.disabled:
                return
            if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
                log_reconnect(self.name.lower())
                await asyncio.sleep(2)
                if self.manual_mode and not self.disabled:
                    await self.connect(MAIN_CHANNEL_ID)

    def _start_watchdog(self):
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = self.client.loop.create_task(self._watchdog())

    async def _watchdog(self):
        """Следит за удержанием в войсе для дежурного."""
        while not _shutdown_event.is_set():
            interval = _watchdog_intervals.get(self.name.lower(), WATCHDOG_BASE_INTERVAL)
            await asyncio.sleep(interval)
            if self.disabled or not self.manual_mode:
                continue
            name_key = self.name.lower()
            if not _is_in_voice(self.client, MAIN_CHANNEL_ID):
                now = time.time()
                if now - _reconnect_guard.get(name_key, 0) < RECONNECT_GUARD_SEC:
                    continue
                _reconnect_guard[name_key] = now
                _register_voice_drop(name_key, "watcher-watchdog")
                log_reconnect(name_key)
                await self._force_reconnect()
            else:
                _register_voice_ok(name_key)

    async def _force_reconnect(self):
        """Жёсткий реконнект: разрыв + пауза + подключение."""
        async with self._connect_lock:
            try:
                if self.client.voice_clients:
                    await self.client.voice_clients[0].disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(RECONNECT_MIN_DELAY)
        await self.connect(MAIN_CHANNEL_ID)

    async def connect(self, channel_id):
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            if self.disabled or channel_id == 0:
                return False, "Отключен"
            try:
                channel = await _safe_get_channel(self.client, channel_id)
                if not channel:
                    return False, "Канал не найден"

                if _is_in_voice(self.client, channel_id):
                    return True, "Уже в канале"

                if self.client.voice_clients:
                    try:
                        await self.client.voice_clients[0].disconnect(force=True)
                    except Exception:
                        pass
                    await asyncio.sleep(1)

                await channel.connect(timeout=CONNECT_TIMEOUT, self_deaf=False, self_mute=False)
                log.info("[%s] подключен к %s (без мута)", self.name, channel.name)
                return True, "Успешно"
            except Exception as e:
                return False, str(e)

    async def disconnect(self):
        if self.client.voice_clients:
            try:
                await self.client.voice_clients[0].disconnect(force=True)
            except Exception:
                pass

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
            if not channel:
                return False
            for _ in range(count):
                await channel.send(''.join(random.choices(string.digits, k=6)))
                total_messages_sent += 1
                await asyncio.sleep(2.0)
            log.info("[%s] Отправил %d сообщений", self.name, count)
            return True
        except Exception as e:
            log.error("[%s] Ошибка спама: %s", self.name, e)
            return False


# ====================================================
# ЗАЩИЩЁННОЕ ПОДКЛЮЧЕНИЕ 24/7
# ====================================================
async def _force_reconnect(client_name: str):
    """Жёсткий реконнект: разрыв + пауза + подключение."""
    client = _CLIENTS[client_name]
    lock = _reconnect_locks[client_name]
    async with lock:
        try:
            if client.voice_clients:
                await client.voice_clients[0].disconnect(force=True)
                log.info("[%s] принудительный disconnect", client_name.upper())
        except Exception as e:
            log.debug("[%s] disconnect error: %s", client_name, e)
        await asyncio.sleep(RECONNECT_MIN_DELAY)
    await _ensure_connected(client_name)


async def _enforce_mute(client_name: str):
    """Принудительно ставит мут и деаф."""
    client = _CLIENTS[client_name]
    if not client.voice_clients:
        return
    try:
        vc = client.voice_clients[0]
        if not vc.is_connected() or not vc.channel:
            return
        guild = vc.guild
        if guild is None:
            return
        me = guild.get_member(client.user.id)
        if me and me.voice and (not me.voice.self_mute or not me.voice.self_deaf):
            await guild.change_voice_state(
                channel=vc.channel, self_mute=True, self_deaf=True
            )
            log.info("[%s] принудительный мьют/деаф", client_name.upper())
    except Exception as e:
        log.debug("[%s] enforce_mute: %s", client_name, e)


async def _ensure_connected(client_name: str):
    """Проверка и принудительное подключение 24/7 ботов (в муте)."""
    client = _CLIENTS[client_name]
    if MAIN_CHANNEL_ID == 0:
        return

    # Уже в нужном канале — проверим мьют
    if _is_in_voice(client, MAIN_CHANNEL_ID):
        await _enforce_mute(client_name)
        return

    async with _LOCKS[client_name]:
        if _is_in_voice(client, MAIN_CHANNEL_ID):
            return

        channel = await _safe_get_channel(client, MAIN_CHANNEL_ID)
        if not channel:
            log.warning("[%s] канал %s не найден", client_name, MAIN_CHANNEL_ID)
            return

        if client.voice_clients:
            try:
                await client.voice_clients[0].disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(1)

        try:
            await channel.connect(timeout=CONNECT_TIMEOUT, self_deaf=True, self_mute=True)
            log.info("[%s] подключен к %s (В МУТЕ 24/7)", client_name.upper(), channel.name)
            _register_voice_ok(client_name)
        except Exception as e:
            log.warning("[%s] ошибка подключения: %s", client_name.upper(), e)
            _register_voice_drop(client_name, f"connect: {e}")


async def _watchdog_loop(client_name: str):
    """Watchdog с адаптивным интервалом и dead-socket детектором."""
    client = _CLIENTS[client_name]
    while not _shutdown_event.is_set():
        interval = _watchdog_intervals.get(client_name, WATCHDOG_BASE_INTERVAL)
        await asyncio.sleep(interval)

        # Dead socket
        if _is_dead_socket(client):
            log_event(f"[{client_name}] dead socket (latency={client.latency})")
            _register_voice_drop(client_name, "dead-socket")
            log_reconnect(client_name)
            await _force_reconnect(client_name)
            continue

        # Не в войсе?
        if not _is_in_voice(client, MAIN_CHANNEL_ID):
            now = time.time()
            if now - _reconnect_guard.get(client_name, 0) < RECONNECT_GUARD_SEC:
                continue
            _reconnect_guard[client_name] = now
            _register_voice_drop(client_name, "not-in-voice")
            log_reconnect(client_name)
            await _ensure_connected(client_name)
            continue

        # В войсе — обновляем uptime и мьют
        _register_voice_ok(client_name)
        await _enforce_mute(client_name)


def _bind_client_events(name, client):
    @client.event
    async def on_ready():
        log.info("[%s] авторизован как %s", name.upper(), client.user)
        await _ensure_connected(name)
        if name not in _watchdogs_started:
            _watchdogs_started.add(name)
            client.loop.create_task(_watchdog_loop(name))
            client.loop.create_task(status_changer(client, name.upper()))

    @client.event
    async def on_disconnect():
        log.warning("[%s] on_disconnect", name.upper())
        # Даём шанс переподключиться библиотеке

    @client.event
    async def on_resumed():
        log.info("[%s] resumed", name.upper())
        await _ensure_connected(name)

    @client.event
    async def on_voice_state_update(member, before, after):
        if GUILD_ID and member.guild.id != GUILD_ID:
            return
        me = client.user
        if me is None or member.id != me.id:
            return

        # Выпал из канала
        if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
            await asyncio.sleep(2)
            await _ensure_connected(name)
            return

        # Сняли мут/деаф — вернём
        if not after.self_mute or not after.self_deaf:
            await asyncio.sleep(1)
            await _enforce_mute(name)


if TOKEN_MAIN:
    _bind_client_events("main", main_client)
if TOKEN_SECOND:
    _bind_client_events("second", second_client)
if TOKEN_THIRD:
    _bind_client_events("third", third_client)


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
                    except Exception:
                        pass
            await asyncio.sleep(61)
        await asyncio.sleep(30)


# ====================================================
# ПАНЕЛИ
# ====================================================
def _w_state(w) -> str:
    if not w:
        return "⚪ не настроен"
    if w.disabled:
        return "🔴 выкл"
    if w.manual_mode:
        return "🟢 вкл (ручной)"
    return "🟡 ожидание"


def _build_panel() -> str:
    lines = [
        "🎛 **ПАНЕЛЬ TT11TT**",
        f"⏱ Uptime: **{fmt_uptime()}**",
        f"🔻 Всего вылетов: **{total_voice_drops}**",
        "",
        "**Дежурные:**",
        f"  • Egoist: {_w_state(watcher_egoist)}",
        f"  • Egorik: {_w_state(watcher_egorik)}",
        "",
        "**24/7 боты:**",
    ]
    for n in ("main", "second", "third"):
        c = _CLIENTS[n]
        ch = _voice_channel_id(c)
        if ch == MAIN_CHANNEL_ID:
            status = f"🟢 `{ch}` (uptime {fmt_duration(_voice_uptime.get(n, 0))})"
        elif not ch:
            status = "🔴 не в голосе"
        else:
            status = f"🟡 другой канал `{ch}`"
        lines.append(f"  • {n.upper()}: {status}")
    lines += [
        "",
        f"**Сообщений:** {total_messages_sent} | **TG:** {total_telegram_sent}",
        f"**Команд:** {total_commands_handled}",
        "",
        "**Команды:** `!panel` `!ping` `!stats` `!info` `!help`",
        "`!on egoist` `!off egoist` `!on egorik` `!off egorik`",
        "`!all on` `!all off` `!write <n>`",
    ]
    return "\n".join(lines)


def _build_ping(client_list) -> str:
    lines = ["🏓 **PONG**", ""]
    for name, client in client_list:
        if client.is_ready():
            try:
                latency = client.latency * 1000
                flag = "💀" if latency > DEAD_SOCKET_LATENCY_MS else ""
                lines.append(f"• **{name}**: `{latency:.0f} ms` {flag}")
            except Exception:
                lines.append(f"• **{name}**: `n/a`")
        else:
            lines.append(f"• **{name}**: ❌ не готов")
    lines.append("")
    lines.append(f"⏱ Uptime: **{fmt_uptime()}**")
    return "\n".join(lines)


def _build_stats() -> str:
    lines = ["📊 **СТАТИСТИКА**", ""]
    lines.append("**Реконнекты:**")
    for n, v in reconnect_stats.items():
        last = last_event_time.get(n)
        upt = fmt_duration(_voice_uptime.get(n, 0))
        lines.append(f"  • {n}: `{v}` (uptime {upt}, {fmt_ago(last)})")
    lines.append("")
    lines.append(f"**Вылетов из войса:** {total_voice_drops}")
    lines.append(f"**Сообщений:** {total_messages_sent}")
    lines.append(f"**Telegram:** {total_telegram_sent}")
    lines.append(f"**Команд:** {total_commands_handled}")
    lines.append("")
    lines.append("**Последние события:**")
    for e in list(event_log)[-12:]:
        lines.append(f"  {e}")
    return "\n".join(lines)


def _build_info() -> str:
    lines = ["ℹ️ **ИНФО**", ""]
    lines.append(f"Гильдия: `{GUILD_ID or 'все'}`")
    lines.append(f"Голосовой канал: `{MAIN_CHANNEL_ID}`")
    lines.append(f"Спам-канал: `{SPAM_CHANNEL_ID}`")
    lines.append(f"Rotate status: `{STATUS_ROTATION}`")
    lines.append("")
    lines.append("**Клиенты:**")
    for name, client in [("MAIN", main_client), ("SECOND", second_client), ("THIRD", third_client)]:
        if client.user:
            lines.append(f"  • {name}: `{client.user}` ready={client.is_ready()}")
        else:
            lines.append(f"  • {name}: не авторизован")
    for w in (watcher_egoist, watcher_egorik):
        if w:
            lines.append(
                f"  • {w.name}: `{w.client.user or 'n/a'}` "
                f"manual={w.manual_mode} disabled={w.disabled}"
            )
    return "\n".join(lines)


def _build_help() -> str:
    return (
        "📖 **HELP**\n"
        "`!panel` — панель\n"
        "`!ping` — задержки\n"
        "`!stats` — статистика\n"
        "`!info` — конфигурация\n"
        "`!help` — справка\n"
        "\n"
        "`!on egoist` / `!off egoist`\n"
        "`!on egorik` / `!off egorik`\n"
        "`!all on` / `!all off`\n"
        "`!write <n>` — спам (до 100, кулдаун 15с)"
    )


# ====================================================
# КОМАНДЫ
# ====================================================
@main_client.event
async def on_message(message):
    global total_commands_handled, _last_write_at

    if main_client.user is None:
        return
    if message.author.id == main_client.user.id:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return
    if message.author.id not in {USER_ID_EGOIST, USER_ID_EGORIK, MAIN_BOT_USER_ID, main_client.user.id}:
        return

    content = message.content.strip()
    parts = content.split()
    if not parts:
        return

    total_commands_handled += 1
    low = content.lower()

    if parts[0].lower() == "!write":
        if len(parts) < 2 or not parts[1].isdigit():
            await message.channel.send("⚠️ `!write <число>` (до 100)")
            return
        count = int(parts[1])
        if count < 1 or count > MAX_WRITE_COUNT:
            await message.channel.send(f"⚠️ Диапазон: 1–{MAX_WRITE_COUNT}")
            return
        now = time.time()
        if now - _last_write_at < WRITE_COOLDOWN:
            await message.channel.send(f"⏳ Подождите {int(WRITE_COOLDOWN - (now - _last_write_at))}с")
            return
        active = None
        if watcher_egoist and watcher_egoist.manual_mode:
            active = watcher_egoist
        elif watcher_egorik and watcher_egorik.manual_mode:
            active = watcher_egorik
        if not active:
            await message.channel.send("⚠️ Дежурный не включён (`!on egoist` / `!on egorik`).")
            return
        _last_write_at = now
        await message.channel.send(f"⏳ {active.name} спамит ({count})...")
        ok = await active.spam_messages(SPAM_CHANNEL_ID, count)
        await message.channel.send(
            f"✅ {active.name} отправил {count}." if ok else "❌ Ошибка."
        )
        return

    if low in ("!status", "!panel", "!menu"):
        await message.channel.send(_build_panel())
        return
    if low == "!ping":
        clients = [("MAIN", main_client), ("SECOND", second_client), ("THIRD", third_client)]
        if watcher_egoist:
            clients.append(("Egoist", watcher_egoist.client))
        if watcher_egorik:
            clients.append(("Egorik", watcher_egorik.client))
        await message.channel.send(_build_ping(clients))
        return
    if low == "!stats":
        await message.channel.send(_build_stats())
        return
    if low == "!info":
        await message.channel.send(_build_info())
        return
    if low in ("!help", "!commands"):
        await message.channel.send(_build_help())
        return
    if low == "!all on":
        for w in (watcher_egoist, watcher_egorik):
            if w:
                w.disabled, w.manual_mode = False, True
                await w.connect(MAIN_CHANNEL_ID)
        await message.channel.send("✅ Все включены.")
        return
    if low == "!all off":
        for w in (watcher_egoist, watcher_egorik):
            if w:
                w.manual_mode, w.disabled = False, True
                await w.disconnect()
        await message.channel.send("⛔ Все выключены.")
        return
    if len(parts) >= 2:
        cmd, t = parts[0].lower(), parts[1].lower()
        w = watcher_egoist if t == "egoist" else (watcher_egorik if t == "egorik" else None)
        if w:
            if cmd == "!on":
                w.disabled, w.manual_mode = False, True
                ok, msg = await w.connect(MAIN_CHANNEL_ID)
                await message.channel.send(
                    f"✅ {t} вкл ({msg})." if ok else f"⚠️ {t}: {msg}"
                )
            elif cmd == "!off":
                w.manual_mode, w.disabled = False, True
                await w.disconnect()
                await message.channel.send(f"⚙️ {t} выключен.")


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

    watcher_egoist = Watcher(TOKEN_EGOIST, USER_ID_EGOIST, "Egoist") if TOKEN_EGOIST else None
    watcher_egorik = Watcher(TOKEN_EGORIK, USER_ID_EGORIK, "Egorik") if TOKEN_EGORIK else None

    tasks = []
    if TOKEN_MAIN:
        tasks.append(safe_start(main_client, TOKEN_MAIN, "MAIN"))
    if TOKEN_SECOND:
        tasks.append(safe_start(second_client, TOKEN_SECOND, "SECOND"))
    if TOKEN_THIRD:
        tasks.append(safe_start(third_client, TOKEN_THIRD, "THIRD"))
    if watcher_egoist:
        tasks.append(watcher_egoist.start())
    if watcher_egorik:
        tasks.append(watcher_egorik.start())
    tasks.append(uptime_pinger())
    tasks.append(timely_scheduler())
    tasks.append(_log_voice_uptime_periodic())

    log.info("Запуск всех ботов...")
    await asyncio.gather(*tasks, return_exceptions=True)


def _install_signal_handlers(loop):
    def _stop():
        log.info("Получен сигнал завершения...")
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
        loop.set_exception_handler(exception_handler)
        _install_signal_handlers(loop)
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
