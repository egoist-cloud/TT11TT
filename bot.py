@client.event
async def on_message(message):
    # Игнорируем сообщения от самого себя
    if message.author.id == client.user.id:
        return

    # Проверяем, что сообщение пришло из текстового чата того же канала,
    # где сидит бот (если у канала есть текстовый чат)
    voice = get_voice_client()
    if not voice or not voice.channel:
        return

    # Если у голосового канала есть текстовый чат, его ID совпадает с ID канала
    # В discord.py у голосового канала может быть атрибут .text_channel или проверка по ID
    if message.channel.id != voice.channel.id:
        # Разрешаем также команды в личке бота
        if not isinstance(message.channel, discord.DMChannel):
            return

    content = message.content.strip().lower()

    # Команда !play <номер>
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
        await message.channel.send(f"Включаю трек {track_key}.")

    # Команда !stop
    elif content == "!stop":
        if voice.is_playing():
            voice.stop()
            await message.channel.send("Музыка остановлена.")
        else:
            await message.channel.send("Музыка и так не играет.")

    # Команда !list
    elif content == "!list":
        track_list = "\n".join([f"`{k}` — {v}" for k, v in TRACKS.items()])
        await message.channel.send(f"Доступные треки:\n{track_list}")
