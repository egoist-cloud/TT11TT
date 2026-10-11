import os
import asyncio
import random
import discord
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# --- Чтение переменных окружения ---
TOKEN = os.getenv("DISCORD_TOKEN")
VOICE_CHANNEL_ID = os.getenv("VOICE_CHANNEL_ID")

if not TOKEN or not VOICE_CHANNEL_ID:
    raise ValueError("Не заданы переменные окружения DISCORD_TOKEN или VOICE_CHANNEL_ID")

VOICE_CHANNEL_ID = int(VOICE_CHANNEL_ID)

PRIMARY_CHANNEL_ID = VOICE_CHANNEL_ID
FALLBACK_CHANNEL_ID = 1544265694156423228

# Флаг, чтобы не реагировать на свои же действия
is_reconnecting = False

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

client = discord.Client()
current_channel_id = PRIMARY_CHANNEL_ID

async def mute_unmute_loop():
    """Каждые 15-30 минут размучивает и замучивает бота."""
    while True:
        delay = random.randint(900, 1800)
        print(f"Следующий размут через {delay // 60} мин {delay % 60} сек")
        await asyncio.sleep(delay)

        voice = client.voice_clients[0] if client.voice_clients else None
        if not voice or not voice.is_connected():
            continue

        try:
            await voice.guild.change_voice_state(
                channel=voice.channel,
                self_mute=False,
                self_deaf=False
            )
            print("Размучен на 5 секунд...")
            await asyncio.sleep(5)

            await voice.guild.change_voice_state(
                channel=voice.channel,
                self_mute=True,
                self_deaf=True
            )
            print("Снова в муте.")
        except Exception as e:
            print(f"Ошибка при смене мута: {e}")

async def connect_to_channel(channel_id):
    """Подключается к указанному каналу."""
    global current_channel_id, is_reconnecting

    if is_reconnecting:
        print("Уже идёт переподключение, пропускаю.")
        return None

    is_reconnecting = True
    try:
        channel = client.get_channel(channel_id)
        if not channel:
            print(f"Канал {channel_id} не найден.")
            return None

        if client.voice_clients:
            try:
                await client.voice_clients[0].disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(2)

        voice = await channel.connect(self_mute=True, self_deaf=True)
        current_channel_id = channel_id
        print(f"Успешно подключен к {channel.name} (в полном муте)")
        return voice
    except Exception as e:
        print(f"Ошибка при подключении: {e}")
        return None
    finally:
        is_reconnecting = False

@client.event
async def on_ready():
    print(f"Авторизован как {client.user}")
    await connect_to_channel(PRIMARY_CHANNEL_ID)
    client.loop.create_task(mute_unmute_loop())

@client.event
async def on_voice_state_update(member, before, after):
    if member.id != client.user.id:
        return

    global current_channel_id

    # Если бот переподключается сам — игнорируем
    if is_reconnecting:
        return

    # Случай 1: Бота кикнули (он вышел и не подключён)
    if before.channel and not after.channel:
        # Проверяем, что это не наш собственный выход
        if client.voice_clients and client.voice_clients[0].is_connected():
            return

        print("Бота кикнули. Захожу в резервный канал через 10 секунд...")
        await asyncio.sleep(10)
        await connect_to_channel(FALLBACK_CHANNEL_ID)

    # Случай 2: Бота перекинули в другой канал
    elif before.channel and after.channel and before.channel != after.channel:
        print(f"Бота перекинули в {after.channel.name}. Остаюсь здесь.")
        current_channel_id = after.channel.id

client.run(TOKEN)
