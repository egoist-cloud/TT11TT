# ====================================================
# TT11TT — Discord боты (main + second + 2 наблюдателя)
# ====================================================
import os
import sys
import asyncio
import logging
import random
import time
import json
import aiohttp
import discord
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import traceback

# ====================================================
# ЛОГИРОВАНИЕ
# ====================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

# ====================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# ====================================================
TOKEN_MAIN = os.getenv("DISCORD_TOKEN_1")
TOKEN_SECOND = os.getenv("DISCORD_TOKEN_4")
MAIN_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "0"))

TOKEN_EGOIST = os.getenv("DISCORD_TOKEN_2")
USER_ID_EGOIST = int(os.getenv("USER_ID_1", "0"))

TOKEN_EGORIK = os.getenv("DISCORD_TOKEN_3")
USER_ID_EGORIK = int(os.getenv("USER_ID_2", "0"))

GUILD_ID = int(os.getenv("GUILD_ID", "0"))
MAIN_BOT_USER_ID = int(os.getenv("MAIN_BOT_USER_ID", "1241012523952508978"))
RENDER_URL = os.getenv("RENDER_URL", "https://tt11tt.onrender.com")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

log.info("=" * 50)
log.info("ЗАГРУЗКА ПЕРЕМЕННЫХ:")
log.info(f"  TOKEN_MAIN: {'есть' if TOKEN_MAIN else 'НЕТ'}")
log.info(f"  TOKEN_SECOND: {'есть' if TOKEN_SECOND else 'НЕТ'}")
log.info(f"  TOKEN_EGOIST: {'есть' if TOKEN_EGOIST else 'НЕТ'}")
log.info(f"  TOKEN_EGORIK: {'есть' if TOKEN_EGORIK else 'НЕТ'}")
log.info(f"  MAIN_CHANNEL_ID: {MAIN_CHANNEL_ID}")
log.info(f"  TELEGRAM: {'есть' if TELEGRAM_TOKEN else 'НЕТ'}")
log.info("=" * 50)

if not MAIN_CHANNEL_ID:
    raise ValueError("Не задан VOICE_CHANNEL_ID")

# ====================================================
# КОНСТАНТЫ
# ====================================================
STATUS_INTERVAL = 1800          # 30 минут
DEAD_SOCKET_TIMEOUT = 600       # 10 минут без событий = мёртвый сокет

GAMES_POOL = [
    {"name": "Counter-Strike 2", "app_id": 730},
    {"name": "Dota 2", "app_id": 570},
    {"name": "PUBG: BATTLEGROUNDS", "app_id": 578080},
    {"name": "Apex Legends", "app_id": 1172470},
    {"name": "Grand Theft Auto V", "app_id": 271590},
    {"name": "Rust", "app_id": 252490},
]

TEXT_STATUSES = [
    "Commback", "TALENT 24/7", "На страже",
    "Сижу в войсе", "zzZ", "Работаю",
]

# ====================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ====================================================
watcher_egoist = None
watcher_egorik = None
start_time = time.time()
reconnect_stats = {"main": 0, "second": 0, "egoist": 0, "egorik": 0}
reconnect_history = {
    "main": deque(maxlen=200),
    "second": deque(maxlen=200),
    "egoist": deque(maxlen=200),
    "egorik": deque(maxlen=200),
}
last_event_time = {
    "main": time.time(),
    "second": time.time(),
    "egoist": time.time(),
    "egorik": time.time(),
}
event_log = deque(maxlen=30)  # последние события
main_watchdogs_started = False
second_watchdogs_started = False

# ====================================================
# ВЕБ-СЕРВЕР (для Render health check)
# ====================================================
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
    log.info(f"Веб-сервер запущен на порту {port}")
    server.serve_forever()


threading.Thread(target=run_web_server, daemon=True).start()

# ====================================================
# УТИЛИТЫ
# ====================================================
def log_event(text: str):
    """Добавляет событие в ring-buffer."""
    ts = time.strftime("%H:%M:%S")
    event_log.append(f"[{ts}] {text}")
    log.info(text)


def log_reconnect(name: str):
    """Регистрирует реконнект."""
    reconnect_stats[name] += 1
    reconnect_history[name].append(time.time())
    log_event(f"🔄 {name}: реконнект #{reconnect_stats[name]}")


