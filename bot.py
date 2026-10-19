# ====================================================
# TT11TT — Discord боты (v15, Полная версия, 1000+ строк)
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
import string
import traceback
import threading
import json
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta

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
    force=True
)
log = logging.getLogger(__name__)

def exception_handler(loop, context):
    """Глобальный обработчик исключений для asyncio."""
    log.error("Необработанная ошибка: %s", context.get("exception", context["message"]))
    exc = context.get("exception")
    if exc and hasattr(exc, "__traceback__"):
        log.error("".join(traceback.format_tb(exc.__traceback__)))

# ====================================================
# ЧТЕНИЕ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ====================================================
def get_env_int(name, default=0):
    """Безопасное получение целого числа из env."""
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
    """Безопасное получение строки из env."""
    val = os.getenv(name)
    if not val: 
        return None
    return val.strip().strip('"').strip("'")

def get_env_bool(name, default=False):
    """Безопасное получение булева значения из env."""
    val = get_env_str(name)
    if val is None: 
        return default
    return val.lower() in ("1", "true", "yes", "on")

# --- ТОКЕНЫ ---
TOKEN_MAIN = get_env_str("DISCORD_TOKEN_1")
TOKEN_SECOND = get_env_str("DISCORD_TOKEN_4")
TOKEN_THIRD = get_env_str("DISCORD_TOKEN_5")

# --- ID КАНАЛОВ И ГИЛЬДИИ ---
MAIN_CHANNEL_ID = get_env_int("VOICE_CHANNEL_ID")
GUILD_ID = get_env_int("GUILD_ID")

# --- ДЕЖУРНЫЕ ---
TOKEN_EGOIST = get_env_str("DISCORD_TOKEN_2")
USER_ID_EGOIST = get_env_int("USER_ID_1")

TOKEN_EGORIK = get_env_str("DISCORD_TOKEN_3")
USER_ID_EGORIK = get_env_int("USER_ID_2")

# --- ДОПОЛНИТЕЛЬНО ---
MAIN_BOT_USER_ID = get_env_int("MAIN_BOT_USER_ID", 0)
RENDER_URL = get_env_str("RENDER_URL") or "https://tt11tt-1.onrender.com"
TELEGRAM_TOKEN = get_env_str("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = get_env_str("TELEGRAM_CHAT_ID")
STATUS_ROTATION = get_env_bool("STATUS_ROTATION", False)

# ID канала для спама и /timely
SPAM_CHANNEL_ID = 1270672504582832150

log.info("=" * 50)
log.info("Конфиг: канал=%s, гильдия=%s", MAIN_CHANNEL_ID, GUILD_ID or "все")
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
# ИНИЦИАЛИЗАЦИЯ КЛИЕНТОВ DISCORD
# ====================================================
main_client = discord.Client(chunk_guilds_at_startup=False)
second_client = discord.Client(chunk_guilds_at_startup=False)
third_client = discord.Client(chunk_guilds_at_startup=False)

# ====================================================
# ВЕБ-СЕРВЕР (Для Render)
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
            payload = {"ok": True, "uptime_sec": int(time.time() - start_time)}
            self._respond(200, json.dumps(payload).encode(), "application/json")
        else:
            self._respond(200, b"Bot is running")

    def log_message(self, *args): 
        pass

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ====================================================
# УТИЛИТЫ
# ====================================================
def log_event(text: str):
    """Запись события в лог и историю."""
    ts = time.strftime("%H:%M:%S")
    event_log.append(f"[{ts}] {text}")
    log.info(text)

def log_reconnect(name: str):
    """Учет реконнекта."""
    reconnect_stats[name] += 1
    reconnect_history[name].append(time.time())
    log_event(f"реконнект {name} #{reconnect_stats[name]}")

async def get_cached_user(client, user_id):
    """Получение пользователя с кэшированием."""
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
    """Экранирование HTML для Telegram."""
    return html.escape(str(s), quote=False)

async def send_telegram(text: str):
    """Отправка сообщения в Telegram."""
    global total_telegram_sent
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: 
        return
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            async with session.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10) as resp:
                if resp.status == 200: 
                    total_telegram_sent += 1
    except Exception: 
        pass

