import os
import asyncio
import discord
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# --- Токен бота Старый спит ---
TOKEN_MAIN = os.getenv("DISCORD_TOKEN_1")
MAIN_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "1558455255791050803"))

if not TOKEN_MAIN:
    raise ValueError("Не задан DISCORD_TOKEN_1")

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

async def ensure_connected():
    """Гарантирует, что бот в канале. Если нет — подключает."""
    global is_reconnecting
    if is_reconnecting:
        return
    is_reconnecting = True
    try:
        channel = client.get_channel(MAIN_CHANNEL_ID)
        if not channel:
            print(f"[MAIN] Канал {MAIN_CHANNEL_ID} не найден.")
            return
        # Если уже подключены — ничего не делаем
        if client.voice_clients and client.voice_clients[0].is_connected():
            return
        # Отключаем старые подключения
        if client.voice_clients:
            try:
                await client.voice_clients[0].disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(2)
        await channel.connect(self_mute=True, self_deaf=True)
        print(f"[MAIN] Подключен к {channel.name} (в полном муте)")
    except Exception as e:
        print(f"[MAIN] Ошибка подключения: {e}")
    finally:
        is_reconnecting = False

@client.event
async def on_ready():
    print(f"[MAIN] Авторизован как {client.user}")
    await ensure_connected()

@client.event
async def on_voice_state_update(member, before, after):
    if member.id != client.user.id:
        return
    if before.channel and not after.channel:
        print("[MAIN] Бот отключён. Переподключение через 5 секунд...")
        await asyncio.sleep(5)
        await ensure_connected()

async def watchdog():
    """Каждые 30 секунд проверяет, что бот в канале."""
    while True:
        await asyncio.sleep(30)
        if not client.voice_clients or not client.voice_clients[0].is_connected():
            print("[MAIN] Бот не в канале. Переподключаюсь...")
            await ensure_connected()

@client.event
async def on_ready_watchdog():
    pass

# Запускаем watchdog после старта
@client.event
async def on_ready_start_watchdog():
    pass

client.loop.create_task(watchdog())
client.run(TOKEN_MAIN)