async def send_dm_notification(user_id, text):
    try:
        user = await main_client.fetch_user(user_id)
        if user:
            await user.send(text)
    except Exception as e:
        log.warning(f"Не удалось отправить уведомление: {e}")


async def send_telegram(text: str):
    """Отправляет сообщение в Telegram (если настроен)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            async with session.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
            }, timeout=10) as resp:
                if resp.status != 200:
                    log.warning(f"[TG] Ошибка: {resp.status}")
    except Exception as e:
        log.warning(f"[TG] Ошибка отправки: {e}")


async def alert_reconnect(name: str):
    """Алерт при 3+ реконнектах за 10 минут."""
    now = time.time()
    recent = sum(1 for ts in reconnect_history[name] if ts >= now - 600)
    if recent >= 3:
        msg = f"🚨 <b>Алерт!</b>\n{name}: {recent} реконнектов за 10 минут.\nПроверь токен и сеть."
        await send_telegram(msg)
        # Чтобы не спамить — маркер
        reconnect_history[name].append(now + 600)  # сдвигаем маркер


async def uptime_pinger():
    await asyncio.sleep(60)
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RENDER_URL, timeout=20) as resp:
                    log.info(f"[UPTIME] Пинг {RENDER_URL} → {resp.status}")
        except Exception as e:
            log.warning(f"[UPTIME] Ошибка пинга: {e}")
        await asyncio.sleep(14 * 60)


# ====================================================
# СМЕНА СТАТУСА
# ====================================================
async def status_changer(client, name):
    """Основные боты — 50/50 игры и текст, раз в 30 минут."""
    while True:
        try:
            if random.random() < 0.5:
                game = random.choice(GAMES_POOL)
                activity = discord.Activity(
                    type=discord.ActivityType.playing,
                    name=game["name"],
                    application_id=game["app_id"],
                    timestamps={'start': int(time.time() * 1000)},
                )
                log.info(f"[{name}] Статус: играет в {game['name']}")
            else:
                text = random.choice(TEXT_STATUSES)
                activity = discord.Game(name=text)
                log.info(f"[{name}] Статус: {text}")

            await client.change_presence(activity=activity)
        except Exception as e:
            log.warning(f"[{name}] Ошибка смены статуса: {e}")
        await asyncio.sleep(STATUS_INTERVAL)


async def watcher_status_changer(client, name):
    """Наблюдатели — 70% игры (CS2), 30% текст."""
    while True:
        try:
            if random.random() < 0.7:
                game = random.choice(GAMES_POOL)
                activity = discord.Activity(
                    type=discord.ActivityType.playing,
                    name=game["name"],
                    application_id=game["app_id"],
                    timestamps={'start': int(time.time() * 1000)},
                )
            else:
                activity = discord.Game(name=random.choice(TEXT_STATUSES))

            await client.change_presence(activity=activity)
        except Exception as e:
            log.warning(f"[{name}] Ошибка статуса: {e}")
        await asyncio.sleep(STATUS_INTERVAL)


# ====================================================
# ГРАФИКИ И СТАТИСТИКА
# ====================================================
def build_reconnect_chart(name: str, hours: int = 24) -> str:
    now = time.time()
    cutoff = now - hours * 3600
    buckets = [0] * hours
    for ts in reconnect_history[name]:
        if ts < cutoff:
            continue
        idx = int((ts - cutoff) / 3600)
        if 0 <= idx < hours:
            buckets[idx] += 1

    if max(buckets) == 0:
        return "▁" * hours

    chars = "▁▂▃▄▅▆▇█"
    max_val = max(buckets)
    return "".join(chars[int(b / max_val * (len(chars) - 1))] for b in buckets)


def get_latency_info() -> str:
    info = []
    pairs = [
        ("main", main_client),
        ("second", second_client if TOKEN_SECOND else None),
        ("egoist", watcher_egoist.client if watcher_egoist else None),
        ("egorik", watcher_egorik.client if watcher_egorik else None),
    ]
    for name, client in pairs:
        if client and client.latency:
            ms = int(client.latency * 1000)
            if ms < 100:
                q = "🟢"
            elif ms < 200:
                q = "🟡"
            elif ms < 400:
                q = "🟠"
            else:
                q = "🔴"
            info.append(f"`{name:8}` {ms}ms {q}")
    return "\n".join(info) if info else "нет данных"


# ====================================================
# ДЕЖУРНЫЙ (НАБЛЮДАТЕЛЬ)
# ====================================================
class Watcher:
    def __init__(self, token, user_id, name):
        self.token = token
        self.user_id = user_id
        self.name = name
        self.client = discord.Client()
        self.is_reconnecting = False
        self.manual_mode = False
        self.disabled = False       # полностью выключен
        self.auto_off_task = None
        self._watchdog_started = False
        self._status_started = False
        self._register_events()

    def _register_events(self):
        @self.client.event
        async def on_ready():
            log.info(f"[{self.name}] Авторизован как {self.client.user}")
            if not self._watchdog_started:
                self._watchdog_started = True
                self.client.loop.create_task(self.watchdog())
            if not self._status_started:
                self._status_started = True
                self.client.loop.create_task(
                    watcher_status_changer(self.client, self.name)
                )

        @self.client.event
        async def on_socket_event_type(event_type):
            last_event_time[self.name.lower()] = time.time()

        @self.client.event
        async def on_presence_update(before, after):
            if self.disabled or self.manual_mode:
                return
            if after.id != self.user_id:
                return
            if after.status == discord.Status.offline:
                log_event(f"[{self.name}] владелец ушёл в офлайн. Захожу через 30с.")
                await asyncio.sleep(30)
                if not self.disabled and not self.manual_mode:
                    await self.connect(MAIN_CHANNEL_ID)
            elif after.status != discord.Status.offline:
                log_event(f"[{self.name}] владелец вернулся. Выхожу.")
                await self.disconnect()

        @self.client.event
        async def on_voice_state_update(member, before, after):
            if self.disabled or not self.manual_mode:
                return
            if member.id != self.client.user.id:
                return
            if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
                log.warning(f"[{self.name}] Бот вылетел. Возвращаю...")
                log_reconnect(self.name.lower())
                await asyncio.sleep(5)
                if self.manual_mode and not self.disabled:
                    await self.connect(MAIN_CHANNEL_ID)

    async def watchdog(self):
        while True:
            await asyncio.sleep(10)
            if self.disabled or not self.manual_mode:
                continue
            if (not self.client.voice_clients
                    or not self.client.voice_clients[0].is_connected()
                    or self.client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
                log.warning(f"[{self.name}] Не в канале. Возвращаю...")
                log_reconnect(self.name.lower())
                await self.connect(MAIN_CHANNEL_ID)

    async def connect(self, channel_id):
        if self.is_reconnecting or self.disabled:
            return
        self.is_reconnecting = True
        try:
            channel = self.client.get_channel(channel_id)
            if not channel:
                return
            if (self.client.voice_clients
                    and self.client.voice_clients[0].is_connected()
                    and self.client.voice_clients[0].channel.id == channel_id):
                return
            if self.client.voice_clients:
                try:
                    await self.client.voice_clients[0].disconnect(force=True)
                except Exception:
                    pass
                await asyncio.sleep(1)
            await channel.connect(timeout=30.0, self_deaf=False, self_mute=False)
            log.info(f"[{self.name}] Подключен к {channel.name}")
        except Exception as e:
            log.warning(f"[{self.name}] Ошибка: {type(e).__name__}: {e}")
        finally:
            self.is_reconnecting = False

    async def disconnect(self):
        if self.client.voice_clients:
            try:
                await self.client.voice_clients[0].disconnect(force=True)
                log.info(f"[{self.name}] Отключен.")
            except Exception:
                pass

    async def schedule_auto_off(self, minutes: int):
        """Выйти через N минут."""
        if self.auto_off_task:
            self.auto_off_task.cancel()
        async def _off():
            await asyncio.sleep(minutes * 60)
            self.manual_mode = False
            await self.disconnect()
            log_event(f"[{self.name}] авто-выход по таймеру ({minutes} мин)")
        self.auto_off_task = asyncio.create_task(_off())

    async def start(self):
        while True:
            try:
                await self.client.start(self.token)
            except Exception as e:
                log.error(f"[{self.name}] Упал: {type(e).__name__}: {e}. "
                          f"Перезапуск через 30 сек.")
                await asyncio.sleep(30)


# ====================================================
# ОСНОВНОЙ БОТ (commback)
# ====================================================
main_client = discord.Client()
main_reconnecting = False


async def ensure_main_connected():
    global main_reconnecting
    if main_reconnecting:
        return
    main_reconnecting = True
    try:
        channel = main_client.get_channel(MAIN_CHANNEL_ID)
        if not channel:
            log.error(f"[MAIN] Канал {MAIN_CHANNEL_ID} не найден.")
            return
        if (main_client.voice_clients
                and main_client.voice_clients[0].is_connected()
                and main_client.voice_clients[0].channel.id == MAIN_CHANNEL_ID):
            return
        if main_client.voice_clients:
            try:
                await main_client.voice_clients[0].disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(1)
        for attempt in range(5):
            try:
                await channel.connect(timeout=30.0, self_deaf=False, self_mute=False)
                log.info(f"[MAIN] Подключен к {channel.name}")
                return
            except Exception as e:
                log.warning(f"[MAIN] Попытка {attempt + 1}: {type(e).__name__}: {e}")
                await asyncio.sleep(5)
    except Exception as e:
        log.error(f"[MAIN] Ошибка: {e}")
    finally:
        main_reconnecting = False


async def main_watchdog():
    while True:
        await asyncio.sleep(10)
        if (not main_client.voice_clients
                or not main_client.voice_clients[0].is_connected()
                or main_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log.warning("[MAIN] Бот не в канале. Возвращаю...")
            log_reconnect("main")
            await ensure_main_connected()


async def dead_socket_watchdog(client, name: str, reconnect_func):
    """Следит за 'мёртвым' сокетом — нет событий > 10 минут."""
    while True:
        await asyncio.sleep(60)
        silence = time.time() - last_event_time.get(name, time.time())
        if silence > DEAD_SOCKET_TIMEOUT:
            log.warning(f"[{name}] 🔴 Мёртвый сокет: {int(silence)}с без событий. "
                        f"Принудительный реконнект...")
            log_reconnect(name)
            if client.voice_clients:
                try:
                    await client.voice_clients[0].disconnect(force=True)
                except Exception:
                    pass
            await asyncio.sleep(2)
            await reconnect_func()


@main_client.event
async def on_ready():
    global main_watchdogs_started
    log.info(f"[MAIN] Авторизован как {main_client.user}")
    await ensure_main_connected()
    if not main_watchdogs_started:
        main_watchdogs_started = True
        main_client.loop.create_task(main_watchdog())
        main_client.loop.create_task(status_changer(main_client, "MAIN"))
        main_client.loop.create_task(uptime_pinger())
        main_client.loop.create_task(
            dead_socket_watchdog(main_client, "main", ensure_main_connected)
        )
        main_client.loop.create_task(daily_report_task())
        main_client.add_view(PanelView())  # восстановление панели
    await send_dm_notification(USER_ID_EGOIST, "✅ Бот запущен и работает.")
    await send_telegram("✅ <b>TT11TT запущен</b> на Render.")


@main_client.event
async def on_socket_event_type(event_type):
    last_event_time["main"] = time.time()


@main_client.event
async def on_voice_state_update(member, before, after):
    if member.id != main_client.user.id:
        return
    if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
        log.warning("[MAIN] Бот вылетел. Возвращаю...")
        log_reconnect("main")
        await send_dm_notification(USER_ID_EGOIST,
                                    "⚠️ Бот `commback` вылетел. Переподключаюсь...")
        await send_telegram("⚠️ <b>MAIN вылетел</b>. Переподключаюсь...")
        await asyncio.sleep(2)
        await ensure_main_connected()
        await alert_reconnect("main")


@main_client.event
async def on_message(message):
    if message.author.id == main_client.user.id:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return
    if message.author.id not in (USER_ID_EGOIST, USER_ID_EGORIK):
        return

    content = message.content.strip().lower()

    if content == "!ping":
        await message.channel.send("pong 🏓")
        return

    if content == "!status":
        uptime = int(time.time() - start_time)
        h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
        vc = main_client.voice_clients[0] if main_client.voice_clients else None
        in_channel = vc.channel.name if vc and vc.is_connected() else "нет"
        await message.channel.send(
            f"**Статус ботов**\n"
            f"Uptime: {h}ч {m}м {s}с\n"
            f"MAIN в канале: {in_channel}\n"
            f"Переподключений: main={reconnect_stats['main']}, "
            f"second={reconnect_stats['second']}, "
            f"egoist={reconnect_stats['egoist']}, "
            f"egorik={reconnect_stats['egorik']}"
        )
        return

    if content in ("!panel", "!menu", "!control", "панель", "меню"):
        await message.channel.send(embed=build_panel_embed(), view=PanelView())
        return

    if content == "!log":
        if not event_log:
            await message.channel.send("Событий пока нет.")
        else:
            await message.channel.send("**📜 Последние события:**\n```\n" +
                                        "\n".join(list(event_log)[-15:]) + "\n```")
        return

    # Fallback: текстовые команды
    parts = content.split()
    if len(parts) < 2:
        await message.channel.send(
            "Команды: `!panel`, `!ping`, `!status`, `!log`, "
            "`!on egoist`, `!off egoist`, `!on egorik`, `!off egorik`"
        )
        return

    command, target = parts[0], parts[1]
    if target == "egoist" and watcher_egoist:
        if command == "!on":
            watcher_egoist.disabled = False
            watcher_egoist.manual_mode = True
            await watcher_egoist.connect(MAIN_CHANNEL_ID)
            await message.channel.send("✅ Бот `egoist` включён.")
        elif command == "!off":
            watcher_egoist.manual_mode = False
            await watcher_egoist.disconnect()
            await message.channel.send("⛔ Бот `egoist` выключен.")
    elif target == "egorik" and watcher_egorik:
        if command == "!on":
            watcher_egorik.disabled = False
            watcher_egorik.manual_mode = True
            await watcher_egorik.connect(MAIN_CHANNEL_ID)
            await message.channel.send("✅ Бот `egorik` включён.")
        elif command == "!off":
            watcher_egorik.manual_mode = False
            await watcher_egorik.disconnect()
            await message.channel.send("⛔ Бот `egorik` выключен.")
    else:
        await message.channel.send("Укажите `egoist` или `egorik`.")


# ====================================================
# ВТОРОЙ БОТ (DISCORD_TOKEN_4)
# ====================================================
second_client = discord.Client()
second_reconnecting = False


async def ensure_second_connected():
    global second_reconnecting
    if second_reconnecting:
        return
    second_reconnecting = True
    try:
        channel = second_client.get_channel(MAIN_CHANNEL_ID)
        if not channel:
            return
        if (second_client.voice_clients
                and second_client.voice_clients[0].is_connected()
                and second_client.voice_clients[0].channel.id == MAIN_CHANNEL_ID):
            return
        if second_client.voice_clients:
            try:
                await second_client.voice_clients[0].disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(1)
        for attempt in range(5):
            try:
                await channel.connect(timeout=30.0, self_deaf=False, self_mute=False)
                log.info(f"[SECOND] Подключен к {channel.name}")
                return
            except Exception as e:
                log.warning(f"[SECOND] Попытка {attempt + 1}: {type(e).__name__}: {e}")
                await asyncio.sleep(5)
    finally:
        second_reconnecting = False


async def second_watchdog():
    while True:
        await asyncio.sleep(10)
        if (not second_client.voice_clients
                or not second_client.voice_clients[0].is_connected()
                or second_client.voice_clients[0].channel.id != MAIN_CHANNEL_ID):
            log.warning("[SECOND] Бот не в канале. Возвращаю...")
            log_reconnect("second")
            await ensure_second_connected()


@second_client.event
async def on_ready():
    global second_watchdogs_started
    log.info(f"[SECOND] Авторизован как {second_client.user}")
    await ensure_second_connected()
    if not second_watchdogs_started:
        second_watchdogs_started = True
        second_client.loop.create_task(second_watchdog())
        second_client.loop.create_task(status_changer(second_client, "SECOND"))
        second_client.loop.create_task(
            dead_socket_watchdog(second_client, "second", ensure_second_connected)
        )


@second_client.event
async def on_socket_event_type(event_type):
    last_event_time["second"] = time.time()


@second_client.event
async def on_voice_state_update(member, before, after):
    if member.id != second_client.user.id:
        return
    if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
        log.warning("[SECOND] Бот вылетел. Возвращаю...")
        log_reconnect("second")
        await asyncio.sleep(2)
        await ensure_second_connected()


# ====================================================
# ПАНЕЛЬ УПРАВЛЕНИЯ (EMBED + SELECT + BUTTONS)
# ====================================================
class ConfirmView(discord.ui.View):
    def __init__(self, action_callback, action_name: str):
        super().__init__(timeout=30)
        self.action_callback = action_callback
        self.action_name = action_name

    @discord.ui.button(label="✅ Да, подтверждаю", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            await self.action_callback()
            await interaction.edit_original_response(
                content=f"✅ Действие «{self.action_name}» выполнено.", view=None
            )
        except Exception as e:
            await interaction.edit_original_response(
                content=f"❌ Ошибка: {e}", view=None
            )
        self.stop()

    @discord.ui.button(label="❌ Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="❌ Действие отменено.", view=None
        )
        self.stop()


async def confirm_action(interaction: discord.Interaction, action_name: str, callback):
    view = ConfirmView(callback, action_name)
    await interaction.response.send_message(
        f"⚠️ **Подтверждение**\n"
        f"Вы собираетесь выполнить: **{action_name}**\n"
        f"Это критичное действие.\n\n"
        f"Подтвердите в течение 30 секунд.",
        view=view,
        ephemeral=True,
    )


class ActionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Включить egoist", value="egoist_on",
                                  description="Наблюдатель заходит в войс", emoji="✅"),
            discord.SelectOption(label="Выключить egoist", value="egoist_off",
                                  description="Наблюдатель выходит (режим авто)", emoji="⛔"),
            discord.SelectOption(label="Отключить egoist полностью", value="egoist_disable",
                                  description="Не реагирует ни на что", emoji="🔴"),
            discord.SelectOption(label="Включить egorik", value="egorik_on",
                                  description="Наблюдатель заходит в войс", emoji="✅"),
            discord.SelectOption(label="Выключить egorik", value="egorik_off",
                                  description="Наблюдатель выходит (режим авто)", emoji="⛔"),
            discord.SelectOption(label="Отключить egorik полностью", value="egorik_disable",
                                  description="Не реагирует ни на что", emoji="🔴"),
            discord.SelectOption(label="Включить ВСЕХ", value="all_on",
                                  description="Оба наблюдателя заходят", emoji="🟢"),
            discord.SelectOption(label="Выключить ВСЕХ", value="all_off",
                                  description="Оба наблюдателя выходят", emoji="🔴"),
            discord.SelectOption(label="📊 Статус", value="status",
                                  description="Uptime, пинг, реконнекты", emoji="📊"),
            discord.SelectOption(label="📈 График реконнектов", value="chart",
                                  description="За 24 часа", emoji="📈"),
            discord.SelectOption(label="📜 Последние события", value="log",
                                  description="Лог действий", emoji="📜"),
            discord.SelectOption(label="🎙 Кто в войсе", value="who",
                                  description="Список участников канала", emoji="🎙"),
        ]
        super().__init__(placeholder="Выберите действие",
                          min_values=1, max_values=1,
                          options=options, custom_id="control_select")

    async def callback(self, interaction: discord.Interaction):
        v = self.values[0]

        # ─── egoist ───
        if v == "egoist_on" and watcher_egoist:
            watcher_egoist.disabled = False
            watcher_egoist.manual_mode = True
            await watcher_egoist.connect(MAIN_CHANNEL_ID)
        elif v == "egoist_off" and watcher_egoist:
            watcher_egoist.manual_mode = False
            await watcher_egoist.disconnect()
        elif v == "egoist_disable" and watcher_egoist:
            watcher_egoist.disabled = True
            watcher_egoist.manual_mode = False
            await watcher_egoist.disconnect()

        # ─── egorik ───
        elif v == "egorik_on" and watcher_egorik:
            watcher_egorik.disabled = False
            watcher_egorik.manual_mode = True
            await watcher_egorik.connect(MAIN_CHANNEL_ID)
        elif v == "egorik_off" and watcher_egorik:
            watcher_egorik.manual_mode = False
            await watcher_egorik.disconnect()
        elif v == "egorik_disable" and watcher_egorik:
            watcher_egorik.disabled = True
            watcher_egorik.manual_mode = False
            await watcher_egorik.disconnect()

        # ─── Массовые (с 2FA) ───
        elif v == "all_on":
            async def _do_all_on():
                for w in (watcher_egoist, watcher_egorik):
                    if w:
                        w.disabled = False
                        w.manual_mode = True
                        await w.connect(MAIN_CHANNEL_ID)
            await confirm_action(interaction, "Включить ВСЕХ наблюдателей", _do_all_on)
            return
        elif v == "all_off":
            async def _do_all_off():
                for w in (watcher_egoist, watcher_egorik):
                    if w:
                        w.manual_mode = False
                        await w.disconnect()
            await confirm_action(interaction, "Выключить ВСЕХ наблюдателей", _do_all_off)
            return

        # ─── Статистика ───
        elif v == "status":
            uptime = int(time.time() - start_time)
            h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
            await interaction.response.send_message(
                f"**📊 Статус системы**\n"
                f"Uptime: `{h}ч {m}м {s}с`\n\n"
                f"**Реконнекты:**\n"
                f"`main   ` {reconnect_stats['main']}\n"
                f"`second ` {reconnect_stats['second']}\n"
                f"`egoist ` {reconnect_stats['egoist']}\n"
                f"`egorik ` {reconnect_stats['egorik']}\n\n"
                f"**📡 Пинг до Discord:**\n{get_latency_info()}",
                ephemeral=True,
            )
            return

        elif v == "chart":
            lines = ["**📈 Реконнекты за 24ч**\n"]
            for name in ["main", "second", "egoist", "egorik"]:
                chart = build_reconnect_chart(name, 24)
                lines.append(f"`{name:8}` {chart}  всего: {reconnect_stats[name]}")
            await interaction.response.send_message("\n".join(lines), ephemeral=True)
            return

        elif v == "log":
            if not event_log:
                await interaction.response.send_message("Событий нет.", ephemeral=True)
                return
            await interaction.response.send_message(
                "**📜 Последние события:**\n```\n" +
                "\n".join(list(event_log)[-15:]) + "\n```",
                ephemeral=True,
            )
            return

        elif v == "who":
            channel = main_client.get_channel(MAIN_CHANNEL_ID)
            if not channel:
                await interaction.response.send_message("Канал не найден.", ephemeral=True)
                return
            members = [m.display_name for m in channel.members]
            if not members:
                await interaction.response.send_message("Никого в войсе.", ephemeral=True)
                return
            await interaction.response.send_message(
                f"**🎙 В войсе ({len(members)}):**\n" +
                "\n".join(f"• {m}" for m in members),
                ephemeral=True,
            )
            return

        # Перерисовываем панель
        embed = build_panel_embed()
        await interaction.response.edit_message(embed=embed, view=self.view)


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ActionSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id in (USER_ID_EGOIST, USER_ID_EGORIK)

    @discord.ui.button(label="🔄 Обновить", style=discord.ButtonStyle.secondary,
                        custom_id="refresh", row=1)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_panel_embed(), view=self)

    @discord.ui.button(label="Render", style=discord.ButtonStyle.link,
                        url=os.getenv("RENDER_URL", "https://tt11tt.onrender.com"),
                        row=1)
    async def render_link(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass


def build_panel_embed() -> discord.Embed:
    def w_state(w):
        if not w:
            return "❌ не инициализирован"
        if w.disabled:
            return "🔴 отключён"
        if w.manual_mode:
            return "🟢 ручной (в войсе)"
        return "⚙️ авто"

    main_vc = main_client.voice_clients[0] if main_client.voice_clients else None
    main_in = "🟢 в войсе" if (main_vc and main_vc.is_connected()) else "🔴 нет"

    embed = discord.Embed(
        title="🎛 Панель управления наблюдателями",
        description=(
            "Управляйте твинками `egoist` и `egorik`.\n"
            "**Основные боты работают 24/7 автоматически.**"
        ),
        color=discord.Color.from_rgb(88, 101, 242),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="Фон:",
        value=(
            f"• Канал: <#{MAIN_CHANNEL_ID}>\n"
            f"• MAIN-бот: {main_in}\n"
            f"• Реконнектов MAIN: `{reconnect_stats['main']}`"
        ),
        inline=False,
    )

    embed.add_field(
        name="egoist:",
        value=(
            f"• {w_state(watcher_egoist)}\n"
            f"• Реконнектов — `{reconnect_stats['egoist']}`"
        ),
        inline=True,
    )

    embed.add_field(
        name="egorik:",
        value=(
            f"• {w_state(watcher_egorik)}\n"
            f"• Реконнектов — `{reconnect_stats['egorik']}`"
        ),
        inline=True,
    )

    embed.add_field(
        name="Режимы:",
        value=(
            "🟢 **Ручной** — сидит в войсе, игнорирует владельца.\n"
            "⚙️ **Авто** — ушёл владелец → заходит, вернулся → выходит.\n"
            "🔴 **Отключён** — не реагирует ни на что."
        ),
        inline=False,
    )

    uptime = int(time.time() - start_time)
    h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
    embed.set_footer(text=f"Uptime: {h}ч {m}м {s}с • TT11TT")
    return embed


# ====================================================
# ЕЖЕДНЕВНЫЙ ОТЧЁТ (только egoist)
# ====================================================
async def daily_report_task():
    """Раз в сутки шлёт отчёт по egoist в ЛС USER_ID_EGOIST."""
    # Ждём первые 24 часа
    await asyncio.sleep(24 * 3600)
    while True:
        try:
            now = time.time()
            cutoff = now - 24 * 3600
            day_reconnects = sum(1 for ts in reconnect_history["egoist"] if ts >= cutoff)

            uptime = int(time.time() - start_time)
            h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60

            vc = None
            if watcher_egoist and watcher_egoist.client.voice_clients:
                vc = watcher_egoist.client.voice_clients[0]
            in_vc = vc and vc.is_connected()

            mode = "отключён" if (watcher_egoist and watcher_egoist.disabled) else (
                "ручной" if (watcher_egoist and watcher_egoist.manual_mode) else "авто"
            )
            ping = int(watcher_egoist.client.latency * 1000) if watcher_egoist else 0

            report = (
                f"📅 **Ежедневный отчёт — egoist**\n\n"
                f"🕐 Uptime: `{h}ч {m}м {s}с`\n"
                f"🔄 Реконнектов за сутки: `{day_reconnects}`\n"
                f"📊 Всего реконнектов: `{reconnect_stats['egoist']}`\n"
                f"🎙 В войсе: `{'да' if in_vc else 'нет'}`\n"
                f"⚙️ Режим: `{mode}`\n"
                f"📡 Пинг: `{ping}ms`"
            )
            await send_dm_notification(USER_ID_EGOIST, report)
            await send_telegram(report)
            log_event("[REPORT] Ежедневный отчёт отправлен")
        except Exception as e:
            log.error(f"[REPORT] Ошибка: {e}")

        await asyncio.sleep(24 * 3600)


# ====================================================
# АВТОПЕРЕЗАПУСК
# ====================================================
async def safe_start(client, token, name):
    while True:
        try:
            await client.start(token)
        except Exception as e:
            log.error(f"[{name}] Упал: {type(e).__name__}: {e}. Перезапуск через 30 сек.")
            traceback.print_exc()
            await asyncio.sleep(30)


# ====================================================
# ЗАПУСК
# ====================================================
async def main():
    global watcher_egoist, watcher_egorik

    log.info("=" * 50)
    log.info("ЗАПУСК БОТОВ...")
    log.info("=" * 50)

    watcher_egoist = Watcher(TOKEN_EGOIST, USER_ID_EGOIST, "Egoist")
    watcher_egorik = Watcher(TOKEN_EGORIK, USER_ID_EGORIK, "Egorik")

    tasks = [
        safe_start(main_client, TOKEN_MAIN, "MAIN"),
        watcher_egoist.start(),
        watcher_egorik.start(),
    ]

    if TOKEN_SECOND:
        tasks.append(safe_start(second_client, TOKEN_SECOND, "SECOND"))

    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        log.error(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        log.error(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
        traceback.print_exc()
