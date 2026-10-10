import os
import asyncio
import discord

# Получаем токен и ID канала из переменных окружения Render
TOKEN = os.getenv("DISCORD_TOKEN")
VOICE_CHANNEL_ID = os.getenv("VOICE_CHANNEL_ID")

if not TOKEN or not VOICE_CHANNEL_ID:
    raise ValueError("Не заданы переменные окружения DISCORD_TOKEN или VOICE_CHANNEL_ID")

# ID канала преобразуем в число
VOICE_CHANNEL_ID = int(VOICE_CHANNEL_ID)

# Создаем клиента с нужными разрешениями (intents)
client = discord.Client()

@client.event
async def on_ready():
    print(f"Авторизован как {client.user}")
    print(f"Попытка подключиться к каналу {VOICE_CHANNEL_ID}...")

    try:
        # Получаем канал по ID
        channel = client.get_channel(VOICE_CHANNEL_ID)
        if not channel:
            print("Канал не найден. Проверьте ID и права доступа.")
            return

        # Подключаемся к голосовому каналу
        if not client.voice_clients:
            await channel.connect()
            print(f"Успешно подключен к {channel.name}")
        else:
            print("Уже подключен к голосовому каналу.")
    except Exception as e:
        print(f"Ошибка при подключении: {e}")

@client.event
async def on_voice_state_update(member, before, after):
    # Если нас случайно отключили, пытаемся переподключиться
    if member.id == client.user.id:
        if before.channel and not after.channel:
            print("Соединение потеряно. Переподключение...")
            await asyncio.sleep(5)
            try:
                channel = client.get_channel(VOICE_CHANNEL_ID)
                if channel:
                    await channel.connect()
                    print("Переподключение успешно.")
            except Exception as e:
                print(f"Не удалось переподключиться: {e}")

# Запускаем клиента
client.run(TOKEN)