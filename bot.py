import os
import sys
import asyncio
import discord
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import traceback

# ====================================================
# НАСТРОЙКА ВЫВОДА
# ====================================================
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ====================================================
# ЧТЕНИЕ ПЕРЕМЕННЫХ
# ====================================================
TOKEN_MAIN = os.getenv("DISCORD_TOKEN_1")
# ID голосового канала TALENT (НЕ лички!)
MAIN_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "0"))

TOKEN_EGOIST = os.getenv("DISCORD_TOKEN_2")
USER_ID_EGOIST = int(os.getenv("USER_ID_1"))

TOKEN_EGORIK = os.getenv("DISCORD_TOKEN_3")
USER_ID_EGORIK = int(os.getenv("USER_ID_2"))

GUILD_ID = int(os.getenv("GUILD_ID"))

# ID основного бота (commback / asiris.cs2_02506)
MAIN_BOT_USER_ID = int(os.getenv("MAIN_BOT_USER_ID", "1241012523952508978"))

print("=" * 50, flush=True)
print("ЗАГРУЗКА ПЕРЕМЕННЫХ:", flush=True)
print(f"  TOKEN_MAIN: {'есть' if TOKEN_MAIN else 'НЕТ'}", flush=True)
print(f"  TOKEN_EGOIST: {'есть' if TOKEN_EGOIST else 'НЕТ'}", flush=True)
print(f"  TOKEN_EGORIK: {'есть' if TOKEN_EGORIK else 'НЕТ'}", flush=True)
print(f"  USER_ID_EGOIST: {USER_ID_EGOIST}", flush=True)
print(f"  USER_ID_EGORIK: {USER_ID_EGORIK}", flush=True)
print(f"  GUILD_ID: {GUILD_ID}", flush=True)
print(f"  MAIN_CHANNEL_ID: {MAIN_CHANNEL_ID}", flush=True)
print(f"  MAIN_BOT_USER_ID: {MAIN_BOT_USER_ID}", flush=True)
print("=" * 50, flush=True)

if not MAIN_CHANNEL_ID:
    raise ValueError("Не задан VOICE_CHANNEL_ID (ID голосового канала TALENT)")

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
    print(f"Веб-сервер запущен на порту {port}", flush=True)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ====================================================
# АККАУНТ №1 (commback): 24/7 + ПРИНИМАЕТ КОМАНДЫ
# ====================================================
main_client = discord.Client()
main_reconnecting = False

watcher_egoist = None
watcher_egorik = None

async def ensure_main_connected():
    global main_reconnecting
    if main_reconnecting:
        return
    main_reconnecting = True
    try:
        channel = main_client.get_channel(MAIN_CHANNEL_ID)
        if not channel:
            print(f"[MAIN] Канал {MAIN_CHANNEL_ID} не найден.", flush=True)
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
            await asyncio.sleep(2)
        for attempt in range(3):
            try:
                await channel.connect(self_mute=True, self_deaf=True, timeout=30.0)
                print(f"[MAIN] Подключен к {channel.name} (в полном муте)", flush=True)
                return
            except Exception as e:
                print(f"[MAIN] Попытка {attempt + 1}: {type(e).__name__}: {e}", flush=True)
                await asyncio.sleep(10)
        print("[MAIN] Не удалось подключиться.", flush=True)
    except Exception as e:
        print(f"[MAIN] Ошибка: {e}", flush=True)
        traceback.print_exc()
    finally:
        main_reconnecting = False

