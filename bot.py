# ====================================================
# TT11TT — Discord боты (Финальная версия)
# ====================================================
import os
import sys
import asyncio
import logging
import random
import time
import aiohttp
import discord
from collections import deque
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
    ]
)
log = logging.getLogger(__name__)

# ====================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# ====================================================
def get_env_int(name, default=0):
    val = os.getenv(name)
    if not val: return default
    val = str(val).strip().replace('"', '').replace("'", "")
    try: return int(val)
    except ValueError:
        log.error(f"❌ ОШИБКА: Переменная {name} должна быть числом, а не '{val}'!")
        return default

def get_env_str(name):
    val = os.getenv(name)
    if not val: return None
    return val.strip().replace('"', '').replace("'", "")

log.info("=" * 50)
log.info("ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ:")

TOKEN_MAIN = get_env_str("DISCORD_TOKEN_1")
TOKEN_SECOND = get_env_str("DISCORD_TOKEN_4")
TOKEN_THIRD = get_env_str("DISCORD_TOKEN_5") # УПРАВЛЯЮЩИЙ БОТ

MAIN_CHANNEL_ID = get_env_int("VOICE_CHANNEL_ID")
GUILD_ID = get_env_int("GUILD_ID")

TOKEN_EGOIST = get_env_str("DISCORD_TOKEN_2")
USER_ID_EGOIST = get_env_int("USER_ID_1")

TOKEN_EGORIK = get_env_str("DISCORD_TOKEN_3")
USER_ID_EGORIK = get_env_int("USER_ID_2")

MAIN_BOT_USER_ID = get_env_int("MAIN_BOT_USER_ID", 1241012523952508978)
RENDER_URL = get_env_str("RENDER_URL") or "https://tt11tt.onrender.com"
TELEGRAM_TOKEN = get_env_str("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = get_env_str("TELEGRAM_CHAT_ID")

log.info(f"  MAIN_CHANNEL_ID: {MAIN_CHANNEL_ID}")
log.info(f"  GUILD_ID: {GUILD_ID}")
log.info(f"  USER_ID_EGOIST: {USER_ID_EGOIST}")
log.info(f"  USER_ID_EGORIK: {USER_ID_EGORIK}")
log.info("=" * 50)

# ====================================================
# КОНСТАНТЫ
# ====================================================
STATUS_INTERVAL = 3600
DEAD_SOCKET_TIMEOUT = 600

GAMES_POOL = [
    {"name": "Counter-Strike 2", "app_id": 730},
    {"name": "Dota 2", "app_id": 570},
    {"name": "PUBG: BATTLEGROUNDS", "app_id": 578080},
    {"name": "Apex Legends", "app_id": 1172470},
    {"name": "Grand Theft Auto V", "app_id": 271590},
    {"name": "Rust", "app_id": 252490},
]

TEXT_STATUSES = ["Commback", "TALENT 24/7", "На страже", "Сижу в войсе", "zzZ", "Работаю"]
WATCHING_STATUSES = ["как вы играете", "на ваши сообщения", "за порядком", "как падает пинг"]

# ====================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ====================================================
watcher_egoist = None
watcher_egorik = None
start_time = time.time()

reconnect_stats = {"main": 0, "second": 0, "third": 0, "egoist": 0, "egorik": 0}
reconnect_history = {k: deque(maxlen=200) for k in reconnect_stats}
last_event_time = {"main": time.time(), "second": time.time(), "third": time.time(), "egoist": time.time(), "egorik": time.time()}
total_messages_sent = 0
event_log = deque(maxlen=30)

main_watchdogs_started = False
second_watchdogs_started = False
third_watchdogs_started = False

# ====================================================
# ВЕБ-СЕРВЕР
# ====================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"Bot is running"
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, format, *args): pass

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    log.info(f"Веб-сервер запущен на порту {port}")
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

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
    log_event(f"🔄 {name}: реконнект #{reconnect_stats[name]}")

async def send_dm_notification(user_id, text):
    global total_messages_sent
    if not third_client or not third_client.is_ready(): return
    try:
        user = await third_client.fetch_user(user_id)
        if user: 
            await user.send(text)
            total_messages_sent += 1
    except Exception as e:
        log.warning(f"Не удалось отправить уведомление: {e}")