async def uptime_pinger():
    """Пингует Render, чтобы он не засыпал."""
    await asyncio.sleep(60)
    while not _shutdown_event.is_set():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RENDER_URL, timeout=20) as resp: 
                    pass
        except Exception: 
            pass
        try: 
            await asyncio.wait_for(_shutdown_event.wait(), timeout=14 * 60)
        except asyncio.TimeoutError: 
            pass

def fmt_uptime() -> str:
    """Форматирование времени работы."""
    u = int(time.time() - start_time)
    return f"{u // 3600}ч {(u % 3600) // 60}м {u % 60}с"

# ====================================================
# СМЕНА СТАТУСА
# ====================================================
async def status_changer(client, name):
    """Меняет статус бота, если включена ротация."""
    if not STATUS_ROTATION: 
        return
    while not _shutdown_event.is_set():
        try:
            if client.is_ready():
                activity = discord.Activity(type=discord.ActivityType.playing, name=random.choice(TEXT_STATUSES))
                await client.change_presence(activity=activity)
        except Exception: 
            pass
        try: 
            await asyncio.wait_for(_shutdown_event.wait(), timeout=STATUS_INTERVAL)
        except asyncio.TimeoutError: 
            pass

# ====================================================
# ДЕЖУРНЫЕ (Egoist и Egorik)
# ====================================================
class Watcher:
    def __init__(self, token, user_id, name):
        self.token = token
        self.user_id = user_id
        self.name = name
        self.client = discord.Client(chunk_guilds_at_startup=False)
        self.manual_mode = False
        self.disabled = True
        self._connect_lock = None
        self._register_events()

    def _register_events(self):
        """Регистрация событий дежурного бота."""
        @self.client.event
        async def on_ready():
            log.info("[%s] авторизован как %s", self.name, self.client.user)
            self.client.loop.create_task(status_changer(self.client, self.name))

        @self.client.event
        async def on_voice_state_update(member, before, after):
            if GUILD_ID and member.guild.id != GUILD_ID: 
                return
            me = self.client.user
            if me is None: 
                return
            
            if member.id == me.id and self.manual_mode and not self.disabled:
                if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
                    log_reconnect(self.name.lower())
                    await asyncio.sleep(3)
                    if self.manual_mode and not self.disabled:
                        await self.connect(MAIN_CHANNEL_ID)

    async def connect(self, channel_id):
        """Подключение к голосовому каналу (без мута)."""
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
                        return False, "Канал не найден"
                
                if (self.client.voice_clients and self.client.voice_clients[0].is_connected() and self.client.voice_clients[0].channel.id == channel_id):
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
        """Отключение от канала."""
        if self.client.voice_clients:
            try: 
                await self.client.voice_clients[0].disconnect(force=True)
            except Exception: 
                pass

    async def start(self):
        """Запуск клиента с переподключением."""
        if self._connect_lock is None: 
            self._connect_lock = asyncio.Lock()
        while not _shutdown_event.is_set():
            try: 
                await self.client.start(self.token)
            except Exception: 
                await asyncio.sleep(30)

    async def spam_messages(self, channel_id, count):
        """Пишет count сообщений в указанный канал (задержка 2 сек)."""
        try:
            channel = self.client.get_channel(channel_id)
            if not channel:
                channel = await self.client.fetch_channel(channel_id)
            
            for _ in range(count):
                random_text = ''.join(random.choices(string.digits, k=6)) 
                await channel.send(random_text)
                await asyncio.sleep(2.0)
            
            log.info(f"[{self.name}] Отправил {count} сообщений в канал {channel_id}")
            return True
        except Exception as e:
            log.error(f"[{self.name}] Ошибка спама: {e}")
            return False

# ====================================================
# 24/7 БОТЫ (Main, Second, Third) - ВСЕГДА В МУТЕ
# ====================================================
_LOCKS = {"main": _main_lock, "second": _second_lock, "third": _third_lock}
_CLIENTS = {"main": main_client, "second": second_client, "third": third_client}

