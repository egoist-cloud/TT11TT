# ====================================================
# TT11TT — Бот под прикрытием (Main) + Дежурные
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
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
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

# --- ТОКЕНЫ ---
TOKEN_MAIN = get_env_str("DISCORD_TOKEN_1")     # Управляющий (под прикрытием)
TOKEN_EGOIST = get_env_str("DISCORD_TOKEN_2")   # Дежурный 1
TOKEN_EGORIK = get_env_str("DISCORD_TOKEN_3")   # Дежурный 2

# --- ID ---
MANAGER_USER_ID = get_env_int("MAIN_BOT_USER_ID", 1241012523952508978)
USER_ID_EGOIST = get_env_int("USER_ID_1")
USER_ID_EGORIK = get_env_int("USER_ID_2")

MAIN_CHANNEL_ID = get_env_int("VOICE_CHANNEL_ID")
GUILD_ID = get_env_int("GUILD_ID")

RENDER_URL = get_env_str("RENDER_URL") or "https://tt11tt.onrender.com"
TELEGRAM_TOKEN = get_env_str("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = get_env_str("TELEGRAM_CHAT_ID")
STATUS_ROTATION = get_env_bool("STATUS_ROTATION", True)

log.info("=" * 50)
log.info("Управляющий (Main) + Дежурные (Egoist, Egorik)")
log.info("Канал: %s | Гильдия: %s", MAIN_CHANNEL_ID, GUILD_ID or "все")
log.info("=" * 50)

# ====================================================
# КОНСТАНТЫ
# ====================================================
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
# INTENTS
# ====================================================
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True
intents.guilds = True
intents.voice_states = True

# ====================================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ
# ====================================================
watcher_egoist = None
watcher_egorik = None
main_client = discord.Client(intents=intents, chunk_guilds_at_startup=False)

start_time = time.time()
reconnect_stats = {"egoist": 0, "egorik": 0}
last_event_time = {"egoist": time.time(), "egorik": time.time()}

_shutdown_event = asyncio.Event()

# ====================================================
# ВЕБ-СЕРВЕР (для Render)
# ====================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"Bot is running"
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    log.info("Веб-сервер запущен на порту %s", port)
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ====================================================
# УТИЛИТЫ
# ====================================================
def log_reconnect(name: str):
    reconnect_stats[name] += 1
    log.info("🔄 %s: реконнект #%s", name, reconnect_stats[name])

def tg_escape(s: str) -> str:
    return html.escape(str(s), quote=False)

def fmt_uptime() -> str:
    u = int(time.time() - start_time)
    return f"{u // 3600}ч {(u % 3600) // 60}м {u % 60}с"

async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            async with session.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10) as resp:
                if resp.status != 200:
                    log.warning("TG: ошибка %s", resp.status)
    except Exception as e:
        log.warning("TG: ошибка отправки: %s", e)

async def auto_delete_message(msg, delay=MESSAGE_DELETE_DELAY):
    """Удаляет сообщение спустя указанное время (по умолчанию 15 минут)."""
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass

# ====================================================
# СМЕНА СТАТУСА (Прикрытие)
# ====================================================
async def status_changer(client, name):
    if not STATUS_ROTATION: return
    while not _shutdown_event.is_set():
        try:
            if not getattr(client, "is_afk", False) and client.is_ready():
                r = random.random()
                if r < 0.4:
                    game = random.choice(GAMES_POOL)
                    activity = discord.Activity(
                        type=discord.ActivityType.playing, name=game["name"],
                        application_id=game["app_id"],
                        timestamps={"start": int(time.time() * 1000)}
                    )
                elif r < 0.7:
                    activity = discord.Activity(
                        type=discord.ActivityType.watching, name=random.choice(WATCHING_STATUSES)
                    )
                else:
                    activity = discord.Activity(
                        type=discord.ActivityType.playing, name=random.choice(TEXT_STATUSES)
                    )
                await client.change_presence(activity=activity)
        except Exception as e:
            log.warning("[%s] ошибка статуса: %s", name, e)
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=3600 + random.uniform(0, 300))
        except asyncio.TimeoutError:
            pass

