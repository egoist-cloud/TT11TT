import os
import asyncio
import discord
import yt_dlp
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# Импортируем список треков из отдельного файла
from tracks import TRACKS

# --- Чтение переменных окружения ---
TOKEN = os.getenv("DISCORD_TOKEN")
VOICE_CHANNEL_ID = os.getenv("VOICE_CHANNEL_ID")

if not TOKEN or not VOICE_CHANNEL_ID:
    raise ValueError("Не заданы переменные окружения DISCORD_TOKEN или VOICE_CHANNEL_ID")

VOICE_CHANNEL_ID = int(VOICE_CHANNEL_ID)

# Текущий трек (по умолчанию первый)
current_track = "1"

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

def get_audio_source(url):
    """Извлекает прямой аудио-URL из YouTube-ссылки с помощью yt-dlp."""
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'cookiefile': 'youtube.com_cookies.txt',  # <-- Имя файла, как в репозитории
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

        audio_url = get_audio_source(url)

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
            # Без параметров self_mute и self_deaf — микрофон и наушники включены
            voice = await channel.connect()
            print(f"Успешно подключен к {channel.name}")
            play_music(voice, current_track)
        else:
            print("Уже подключен к голосовому каналу.")
    except Exception as e:
        print(f"Ошибка при подключении: {e}")

@client.event
async def on_message(message):
    # Игнорируем сообщения от самого себя
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

    # Команда !play <номер>
    if content.startswith("!play"):
        parts = content.split()
        if len(parts) < 2:
            msg = await message.channel.send("Укажите номер трека: `!play 1`")
            await msg.delete(delay=300)
            return
        track_key = parts[1]
        if track_key not in TRACKS:
            msg = await message.channel.send(f"Трек `{track_key}` не найден. Доступные: {', '.join(TRACKS.keys())}")
            await msg.delete(delay=300)
            return
        voice.stop()
        play_music(voice, track_key)
        msg = await message.channel.send(f"Включаю трек {track_key} (YouTube).")
        await msg.delete(delay=300)

    # Команда !stop
    elif content == "!stop":
        if voice.is_playing():
            voice.stop()
            msg = await message.channel.send("Музыка остановлена.")
            await msg.delete(delay=300)
        else:
            msg = await message.channel.send("Музыка и так не играет.")
            await msg.delete(delay=300)

    # Команда !list
    elif content == "!list":
        track_list = "\n".join([f"`{k}` — {v}" for k, v in TRACKS.items()])
        msg = await message.channel.send(f"Доступные треки:\n{track_list}")
        await msg.delete(delay=300)

@client.event
async def on_voice_state_update(member, before, after):
    # Если нас случайно отключили, пытаемся переподключиться
    if member.id == client.user.id:
        if before.channel and not after.channel:
            print("Соединение потеряно. Переподключение через 5 секунд...")
            await asyncio.sleep(5)
            try:
                channel = client.get_channel(VOICE_CHANNEL_ID)
                if channel:
                    voice = await channel.connect()
                    print("Переподключение успешно.")
                    play_music(voice, current_track)
            except Exception as e:
                print(f"Не удалось переподключиться: {e}")

# Запускаем Discord-клиента
client.run(TOKEN)
