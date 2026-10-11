import os
import asyncio
import discord
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# --- Токены трёх аккаунтов ---
TOKENS = [
    os.getenv("DISCORD_TOKEN_1"),
    os.getenv("DISCORD_TOKEN_2"),
    os.getenv("DISCORD_TOKEN_3"),
]
TOKENS = [t for t in TOKENS if t]

if not TOKENS:
    raise ValueError("Не задан ни один токен (DISCORD_TOKEN_1/2/3)")

# --- Канал, в котором сидят все ---
VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "1558455255791050803"))

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

async def run_account(token, name):
    client = discord.Client()
    is_reconnecting = False

    async def connect():
        nonlocal is_reconnecting
        if is_reconnecting:
            return
        is_reconnecting = True
        try:
            channel = client.get_channel(VOICE_CHANNEL_ID)
            if not channel:
                print(f"[{name}] Канал {VOICE_CHANNEL_ID} не найден.")
                return
            if client.voice_clients:
                try:
                    await client.voice_clients[0].disconnect(force=True)
                except Exception:
                    pass
                await asyncio.sleep(2)
            await channel.connect(self_mute=True, self_deaf=True)
            print(f"[{name}] Подключен к {channel.name} (в полном муте)")
        except Exception as e:
            print(f"[{name}] Ошибка подключения: {e}")
        finally:
            is_reconnecting = False

    @client.event
    async def on_ready():
        print(f"[{name}] Авторизован как {client.user}")
        await connect()

    @client.event
    async def on_voice_state_update(member, before, after):
        if member.id != client.user.id:
            return
        # Если бота кикнули — переподключаемся
        if before.channel and not after.channel:
            print(f"[{name}] Кикнули. Переподключаюсь через 5 секунд...")
            await asyncio.sleep(5)
            await connect()
        # Если бота перекинули — остаёмся в новом канале
        elif before.channel and after.channel and before.channel != after.channel:
            print(f"[{name}] Перекинули в {after.channel.name}. Остаюсь здесь.")

    await client.start(token)

async def main():
    tasks = []
    for i, token in enumerate(TOKENS, start=1):
        tasks.append(run_account(token, f"Account-{i}"))
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
