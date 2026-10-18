# ====================================================
# TT11TT — Discord боты
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
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ====================================================
# БЕЗОПАСНАЯ ЗАГРУЗКА ПЕРЕМЕННЫХ
# ====================================================
def get_env_int(name, default=0):
    val = os.getenv(name)
    if not val: return default
    val = str(val).strip().replace('"', '').replace("'", "")
    try:
        return int(val)
    except ValueError:
        log.error(f"❌ ОШИБКА: Переменная {name} должна быть числом, а не '{val}'!")
        return default

def get_env_str(name):
    val = os.getenv(name)
    return val.strip() if val else None

log.info("=" * 50)
log.info("ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ:")

TOKEN_MAIN = get_env_str("DISCORD_TOKEN_1")
TOKEN_SECOND = get_env_str("DISCORD_TOKEN_4")
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
log.info(f"  GUILD_ID (Только этот сервер): {GUILD_ID}")
log.info(f"  USER_ID_EGOIST: {USER_ID_EGOIST}")
log.info(f"  USER_ID_EGORIK: {USER_ID_EGORIK}")
log.info("=" * 50)

# ====================================================
# КОНСТАНТЫ
# ====================================================
STATUS_INTERVAL = 1800

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

# ====================================================
# ВЕБ-СЕРВЕР (Для Render Health Check)
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
    log.info(f"Запуск веб-сервера на порту {port}...")
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

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
# НАБЛЮДАТЕЛИ (Selfbot)
# ====================================================
class Watcher:
    def __init__(self, token, user_id, name):
        self.token = token
        self.user_id = user_id
        self.name = name
        
        # Обычный клиент без лишних параметров, чтобы не было ошибок
        self.client = discord.Client()
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
            log.info(f"[{self.name}] Успешно авторизован как {self.client.user}")
            if not self._watchdog_started:
                self._watchdog_started = True
                self.client.loop.create_task(self.watchdog())
            if not self._status_started:
                self._status_started = True
                self.client.loop.create_task(status_changer(self.client, self.name))

        @self.client.event
        async def on_presence_update(before, after):
            # Фильтруем события только по нашему серверу (если GUILD_ID задан)
            if GUILD_ID != 0 and after.guild and after.guild.id != GUILD_ID:
                return
                
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
            # Фильтруем события только по нашему серверу
            if GUILD_ID != 0 and member.guild.id != GUILD_ID:
                return

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
        try:
            await self.client.start(self.token)
        except Exception as e:
            log.error(f"❌ [{self.name}] Ошибка запуска (возможно, неверный токен): {e}")

# ====================================================
# MAIN и SECOND (Боты)
# ====================================================
main_client = discord.Client()
main_reconnecting = False

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

async def main_watchdog():
    while True:
        await asyncio.sleep(3)
        if (not main_client.voice_clients or not main_client.voice_clients[0].is_connected() or main_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log_reconnect("main")
            await ensure_main_connected()

@main_client.event
async def on_ready():
    log.info(f"[MAIN] Успешно авторизован как {main_client.user}")
    await ensure_main_connected()
    main_client.loop.create_task(main_watchdog())
    main_client.loop.create_task(status_changer(main_client, "MAIN"))
    await send_telegram("✅ <b>TT11TT запущен</b> на Render.")

@main_client.event
async def on_voice_state_update(member, before, after):
    if GUILD_ID != 0 and member.guild.id != GUILD_ID:
        return
    if member.id != main_client.user.id: return
    if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
        log_reconnect("main")
        await ensure_main_connected()

second_client = discord.Client()
second_reconnecting = False

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

async def second_watchdog():
    while True:
        await asyncio.sleep(3)
        if (not second_client.voice_clients or not second_client.voice_clients[0].is_connected() or second_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log_reconnect("second")
            await ensure_second_connected()

@second_client.event
async def on_ready():
    log.info(f"[SECOND] Успешно авторизован как {second_client.user}")
    await ensure_second_connected()
    second_client.loop.create_task(second_watchdog())
    second_client.loop.create_task(status_changer(second_client, "SECOND"))

@second_client.event
async def on_voice_state_update(member, before, after):
    if GUILD_ID != 0 and member.guild.id != GUILD_ID:
        return
    if member.id != second_client.user.id: return
    if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
        log_reconnect("second")
        await ensure_second_connected()

# ====================================================
# КОМАНДЫ
# ====================================================
def build_panel_text() -> str:
    def w_state(w):
        if not w: return "❌ нет"
        if w.disabled: return "🔴 выключен"
        afk = "💤 [AFK]" if getattr(w.client, 'is_afk', False) else ""
        return f"🟢 ручной {afk}" if w.manual_mode else f"⚙️ авто {afk}"

    uptime = int(time.time() - start_time)
    h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60

    return (
        f"🎛 **ПАНЕЛЬ УПРАВЛЕНИЯ**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• egoist: {w_state(watcher_egoist)}\n"
        f"• egorik: {w_state(watcher_egorik)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"`!on egoist` / `!off egoist`\n"
        f"`!all on` / `!all off`\n"
        f"`!afk` — режим AFK\n"
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

    if content == "!afk":
        target = watcher_egoist if message.author.id == USER_ID_EGOIST else (watcher_egorik if message.author.id == USER_ID_EGORIK else None)
        if target:
            success, state = await target.toggle_afk()
            msg = f"💤 Режим AFK {state}." if success else f"⚠️ Ошибка: {state}"
            await message.channel.send(msg)
        return

    if content == "!all on":
        for w in (watcher_egoist, watcher_egorik):
            if w: w.disabled = False; w.manual_mode = True; await w.connect(MAIN_CHANNEL_ID)
        await message.channel.send("✅ Все включены.")
        return
    
    if content == "!all off":
        for w in (watcher_egoist, watcher_egorik):
            if w: w.manual_mode = False; w.disabled = True; await w.disconnect()
        await message.channel.send("⛔ Все выключены.")
        return

    parts = content.split()
    if len(parts) >= 2:
        cmd, target_name = parts[0], parts[1]
        w = watcher_egoist if target_name == "egoist" else (watcher_egorik if target_name == "egorik" else None)
        if w:
            if cmd == "!on":
                w.disabled = False; w.manual_mode = True; await w.connect(MAIN_CHANNEL_ID)
                await message.channel.send(f"✅ {target_name} в ручном режиме.")
            elif cmd == "!off":
                w.manual_mode = False; w.disabled = False; await w.disconnect()
                await message.channel.send(f"⚙️ {target_name} в авто-режиме.")

# ====================================================
# ЗАПУСК
# ====================================================
async def main():
    global watcher_egoist, watcher_egorik

    log.info("Создание клиентов...")
    watcher_egoist = Watcher(TOKEN_EGOIST, USER_ID_EGOIST, "Egoist")
    watcher_egorik = Watcher(TOKEN_EGORIK, USER_ID_EGORIK, "Egorik")

    tasks = []
    
    if TOKEN_MAIN: tasks.append(main_client.start(TOKEN_MAIN))
    if TOKEN_EGOIST: tasks.append(watcher_egoist.start())
    if TOKEN_EGORIK: tasks.append(watcher_egorik.start())
    if TOKEN_SECOND: tasks.append(second_client.start(TOKEN_SECOND))

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
