# ====================================================
# TT11TT — Discord боты (v51 - Фильтр по пользователям + каналу)
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
    """Хендлеры логов. Файл может быть недоступен (read-only FS) - тогда
    не падаем, а пишем только в stdout: иначе бот вообще не стартует."""
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

def get_env_id_list(name):
    raw = get_env_str(name)
    if not raw: return []
    out = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part: continue
        try: out.append(int(part))
        except ValueError:
            log.error("ОШИБКА: переменная %s содержит не-число '%s'!", name, part)
    return out

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

# ====================================================
# ДОСТУП К КОМАНДАМ
# ====================================================
# Кому разрешено управлять ботами
ALLOWED_COMMAND_USERS = {1113899288192356402, 1416125087324831815}

# Где разрешено принимать команды
# Только ЛС ИЛИ канал 1241013075151425650
COMMAND_ALLOWED_CHANNEL_IDS = {1241013075151425650}

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
    if not RENDER_URL:
        log.warning("RENDER_URL не задан - keepalive-пинг выключен")
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

def _expected_clients():
    out = []
    for name, client in _CLIENTS.items():
        if _get_token(name):
            out.append((name, client))
    for w in (watcher_egoist, watcher_egorik):
        if w is not None and w.token:
            out.append((w.key, w.client))
    return out

def _is_watcher_client(client) -> bool:
    for w in (watcher_egoist, watcher_egorik):
        if w is not None and w.client is client:
            return True
    return False

# ====================================================
# ДОСТУП К КОМАНДАМ
# ====================================================
def _is_allowed_user(message) -> bool:
    author_id = getattr(message.author, "id", None)
    return author_id in ALLOWED_COMMAND_USERS

def _is_command_channel(message) -> bool:
    if getattr(message, "guild", None) is None:
        return True
    if isinstance(message.channel, (discord.DMChannel, discord.PartialMessageable)):
        return True
    cid = getattr(message.channel, "id", None)
    return cid in COMMAND_ALLOWED_CHANNEL_IDS

def _should_defer_to_watcher(bound_client) -> bool:
    if _is_watcher_client(bound_client):
        return False
    for w in (watcher_egoist, watcher_egorik):
        if w is not None and w.client.is_ready():
            return True
    return False

# ====================================================
# ОБРАБОТЧИК КОМАНД
# ====================================================
_HANDLED_MSG_MAX = 2000
_handled_msg_ids = set()
_handled_msg_order = deque()

def _mark_message_handled(message_id: int) -> bool:
    if message_id in _handled_msg_ids:
        return True
    if len(_handled_msg_order) >= _HANDLED_MSG_MAX:
        old = _handled_msg_order.popleft()
        _handled_msg_ids.discard(old)
    _handled_msg_order.append(message_id)
    _handled_msg_ids.add(message_id)
    return False

def _make_on_message(bound_client):
    async def on_message(message):
        try:
            await _handle_message(bound_client, message)
        except discord.HTTPException as e:
            log.error("[%s] HTTP-ошибка при ответе на команду: %s", getattr(bound_client, "user", "?"), e)
        except Exception as e:
            log.error("[%s] Ошибка при обработке команды %r: %s", getattr(bound_client, "user", "?"),
                      getattr(message, "content", "")[:80], e, exc_info=True)
    return on_message

async def _set_watcher_enabled(w, enabled: bool) -> tuple:
    if enabled:
        if not MAIN_CHANNEL_ID:
            return False, "не задан `VOICE_CHANNEL_ID` - некуда подключаться"
        if not w.client.is_ready():
            return False, "ещё не авторизован, попробуй через пару секунд"
        w.disabled = False
        w.manual_mode = True
        ok, msg = await w.connect(MAIN_CHANNEL_ID)
        if ok:
            return True, msg
        w.manual_mode = False
        w.disabled = True
        return False, msg
    w.manual_mode = False
    w.disabled = True
    await w.disconnect()
    return True, "выключен"

