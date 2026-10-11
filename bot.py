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
MAIN_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "1558455255791050803"))

TOKEN_EGOIST = os.getenv("DISCORD_TOKEN_2")
USER_ID_EGOIST = int(os.getenv("USER_ID_1"))

TOKEN_EGORIK = os.getenv("DISCORD_TOKEN_3")
USER_ID_EGORIK = int(os.getenv("USER_ID_2"))

GUILD_ID = int(os.getenv("GUILD_ID"))

print("=" * 50, flush=True)
print("ЗАГРУЗКА ПЕРЕМЕННЫХ:", flush=True)
print(f"  TOKEN_MAIN: {'есть' if TOKEN_MAIN else 'НЕТ'}", flush=True)
print(f"  TOKEN_EGOIST: {'есть' if TOKEN_EGOIST else 'НЕТ'}", flush=True)
print(f"  TOKEN_EGORIK: {'есть' if TOKEN_EGORIK else 'НЕТ'}", flush=True)
print(f"  USER_ID_EGOIST: {USER_ID_EGOIST}", flush=True)
print(f"  USER_ID_EGORIK: {USER_ID_EGORIK}", flush=True)
print(f"  GUILD_ID: {GUILD_ID}", flush=True)
print(f"  MAIN_CHANNEL_ID: {MAIN_CHANNEL_ID}", flush=True)
print("=" * 50, flush=True)

if not TOKEN_MAIN or not TOKEN_EGOIST or not TOKEN_EGORIK:
    raise ValueError("Не заданы DISCORD_TOKEN_1/2/3")
if not GUILD_ID:
    raise ValueError("Не задан GUILD_ID")

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
# АККАУНТ №1: СИДИТ 24/7
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
            print(f"[MAIN] Канал {MAIN_CHANNEL_ID} не найден.", flush=True)
            return

        # Уже сидим в нужном канале — ничего не делаем
        if (main_client.voice_clients
                and main_client.voice_clients[0].is_connected()
                and main_client.voice_clients[0].channel.id == MAIN_CHANNEL_ID):
            return

        # Сидим в другом канале — отключаемся
        if main_client.voice_clients:
            try:
                await main_client.voice_clients[0].disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(2)

        # Пробуем подключиться 3 раза
        for attempt in range(3):
            try:
                await channel.connect(self_mute=True, self_deaf=True, timeout=30.0)
                print(f"[MAIN] Подключен к {channel.name} (в полном муте)", flush=True)
                return
            except Exception as e:
                print(f"[MAIN] Попытка {attempt + 1} не удалась: {e}", flush=True)
                await asyncio.sleep(10)
        print("[MAIN] Не удалось подключиться после 3 попыток.", flush=True)
    except Exception as e:
        print(f"[MAIN] Ошибка подключения: {e}", flush=True)
        traceback.print_exc()
    finally:
        main_reconnecting = False

async def main_watchdog():
    while True:
        await asyncio.sleep(30)
        if (not main_client.voice_clients
                or not main_client.voice_clients[0].is_connected()
                or main_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            print("[MAIN] Бот не в нужном канале. Переподключаюсь...", flush=True)
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
        self.pending_join = None
        self._register_events()

    def _register_events(self):
        @self.client.event
        async def on_ready():
            print(f"[{self.name}] Авторизован как {self.client.user}", flush=True)

        @self.client.event
        async def on_voice_state_update(member, before, after):
            # Игнорируем свои события
            if member.id == self.client.user.id:
                return
            # Следим только за нужным человеком
            if member.id != self.user_id:
                return
            # Событие на нашем сервере
            if before.channel and before.channel.guild.id != GUILD_ID:
                return
            if after.channel and after.channel.guild.id != GUILD_ID:
                return

            # Человек ВЫШЕЛ
            if before.channel and not after.channel:
                print(f"[{self.name}] {member} вышел. Жду 30 секунд...", flush=True)
                if self.pending_join and not self.pending_join.done():
                    self.pending_join.cancel()
                self.pending_join = asyncio.create_task(
                    self.delayed_join(before.channel.id)
                )

            # Человек ЗАШЁЛ
            elif not before.channel and after.channel:
                print(f"[{self.name}] {member} зашёл. Отменяю таймер и выхожу.", flush=True)
                if self.pending_join and not self.pending_join.done():
                    self.pending_join.cancel()
                    self.pending_join = None
                await self.disconnect()

            # Человек ПЕРЕШЁЛ
            elif before.channel and after.channel and before.channel != after.channel:
                print(f"[{self.name}] {member} перешёл. Отменяю таймер.", flush=True)
                if self.pending_join and not self.pending_join.done():
                    self.pending_join.cancel()
                    self.pending_join = None
                await self.disconnect()

    async def delayed_join(self, channel_id):
        try:
            await asyncio.sleep(30)
            print(f"[{self.name}] 30 секунд прошло. Проверяю...", flush=True)
            guild = self.client.get_guild(GUILD_ID)
            if guild:
                for vc in guild.voice_channels:
                    if any(m.id == self.user_id for m in vc.members):
                        print(f"[{self.name}] Человек вернулся. Не захожу.", flush=True)
                        return
            print(f"[{self.name}] Человек не вернулся. Захожу.", flush=True)
            await self.connect(channel_id)
        except asyncio.CancelledError:
            print(f"[{self.name}] Таймер отменён.", flush=True)

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
            print(f"[{self.name}] Ошибка: {e}", flush=True)
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
            print(f"[{self.name}] КРИТИЧЕСКАЯ ОШИБКА: {e}", flush=True)
            traceback.print_exc()

# ====================================================
# ЗАПУСК
# ====================================================
async def main():
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
        print(f"КРИТИЧЕСКАЯ ОШИБКА В GATHER: {e}", flush=True)
        traceback.print_exc()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА В ASYNCIO.RUN: {e}", flush=True)
        traceback.print_exc()
