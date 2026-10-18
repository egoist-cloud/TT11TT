# ====================================================
# TT11TT — Discord боты (main + second + 2 наблюдателя)
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
TOKEN_MAIN = os.getenv("DISCORD_TOKEN_1")
TOKEN_SECOND = os.getenv("DISCORD_TOKEN_4")
MAIN_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "0"))

TOKEN_EGOIST = os.getenv("DISCORD_TOKEN_2")
USER_ID_EGOIST = int(os.getenv("USER_ID_1", "0"))

TOKEN_EGORIK = os.getenv("DISCORD_TOKEN_3")
USER_ID_EGORIK = int(os.getenv("USER_ID_2", "0"))

GUILD_ID = int(os.getenv("GUILD_ID", "0"))
MAIN_BOT_USER_ID = int(os.getenv("MAIN_BOT_USER_ID", "1241012523952508978"))
RENDER_URL = os.getenv("RENDER_URL", "https://tt11tt.onrender.com")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

log.info("=" * 50)
log.info("ЗАГРУЗКА ПЕРЕМЕННЫХ:")
log.info(f"  TOKEN_MAIN: {'есть' if TOKEN_MAIN else 'НЕТ'}")
log.info(f"  TOKEN_SECOND: {'есть' if TOKEN_SECOND else 'НЕТ'}")
log.info(f"  TOKEN_EGOIST: {'есть' if TOKEN_EGOIST else 'НЕТ'}")
log.info(f"  TOKEN_EGORIK: {'есть' if TOKEN_EGORIK else 'НЕТ'}")
log.info(f"  MAIN_CHANNEL_ID: {MAIN_CHANNEL_ID}")
log.info("=" * 50)

if MAIN_CHANNEL_ID == 0:
    raise ValueError("Не задан VOICE_CHANNEL_ID")

# ====================================================
# КОНСТАНТЫ
# ====================================================
STATUS_INTERVAL = 1800  # 30 минут

GAMES_POOL = [
    {"name": "Counter-Strike 2", "app_id": 730},
    {"name": "Dota 2", "app_id": 570},
    {"name": "PUBG: BATTLEGROUNDS", "app_id": 578080},
    {"name": "Apex Legends", "app_id": 1172470},
    {"name": "Grand Theft Auto V", "app_id": 271590},
    {"name": "Rust", "app_id": 252490},
]

TEXT_STATUSES = ["Commback", "TALENT 24/7", "На страже", "Сижу в войсе", "zzZ", "Работаю"]

# ====================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ====================================================
watcher_egoist = None
watcher_egorik = None
start_time = time.time()
reconnect_stats = {"main": 0, "second": 0, "egoist": 0, "egorik": 0}
reconnect_history = {k: deque(maxlen=200) for k in reconnect_stats}
event_log = deque(maxlen=30)
main_watchdogs_started = False
second_watchdogs_started = False

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
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ====================================================
# УТИЛИТЫ
# ====================================================
def log_event(text: str):
    event_log.append(f"[{time.strftime('%H:%M:%S')}] {text}")
    log.info(text)

def log_reconnect(name: str):
    reconnect_stats[name] += 1
    reconnect_history[name].append(time.time())
    log_event(f"🔄 {name}: реконнект #{reconnect_stats[name]}")

async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            await session.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        log.warning(f"[TG] Ошибка: {e}")

# ====================================================
# СМЕНА СТАТУСА
# ====================================================
async def status_changer(client, name):
    while True:
        try:
            # Если включен AFK - не трогаем статус
            if not getattr(client, 'is_afk', False):
                if random.random() < 0.5:
                    game = random.choice(GAMES_POOL)
                    activity = discord.Activity(
                        type=discord.ActivityType.playing,
                        name=game["name"],
                        application_id=game["app_id"],
                        timestamps={'start': int(time.time() * 1000)},
                    )
                else:
                    activity = discord.Activity(type=discord.ActivityType.playing, name=random.choice(TEXT_STATUSES))
                await client.change_presence(activity=activity)
        except Exception as e:
            log.warning(f"[{name}] Ошибка статуса: {e}")
        await asyncio.sleep(STATUS_INTERVAL)

