# ====================================================
# TT11TT — Discord боты (v5, Main + 4 Дежурных)
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

# --- ТОКЕНЫ ---
TOKEN_MAIN = get_env_str("DISCORD_TOKEN_1")     # Управляющий
TOKEN_EGOIST = get_env_str("DISCORD_TOKEN_2")   # Дежурный 1
TOKEN_EGORIK = get_env_str("DISCORD_TOKEN_3")   # Дежурный 2
TOKEN_EGOIST2 = get_env_str("DISCORD_TOKEN_4")  # Дежурный 3
TOKEN_EGORIK2 = get_env_str("DISCORD_TOKEN_5")  # Дежурный 4

# --- ID ---
MANAGER_USER_ID = get_env_int("MAIN_BOT_USER_ID", 1241012523952508978)
USER_ID_EGOIST = get_env_int("USER_ID_1")
USER_ID_EGORIK = get_env_int("USER_ID_2")
USER_ID_EGOIST2 = get_env_int("USER_ID_3")
USER_ID_EGORIK2 = get_env_int("USER_ID_4")

MAIN_CHANNEL_ID = get_env_int("VOICE_CHANNEL_ID")
GUILD_ID = get_env_int("GUILD_ID")

RENDER_URL = get_env_str("RENDER_URL") or "https://tt11tt.onrender.com"
TELEGRAM_TOKEN = get_env_str("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = get_env_str("TELEGRAM_CHAT_ID")
STATUS_ROTATION = get_env_bool("STATUS_ROTATION", False)

log.info("=" * 50)
log.info("Управляющий (Main) + 4 Дежурных")
log.info("Канал: %s | Гильдия: %s", MAIN_CHANNEL_ID, GUILD_ID or "все")
log.info("Статус-ротация: %s", "ON" if STATUS_ROTATION else "OFF")
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
watcher_egoist2 = None
watcher_egorik2 = None
main_client = discord.Client(chunk_guilds_at_startup=False)

start_time = time.time()

reconnect_stats = {"main": 0, "egoist": 0, "egorik": 0, "egoist2": 0, "egorik2": 0}
reconnect_history = {k: deque(maxlen=200) for k in reconnect_stats}
last_event_time = {k: time.time() for k in reconnect_stats}
total_messages_sent = 0
total_telegram_sent = 0
event_log = deque(maxlen=30)

_watchdogs_started = set()
_user_cache = {}
_dm_notifications_enabled = True

_main_lock = asyncio.Lock()

_shutdown_event = asyncio.Event()

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
                    "egoist": watcher_egoist.client.is_ready() if watcher_egoist and TOKEN_EGOIST else None,
                    "egorik": watcher_egorik.client.is_ready() if watcher_egorik and TOKEN_EGORIK else None,
                    "egoist2": watcher_egoist2.client.is_ready() if watcher_egoist2 and TOKEN_EGOIST2 else None,
                    "egorik2": watcher_egorik2.client.is_ready() if watcher_egorik2 and TOKEN_EGORIK2 else None,
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
    if not main_client or not main_client.is_ready():
        return
    try:
        user = await get_cached_user(main_client, user_id)
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
        ("main", main_client),
        ("egoist", watcher_egoist.client if watcher_egoist else None),
        ("egorik", watcher_egorik.client if watcher_egorik else None),
        ("egoist2", watcher_egoist2.client if watcher_egoist2 else None),
        ("egorik2", watcher_egorik2.client if watcher_egorik2 else None),
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
        self.disabled = True
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

    async def watchdog(self):
        while not _shutdown_event.is_set():
            await asyncio.sleep(random.uniform(3, 7))
            if self.disabled or not self.manual_mode:
                continue
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
# УПРАВЛЯЮЩИЙ (Main)
# ====================================================
@main_client.event
async def on_ready():
    log.info("[MAIN] Управляющий бот запущен под прикрытием как %s", main_client.user)
    if STATUS_ROTATION:
        main_client.loop.create_task(status_changer(main_client, "MAIN"))
    await send_telegram(f"👑 <b>Управляющий (Main) запущен</b>\nАптайм: <code>{tg_escape(fmt_uptime())}</code>")

@main_client.event
async def on_message(message):
    if main_client.user is None:
        return
    if message.author.id == main_client.user.id:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return
    
    content = message.content.strip().lower()

    if content == "!myid":
        msg = await message.channel.send(f"Твой ID: `{message.author.id}`")
        main_client.loop.create_task(auto_delete_message(msg, 60))
        return

    if message.author.id not in (MANAGER_USER_ID, USER_ID_EGOIST, USER_ID_EGORIK, USER_ID_EGOIST2, USER_ID_EGORIK2):
        log.warning(f"[IGNORE] Сообщение от неизвестного ID: {message.author.id} | Текст: {content}")
        return

    if content in ("!status", "!panel", "!menu"):
        def w_state(w):
            if not w:
                return "❌ нет"
            if w.disabled:
                return "🔴 выключен (не в войсе)"
            return "🟢 РАБОТАЕТ (в войсе)" if w.manual_mode else "⚙️ авто"

        panel_text = (
            f"🎛 **ПАНЕЛЬ УПРАВЛЕНИЯ (Дежурные)**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Egoist:** {w_state(watcher_egoist)}\n"
            f"**Egorik:** {w_state(watcher_egorik)}\n"
            f"**Egoist2:** {w_state(watcher_egoist2)}\n"
            f"**Egorik2:** {w_state(watcher_egorik2)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"`!on egoist` / `!off egoist`\n"
            f"`!on egorik` / `!off egorik`\n"
            f"`!on egoist2` / `!off egoist2`\n"
            f"`!on egorik2` / `!off egorik2`\n"
            f"`!all on` / `!all off`\n"
            f"`!ping` / `!stats` / `!chart` / `!uptime`\n"
            f"`!log` / `!who`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Uptime: `{fmt_uptime()}`\n"
            f"*(это сообщение удалится через 15 минут)*"
        )
        msg = await message.channel.send(panel_text)
        main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    if content == "!uptime":
        msg = await message.channel.send(f"Uptime: `{fmt_uptime()}`")
        main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    if content == "!ping":
        msg = await message.channel.send("🏓 Пингую...")
        ws_ping = int(main_client.latency * 1000)
        await msg.edit(content=f"🏓 Пинг: `{ws_ping}ms`\n*(удалится через 15 минут)*")
        main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    if content == "!stats":
        stats_text = (
            f"📊 **СТАТИСТИКА**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 Uptime: `{fmt_uptime()}`\n"
            f"💬 Сообщений: `{total_messages_sent}`\n"
            f"📨 Telegram: `{total_telegram_sent}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔄 Реконнекты:\n"
            f"• MAIN: `{reconnect_stats['main']}`\n"
            f"• Egoist: `{reconnect_stats['egoist']}`\n"
            f"• Egorik: `{reconnect_stats['egorik']}`\n"
            f"• Egoist2: `{reconnect_stats['egoist2']}`\n"
            f"• Egorik2: `{reconnect_stats['egorik2']}`\n"
        )
        msg = await message.channel.send(stats_text)
        main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    if content == "!log":
        logs = "\n".join(list(event_log)[-15:]) if event_log else "пусто"
        msg = await message.channel.send(f"**📜 Лог:**\n```\n{logs}\n```")
        main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    if content == "!chart":
        lines = ["**📈 Реконнекты за 24ч**\n"]
        for n in reconnect_stats:
            lines.append(f"`{n:8}` {build_reconnect_chart(n, 24)}  всего: {reconnect_stats[n]}")
        msg = await message.channel.send("\n".join(lines))
        main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    if content == "!who":
        channel = main_client.get_channel(MAIN_CHANNEL_ID)
        if not channel:
            msg = await message.channel.send("⚠️ Канал не найден.")
        else:
            members = list(getattr(channel, "members", []) or [])
            if members:
                names = [tg_escape(m.display_name) for m in members]
                msg = await message.channel.send(f"**🎙 В войсе ({len(names)}):**\n" + "\n".join(f"• {n}" for n in names))
            else:
                msg = await message.channel.send("🎙 В войсе никого.")
        main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    if content == "!all on":
        for w in (watcher_egoist, watcher_egorik, watcher_egoist2, watcher_egorik2):
            if w:
                w.disabled = False
                w.manual_mode = True
                w.client.is_afk = False
                success, err = await w.connect(MAIN_CHANNEL_ID)
                if not success:
                    await message.channel.send(f"⚠️ {w.name} не смог зайти: {err}")
        msg = await message.channel.send("✅ Команда выполнена для всех.\n*(удалится через 15 минут)*")
        main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    if content == "!all off":
        for w in (watcher_egoist, watcher_egorik, watcher_egoist2, watcher_egorik2):
            if w:
                w.manual_mode = False
                w.disabled = True
                await w.disconnect()
        msg = await message.channel.send("⛔ Все дежурные выключены и вышли из войса.\n*(удалится через 15 минут)*")
        main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    parts = content.split()
    if len(parts) >= 2:
        cmd, target_name = parts[0], parts[1]
        w = None
        if target_name == "egoist":
            w = watcher_egoist
        elif target_name == "egorik":
            w = watcher_egorik
        elif target_name == "egoist2":
            w = watcher_egoist2
        elif target_name == "egorik2":
            w = watcher_egorik2
        
        if w:
            if cmd == "!on":
                w.disabled = False
                w.manual_mode = True
                w.client.is_afk = False
                
                success, err = await w.connect(MAIN_CHANNEL_ID)
                
                if success:
                    msg = await message.channel.send(f"✅ {target_name.capitalize()} включен и работает.\n*(удалится через 15 минут)*")
                else:
                    msg = await message.channel.send(f"❌ Ошибка включения {target_name.capitalize()}:\n`{err}`")
                
                main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
                
            elif cmd == "!off":
                w.manual_mode = False
                w.disabled = True
                await w.disconnect()
                msg = await message.channel.send(f"⛔ {target_name.capitalize()} выключен и вышел из войса.\n*(удалится через 15 минут)*")
                main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))