async def _ensure_connected(client_name):
    """Проверка и принудительное подключение 24/7 ботов (в муте)."""
    client = _CLIENTS[client_name]
    if MAIN_CHANNEL_ID == 0: 
        return
    
    if (client.voice_clients and client.voice_clients[0].is_connected() and client.voice_clients[0].channel.id == MAIN_CHANNEL_ID):
        if not client.voice_clients[0].self_mute or not client.voice_clients[0].self_deaf:
            await client.voice_clients[0].guild.change_voice_state(channel=client.voice_clients[0].channel, self_mute=True, self_deaf=True)
        return
    
    async with _LOCKS[client_name]:
        if (client.voice_clients and client.voice_clients[0].is_connected() and client.voice_clients[0].channel.id == MAIN_CHANNEL_ID): 
            return
        
        channel = client.get_channel(MAIN_CHANNEL_ID)
        if not channel:
            try: 
                channel = await client.fetch_channel(MAIN_CHANNEL_ID)
            except Exception: 
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
        except Exception as e:
            log.warning("[%s] ошибка подключения: %s", client_name.upper(), e)

async def main_watchdog():
    while not _shutdown_event.is_set():
        await asyncio.sleep(10)
        if (not main_client.voice_clients or not main_client.voice_clients[0].is_connected() or main_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log_reconnect("main")
            await _ensure_connected("main")

async def second_watchdog():
    while not _shutdown_event.is_set():
        await asyncio.sleep(10)
        if (not second_client.voice_clients or not second_client.voice_clients[0].is_connected() or second_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log_reconnect("second")
            await _ensure_connected("second")

async def third_watchdog():
    while not _shutdown_event.is_set():
        await asyncio.sleep(10)
        if (not third_client.voice_clients or not third_client.voice_clients[0].is_connected() or third_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log_reconnect("third")
            await _ensure_connected("third")

def _bind_client_events(name, client, watchdog_coro):
    @client.event
    async def on_ready():
        log.info("[%s] авторизован как %s", name.upper(), client.user)
        await _ensure_connected(name)
        if name not in _watchdogs_started:
            _watchdogs_started.add(name)
            client.loop.create_task(watchdog_coro)
            client.loop.create_task(status_changer(client, name.upper()))

    @client.event
    async def on_voice_state_update(member, before, after):
        if GUILD_ID and member.guild.id != GUILD_ID: 
            return
        me = client.user
        if me is None: 
            return
        if member.id != me.id: 
            return
        
        if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
            await asyncio.sleep(3)
            await _ensure_connected(name)
        elif not after.self_mute or not after.self_deaf:
            await asyncio.sleep(1)
            await _ensure_connected(name)

if TOKEN_MAIN: 
    _bind_client_events("main", main_client, main_watchdog())
if TOKEN_SECOND: 
    _bind_client_events("second", second_client, second_watchdog())
if TOKEN_THIRD: 
    _bind_client_events("third", third_client, third_watchdog())

# ====================================================
# РАСПИСАНИЕ (МСК) /timely
# ====================================================
async def timely_scheduler():
    """Отправка /timely по расписанию (04:05 и 04:10 МСК)."""
    MSK = timezone(timedelta(hours=3))
    
    while not _shutdown_event.is_set():
        now = datetime.now(MSK)
        
        if now.hour == 4 and now.minute == 5:
            if watcher_egoist and watcher_egoist.manual_mode and watcher_egoist.client.is_ready():
                try:
                    channel = watcher_egoist.client.get_channel(SPAM_CHANNEL_ID)
                    if channel:
                        await channel.send("/timely")
                        log.info("[Egoist] Отправил /timely по расписанию (04:05 МСК)")
                except Exception as e: 
                    log.error(f"Ошибка /timely Egoist: {e}")
            await asyncio.sleep(61)
            
        elif now.hour == 4 and now.minute == 10:
            if watcher_egorik and watcher_egorik.manual_mode and watcher_egorik.client.is_ready():
                try:
                    channel = watcher_egorik.client.get_channel(SPAM_CHANNEL_ID)
                    if channel:
                        await channel.send("/timely")
                        log.info("[Egorik] Отправил /timely по расписанию (04:10 МСК)")
                except Exception as e: 
                    log.error(f"Ошибка /timely Egorik: {e}")
            await asyncio.sleep(61)
        
        await asyncio.sleep(30)

# ====================================================
# КОМАНДЫ MAIN (ЛС)
# ====================================================
@main_client.event
async def on_message(message):
    if main_client.user is None: 
        return
    if message.author.id == main_client.user.id: 
        return
    if not isinstance(message.channel, discord.DMChannel): 
        return
    if message.author.id not in (USER_ID_EGOIST, USER_ID_EGORIK, MAIN_BOT_USER_ID, main_client.user.id): 
        return

    content = message.content.strip()
    parts = content.split()

    if parts[0].lower() == "!write":
        if len(parts) < 2 or not parts[1].isdigit():
            await message.channel.send("⚠️ Использование: `!write <число>` (например, `!write 20`)")
            return
        
        count = int(parts[1])
        if count > 100: 
            await message.channel.send("⚠️ Максимум 100 сообщений.")
            return
        
        active_watcher = None
        if watcher_egoist and watcher_egoist.manual_mode: 
            active_watcher = watcher_egoist
        elif watcher_egorik and watcher_egorik.manual_mode: 
            active_watcher = watcher_egorik
        
        if not active_watcher:
            await message.channel.send("⚠️ Ни один дежурный не включен (`!on egoist` или `!on egorik`).")
            return
            
        await message.channel.send(f"⏳ {active_watcher.name} начинает спам ({count} сообщений)...")
        success = await active_watcher.spam_messages(SPAM_CHANNEL_ID, count)
        if success:
            await message.channel.send(f"✅ {active_watcher.name} отправил {count} сообщений.")
        else:
            await message.channel.send(f"❌ Ошибка при отправке сообщений.")
        return

    content = content.lower()
    if content in ("!status", "!panel", "!menu"):
        def w_state(w):
            if not w: 
                return "❌ нет"
            return "🟢 в войсе" if w.manual_mode else "🔴 выкл"
        
        panel = (
            f"🎛 **ПАНЕЛЬ УПРАВЛЕНИЯ**\n"
            f"Egoist: {w_state(watcher_egoist)}\n"
            f"Egorik: {w_state(watcher_egorik)}\n"
            f"`!on egoist` / `!off egoist`\n"
            f"`!write 20` - спам сообщениями\n"
            f"`!all on` / `!all off`"
        )
        await message.channel.send(panel)
        return

    if content == "!all on":
        for w in (watcher_egoist, watcher_egorik):
            if w:
                w.disabled = False
                w.manual_mode = True
                await w.connect(MAIN_CHANNEL_ID)
        await message.channel.send("✅ Все включены.")
        return

    if content == "!all off":
        for w in (watcher_egoist, watcher_egorik):
            if w:
                w.manual_mode = False
                w.disabled = True
                await w.disconnect()
        await message.channel.send("⛔ Все выключены.")
        return

    if len(parts) >= 2:
        cmd, target_name = parts[0].lower(), parts[1].lower()
        w = (watcher_egoist if target_name == "egoist" else watcher_egorik if target_name == "egorik" else None)
        if w:
            if cmd == "!on":
                w.disabled = False
                w.manual_mode = True
                await w.connect(MAIN_CHANNEL_ID)
                await message.channel.send(f"✅ {target_name} в ручном режиме.")
            elif cmd == "!off":
                w.manual_mode = False
                w.disabled = True
                await w.disconnect()
                await message.channel.send(f"⚙️ {target_name} выключен.")

# ====================================================
# БЕЗОПАСНЫЙ ЗАПУСК
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

# ====================================================
# ТОЧКА ВХОДА
# ====================================================
async def main():
    global watcher_egoist, watcher_egorik
    log.info("Инициализация...")
    
    watcher_egoist = Watcher(TOKEN_EGOIST, USER_ID_EGOIST, "Egoist") if TOKEN_EGOIST else None
    watcher_egorik = Watcher(TOKEN_EGORIK, USER_ID_EGORIK, "Egorik") if TOKEN_EGORIK else None

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
        return

    tasks.append(uptime_pinger())
    tasks.append(timely_scheduler())

    log.info("Запуск всех ботов...")
    await asyncio.gather(*tasks, return_exceptions=True)

def _install_signal_handlers(loop):
    def _stop(): 
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
        pass