# ====================================================
# ДЕЖУРНЫЙ (НАБЛЮДАТЕЛЬ)
# ====================================================
class Watcher:
    def __init__(self, token, user_id, name):
        self.token = token
        self.user_id = user_id
        self.name = name
        
        intents = discord.Intents.default()
        intents.presences = True
        intents.members = True
        
        self.client = discord.Client(intents=intents)
        self.client.is_afk = False  # Флаг AFK для смены статуса
        
        self.is_reconnecting = False
        self.manual_mode = False # По умолчанию авто
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
            if not self._status_started:
                self._status_started = True
                self.client.loop.create_task(status_changer(self.client, self.name))

        @self.client.event
        async def on_presence_update(before, after):
            if self.disabled or self.manual_mode: return
            if after.id != self.user_id: return

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

        @self.client.event
        async def on_voice_state_update(member, before, after):
            # 1. Если кикнули бота
            if member.id == self.client.user.id:
                if self.disabled or not self.manual_mode: return
                if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
                    log.warning(f"[{self.name}] Бот вылетел. Возвращаю...")
                    log_reconnect(self.name.lower())
                    await asyncio.sleep(2)
                    if self.manual_mode and not self.disabled:
                        await self.connect(MAIN_CHANNEL_ID)

            # 2. Если владелец зашел в войс (и мы в ручном режиме)
            elif member.id == self.user_id and after.channel:
                if self.manual_mode and not self.disabled:
                    if not self.client.voice_clients or self.client.voice_clients[0].channel.id != after.channel.id:
                        await self.connect(after.channel.id)
            
            # 3. Если владелец вышел из войса
            elif member.id == self.user_id and not after.channel:
                if self.manual_mode and not self.disabled:
                    if self.client.voice_clients and self.client.voice_clients[0].channel.id != MAIN_CHANNEL_ID:
                        await self.connect(MAIN_CHANNEL_ID)

    async def toggle_afk(self):
        """Переключает режим AFK (мьют + глухота)."""
        vc = self.client.voice_clients[0] if self.client.voice_clients else None
        if not vc or not vc.is_connected():
            return False, "Бот не в войсе"

        self.client.is_afk = not self.client.is_afk
        try:
            if self.client.is_afk:
                await vc.guild.change_voice_state(channel=vc.channel, self_mute=True, self_deaf=True)
                # Ставим статус AFK
                await self.client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="AFK"))
                return True, "включен"
            else:
                await vc.guild.change_voice_state(channel=vc.channel, self_mute=False, self_deaf=False)
                return True, "выключен"
        except Exception as e:
            log.warning(f"[{self.name}] Ошибка AFK: {e}")
            return False, str(e)

    async def watchdog(self):
        while True:
            await asyncio.sleep(3) # 3 сек для мгновенного возврата
            if self.disabled or not self.manual_mode: continue
            
            owner_in_vc = False
            if self.user_id:
                try:
                    user = self.client.get_user(self.user_id)
                    if user and user.voice and user.voice.channel: owner_in_vc = True
                except Exception: pass
            
            if not owner_in_vc:
                if (not self.client.voice_clients or not self.client.voice_clients[0].is_connected() or self.client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
                    log.warning(f"[{self.name}] Не в канале. Возвращаю...")
                    log_reconnect(self.name.lower())
                    await self.connect(MAIN_CHANNEL_ID)

    async def connect(self, channel_id):
        if self.is_reconnecting or self.disabled: return
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
                log.info(f"[{self.name}] Вышел из канала.")
            except Exception: pass

    async def start(self):
        while True:
            try: await self.client.start(self.token)
            except Exception as e:
                log.error(f"[{self.name}] Упал: {e}. Перезапуск через 30 сек.")
                await asyncio.sleep(30)

# ====================================================
# MAIN и SECOND БОТЫ
# ====================================================
main_client = discord.Client()
main_reconnecting = False

async def ensure_main_connected():
    global main_reconnecting
    if main_reconnecting: return
    main_reconnecting = True
    try:
        channel = main_client.get_channel(MAIN_CHANNEL_ID)
        if not channel: return
        if (main_client.voice_clients and main_client.voice_clients[0].is_connected() and main_client.voice_clients[0].channel.id == MAIN_CHANNEL_ID): return
        if main_client.voice_clients:
            try: await main_client.voice_clients[0].disconnect(force=True)
            except Exception: pass
            await asyncio.sleep(1)
        for _ in range(5):
            try:
                await channel.connect(timeout=30.0, self_deaf=False, self_mute=False)
                return
            except Exception: await asyncio.sleep(5)
    finally: main_reconnecting = False

async def main_watchdog():
    while True:
        await asyncio.sleep(3)
        if (not main_client.voice_clients or not main_client.voice_clients[0].is_connected() or main_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log_reconnect("main")
            await ensure_main_connected()

@main_client.event
async def on_ready():
    global main_watchdogs_started
    log.info(f"[MAIN] Авторизован как {main_client.user}")
    await ensure_main_connected()
    if not main_watchdogs_started:
        main_watchdogs_started = True
        main_client.loop.create_task(main_watchdog())
        main_client.loop.create_task(status_changer(main_client, "MAIN"))
    await send_telegram("✅ <b>TT11TT запущен</b> на Render.")

@main_client.event
async def on_voice_state_update(member, before, after):
    if member.id != main_client.user.id: return
    if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
        log_reconnect("main")
        await ensure_main_connected()

second_client = discord.Client()
second_reconnecting = False

async def ensure_second_connected():
    global second_reconnecting
    if second_reconnecting: return
    second_reconnecting = True
    try:
        channel = second_client.get_channel(MAIN_CHANNEL_ID)
        if not channel: return
        if (second_client.voice_clients and second_client.voice_clients[0].is_connected() and second_client.voice_clients[0].channel.id == MAIN_CHANNEL_ID): return
        if second_client.voice_clients:
            try: await second_client.voice_clients[0].disconnect(force=True)
            except Exception: pass
            await asyncio.sleep(1)
        for _ in range(5):
            try:
                await channel.connect(timeout=30.0, self_deaf=False, self_mute=False)
                return
            except Exception: await asyncio.sleep(5)
    finally: second_reconnecting = False

async def second_watchdog():
    while True:
        await asyncio.sleep(3)
        if (not second_client.voice_clients or not second_client.voice_clients[0].is_connected() or second_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log_reconnect("second")
            await ensure_second_connected()

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
async def on_voice_state_update(member, before, after):
    if member.id != second_client.user.id: return
    if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
        log_reconnect("second")
        await ensure_second_connected()

# ====================================================
# КОМАНДЫ И ПАНЕЛЬ
# ====================================================
def build_panel_text() -> str:
    def w_state(w):
        if not w: return "❌ нет"
        if w.disabled: return "🔴 выключен"
        afk = "💤 [AFK]" if getattr(w.client, 'is_afk', False) else ""
        if w.manual_mode: return f"🟢 ручной {afk}"
        return f"⚙️ авто {afk}"

    uptime = int(time.time() - start_time)
    h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60

    return (
        f"🎛 **ПАНЕЛЬ УПРАВЛЕНИЯ**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Наблюдатели:**\n"
        f"• egoist: {w_state(watcher_egoist)}\n"
        f"• egorik: {w_state(watcher_egorik)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Команды:**\n"
        f"`!on egoist` / `!off egoist`\n"
        f"`!all on` / `!all off`\n"
        f"`!afk` — уйти в AFK (мьют)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Uptime: `{h}ч {m}м {s}с`"
    )

@main_client.event
async def on_message(message):
    if message.author.id == main_client.user.id: return
    if not isinstance(message.channel, discord.DMChannel): return
    if message.author.id not in (USER_ID_EGOIST, USER_ID_EGORIK, MAIN_BOT_USER_ID): return

    content = message.content.strip().lower()

    if content == "!status":
        await message.channel.send(build_panel_text())
        return

    # --- AFK ---
    if content == "!afk":
        # Определяем, кто пишет
        target_watcher = None
        if message.author.id == USER_ID_EGOIST: target_watcher = watcher_egoist
        elif message.author.id == USER_ID_EGORIK: target_watcher = watcher_egorik
        
        if target_watcher:
            success, state = await target_watcher.toggle_afk()
            if success:
                await message.channel.send(f"💤 Режим AFK {state} для аккаунта <@{message.author.id}>.")
            else:
                await message.channel.send(f"⚠️ Ошибка: {state}")
        else:
            await message.channel.send("⚠️ Не удалось определить ваш аккаунт для AFK.")
        return

    if content == "!all on":
        for w in (watcher_egoist, watcher_egorik):
            if w:
                w.disabled = False
                w.manual_mode = True
                await w.connect(MAIN_CHANNEL_ID)
        await message.channel.send("✅ Все наблюдатели включены.")
        return
    
    if content == "!all off":
        for w in (watcher_egoist, watcher_egorik):
            if w:
                w.manual_mode = False
                w.disabled = True
                await w.disconnect()
        await message.channel.send("⛔ Все наблюдатели выключены.")
        return

    parts = content.split()
    if len(parts) < 2: return
    command, target = parts[0], parts[1]

    if target == "egoist" and watcher_egoist:
        if command == "!on":
            watcher_egoist.disabled = False
            watcher_egoist.manual_mode = True
            await watcher_egoist.connect(MAIN_CHANNEL_ID)
            await message.channel.send("✅ Бот `egoist` в ручном режиме.")
        elif command == "!off":
            watcher_egoist.manual_mode = False
            watcher_egoist.disabled = False
            await watcher_egoist.disconnect()
            await message.channel.send("⚙️ Бот `egoist` в авто-режиме.")

    elif target == "egorik" and watcher_egorik:
        if command == "!on":
            watcher_egorik.disabled = False
            watcher_egorik.manual_mode = True
            await watcher_egorik.connect(MAIN_CHANNEL_ID)
            await message.channel.send("✅ Бот `egorik` в ручном режиме.")
        elif command == "!off":
            watcher_egorik.manual_mode = False
            watcher_egorik.disabled = False
            await watcher_egorik.disconnect()
            await message.channel.send("⚙️ Бот `egorik` в авто-режиме.")

# ====================================================
# ЗАПУСК
# ====================================================
async def safe_start(client, token, name):
    while True:
        try: await client.start(token)
        except Exception as e:
            log.error(f"[{name}] Упал: {e}. Перезапуск...")
            await asyncio.sleep(30)

async def main():
    global watcher_egoist, watcher_egorik
    watcher_egoist = Watcher(TOKEN_EGOIST, USER_ID_EGOIST, "Egoist")
    watcher_egorik = Watcher(TOKEN_EGORIK, USER_ID_EGORIK, "Egorik")

    tasks = [
        safe_start(main_client, TOKEN_MAIN, "MAIN"),
        watcher_egoist.start(),
        watcher_egorik.start(),
    ]
    if TOKEN_SECOND:
        tasks.append(safe_start(second_client, TOKEN_SECOND, "SECOND"))

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try: asyncio.run(main())
    except Exception as e: log.error(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