# ====================================================
# ЗАПУСК
# ====================================================
async def main():
    global watcher_egoist, watcher_egorik, watcher_egoist2, watcher_egorik2
    log.info("Инициализация...")
    
    watcher_egoist = Watcher(TOKEN_EGOIST, USER_ID_EGOIST, "Egoist") if TOKEN_EGOIST else None
    watcher_egorik = Watcher(TOKEN_EGORIK, USER_ID_EGORIK, "Egorik") if TOKEN_EGORIK else None
    watcher_egoist2 = Watcher(TOKEN_EGOIST2, USER_ID_EGOIST2, "Egoist2") if TOKEN_EGOIST2 else None
    watcher_egorik2 = Watcher(TOKEN_EGORIK2, USER_ID_EGORIK2, "Egorik2") if TOKEN_EGORIK2 else None

    tasks = []
    
    if TOKEN_MAIN:
        tasks.append(main_client.start(TOKEN_MAIN))
        await asyncio.sleep(2)
    
    if watcher_egoist:
        tasks.append(watcher_egoist.start())
        await asyncio.sleep(2)
    if watcher_egorik:
        tasks.append(watcher_egorik.start())
        await asyncio.sleep(2)
    if watcher_egoist2:
        tasks.append(watcher_egoist2.start())
        await asyncio.sleep(2)
    if watcher_egorik2:
        tasks.append(watcher_egorik2.start())

    if not tasks:
        log.error("Ни один токен не задан!")
        return

    tasks.append(uptime_pinger())

    log.info("Все 5 клиентов запущены. Ожидание команд в ЛС...")
    await asyncio.gather(*tasks, return_exceptions=True)

def _install_signal_handlers(loop):
    def _stop():
        log.info("Остановка...")
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
        log.info("Остановлено.")
    except Exception as e:
        log.error("КРИТИЧЕСКАЯ ОШИБКА: %s", e)
        traceback.print_exc()
    finally:
        try: loop.close()
        except Exception: pass
