import os
import asyncio
import discord
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import yt_dlp  # <-- Новая библиотека

# --- Чтение переменных окружения ---
TOKEN = os.getenv("DISCORD_TOKEN")
VOICE_CHANNEL_ID = os.getenv("VOICE_CHANNEL_ID")

if not TOKEN or not VOICE_CHANNEL_ID:
    raise ValueError("Не заданы переменные окружения DISCORD_TOKEN или VOICE_CHANNEL_ID")

VOICE_CHANNEL_ID = int(VOICE_CHANNEL_ID)

# --- Список доступных треков (ссылки на YouTube) ---
TRACKS = {
    "1": "https://www.youtube.com/watch?v=rB11e52E9LM",  # Ваша музыка
    # "2": "https://www.youtube.com/watch?v=другая_ссылка",
}

current_track = "1"

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

def get_audio_source(url):
    """Извлекает прямой аудио-URL из YouTube-ссылки с помощью yt-dlp."""
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info['url']

def play_music(voice_client, track_key):
    """Запускает трек с YouTube и ставит его на повтор."""
    global current_track
    current_track = track_key

    if not voice_client or not voice_client.is_connected():
        return
    try:
        url = TRACKS.get(track_key)
        if not url:
            print(f"Трек {track_key} не найден.")
            return

        # Получаем прямой URL аудиопотока
        audio_url = get_audio_source(url)

        # Опции FFmpeg для стабильного стриминга
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }

        source = discord.FFmpegPCMAudio(audio_url, **ffmpeg_options)
        voice_client.play(source, after=lambda e: play_music(voice_client, current_track))
        print(f"Играет трек {track_key} (YouTube)")
    except Exception as e:
        print(f"Ошибка при воспроизведении: {e}")

def get_voice_client():
    if client.voice_clients:
        return client.voice_clients[0]
    return None

@client.event
async def on_ready():
    print(f"Авторизован как {client.user}")
    print(f"Попытка подключиться к каналу {VOICE_CHANNEL_ID}...")

    try:
        channel = client.get_channel(VOICE_CHANNEL_ID)
        if not channel:
            print("Канал не найден. Проверьте ID и права доступа.")
            return

        if not client.voice_clients:
            voice = await channel.connect(self_mute=True, self_deaf=True)
            print(f"Успешно подключен к {channel.name} (в муте и с выключенными наушниками)")
            play_music(voice, current_track)
        else:
            print("Уже подключен к голосовому каналу.")
    except Exception as e:
        print(f"Ошибка при подключении: {e}")

@client.event
async def on_message(message):
    if message.author.id == client.user.id:
        return

    voice = get_voice_client()
    if not voice or not voice.channel:
        return

    # Реагируем только на текстовый чат того же канала, где сидит бот, или на личку
    if message.channel.id != voice.channel.id:
        if not isinstance(message.channel, discord.DMChannel):
            return

    content = message.content.strip().lower()

    if content.startswith("!play"):
        parts = content.split()
        if len(parts) < 2:
            await message.channel.send("Укажите номер трека: `!play 1`")
            return
        track_key = parts[1]
        if track_key not in TRACKS:
            await message.channel.send(f"Трек `{track_key}` не найден. Доступные: {', '.join(TRACKS.keys())}")
            return
        voice.stop()
        play_music(voice, track_key)
        await message.channel.send(f"Включаю трек {track_key} (YouTube).")

    elif content == "!stop":
        if voice.is_playing():
            voice.stop()
            await message.channel.send("Музыка остановлена.")
        else:
            await message.channel.send("Музыка и так не играет.")

    elif content == "!list":
        track_list = "\n".join([f"`{k}` — {v}" for k, v in TRACKS.items()])
        await message.channel.send(f"Доступные треки:\n{track_list}")

@client.event
async def on_voice_state_update(member, before, after):
    if member.id == client.user.id:
        if before.channel and not after.channel:
            print("Соединение потеряно. Переподключение через 5 секунд...")
            await asyncio.sleep(5)
            try:
                channel = client.get_channel(VOICE_CHANNEL_ID)
                if channel:
                    voice = await channel.connect(self_mute=True, self_deaf=True)
                    print("Переподключение успешно (в муте и с выключенными наушниками).")
                    play_music(voice, current_track)
            except Exception as e:
                print(f"Не удалось переподключиться: {e}")

client.run(TOKEN)
