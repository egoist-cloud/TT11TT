import os
import sys
import asyncio
import logging
import random
import time
import aiohttp
import discord
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import traceback

# ====================================================
# ЛОГИРОВАНИЕ В ФАЙЛ
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
# ЧТЕНИЕ ПЕРЕМЕННЫХ
# ====================================================
TOKEN_MAIN = os.getenv("DISCORD_TOKEN_1")
TOKEN_SECOND = os.getenv("DISCORD_TOKEN_4")

MAIN_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "0"))

TOKEN_EGOIST = os.getenv("DISCORD_TOKEN_2")
USER_ID_EGOIST = int(os.getenv("USER_ID_1"))

TOKEN_EGORIK = os.getenv("DISCORD_TOKEN_3")
USER_ID_EGORIK = int(os.getenv("USER_ID_2"))

GUILD_ID = int(os.getenv("GUILD_ID"))
MAIN_BOT_USER_ID = int(os.getenv("MAIN_BOT_USER_ID", "1241012523952508978"))
RENDER_URL = os.getenv("RENDER_URL", "https://tt11tt.onrender.com")

log.info("=" * 50)
log.info("ЗАГРУЗКА ПЕРЕМЕННЫХ:")
log.info(f"  TOKEN_MAIN: {'есть' if TOKEN_MAIN else 'НЕТ'}")
log.info(f"  TOKEN_SECOND: {'есть' if TOKEN_SECOND else 'НЕТ'}")
log.info(f"  TOKEN_EGOIST: {'есть' if TOKEN_EGOIST else 'НЕТ'}")
log.info(f"  TOKEN_EGORIK: {'есть' if TOKEN_EGORIK else 'НЕТ'}")
log.info(f"  MAIN_CHANNEL_ID: {MAIN_CHANNEL_ID}")
log.info(f"  RENDER_URL: {RENDER_URL}")
log.info("=" * 50)

if not MAIN_CHANNEL_ID:
    raise ValueError("Не задан VOICE_CHANNEL_ID")

# ====================================================
# ВЕБ-СЕРВЕР
# ====================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info(f"Веб-сервер запущен на порту {port}")
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ====================================================
# ОБЩИЕ ПЕРЕМЕННЫЕ
# ====================================================
watcher_egoist = None
watcher_egorik = None
start_time = time.time()
reconnect_stats = {"main": 0, "second": 0, "egoist": 0, "egorik": 0}

# ====================================================
# УТИЛИТЫ
# ====================================================
async def send_dm_notification(user_id, text):
    try:
        user = await main_client.fetch_user(user_id)
        if user:
            await user.send(text)
    except Exception as e:
        log.warning(f"Не удалось отправить уведомление: {e}")

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

async def status_changer(client, name):
    statuses = [
        "Commback", "TALENT 24/7", "На страже",
        "Сижу в войсе", "zzZ", "Работаю",
    ]
    while True:
        try:
            await client.change_presence(
                activity=discord.Game(name=random.choice(statuses))
            )
        except Exception as e:
            log.warning(f"[{name}] Ошибка смены статуса: {e}")
        await asyncio.sleep(3600)

# ====================================================
# ОСНОВНОЙ БОТ (commback)
# ====================================================
main_client = discord.Client()
main_reconnecting = False

async def ensure_main_connected():
    global main_reconnecting
    if main_reconnecting:
        return
    main_reconnecting = True
    try:
        channel = main_client.get_channel(MAIN_CHANNEL_ID)
        if not channel:
            log.error(f"[MAIN] Канал {MAIN_CHANNEL_ID} не найден.")
            return
        if (main_client.voice_clients
                and main_client.voice_clients[0].is_connected()
                and main_client.voice_clients[0].channel.id == MAIN_CHANNEL_ID):
            return
        if main_client.voice_clients:
            try:
                await main_client.voice_clients[0].disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(1)
        for attempt in range(5):
            try:
                await channel.connect(timeout=30.0)
                log.info(f"[MAIN] Подключен к {channel.name}")
                return
            except Exception as e:
                log.warning(f"[MAIN] Попытка {attempt + 1}: {type(e).__name__}: {e}")
                await asyncio.sleep(5)
    except Exception as e:
        log.error(f"[MAIN] Ошибка: {e}")
    finally:
        main_reconnecting = False

