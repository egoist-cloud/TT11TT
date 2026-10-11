FROM python:3.10-slim

# Устанавливаем FFmpeg и Rust (для сборки discord-native-voice)
RUN apt-get update && apt-get install -y ffmpeg curl && \
    curl https://sh.rustup.rs -sSf | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt

# Команда запуска (но это будет воркер, он не будет слушать порты)
CMD ["python", "bot.py"]
