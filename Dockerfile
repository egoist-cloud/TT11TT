FROM python:3.10-slim

# Устанавливаем FFmpeg и Rust (нужен для сборки discord-native-voice)
RUN apt-get update && apt-get install -y ffmpeg curl && \
    curl https://sh.rustup.rs -sSf | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app
COPY . .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Открываем порт для Render
EXPOSE 10000

CMD ["python", "bot.py"]