async def main_watchdog():
    while True:
        await asyncio.sleep(30)
        if (not main_client.voice_clients
                or not main_client.voice_clients[0].is_connected()
                or main_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            print("[MAIN] Бот не в канале. Переподключаюсь...", flush=True)
            await ensure_main_connected()

@main_client.event
async def on_ready():
    print(f"[MAIN] Авторизован как {main_client.user}", flush=True)
    await ensure_main_connected()
    main_client.loop.create_task(main_watchdog())

@main_client.event
async def on_voice_state_update(member, before, after):
    if member.id != main_client.user.id:
        return
    if before.channel and not after.channel:
        print("[MAIN] Кикнули. Переподключаюсь через 5 секунд...", flush=True)
        await asyncio.sleep(5)
        await ensure_main_connected()

@main_client.event
async def on_message(message):
    if message.author.id == main_client.user.id:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return
    if message.author.id not in (USER_ID_EGOIST, USER_ID_EGORIK):
        print(f"[MAIN] Команда от неизвестного пользователя {message.author.id} проигнорирована.", flush=True)
        return

    content = message.content.strip().lower()
    parts = content.split()

    if len(parts) < 2:
        await message.channel.send(
            "Использование: `!on egoist` / `!off egoist` / `!on egorik` / `!off egorik`"
        )
        return

    command = parts[0]
    target = parts[1]

    if target == "egoist" and watcher_egoist:
        if command == "!on":
            print(f"[MAIN] Команда: !on egoist", flush=True)
            watcher_egoist.manual_mode = True
            await watcher_egoist.connect(MAIN_CHANNEL_ID)
            await message.channel.send("✅ Бот `egoist` включён (24/7).")
        elif command == "!off":
            print(f"[MAIN] Команда: !off egoist", flush=True)
            watcher_egoist.manual_mode = False
            await watcher_egoist.disconnect()
            await message.channel.send("⛔ Бот `egoist` выключен.")

    elif target == "egorik" and watcher_egorik:
        if command == "!on":
            print(f"[MAIN] Команда: !on egorik", flush=True)
            watcher_egorik.manual_mode = True
            await watcher_egorik.connect(MAIN_CHANNEL_ID)
            await message.channel.send("✅ Бот `egorik` включён (24/7).")
        elif command == "!off":
            print(f"[MAIN] Команда: !off egorik", flush=True)
            watcher_egorik.manual_mode = False
            await watcher_egorik.disconnect()
            await message.channel.send("⛔ Бот `egorik` выключен.")

    else:
        await message.channel.send("Укажите `egoist` или `egorik`.")

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
            print(f"[{self.name}] Авторизован как {self.client.user}", flush=True)

        @self.client.event
        async def on_presence_update(before, after):
            if self.manual_mode:
                return
            if after.id != self.user_id:
                return

            if after.status == discord.Status.offline:
                print(f"[{self.name}] {after} ушёл в офлайн. Захожу через 30 сек.", flush=True)
                await asyncio.sleep(30)
                await self.connect(MAIN_CHANNEL_ID)
            elif after.status != discord.Status.offline:
                print(f"[{self.name}] {after} вернулся. Выхожу.", flush=True)
                await self.disconnect()

        @self.client.event
        async def on_voice_state_update(member, before, after):
            if self.manual_mode:
                return
            if member.id == self.client.user.id:
                return
            if member.id != self.user_id:
                return
            if before.channel and before.channel.guild.id != GUILD_ID:
                return
            if after.channel and after.channel.guild.id != GUILD_ID:
                return

            if before.channel and not after.channel:
                if member.status == discord.Status.offline:
                    await asyncio.sleep(5)
                    await self.connect(before.channel.id)
            elif not before.channel and after.channel:
                await self.disconnect()

    async def connect(self, channel_id):
        if self.is_reconnecting:
            return
        self.is_reconnecting = True
        try:
            channel = self.client.get_channel(channel_id)
            if not channel:
                print(f"[{self.name}] Канал {channel_id} не найден.", flush=True)
                return
            if self.client.voice_clients:
                try:
                    await self.client.voice_clients[0].disconnect(force=True)
                except Exception:
                    pass
                await asyncio.sleep(2)
            await channel.connect(self_mute=True, self_deaf=True, timeout=30.0)
            print(f"[{self.name}] Подключен к {channel.name} (в полном муте)", flush=True)
        except Exception as e:
            print(f"[{self.name}] Ошибка: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
        finally:
            self.is_reconnecting = False

    async def disconnect(self):
        if self.client.voice_clients:
            try:
                await self.client.voice_clients[0].disconnect(force=True)
                print(f"[{self.name}] Отключен.", flush=True)
            except Exception as e:
                print(f"[{self.name}] Ошибка отключения: {e}", flush=True)

    async def start(self):
        try:
            await self.client.start(self.token)
        except Exception as e:
            print(f"[{self.name}] КРИТИЧЕСКАЯ ОШИБКА: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()

# ====================================================
# ЗАПУСК
# ====================================================
async def main():
    global watcher_egoist, watcher_egorik

    print("=" * 50, flush=True)
    print("ЗАПУСК БОТОВ...", flush=True)
    print("=" * 50, flush=True)

    watcher_egoist = Watcher(TOKEN_EGOIST, USER_ID_EGOIST, "Egoist")
    watcher_egorik = Watcher(TOKEN_EGORIK, USER_ID_EGORIK, "Egorik")

    try:
        await asyncio.gather(
            main_client.start(TOKEN_MAIN),
            watcher_egoist.start(),
            watcher_egorik.start(),
        )
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА: {e}", flush=True)
        traceback.print_exc()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА: {e}", flush=True)
        traceback.print_exc()
