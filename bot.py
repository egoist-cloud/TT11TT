import os
import asyncio
import discord
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# --- Токены ---
# Аккаунт №1 (сидит 24/7)
TOKEN_MAIN = os.getenv("DISCORD_TOKEN_1")
MAIN_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "1558455255791050803"))

# Аккаунт №2 (следит за egoist)
TOKEN_EGOIST = os.getenv("DISCORD_TOKEN_2")
USER_ID_EGOIST = int(os.getenv("USER_ID_1"))

# Аккаунт №3 (следит за egorik)
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

# --- Аккаунт №1: сидит 24/7 ---
main_client = discord.Client()

@main_client.event
async def on_ready():
    print(f"[MAIN] Авторизован как {main_client.user}")
    try:
        channel = main_client.get_channel(MAIN_CHANNEL_ID)
        if not channel:
            print(f"[MAIN] Канал {MAIN_CHANNEL_ID} не найден.")
            return
        if not main_client.voice_clients:
            await channel.connect(self_mute=True, self_deaf=True)
            print(f"[MAIN] Подключен к {channel.name} (в полном муте)")
    except Exception as e:
        print(f"[MAIN] Ошибка подключения: {e}")

@main_client.event
async def on_voice_state_update(member, before, after):
    if member.id != main_client.user.id:
        return
    if before.channel and not after.channel:
        print("[MAIN] Кикнули. Переподключаюсь через 5 секунд...")
        await asyncio.sleep(5)
        try:
            channel = main_client.get_channel(MAIN_CHANNEL_ID)
            if channel:
                await channel.connect(self_mute=True, self_deaf=True)
                print("[MAIN] Переподключение успешно.")
        except Exception as e:
            print(f"[MAIN] Ошибка переподключения: {e}")

# --- Дежурный аккаунт: следит за своим же «человеческим» аккаунтом ---
class SelfWatcher:
    def __init__(self, token, user_id, name):
        self.token = token
        self.user_id = user_id
        self.name = name
        self.client = discord.Client()
        self.is_reconnecting = False
        self.current_channel_id = None
        self._register_events()

    def _register_events(self):
        @self.client.event
        async def on_ready():
            print(f"[{self.name}] Авторизован как {self.client.user}")

        @self.client.event
        async def on_voice_state_update(member, before, after):
            # Следим только за нужным пользователем
            if member.id != self.user_id:
                return

            # Проверяем, что событие на нашем сервере
            if before.channel and before.channel.guild.id != GUILD_ID:
                return
            if after.channel and after.channel.guild.id != GUILD_ID:
                return

            # Человек вышел — заходим через 5 секунд
            if before.channel and not after.channel:
                print(f"[{self.name}] {member} вышел. Захожу в {before.channel.name}")
                await asyncio.sleep(5)
                await self.connect(before.channel.id)

            # Человек перешёл — следуем
            elif before.channel and after.channel and before.channel != after.channel:
                print(f"[{self.name}] {member} перешёл в {after.channel.name}. Следую.")
                await asyncio.sleep(5)
                await self.connect(after.channel.id)

            # Человек зашёл — выходим, чтобы не мешать
            elif not before.channel and after.channel:
                print(f"[{self.name}] {member} зашёл. Выхожу.")
                await self.disconnect()

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
    watcher1 = SelfWatcher(TOKEN_EGOIST, USER_ID_EGOIST, "Egoist")
    watcher2 = SelfWatcher(TOKEN_EGORIK, USER_ID_EGORIK, "Egorik")

    await asyncio.gather(
        main_client.start(TOKEN_MAIN),
        watcher1.start(),
        watcher2.start(),
    )

if __name__ == "__main__":
    asyncio.run(main())
