import os
import asyncio
import discord
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# --- Токены ---
# Аккаунт №1 (сидит 24/7)
TOKEN_MAIN = os.getenv("DISCORD_TOKEN_1")
MAIN_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "1558455255791050803"))

# Аккаунты №2 и №3 (дежурят за людьми)
TOKEN_WATCH_1 = os.getenv("DISCORD_TOKEN_2")
USER_ID_1 = int(os.getenv("USER_ID_1"))

TOKEN_WATCH_2 = os.getenv("DISCORD_TOKEN_3")
USER_ID_2 = int(os.getenv("USER_ID_2"))

if not TOKEN_MAIN or not TOKEN_WATCH_1 or not TOKEN_WATCH_2:
    raise ValueError("Не заданы DISCORD_TOKEN_1, DISCORD_TOKEN_2 или DISCORD_TOKEN_3")

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

# --- Дежурный аккаунт: заходит, если человек отсутствует > 30 секунд ---
class Watcher:
    def __init__(self, token, user_id, name):
        self.token = token
        self.user_id = user_id
        self.name = name
        self.client = discord.Client()
        self.is_reconnecting = False
        self.join_task = None  # задача ожидания 30 секунд
        self.current_channel_id = None
        self._register_events()

    def _register_events(self):
        @self.client.event
        async def on_ready():
            print(f"[{self.name}] Авторизован как {self.client.user}")

        @self.client.event
        async def on_voice_state_update(member, before, after):
            if member.id != self.user_id:
                return

            # Человек ЗАШЁЛ в канал — отменяем ожидание и выходим
            if not before.channel and after.channel:
                if self.join_task and not self.join_task.done():
                    self.join_task.cancel()
                    self.join_task = None
                    print(f"[{self.name}] Человек вернулся, отменяю вход.")
                await self.disconnect()

            # Человек ВЫШЕЛ из канала — запускаем таймер на 30 секунд
            elif before.channel and not after.channel:
                print(f"[{self.name}] Человек вышел. Жду 30 секунд...")
                self.current_channel_id = before.channel.id
                self.join_task = asyncio.create_task(self.delayed_join(before.channel.id))

            # Человек ПЕРЕШЁЛ в другой канал — отменяем таймер и выходим
            elif before.channel and after.channel and before.channel != after.channel:
                if self.join_task and not self.join_task.done():
                    self.join_task.cancel()
                    self.join_task = None
                print(f"[{self.name}] Человек перешёл в {after.channel.name}. Жду 30 секунд...")
                self.current_channel_id = after.channel.id
                self.join_task = asyncio.create_task(self.delayed_join(after.channel.id))

    async def delayed_join(self, channel_id):
        """Ждёт 30 секунд и заходит в канал."""
        try:
            await asyncio.sleep(30)
            # Проверяем, что человек всё ещё не в канале
            channel = self.client.get_channel(channel_id)
            if not channel:
                return
            # Проверяем, есть ли человек в канале
            if any(m.id == self.user_id for m in channel.members):
                print(f"[{self.name}] Человек вернулся до истечения таймера.")
                return
            print(f"[{self.name}] 30 секунд прошло. Захожу в {channel.name}")
            await self.connect(channel_id)
        except asyncio.CancelledError:
            pass

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
    watcher1 = Watcher(TOKEN_WATCH_1, USER_ID_1, "Watch-1")
    watcher2 = Watcher(TOKEN_WATCH_2, USER_ID_2, "Watch-2")

    await asyncio.gather(
        main_client.start(TOKEN_MAIN),
        watcher1.start(),
        watcher2.start(),
    )

if __name__ == "__main__":
    asyncio.run(main())