async def main_watchdog():
    while True:
        await asyncio.sleep(10)
        if (not main_client.voice_clients
                or not main_client.voice_clients[0].is_connected()
                or main_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log.warning("[MAIN] Бот не в канале. Возвращаю...")
            reconnect_stats["main"] += 1
            await ensure_main_connected()

@main_client.event
async def on_ready():
    log.info(f"[MAIN] Авторизован как {main_client.user}")
    await ensure_main_connected()
    main_client.loop.create_task(main_watchdog())
    main_client.loop.create_task(status_changer(main_client, "MAIN"))
    main_client.loop.create_task(uptime_pinger())
    await send_dm_notification(USER_ID_EGOIST, "✅ Бот запущен и работает.")

@main_client.event
async def on_voice_state_update(member, before, after):
    if member.id != main_client.user.id:
        return
    if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
        log.warning("[MAIN] Бот вылетел. Возвращаю...")
        reconnect_stats["main"] += 1
        await send_dm_notification(USER_ID_EGOIST, "⚠️ Бот `commback` вылетел. Переподключаюсь...")
        await asyncio.sleep(2)
        await ensure_main_connected()

@main_client.event
async def on_message(message):
    if message.author.id == main_client.user.id:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return
    if message.author.id not in (USER_ID_EGOIST, USER_ID_EGORIK):
        return

    content = message.content.strip().lower()
    parts = content.split()

    if content == "!ping":
        await message.channel.send("pong 🏓")
        return

    if content == "!status":
        uptime = int(time.time() - start_time)
        h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
        vc = main_client.voice_clients[0] if main_client.voice_clients else None
        in_channel = vc.channel.name if vc and vc.is_connected() else "нет"
        await message.channel.send(
            f"**Статус ботов**\n"
            f"Uptime: {h}ч {m}м {s}с\n"
            f"MAIN в канале: {in_channel}\n"
            f"Переподключений: main={reconnect_stats['main']}, "
            f"second={reconnect_stats['second']}, "
            f"egoist={reconnect_stats['egoist']}, "
            f"egorik={reconnect_stats['egorik']}"
        )
        return

    if len(parts) < 2:
        await message.channel.send(
            "Команды: `!ping`, `!status`, `!on egoist`, `!off egoist`, `!on egorik`, `!off egorik`"
        )
        return

    command = parts[0]
    target = parts[1]

    if target == "egoist" and watcher_egoist:
        if command == "!on":
            watcher_egoist.manual_mode = True
            await watcher_egoist.connect(MAIN_CHANNEL_ID)
            await message.channel.send("✅ Бот `egoist` включён.")
        elif command == "!off":
            watcher_egoist.manual_mode = False
            await watcher_egoist.disconnect()
            await message.channel.send("⛔ Бот `egoist` выключен.")

    elif target == "egorik" and watcher_egorik:
        if command == "!on":
            watcher_egorik.manual_mode = True
            await watcher_egorik.connect(MAIN_CHANNEL_ID)
            await message.channel.send("✅ Бот `egorik` включён.")
        elif command == "!off":
            watcher_egorik.manual_mode = False
            await watcher_egorik.disconnect()
            await message.channel.send("⛔ Бот `egorik` выключен.")
    else:
        await message.channel.send("Укажите `egoist` или `egorik`.")

# ====================================================
# ВТОРОЙ БОТ (DISCORD_TOKEN_4)
# ====================================================
second_client = discord.Client()
second_reconnecting = False

async def ensure_second_connected():
    global second_reconnecting
    if second_reconnecting:
        return
    second_reconnecting = True
    try:
        channel = second_client.get_channel(MAIN_CHANNEL_ID)
        if not channel:
            return
        if (second_client.voice_clients
                and second_client.voice_clients[0].is_connected()
                and second_client.voice_clients[0].channel.id == MAIN_CHANNEL_ID):
            return
        if second_client.voice_clients:
            try:
                await second_client.voice_clients[0].disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(1)
        for attempt in range(5):
            try:
                await channel.connect(timeout=30.0)
                log.info(f"[SECOND] Подключен к {channel.name}")
                return
            except Exception as e:
                log.warning(f"[SECOND] Попытка {attempt + 1}: {type(e).__name__}: {e}")
                await asyncio.sleep(5)
    finally:
        second_reconnecting = False

async def second_watchdog():
    while True:
        await asyncio.sleep(10)
        if (not second_client.voice_clients
                or not second_client.voice_clients[0].is_connected()
                or second_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log.warning("[SECOND] Бот не в канале. Возвращаю...")
            reconnect_stats["second"] += 1
            await ensure_second_connected()

@second_client.event
async def on_ready():
    log.info(f"[SECOND] Авторизован как {second_client.user}")
    await ensure_second_connected()
    second_client.loop.create_task(second_watchdog())
    second_client.loop.create_task(status_changer(second_client, "SECOND"))

@second_client.event
async def on_voice_state_update(member, before, after):
    if member.id != second_client.user.id:
        return
    if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
        log.warning("[SECOND] Бот вылетел. Возвращаю...")
        reconnect_stats["second"] += 1
        await asyncio.sleep(2)
        await ensure_second_connected()

# ====================================================
# ДЕЖУРНЫЙ АККАУНТ
# ====================================================
class Watcher:
    def __init__(self, token, user_id, name):
        self.token = token
        self.user_id = user_id
        self.name = name
        self.client = discord.Client()
        self.is_reconnecting = False
        self.manual_mode = False
        self._register_events()

    def _register_events(self):
        @self.client.event
        async def on_ready():
            log.info(f"[{self.name}] Авторизован как {self.client.user}")
            self.client.loop.create_task(self.watchdog())

        @self.client.event
        async def on_presence_update(before, after):
            if self.manual_mode:
                return
            if after.id != self.user_id:
                return
            if after.status == discord.Status.offline:
                log.info(f"[{self.name}] {after} ушёл в офлайн. Захожу через 30 сек.")
                await asyncio.sleep(30)
                await self.connect(MAIN_CHANNEL_ID)
            elif after.status != discord.Status.offline:
                log.info(f"[{self.name}] {after} вернулся. Выхожу.")
                await self.disconnect()

        @self.client.event
        async def on_voice_state_update(member, before, after):
            if not self.manual_mode:
                return
            if member.id != self.client.user.id:
                return
            if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
                log.warning(f"[{self.name}] Бот вылетел. Возвращаю...")
                reconnect_stats[self.name.lower()] += 1
                await asyncio.sleep(5)
                if self.manual_mode:
                    await self.connect(MAIN_CHANNEL_ID)

    async def watchdog(self):
        while True:
            await asyncio.sleep(10)
            if not self.manual_mode:
                continue
            if (not self.client.voice_clients
                    or not self.client.voice_clients[0].is_connected()
                    or self.client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
                log.warning(f"[{self.name}] Бот не в канале. Возвращаю...")
                reconnect_stats[self.name.lower()] += 1
                await self.connect(MAIN_CHANNEL_ID)

    async def connect(self, channel_id):
        if self.is_reconnecting:
            return
        self.is_reconnecting = True
        try:
            channel = self.client.get_channel(channel_id)
            if not channel:
                return
            if (self.client.voice_clients
                    and self.client.voice_clients[0].is_connected()
                    and self.client.voice_clients[0].channel.id == channel_id):
                return
            if self.client.voice_clients:
                try:
                    await self.client.voice_clients[0].disconnect(force=True)
                except Exception:
                    pass
                await asyncio.sleep(1)
            await channel.connect(timeout=30.0)
            log.info(f"[{self.name}] Подключен к {channel.name}")
        except Exception as e:
            log.warning(f"[{self.name}] Ошибка: {type(e).__name__}: {e}")
        finally:
            self.is_reconnecting = False

    async def disconnect(self):
        if self.client.voice_clients:
            try:
                await self.client.voice_clients[0].disconnect(force=True)
                log.info(f"[{self.name}] Отключен.")
            except Exception:
                pass

    async def start(self):
        while True:
            try:
                await self.client.start(self.token)
            except Exception as e:
                log.error(f"[{self.name}] Упал: {type(e).__name__}: {e}. Перезапуск через 30 сек.")
                await asyncio.sleep(30)

# ====================================================
# АВТОПЕРЕЗАПУСК
# ====================================================
async def safe_start(client, token, name):
    while True:
        try:
            await client.start(token)
        except Exception as e:
            log.error(f"[{name}] Упал: {type(e).__name__}: {e}. Перезапуск через 30 сек.")
            traceback.print_exc()
            await asyncio.sleep(30)

# ====================================================
# ЗАПУСК
# ====================================================
async def main():
    global watcher_egoist, watcher_egorik

    log.info("=" * 50)
    log.info("ЗАПУСК БОТОВ...")
    log.info("=" * 50)

    watcher_egoist = Watcher(TOKEN_EGOIST, USER_ID_EGOIST, "Egoist")
    watcher_egorik = Watcher(TOKEN_EGORIK, USER_ID_EGORIK, "Egorik")

    tasks = [
        safe_start(main_client, TOKEN_MAIN, "MAIN"),
        watcher_egoist.start(),
        watcher_egorik.start(),
    ]

    if TOKEN_SECOND:
        tasks.append(safe_start(second_client, TOKEN_SECOND, "SECOND"))

    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        log.error(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        log.error(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
        traceback.print_exc()
