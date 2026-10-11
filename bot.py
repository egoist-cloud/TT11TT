import os
import asyncio
import discord
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# --- Токены ---
TOKEN_EGOIST = os.getenv("DISCORD_TOKEN_2")
USER_ID_EGOIST = int(os.getenv("USER_ID_1"))
GUILD_ID = int(os.getenv("GUILD_ID"))
VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "1558455255791050803"))

if not TOKEN_EGOIST:
    raise ValueError("Не задан DISCORD_TOKEN_2")
if not GUILD_ID:
    raise ValueError("Не задан GUILD_ID")

# --- Веб-сервер ---
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

client = discord.Client()
is_reconnecting = False
pending_join = None  # задача ожидания

async def connect_to_channel(channel_id):
    global is_reconnecting
    if is_reconnecting:
        return
    is_reconnecting = True
    try:
        channel = client.get_channel(channel_id)
        if not channel:
            print(f"Канал {channel_id} не найден.")
            return
        if client.voice_clients:
            try:
                await client.voice_clients[0].disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(2)
        await channel.connect(self_mute=True, self_deaf=True)
        print(f"Подключен к {channel.name} (в полном муте)")
    except Exception as e:
        print(f"Ошибка подключения: {e}")
    finally:
        is_reconnecting = False

async def delayed_join(channel_id):
    """Ждёт 30 секунд и заходит, если человек не вернулся."""
    global pending_join
    try:
        await asyncio.sleep(30)
        # Проверяем, что человек всё ещё не в канале
        channel = client.get_channel(channel_id)
        if not channel:
            return
        if any(m.id == USER_ID_EGOIST for m in channel.members):
            print("Человек вернулся до истечения таймера.")
            return
        print("30 секунд прошло. Захожу.")
        await connect_to_channel(channel_id)
    except asyncio.CancelledError:
        print("Таймер отменён — человек вернулся.")

@client.event
async def on_ready():
    print(f"Авторизован как {client.user}")

@client.event
async def on_voice_state_update(member, before, after):
    global pending_join

    if member.id != USER_ID_EGOIST:
        return

    # Проверяем, что событие на нашем сервере
    if before.channel and before.channel.guild.id != GUILD_ID:
        return
    if after.channel and after.channel.guild.id != GUILD_ID:
        return

    # Человек вышел из канала — запускаем таймер на 30 секунд
    if before.channel and not after.channel:
        print(f"{member} вышел. Жду 30 секунд...")
        if pending_join and not pending_join.done():
            pending_join.cancel()
        pending_join = asyncio.create_task(delayed_join(before.channel.id))

    # Человек зашёл в канал — отменяем таймер и выходим, если сидим
    elif not before.channel and after.channel:
        print(f"{member} зашёл. Отменяю таймер.")
        if pending_join and not pending_join.done():
            pending_join.cancel()
            pending_join = None
        if client.voice_clients:
            try:
                await client.voice_clients[0].disconnect(force=True)
                print("Выхожу.")
            except Exception as e:
                print(f"Ошибка отключения: {e}")

    # Человек перешёл в другой канал — отменяем таймер
    elif before.channel and after.channel and before.channel != after.channel:
        print(f"{member} перешёл. Отменяю таймер.")
        if pending_join and not pending_join.done():
            pending_join.cancel()
            pending_join = None

client.run(TOKEN_EGOIST)