async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            async with session.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10) as resp:
                if resp.status != 200:
                    log.warning(f"[TG] Ошибка {resp.status}")
    except Exception as e:
        log.warning(f"[TG] Ошибка отправки: {e}")

async def uptime_pinger():
    await asyncio.sleep(60)
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RENDER_URL, timeout=20) as resp:
                    log.info(f"[UPTIME] Пинг {RENDER_URL} → {resp.status}")
        except Exception as e:
            log.warning(f"[UPTIME] Ошибка пинга: {e}")
        await asyncio.sleep(14 * 60)

def build_reconnect_chart(name: str, hours: int = 24) -> str:
    now = time.time()
    cutoff = now - hours * 3600
    buckets = [0] * hours
    for ts in reconnect_history[name]:
        if ts < cutoff: continue
        idx = int((ts - cutoff) / 3600)
        if 0 <= idx < hours: buckets[idx] += 1
    if max(buckets) == 0: return "▁" * hours
    chars = "▁▂▃▄▅▆▇█"
    max_val = max(buckets)
    return "".join(chars[int(b / max_val * (len(chars) - 1))] for b in buckets)

def get_latency_info() -> str:
    info = []
    pairs = [
        ("main", main_client),
        ("second", second_client if TOKEN_SECOND else None),
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

# ====================================================
# СМЕНА СТАТУСА
# ====================================================
async def status_changer(client, name):
    while True:
        try:
            if not getattr(client, 'is_afk', False):
                r = random.random()
                if r < 0.4:
                    game = random.choice(GAMES_POOL)
                    activity = discord.Activity(
                        type=discord.ActivityType.playing,
                        name=game["name"],
                        application_id=game["app_id"],
                        timestamps={'start': int(time.time() * 1000)},
                    )
                elif r < 0.7:
                    activity = discord.Activity(type=discord.ActivityType.watching, name=random.choice(WATCHING_STATUSES))
                else:
                    activity = discord.Activity(type=discord.ActivityType.playing, name=random.choice(TEXT_STATUSES))
                await client.change_presence(activity=activity)
        except Exception as e:
            log.warning(f"[{name}] Ошибка статуса: {e}")
        await asyncio.sleep(STATUS_INTERVAL)

# ====================================================
# НАБЛЮДАТЕЛИ (Дежурные)
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
        self._watchdog_started = False
        self._status_started = False
        self._register_events()

    def _register_events(self):
        @self.client.event
        async def on_ready():
            log.info(f"[{self.name}] Авторизован как {self.client.user}")
            if not self._watchdog_started:
                self._watchdog_started = True
                self.client.loop.create_task(self.watchdog())
                self.client.loop.create_task(self.dead_socket_watchdog())
            if not self._status_started:
                self._status_started = True
                self.client.loop.create_task(status_changer(self.client, self.name))

        @self.client.event
        async def on_socket_event_type(event_type):
            last_event_time[self.name.lower()] = time.time()

        @self.client.event
        async def on_presence_update(before, after):
            last_event_time[self.name.lower()] = time.time()
            if GUILD_ID != 0 and hasattr(after, 'guild') and after.guild and after.guild.id != GUILD_ID: 
                return
            if self.disabled or self.manual_mode: return
            if after.id != self.user_id: return

            try:
                if after.status == discord.Status.offline:
                    log_event(f"[{self.name}] Владелец ушёл в офлайн. Захожу через 30с.")
                    await asyncio.sleep(30)
                    if not self.disabled and not self.manual_mode:
                        user = self.client.get_user(self.user_id)
                        if user and user.status == discord.Status.offline:
                            await self.connect(MAIN_CHANNEL_ID)
                elif after.status != discord.Status.offline:
                    log_event(f"[{self.name}] Владелец вернулся. Выхожу.")
                    await self.disconnect()
            except Exception as e:
                log.warning(f"[{self.name}] Ошибка в on_presence_update: {e}")

        @self.client.event
        async def on_voice_state_update(member, before, after):
            last_event_time[self.name.lower()] = time.time()
            if GUILD_ID != 0 and member.guild.id != GUILD_ID: return

            if member.id == self.client.user.id:
                if self.disabled or not self.manual_mode: return
                if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
                    log.warning(f"[{self.name}] Бот вылетел. Возвращаю...")
                    log_reconnect(self.name.lower())
                    await asyncio.sleep(2)
                    if self.manual_mode and not self.disabled:
                        await self.connect(MAIN_CHANNEL_ID)
            elif member.id == self.user_id and after.channel:
                if self.manual_mode and not self.disabled:
                    if not self.client.voice_clients or self.client.voice_clients[0].channel.id != after.channel.id:
                        await self.connect(after.channel.id)
            elif member.id == self.user_id and not after.channel:
                if self.manual_mode and not self.disabled:
                    if self.client.voice_clients and self.client.voice_clients[0].channel.id != MAIN_CHANNEL_ID:
                        await self.connect(MAIN_CHANNEL_ID)

    async def toggle_afk(self):
        vc = self.client.voice_clients[0] if self.client.voice_clients else None
        if not vc or not vc.is_connected(): return False, "Бот не в войсе"
        self.client.is_afk = not self.client.is_afk
        try:
            if self.client.is_afk:
                await vc.guild.change_voice_state(channel=vc.channel, self_mute=True, self_deaf=True)
                await self.client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="AFK"))
                return True, "включен"
            else:
                await vc.guild.change_voice_state(channel=vc.channel, self_mute=False, self_deaf=False)
                return True, "выключен"
        except Exception as e:
            return False, str(e)

    async def watchdog(self):
        while True:
            await asyncio.sleep(3)
            if self.disabled or not self.manual_mode: continue
            owner_in_vc = False
            if self.user_id:
                try:
                    user = self.client.get_user(self.user_id)
                    if user and user.voice and user.voice.channel: owner_in_vc = True
                except Exception: pass
            if not owner_in_vc:
                if (not self.client.voice_clients or not self.client.voice_clients[0].is_connected() or self.client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
                    log_reconnect(self.name.lower())
                    await self.connect(MAIN_CHANNEL_ID)

    async def dead_socket_watchdog(self):
        while True:
            await asyncio.sleep(60)
            silence = time.time() - last_event_time.get(self.name.lower(), time.time())
            if silence > DEAD_SOCKET_TIMEOUT:
                log.warning(f"[{self.name}] 🔴 Мёртвый сокет ({int(silence)}с). Принудительный реконнект...")
                log_reconnect(self.name.lower())
                if self.client.voice_clients:
                    try: await self.client.voice_clients[0].disconnect(force=True)
                    except Exception: pass
                await asyncio.sleep(2)
                if self.manual_mode and not self.disabled:
                    await self.connect(MAIN_CHANNEL_ID)

    async def connect(self, channel_id):
        if self.is_reconnecting or self.disabled or channel_id == 0: return
        self.is_reconnecting = True
        try:
            channel = self.client.get_channel(channel_id)
            if not channel: return
            if (self.client.voice_clients and self.client.voice_clients[0].is_connected() and self.client.voice_clients[0].channel.id == channel_id): return
            if self.client.voice_clients:
                try: await self.client.voice_clients[0].disconnect(force=True)
                except Exception: pass
                await asyncio.sleep(1)
            await channel.connect(timeout=30.0, self_deaf=False, self_mute=False)
            log.info(f"[{self.name}] Подключен к {channel.name}")
        except Exception as e:
            log.warning(f"[{self.name}] Ошибка подключения: {e}")
        finally:
            self.is_reconnecting = False

    async def disconnect(self):
        if self.client.voice_clients:
            try:
                await self.client.voice_clients[0].disconnect(force=True)
                self.client.is_afk = False
            except Exception: pass

    async def start(self):
        while True:
            try:
                await self.client.start(self.token)
            except discord.HTTPException as e:
                if e.status == 429:
                    log.error(f"❌ [{self.name}] Cloudflare 429. Жду 60 секунд...")
                    await asyncio.sleep(60)
                    continue
                log.error(f"❌ [{self.name}] Ошибка токена или HTTP {e.status}. Рестарт через 30 сек.")
                await asyncio.sleep(30)
            except Exception as e:
                log.error(f"❌ [{self.name}] Ошибка: {e}. Рестарт через 30 сек.")
                await asyncio.sleep(30)

# ====================================================
# MAIN, SECOND, THIRD (Боты 24/7)
# ====================================================
main_client = discord.Client(chunk_guilds_at_startup=False)
second_client = discord.Client(chunk_guilds_at_startup=False)
third_client = discord.Client(chunk_guilds_at_startup=False)

main_reconnecting = False
second_reconnecting = False
third_reconnecting = False

async def ensure_main_connected():
    global main_reconnecting
    if main_reconnecting or MAIN_CHANNEL_ID == 0: return
    main_reconnecting = True
    try:
        channel = main_client.get_channel(MAIN_CHANNEL_ID)
        if not channel: return
        if (main_client.voice_clients and main_client.voice_clients[0].is_connected() and main_client.voice_clients[0].channel.id == MAIN_CHANNEL_ID): return
        if main_client.voice_clients:
            try: await main_client.voice_clients[0].disconnect(force=True)
            except Exception: pass
            await asyncio.sleep(1)
        await channel.connect(timeout=30.0, self_deaf=False, self_mute=False)
        log.info(f"[MAIN] Подключен к {channel.name}")
    except Exception as e:
        log.warning(f"[MAIN] Ошибка подключения: {e}")
    finally: main_reconnecting = False

async def ensure_second_connected():
    global second_reconnecting
    if second_reconnecting or MAIN_CHANNEL_ID == 0: return
    second_reconnecting = True
    try:
        channel = second_client.get_channel(MAIN_CHANNEL_ID)
        if not channel: return
        if (second_client.voice_clients and second_client.voice_clients[0].is_connected() and second_client.voice_clients[0].channel.id == MAIN_CHANNEL_ID): return
        if second_client.voice_clients:
            try: await second_client.voice_clients[0].disconnect(force=True)
            except Exception: pass
            await asyncio.sleep(1)
        await channel.connect(timeout=30.0, self_deaf=False, self_mute=False)
        log.info(f"[SECOND] Подключен к {channel.name}")
    except Exception as e:
        log.warning(f"[SECOND] Ошибка подключения: {e}")
    finally: second_reconnecting = False

async def ensure_third_connected():
    global third_reconnecting
    if third_reconnecting or MAIN_CHANNEL_ID == 0: return
    third_reconnecting = True
    try:
        channel = third_client.get_channel(MAIN_CHANNEL_ID)
        if not channel: return
        if (third_client.voice_clients and third_client.voice_clients[0].is_connected() and third_client.voice_clients[0].channel.id == MAIN_CHANNEL_ID): return
        if third_client.voice_clients:
            try: await third_client.voice_clients[0].disconnect(force=True)
            except Exception: pass
            await asyncio.sleep(1)
        await channel.connect(timeout=30.0, self_deaf=False, self_mute=False)
        log.info(f"[THIRD] Подключен к {channel.name}")
    except Exception as e:
        log.warning(f"[THIRD] Ошибка подключения: {e}")
    finally: third_reconnecting = False

async def main_watchdog():
    while True:
        await asyncio.sleep(3)
        if (not main_client.voice_clients or not main_client.voice_clients[0].is_connected() or main_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log_reconnect("main")
            await ensure_main_connected()

async def second_watchdog():
    while True:
        await asyncio.sleep(3)
        if (not second_client.voice_clients or not second_client.voice_clients[0].is_connected() or second_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log_reconnect("second")
            await ensure_second_connected()

async def third_watchdog():
    while True:
        await asyncio.sleep(3)
        if (not third_client.voice_clients or not third_client.voice_clients[0].is_connected() or third_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log_reconnect("third")
            await ensure_third_connected()

@main_client.event
async def on_ready():
    global main_watchdogs_started
    log.info(f"[MAIN] Авторизован как {main_client.user}")
    await ensure_main_connected()
    if not main_watchdogs_started:
        main_watchdogs_started = True
        main_client.loop.create_task(main_watchdog())
        main_client.loop.create_task(status_changer(main_client, "MAIN"))
        main_client.loop.create_task(uptime_pinger())
        main_client.loop.create_task(daily_report_task())

@main_client.event
async def on_socket_event_type(event_type):
    last_event_time["main"] = time.time()

@main_client.event
async def on_voice_state_update(member, before, after):
    last_event_time["main"] = time.time()
    if GUILD_ID != 0 and member.guild.id != GUILD_ID: return
    if member.id != main_client.user.id: return
    if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
        log_reconnect("main")
        await asyncio.sleep(2)
        await ensure_main_connected()

@second_client.event
async def on_ready():
    global second_watchdogs_started
    log.info(f"[SECOND] Авторизован как {second_client.user}")
    await ensure_second_connected()
    if not second_watchdogs_started:
        second_watchdogs_started = True
        second_client.loop.create_task(second_watchdog())
        second_client.loop.create_task(status_changer(second_client, "SECOND"))

@second_client.event
async def on_socket_event_type(event_type):
    last_event_time["second"] = time.time()

@second_client.event
async def on_voice_state_update(member, before, after):
    last_event_time["second"] = time.time()
    if GUILD_ID != 0 and member.guild.id != GUILD_ID: return
    if member.id != second_client.user.id: return
    if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
        log_reconnect("second")
        await ensure_second_connected()

# ====================================================
# THIRD — УПРАВЛЯЮЩИЙ БОТ
# ====================================================
@third_client.event
async def on_ready():
    global third_watchdogs_started
    log.info(f"[THIRD] Авторизован как {third_client.user} (Управляющий)")
    await ensure_third_connected()
    if not third_watchdogs_started:
        third_watchdogs_started = True
        third_client.loop.create_task(third_watchdog())
        third_client.loop.create_task(status_changer(third_client, "THIRD"))
    
    await send_dm_notification(USER_ID_EGOIST, "✅ Управляющий бот (THIRD) запущен.")
    await send_telegram("✅ <b>TT11TT (THIRD) запущен</b> на Render.")

@third_client.event
async def on_socket_event_type(event_type):
    last_event_time["third"] = time.time()

@third_client.event
async def on_voice_state_update(member, before, after):
    last_event_time["third"] = time.time()
    if GUILD_ID != 0 and member.guild.id != GUILD_ID: return
    if member.id != third_client.user.id: return
    if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
        log_reconnect("third")
        await ensure_third_connected()

@third_client.event
async def on_message(message):
    global total_messages_sent
    
    if message.author.id == third_client.user.id: return
    if not isinstance(message.channel, discord.DMChannel): return
    if message.author.id not in (USER_ID_EGOIST, USER_ID_EGORIK, MAIN_BOT_USER_ID, third_client.user.id): return

    content = message.content.strip().lower()

    # КОМАНДЫ ПАНЕЛИ (setup и help)
    if content in ("!status", "!setup", "!help", "!menu", "!panel"):
        def w_state(w):
            if not w: return "❌ нет"
            if w.disabled: return "🔴 выключен"
            afk = "💤 [AFK]" if getattr(w.client, 'is_afk', False) else ""
            return f"🟢 ручной {afk}" if w.manual_mode else f"⚙️ авто {afk}"

        third_vc = third_client.voice_clients[0] if third_client.voice_clients else None
        third_in = "🟢 в войсе" if (third_vc and third_vc.is_connected()) else "🔴 нет"
        uptime = int(time.time() - start_time)
        h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60

        panel = (
            f"🎛 **ПАНЕЛЬ УПРАВЛЕНИЯ (THIRD)**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Канал: <#{MAIN_CHANNEL_ID}>\n"
            f"• THIRD: {third_in} (рек: `{reconnect_stats['third']}`)\n\n"
            f"• egoist: {w_state(watcher_egoist)} (рек: `{reconnect_stats['egoist']}`)\n"
            f"• egorik: {w_state(watcher_egorik)} (рек: `{reconnect_stats['egorik']}`)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Команды:**\n"
            f"`!on egoist` / `!off egoist`\n"
            f"`!all on` / `!all off`\n"
            f"`!afk` — режим AFK\n"
            f"`!ping` — проверить задержку\n"
            f"`!stats` — статистика\n"
            f"`!log` `!chart` `!who` `!tgtest`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Uptime: `{h}ч {m}м {s}с`\n\n"
            f"**📡 Пинг:**\n{get_latency_info()}"
        )
        await message.channel.send(panel)
        total_messages_sent += 1
        return

    if content == "!ping":
        start_ping = time.time()
        msg = await message.channel.send("🏓 Пингую узлы...")
        total_messages_sent += 1
        
        async with aiohttp.ClientSession() as session:
            try:
                api_start = time.time()
                async with session.get("https://discord.com/api/v9/gateway", timeout=5) as resp:
                    api_ping = int((time.time() - api_start) * 1000)
                    api_status = "🟢 OK" if resp.status == 200 else f"🔴 {resp.status}"
            except Exception:
                api_ping = -1
                api_status = "🔴 Ошибка"

        async with aiohttp.ClientSession() as session:
            try:
                web_start = time.time()
                async with session.get(RENDER_URL, timeout=10) as resp:
                    web_ping = int((time.time() - web_start) * 1000)
                    web_status = "🟢 OK" if resp.status == 200 else f"🔴 {resp.status}"
            except Exception:
                web_ping = -1
                web_status = "🔴 Ошибка"

        ws_ping = int(third_client.latency * 1000)

        result_text = (
            f"🏓 **Умный пинг**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔌 WebSocket (THIRD): `{ws_ping}ms`\n"
            f"🌐 Discord API: `{api_ping}ms` {api_status}\n"
            f"🖥 Render Web: `{web_ping}ms` {web_status}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Задержка сообщения: `{int((time.time() - start_ping) * 1000)}ms`"
        )
        await msg.edit(content=result_text)
        return

    if content == "!stats":
        uptime = int(time.time() - start_time)
        h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
        
        stats_text = (
            f"📊 **СТАТИСТИКА БОТОВ**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 **Uptime:** `{h}ч {m}м {s}с`\n"
            f"💬 **Отправлено сообщений:** `{total_messages_sent}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔄 **Реконнекты (кики):**\n"
            f"• MAIN: `{reconnect_stats['main']}` раз\n"
            f"• SECOND: `{reconnect_stats['second']}` раз\n"
            f"• THIRD: `{reconnect_stats['third']}` раз\n"
            f"• Egoist: `{reconnect_stats['egoist']}` раз\n"
            f"• Egorik: `{reconnect_stats['egorik']}` раз\n"
        )
        await message.channel.send(stats_text)
        total_messages_sent += 1
        return

    if content == "!log":
        logs = "\n".join(list(event_log)[-15:]) if event_log else "Пусто"
        await message.channel.send(f"**📜 Лог:**\n```\n{logs}\n```")
        total_messages_sent += 1
        return

    if content == "!chart":
        lines = ["**📈 Реконнекты за 24ч**\n"]
        for name in ["main", "second", "third", "egoist", "egorik"]:
            lines.append(f"`{name:8}` {build_reconnect_chart(name, 24)}  всего: {reconnect_stats[name]}")
        await message.channel.send("\n".join(lines))
        total_messages_sent += 1
        return

    if content == "!who":
        channel = third_client.get_channel(MAIN_CHANNEL_ID)
        if channel:
            members = [m.display_name for m in channel.members]
            await message.channel.send(f"**🎙 В войсе ({len(members)}):**\n" + "\n".join(f"• {m}" for m in members))
            total_messages_sent += 1
        return

    if content == "!tgtest":
        await send_telegram("🧪 Тестовое сообщение из TT11TT")
        await message.channel.send("Отправлено в Telegram.")
        total_messages_sent += 1
        return

    if content == "!afk":
        target = watcher_egoist if message.author.id == USER_ID_EGOIST else (watcher_egorik if message.author.id == USER_ID_EGORIK else None)
        if target:
            success, state = await target.toggle_afk()
            msg = f"💤 Режим AFK {state}." if success else f"⚠️ Ошибка: {state}"
            await message.channel.send(msg)
            total_messages_sent += 1
        return

    if content == "!all on":
        for w in (watcher_egoist, watcher_egorik):
            if w: w.disabled = False; w.manual_mode = True; await w.connect(MAIN_CHANNEL_ID)
        await message.channel.send("✅ Все включены.")
        total_messages_sent += 1
        return
    
    if content == "!all off":
        for w in (watcher_egoist, watcher_egorik):
            if w: w.manual_mode = False; w.disabled = True; await w.disconnect()
        await message.channel.send("⛔ Все выключены.")
        total_messages_sent += 1
        return

    parts = content.split()
    if len(parts) >= 2:
        cmd, target_name = parts[0], parts[1]
        w = watcher_egoist if target_name == "egoist" else (watcher_egorik if target_name == "egorik" else None)
        if w:
            if cmd == "!on":
                w.disabled = False; w.manual_mode = True; await w.connect(MAIN_CHANNEL_ID)
                await message.channel.send(f"✅ {target_name} в ручном режиме.")
                total_messages_sent += 1
            elif cmd == "!off":
                w.manual_mode = False; w.disabled = False; await w.disconnect()
                await message.channel.send(f"⚙️ {target_name} в авто-режиме.")
                total_messages_sent += 1

# ====================================================
# ЕЖЕДНЕВНЫЙ ОТЧЕТ
# ====================================================
async def daily_report_task():
    await asyncio.sleep(24 * 3600)
    while True:
        try:
            now = time.time()
            cutoff = now - 24 * 3600
            day_reconnects = sum(1 for ts in reconnect_history["third"] if ts >= cutoff)
            uptime = int(time.time() - start_time)
            h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60

            vc = None
            if third_client and third_client.voice_clients:
                vc = third_client.voice_clients[0]
            in_vc = vc and vc.is_connected()
            mode = "в войсе" if in_vc else "нет"
            ping = int(third_client.latency * 1000) if third_client else 0

            await send_dm_notification(USER_ID_EGOIST, f"📅 **Ежедневный отчёт (THIRD)**\n🕐 Uptime: `{h}ч {m}м {s}с`\n🔄 Реконнектов за сутки: `{day_reconnects}`\n⚙️ Режим: `{mode}`\n📡 Пинг: `{ping}ms`")
            await send_telegram(f"📅 <b>Отчёт THIRD</b>\n🕐 Uptime: <code>{h}ч {m}м {s}с</code>\n🔄 Реконнектов: <code>{day_reconnects}</code>\n⚙️ Режим: <code>{mode}</code>\n📡 Пинг: <code>{ping}ms</code>")
            log_event("[REPORT] Ежедневный отчёт отправлен")
        except Exception as e:
            log.error(f"[REPORT] Ошибка: {e}")
        await asyncio.sleep(24 * 3600)

# ====================================================
# ЗАПУСК
# ====================================================
async def safe_start(client, token, name):
    while True:
        try:
            await client.start(token)
        except discord.HTTPException as e:
            if e.status == 429:
                log.error(f"❌ [{name}] Cloudflare 429. Жду 60 секунд...")
                await asyncio.sleep(60)
                continue
            log.error(f"❌ [{name}] Ошибка токена или HTTP {e.status}. Рестарт через 30 сек.")
            await asyncio.sleep(30)
        except Exception as e:
            log.error(f"❌ [{name}] Ошибка: {e}. Рестарт через 30 сек.")
            await asyncio.sleep(30)

async def main():
    global watcher_egoist, watcher_egorik

    log.info("Создание клиентов...")
    watcher_egoist = Watcher(TOKEN_EGOIST, USER_ID_EGOIST, "Egoist")
    watcher_egorik = Watcher(TOKEN_EGORIK, USER_ID_EGORIK, "Egorik")

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
    
    if TOKEN_EGOIST:
        tasks.append(watcher_egoist.start())
        await asyncio.sleep(2)
    
    if TOKEN_EGORIK:
        tasks.append(watcher_egorik.start())

    if not tasks:
        log.error("❌ Ни один токен не задан! Проверь Environment Variables.")
        return

    log.info("Запуск всех ботов...")
    await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.error(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
        traceback.print_exc()
