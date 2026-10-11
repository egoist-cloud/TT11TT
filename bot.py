import os
import asyncio
import discord
from discord.ext.native_voice import VoiceClient, FFmpegMediaSource
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# --- Чтение переменных окружения ---
TOKEN = os.getenv("DISCORD_TOKEN")
VOICE_CHANNEL_ID = os.getenv("VOICE_CHANNEL_ID")

if not TOKEN or not VOICE_CHANNEL_ID:
    raise ValueError("Не заданы переменные окружения DISCORD_TOKEN или VOICE_CHANNEL_ID")

VOICE_CHANNEL_ID = int(VOICE_CHANNEL_ID)

# --- Веб-сервер для Render ---
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

@client.event
async def on_ready():
    print(f"Авторизован как {client.user}")
    print(f"Попытка подключиться к каналу {VOICE_CHANNEL_ID}...")

    try:
        channel = client.get_channel(VOICE_CHANNEL_ID)
        if not channel:
            print("Канал не найден. Проверьте ID и права доступа.")
            return

        # Подключаемся с выключенным микрофоном и включенной камерой
        voice = await channel.connect(cls=VoiceClient, self_mute=True, self_video=True)
        print(f"Успешно подключен к {channel.name} (в муте, с видео)")

        # Запускаем видео-фон
        source = FFmpegMediaSource.from_file(
            'output.mp4',
            'H264',
            width=1280,
            height=720,
            fps=30,
            bitrate=4_000_000,
        )
        voice.play(source)
        print("Воспроизведение видео запущено.")

    except Exception as e:
        print(f"Ошибка при подключении: {e}")

@client.event
async def on_voice_state_update(member, before, after):
    if member.id == client.user.id:
        if before.channel and not after.channel:
            print("Соединение потеряно. Переподключение через 5 секунд...")
            await asyncio.sleep(5)
            try:
                channel = client.get_channel(VOICE_CHANNEL_ID)
                if channel:
                    voice = await channel.connect(cls=VoiceClient, self_mute=True, self_video=True)
                    source = FFmpegMediaSource.from_file(
                        'output.mp4',
                        'H264',
                        width=1280,
                        height=720,
                        fps=30,
                        bitrate=4_000_000,
                    )
                    voice.play(source)
                    print("Переподключение успешно.")
            except Exception as e:
                print(f"Не удалось переподключиться: {e}")

client.run(TOKEN)