async def _handle_message(bound_client, message):
    global total_commands_handled, _last_write_at, reserve_manual_enabled

    if bound_client.user is None:
        return
    if message.author.id == bound_client.user.id:
        return
    if not _is_allowed_user(message):
        return

    content = (message.content or "").strip()
    if not content:
        return
    parts = content.split()
    if not parts:
        return
    if not parts[0].startswith("!"):
        return
    if not _is_command_channel(message):
        return
    if _should_defer_to_watcher(bound_client):
        return
    if _mark_message_handled(message.id):
        return

    total_commands_handled += 1
    low = content.lower()

    # --- УПРАВЛЕНИЕ ДЕЖУРНЫМИ ---
    if len(parts) >= 2:
        cmd, t = parts[0].lower(), parts[1].lower()
        if t in ("egoist", "egorik") and cmd in ("!on", "!off", "!вкл", "!выкл"):
            w = watcher_egoist if t == "egoist" else watcher_egorik
            if not w:
                await message.channel.send(f"⚠️ Дежурный `{t}` не настроен (нет токена DISCORD_TOKEN_1/2).")
                return
            if cmd in ("!on", "!вкл"):
                ok, msg = await _set_watcher_enabled(w, True)
                if ok:
                    await message.channel.send(f"✅ {t} вкл ({msg}).")
                else:
                    await message.channel.send(f"❌ Ошибка при подключении {t}: {msg}")
            else:
                ok, msg = await _set_watcher_enabled(w, False)
                if ok:
                    await message.channel.send(f"⚙️ {t} {msg}.")
                else:
                    await message.channel.send(f"❌ Ошибка при отключении {t}: {msg}")
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
        results = []
        for w in (watcher_egoist, watcher_egorik):
            if w is None:
                results.append("• нет токена")
                continue
            ok, msg = await _set_watcher_enabled(w, True)
            results.append(f"• {w.key}: {'✅ ' + msg if ok else '❌ ' + msg}")
        await message.channel.send("✅ Все включены.\n" + "\n".join(results)); return
    if low == "!all off":
        results = []
        for w in (watcher_egoist, watcher_egorik):
            if w is None:
                results.append("• нет токена")
                continue
            ok, msg = await _set_watcher_enabled(w, False)
            results.append(f"• {w.key}: {'✅ ' + msg if ok else '❌ ' + msg}")
        await message.channel.send("⛔ Все выключены.\n" + "\n".join(results)); return

# ====================================================
# ЗАПУСК
# ====================================================
async def safe_start(client, token, name):
    delay = 30
    while not _shutdown_event.is_set():
        try:
            await client.start(token)
            log.warning("[%s] клиент закрылся, переподключение через %dс", name, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 600)
        except discord.LoginFailure as e:
            log.error("[%s] НЕВЕРНЫЙ ТОКЕН (%s). Ждём 300с перед повтором.", name, e)
            await asyncio.sleep(300)
            delay = 300
        except Exception as e:
            log.error("[%s] ошибка: %s, рестарт через %dс", name, e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 600)

async def main():
    global watcher_egoist, watcher_egorik
    log.info("Инициализация...")
    log.info("-" * 50)
    log.info("VOICE_CHANNEL_ID = %s", MAIN_CHANNEL_ID or "НЕ ЗАДАН (боты не зайдут в войс!)")
    log.info("GUILD_ID         = %s", GUILD_ID or "НЕ ЗАДАН")
    log.info("SPAM_CHANNEL_ID  = %s", SPAM_CHANNEL_ID)
    log.info("Токены: 1=%s 2=%s 3=%s 4=%s 5=%s 6=%s",
             bool(TOKEN_EGOIST), bool(TOKEN_EGORIK), bool(TOKEN_MAIN),
             bool(TOKEN_SECOND), bool(TOKEN_THIRD), bool(TOKEN_RESERVE))
    log.info("-" * 50)

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
    tasks.append(_heartbeat_loop())

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
