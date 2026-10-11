import os
import asyncio
import discord
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# --- Токены ---
TOKEN_MAIN = os.getenv("DISCORD_TOKEN_1")
MAIN_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "1558455255791050803"))

TOKEN_EGOIST = os.getenv("DISCORD_TOKEN_2")
USER_ID_EGOIST = int(os.getenv("USER_ID_1"))

TOKEN_EGORIK = os.getenv("DISCORD_TOKEN_3")
USER_ID_EGORIK = int(os.getenv("USER_ID_2"))

GUILD_ID = int(os.getenv("GUILD_ID"))

if not TOKEN_MAIN or not TOKEN_EGOIST or not TOKEN_EGORIK:
    raise ValueError("Не заданы DISCORD_TOKEN_1/2/3")
if not GUILD_ID:
    raise ValueError("Не задан GUILD_ID")

# --- Веб-сервер для Render (Health Check) ---
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
    print(f"Веб-сервер запущен на порту {port}")
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()
# ----------------------------------------------------

# --- Аккаунт №1: сидит 24/7 с watchdog ---
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
            print(f"[MAIN] Канал {MAIN_CHANNEL_ID} не найден.")
            return
        if main_client.voice_clients and main_client.voice_clients[0].is_connected():
            return
        if main_client.voice_clients:
            try:
                await main_client.voice_clients[0].disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(2)
        await channel.connect(self_mute=True, self_deaf=True)
        print(f"[MAIN] Подключен к {channel.name} (в полном муте)")
    except Exception as e:
        print(f"[MAIN] Ошибка подключения: {e}")
    finally:
        main_reconnecting = False

async def main_watchdog():
    while True:
        await asyncio.sleep(30)
        if not main_client.voice_clients or not main_client.voice_clients[0].is_connected():
            print("[MAIN] Бот не в канале. Переподключаюсь...")
            await ensure_main_connected()

@main_client.event
async def on_ready():
    print(f"[MAIN] Авторизован как {main_client.user}")
    await ensure_main_connected()
    main_client.loop.create_task(main_watchdog())

@main_client.event
async def on_voice_state_update(member, before, after):
    if member.id != main_client.user.id:
        return
    if before.channel and not after.channel:
        print("[MAIN] Кикнули. Переподключаюсь через 5 секунд...")
        await asyncio.sleep(5)
        await ensure_main_connected()

# --- Дежурный аккаунт (следит за человеком) ---
class Watcher:
    def __init__(self, token, user_id, name):
        self.token = token
        self.user_id = user_id
        self.name = name
        self.client = discord.Client()
        self.is_reconnecting = False
        self.pending_join = None
        self._register_events()

    def _register_events(self):
        @self.client.event
        async def on_ready():
            print(f"[{self.name}] Авторизован как {self.client.user}")

        @self.client.event
        async def on_voice_state_update(member, before, after):
            if member.id != self.user_id:
                return

            if before.channel and before.channel.guild.id != GUILD_ID:
                return
            if after.channel and after.channel.guild.id != GUILD_ID:
                return

            # Человек ВЫШЕЛ из канала
            if before.channel and not after.channel:
                print(f"[{self.name}] {member} вышел. Жду 30 секунд...")
                if self.pending_join and not self.pending_join.done():
                    self.pending_join.cancel()
                self.pending_join = asyncio.create_task(
                    self.delayed_join(before.channel.id)
                )

            # Человек ЗАШЁЛ в канал
            elif not before.channel and after.channel:
                print(f"[{self.name}] {member} зашёл. Отменяю таймер и выхожу.")
                if self.pending_join and not self.pending_join.done():
                    self.pending_join.cancel()
                    self.pending_join = None
                await self.disconnect()

            # Человек ПЕРЕШЁЛ в другой канал
            elif before.channel and after.channel and before.channel != after.channel:
                print(f"[{self.name}] {member} перешёл в {after.channel.name}. Отменяю таймер.")
                if self.pending_join and not self.pending_join.done():
                    self.pending_join.cancel()
                    self.pending_join = None
                await self.disconnect()

    async def delayed_join(self, channel_id):
        try:
            await asyncio.sleep(30)
            print(f"[{self.name}] 30 секунд прошло. Проверяю...")
            guild = self.client.get_guild(GUILD_ID)
            if guild:
                for vc in guild.voice_channels:
                    if any(m.id == self.user_id for m in vc.members):
                        print(f"[{self.name}] Человек вернулся. Не захожу.")
                        return
            print(f"[{self.name}] Человек не вернулся. Захожу.")
            await self.connect(channel_id)
        except asyncio.CancelledError:
            print(f"[{self.name}] Таймер отменён.")

    async def connect(self, channel_id):
        if self.is_reconnecting:
            return
        self.is_reconnecting = True
        try:
            channel = self.client.get_channel(channel_id)
            if not channel:
                print(f"[{self.name}] Канал {channel_id} не найден.")
                return
            if self.client.voice_clients:
                try:
                    await self.client.voice_clients[0].disconnect(force=True)
                except Exception:
                    pass
                await asyncio.sleep(2)
            await channel.connect(self_mute=True, self_deaf=True)
            print(f"[{self.name}] Подключен к {channel.name} (в полном муте)")
        except Exception as e:
            print(f"[{self.name}] Ошибка: {e}")
        finally:
            self.is_reconnecting = False

    async def disconnect(self):
        if self.client.voice_clients:
            try:
                await self.client.voice_clients[0].disconnect(force=True)
                print(f"[{self.name}] Отключен.")
            except Exception as e:
                print(f"[{self.name}] Ошибка отключения: {e}")

    async def start(self):
        await self.client.start(self.token)

# --- Запуск ---
async def main():
    watcher_egoist = Watcher(TOKEN_EGOIST, USER_ID_EGOIST, "Egoist")
    watcher_egorik = Watcher(TOKEN_EGORIK, USER_ID_EGORIK, "Egorik")

    await asyncio.gather(
        main_client.start(TOKEN_MAIN),
        watcher_egoist.start(),
        watcher_egorik.start(),
    )

if __name__ == "__main__":
    asyncio.run(main())