# ====================================================
# ДЕЖУРНЫЕ (WATCHERS)
# ====================================================
class Watcher:
    def __init__(self, token, user_id, name):
        self.token = token
        self.user_id = user_id
        self.name = name
        self.client = discord.Client(intents=intents, chunk_guilds_at_startup=False)
        self.client.is_afk = False
        self.manual_mode = False
        self.disabled = True # Изначально выключены, ждут команды !on
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
            log.info("[%s] авторизован как %s (Ожидает команды)", self.name, self.client.user)
            self._task_once("watchdog", self.watchdog())
            self._task_once("dead", self.dead_socket_watchdog())
            self._task_once("status", status_changer(self.client, self.name))

        @self.client.event
        async def on_socket_event_type(event_type):
            last_event_time[self.name.lower()] = time.time()

        @self.client.event
        async def on_voice_state_update(member, before, after):
            last_event_time[self.name.lower()] = time.time()
            if GUILD_ID and member.guild.id != GUILD_ID: return
            me = self.client.user
            if me is None: return
            
            # Если бот вылетел из канала (кик/ошибка)
            if member.id == me.id:
                if self.disabled or not self.manual_mode: return
                if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
                    log.warning("[%s] бот вылетел, возвращаю...", self.name)
                    log_reconnect(self.name.lower())
                    await asyncio.sleep(random.uniform(RECONNECT_MIN_DELAY, RECONNECT_MAX_DELAY))
                    if self.manual_mode and not self.disabled:
                        await self.connect(MAIN_CHANNEL_ID)

    async def watchdog(self):
        while not _shutdown_event.is_set():
            await asyncio.sleep(random.uniform(3, 7))
            if self.disabled or not self.manual_mode: continue
            # Проверяем, сидит ли владелец (хозяин аккаунта) в войсе
            owner_in_vc = False
            if self.user_id:
                try:
                    user = self.client.get_user(self.user_id)
                    if user and user.voice and user.voice.channel:
                        owner_in_vc = True
                except Exception: pass
            
            # Если владелец не в войсе, а бот должен быть - проверяем и заходим
            if not owner_in_vc:
                if (not self.client.voice_clients or not self.client.voice_clients[0].is_connected() or self.client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
                    log_reconnect(self.name.lower())
                    await self.connect(MAIN_CHANNEL_ID)

    async def dead_socket_watchdog(self):
        while not _shutdown_event.is_set():
            await asyncio.sleep(60)
            silence = time.time() - last_event_time.get(self.name.lower(), time.time())
            if silence > DEAD_SOCKET_CRITICAL:
                log.critical("[%s] молчание %dс — возможно, токен мёртв.", self.name, int(silence))
                continue
            if silence > DEAD_SOCKET_TIMEOUT:
                log.warning("[%s] мёртвый сокет (%dс), реконнект", self.name, int(silence))
                log_reconnect(self.name.lower())
                if self.client.voice_clients:
                    try: await self.client.voice_clients[0].disconnect(force=True)
                    except Exception: pass
                await asyncio.sleep(random.uniform(RECONNECT_MIN_DELAY, RECONNECT_MAX_DELAY))
                if self.manual_mode and not self.disabled:
                    await self.connect(MAIN_CHANNEL_ID)

    async def connect(self, channel_id):
        if self._connect_lock is None: self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            if self.disabled or channel_id == 0: return
            try:
                channel = self.client.get_channel(channel_id)
                if not channel:
                    try: channel = await self.client.fetch_channel(channel_id)
                    except Exception:
                        log.warning("[%s] канал %s не найден", self.name, channel_id)
                        return
                if (self.client.voice_clients and self.client.voice_clients[0].is_connected() and self.client.voice_clients[0].channel.id == channel_id):
                    return
                if self.client.voice_clients:
                    try: await self.client.voice_clients[0].disconnect(force=True)
                    except Exception: pass
                    await asyncio.sleep(1)
                await channel.connect(timeout=CONNECT_TIMEOUT, self_deaf=False, self_mute=False)
                log.info("[%s] подключен к %s", self.name, channel.name)
            except asyncio.TimeoutError: log.warning("[%s] таймаут подключения к %s", self.name, channel_id)
            except Exception as e: log.warning("[%s] ошибка подключения: %s", self.name, e)

    async def disconnect(self):
        if self.client.voice_clients:
            try:
                await self.client.voice_clients[0].disconnect(force=True)
                self.client.is_afk = False
            except Exception: pass

    async def start(self):
        if self._connect_lock is None: self._connect_lock = asyncio.Lock()
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
# БОТ ПОД ПРИКРЫТИЕМ (Управляющий)
# ====================================================
@main_client.event
async def on_ready():
    log.info("[MAIN] Управляющий бот запущен под прикрытием как %s", main_client.user)
    if STATUS_ROTATION:
        main_client.loop.create_task(status_changer(main_client, "MAIN"))
    await send_telegram(f"👑 <b>Управляющий (Main) запущен</b>\nАптайм: <code>{tg_escape(fmt_uptime())}</code>")

@main_client.event
async def on_message(message):
    if main_client.user is None: return
    if message.author.id == main_client.user.id: return
    if not isinstance(message.channel, discord.DMChannel): return
    
    # Проверка, что пишет именно хозяин
    if message.author.id not in (MANAGER_USER_ID, USER_ID_EGOIST, USER_ID_EGORIK): return

    content = message.content.strip().lower()

    # --- ПАНЕЛЬ ---
    if content in ("!status", "!panel", "!menu"):
        def w_state(w):
            if not w: return "❌ нет"
            if w.disabled: return "🔴 выключен"
            return "🟢 РАБОТАЕТ (в войсе)" if w.manual_mode else "⚙️ авто"

        panel_text = (
            f"🎛 **ПАНЕЛЬ УПРАВЛЕНИЯ (Дежурные)**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Egoist:** {w_state(watcher_egoist)}\n"
            f"**Egorik:** {w_state(watcher_egorik)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"`!on egoist` / `!off egoist`\n"
            f"`!on egorik` / `!off egorik`\n"
            f"`!all on` / `!all off`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Uptime: `{fmt_uptime()}`\n"
            f"*(это сообщение удалится через 15 минут)*"
        )
        msg = await message.channel.send(panel_text)
        main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    # --- ПИНГ ---
    if content == "!ping":
        msg = await message.channel.send("🏓 Пингую...")
        ws_ping = int(main_client.latency * 1000)
        await msg.edit(content=f"🏓 Пинг: `{ws_ping}ms`\n*(удалится через 15 минут)*")
        main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    # --- ALL ON ---
    if content == "!all on":
        for w in (watcher_egoist, watcher_egorik):
            if w:
                w.disabled = False
                w.manual_mode = True
                w.client.is_afk = False
                await w.connect(MAIN_CHANNEL_ID)
        msg = await message.channel.send("✅ Все дежурные включены и заходят в войс.\n*(удалится через 15 минут)*")
        main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    # --- ALL OFF ---
    if content == "!all off":
        for w in (watcher_egoist, watcher_egorik):
            if w:
                w.manual_mode = False
                w.disabled = True
                await w.disconnect()
        msg = await message.channel.send("⛔ Все дежурные выключены и вышли из войса.\n*(удалится через 15 минут)*")
        main_client.loop.create_task(auto_delete_message(msg, MESSAGE_DELETE_DELAY))
        return

    # --- ON / OFF <target> ---
    parts = content.split()
    if len(parts) >= 2:
        cmd, target_name = parts[0], parts[1]
        w = (watcher_egoist if target_name == "egoist" else watcher_egorik if target_name == "egorik" else None)
        if w:
            if cmd == "!on":
                w.disabled = False
                w.manual_mode = True
                w.client.is_afk = False
                await w.connect(MAIN_CHANNEL_ID)
                msg = await message.channel.send(f"✅ {target_name.capitalize()} включен и работает.\n*(удалится через 15 минут)*")
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
    global watcher_egoist, watcher_egorik
    log.info("Инициализация...")
    
    watcher_egoist = Watcher(TOKEN_EGOIST, USER_ID_EGOIST, "Egoist") if TOKEN_EGOIST else None
    watcher_egorik = Watcher(TOKEN_EGORIK, USER_ID_EGORIK, "Egorik") if TOKEN_EGORIK else None

    tasks = []
    
    # 1. Запускаем Main (Управляющий под прикрытием)
    if TOKEN_MAIN:
        tasks.append(main_client.start(TOKEN_MAIN))
        await asyncio.sleep(2)
    
    # 2. Запускаем Дежурных (они авторизуются, но сидят в режиме disabled=True)
    if watcher_egoist:
        tasks.append(watcher_egoist.start())
        await asyncio.sleep(2)
    if watcher_egorik:
        tasks.append(watcher_egorik.start())

    if not tasks:
        log.error("Ни один токен не задан!")
        return

    log.info("Все клиенты запущены. Ожидание команд в ЛС...")
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
