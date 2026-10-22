# ====================================================
# TT11TT — Discord боты (объединенный main.py)
# ====================================================
import os
import sys
import asyncio
import logging
import random
import time
import string
import traceback
from collections import deque
from datetime import datetime, timezone

import aiohttp
import discord
from aiohttp import web

# ====================================================
# ЛОГИРОВАНИЕ
# ====================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger(__name__)

# ====================================================
# ENV
# ====================================================
def get_env_int(name, default=0):
    val = os.getenv(name)
    if not val: return default
    try: return int(val.strip())
    except ValueError:
        log.error("Переменная %s должна быть числом, а не '%s'!", name, val)
        return default

def get_env_str(name):
    val = os.getenv(name)
    return val.strip() if val else None

def get_env_bool(name, default=False):
    val = get_env_str(name)
    if val is None: return default
    return val.lower() in ("1", "true", "yes", "on")

def get_env_id_list(name):
    raw = get_env_str(name)
    if not raw: return []
    out = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            try: out.append(int(part))
            except ValueError: log.error("В %s не-число: %s", name, part)
    return out

TOKEN_EGOIST = get_env_str("DISCORD_TOKEN_1")
TOKEN_EGORIK = get_env_str("DISCORD_TOKEN_2")
TOKEN_MAIN = get_env_str("DISCORD_TOKEN_3")
TOKEN_SECOND = get_env_str("DISCORD_TOKEN_4")
TOKEN_THIRD = get_env_str("DISCORD_TOKEN_5")
TOKEN_RESERVE = get_env_str("DISCORD_TOKEN_6")

MAIN_CHANNEL_ID = get_env_int("VOICE_CHANNEL_ID")
GUILD_ID = get_env_int("GUILD_ID")
PORT = get_env_int("PORT", 10000)

ALLOW_COMMANDS_IN_GUILD = get_env_bool("ALLOW_COMMANDS_IN_GUILD", True)
ALLOWED_COMMAND_USERS = {1113899288192356402, 1416125087324831815} | set(
    get_env_id_list("ALLOWED_USER_IDS") + get_env_id_list("OWNER_IDS")
)

# ====================================================
# УТИЛИТЫ
# ====================================================
def fmt_uptime():
    u = int(time.time() - start_time)
    return f"{u // 3600}ч {(u % 3600) // 60}м {u % 60}с"

def _voice_supported():
    try:
        from discord.voice_client import has_nacl
        return bool(has_nacl)
    except Exception:
        return False

def _is_in_voice(client, channel_id):
    if not client.voice_clients: return False
    vc = client.voice_clients[0]
    return vc.is_connected() and vc.channel and vc.channel.id == channel_id

# ====================================================
# СОСТОЯНИЕ
# ====================================================
start_time = time.time()
_shutdown_event = asyncio.Event()
_handled_msg_ids = set()
_handled_msg_order = deque(maxlen=2000)

# ====================================================
# HTTP-СЕРВЕР
# ====================================================
async def _http_health(request):
    return web.json_response({"status": "ok", "uptime": fmt_uptime()})

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", _http_health)
    app.router.add_get("/health", _http_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("HTTP-сервер запущен на 0.0.0.0:%d", PORT)

# ====================================================
# WATCHER (Дежурные)
# ====================================================
class Watcher:
    def __init__(self, token, name):
        self.token = token
        self.name = name
        self.key = name.lower()
        self.client = discord.Client(chunk_guilds_at_startup=False)
        self.manual_mode = False
        self.disabled = True
        self._connect_lock = asyncio.Lock()
        self._watchdog_task = None
        self._register_events()

    def _register_events(self):
        @self.client.event
        async def on_ready():
            log.info("[%s] авторизован как %s", self.name, self.client.user)
            self._start_watchdog()

        @self.client.event
        async def on_voice_state_update(member, before, after):
            if GUILD_ID and member.guild.id != GUILD_ID: return
            if member.id != self.client.user.id: return
            if before.channel and after.channel and before.channel.id == after.channel.id: return
            if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
                log.info("[%s] вылетел из канала, переподключение...", self.name)
                await asyncio.sleep(5)
                if self.manual_mode and not self.disabled:
                    await self.connect(MAIN_CHANNEL_ID)

    def _start_watchdog(self):
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = self.client.loop.create_task(self._watchdog())

    async def _watchdog(self):
        while not _shutdown_event.is_set():
            await asyncio.sleep(30)
            if self.manual_mode and not self.disabled and self.client.is_ready():
                if not _is_in_voice(self.client, MAIN_CHANNEL_ID):
                    log.info("[%s] watchdog: не в голосе, переподключение", self.name)
                    await self.connect(MAIN_CHANNEL_ID)

    async def connect(self, channel_id):
        async with self._connect_lock:
            if self.disabled or channel_id == 0: return False, "Отключен"
            if not _voice_supported(): return False, "Нет PyNaCl"
            try:
                channel = self.client.get_channel(channel_id) or await self.client.fetch_channel(channel_id)
                if not channel: return False, "Канал не найден"
                if _is_in_voice(self.client, channel_id): return True, "Уже в канале"
                if self.client.voice_clients:
                    await self.client.voice_clients[0].disconnect(force=True)
                    await asyncio.sleep(2)
                await channel.connect(timeout=60.0, self_deaf=False, self_mute=False)
                log.info("[%s] подключен к %s", self.name, channel.name)
                return True, "Успешно"
            except Exception as e:
                return False, str(e)

    async def disconnect(self):
        if self.client.voice_clients:
            try: await self.client.voice_clients[0].disconnect(force=True)
            except Exception: pass

    async def start(self):
        while not _shutdown_event.is_set():
            try:
                await self.client.start(self.token)
            except Exception as e:
                log.error("[%s] ошибка старта: %s, рестарт через 30с", self.name, e)
                await asyncio.sleep(30)

# ====================================================
# ГЛОБАЛЬНЫЕ КЛИЕНТЫ И WATCHER'Ы
# ====================================================
watcher_egoist = None
watcher_egorik = None
main_client = discord.Client(chunk_guilds_at_startup=False)
second_client = discord.Client(chunk_guilds_at_startup=False)
third_client = discord.Client(chunk_guilds_at_startup=False)
reserve_client = discord.Client(chunk_guilds_at_startup=False) if TOKEN_RESERVE else None

# ====================================================
# 24/7 ЛОГИКА
# ====================================================
async def _ensure_247_connected(client, name):
    if not MAIN_CHANNEL_ID or not client.is_ready(): return
    if _is_in_voice(client, MAIN_CHANNEL_ID): return
    try:
        channel = client.get_channel(MAIN_CHANNEL_ID)
        if not channel: return
        if client.voice_clients:
            await client.voice_clients[0].disconnect(force=True)
            await asyncio.sleep(2)
        await channel.connect(timeout=60.0, self_deaf=True, self_mute=True)
        log.info("[%s] подключен к %s (В МУТЕ)", name.upper(), channel.name)
    except Exception as e:
        log.warning("[%s] ошибка подключения: %s", name.upper(), e)

def _attach_247_events(name, client):
    @client.event
    async def on_ready():
        log.info("[%s] авторизован как %s", name.upper(), client.user)
        await _ensure_247_connected(client, name)
        client.loop.create_task(_watchdog_247(client, name))

    @client.event
    async def on_voice_state_update(member, before, after):
        if GUILD_ID and member.guild.id != GUILD_ID: return
        if member.id != client.user.id: return
        if before.channel and after.channel and before.channel.id == after.channel.id: return
        if not after.channel or after.channel.id != MAIN_CHANNEL_ID:
            log.info("[%s] вылетел из канала, переподключение...", name.upper())
            await asyncio.sleep(5)
            await _ensure_247_connected(client, name)

async def _watchdog_247(client, name):
    while not _shutdown_event.is_set():
        await asyncio.sleep(60)
        if client.is_ready() and not _is_in_voice(client, MAIN_CHANNEL_ID):
            await _ensure_247_connected(client, name)

# ====================================================
# ОБРАБОТКА КОМАНД
# ====================================================
def _is_allowed_user(message):
    return message.author.id in ALLOWED_COMMAND_USERS

def _is_command_channel(message):
    if not message.guild: return True
    return ALLOW_COMMANDS_IN_GUILD

def _should_defer_to_watcher(bound_client):
    """24/7-боты уступают, ТОЛЬКО если дежурный РЕАЛЬНО в голосе."""
    if bound_client in (main_client, second_client, third_client, reserve_client):
        for w in (watcher_egoist, watcher_egorik):
            if w and w.client.is_ready() and _is_in_voice(w.client, MAIN_CHANNEL_ID):
                return True
    return False

async def _set_watcher_enabled(w, enabled):
    if enabled:
        if not MAIN_CHANNEL_ID: return False, "не задан VOICE_CHANNEL_ID"
        if not w.client.is_ready(): return False, "ещё не авторизован"
        w.disabled = False
        w.manual_mode = True
        ok, msg = await w.connect(MAIN_CHANNEL_ID)
        if not ok:
            w.manual_mode = False
            w.disabled = True
        return ok, msg
    w.manual_mode = False
    w.disabled = True
    await w.disconnect()
    return True, "выключен"

def _on_msg(bound_client):
    async def on_message(message):
        if bound_client.user is None or message.author.id == bound_client.user.id:
            return
        content = (message.content or "").strip()
        if not content.startswith("!"): return
        if not _is_command_channel(message) or not _is_allowed_user(message):
            return
        if _should_defer_to_watcher(bound_client):
            return
        if message.id in _handled_msg_ids:
            return
        _handled_msg_ids.add(message.id)
        if len(_handled_msg_ids) > 2000:
            _handled_msg_ids.pop()

        parts = content.lower().split()
        cmd = parts[0]

        if len(parts) >= 2 and parts[1] in ("egoist", "egorik"):
            target = parts[1]
            w = watcher_egoist if target == "egoist" else watcher_egorik
            if not w:
                await message.channel.send(f"⚠️ Дежурный `{target}` не настроен.")
                return
            enabled = cmd in ("!on", "!вкл")
            ok, msg = await _set_watcher_enabled(w, enabled)
            if enabled:
                await message.channel.send(f"✅ {target} вкл ({msg})." if ok else f"❌ Ошибка: {msg}")
            else:
                await message.channel.send(f"⚙️ {target} {msg}." if ok else f"❌ Ошибка: {msg}")
            return

        if cmd == "!all" and len(parts) >= 2:
            enabled = parts[1] == "on"
            results = []
            for w in (watcher_egoist, watcher_egorik):
                if not w: results.append("• нет токена"); continue
                ok, msg = await _set_watcher_enabled(w, enabled)
                results.append(f"• {w.key}: {'✅' if ok else '❌'} {msg}")
            await message.channel.send(("✅ Все включены.\n" if enabled else "⛔ Все выключены.\n") + "\n".join(results))
            return

        if cmd == "!panel":
            lines = [f"📊 **Панель** | Uptime: {fmt_uptime()}"]
            for name, c in (("MAIN", main_client), ("SECOND", second_client), ("THIRD", third_client)):
                lines.append(f"• {name}: {'🟢' if c.is_ready() else '🔴'}")
            if reserve_client: lines.append(f"• RESERVE: {'🟢' if reserve_client.is_ready() else '🔴'}")
            for w in (watcher_egoist, watcher_egorik):
                if w: lines.append(f"• {w.name}: {'🟢' if w.client.is_ready() else '🔴'} (voice={_is_in_voice(w.client, MAIN_CHANNEL_ID)})")
            await message.channel.send("\n".join(lines)); return

        if cmd == "!ping":
            lines = ["🏓 Ping"]
            for name, c in (("MAIN", main_client), ("SECOND", second_client), ("THIRD", third_client)):
                if c.is_ready(): lines.append(f"• {name}: {c.latency*1000:.0f}ms")
            await message.channel.send("\n".join(lines)); return

        if cmd == "!help":
            await message.channel.send("`!on/!off egoist|egorik`\n`!all on|off`\n`!panel`, `!ping`")
            return
    return on_message

# ====================================================
# ЗАПУСК
# ====================================================
async def main():
    global watcher_egoist, watcher_egorik
    log.info("="*50)
    log.info("TT11TT запуск | VOICE_CHANNEL_ID=%s | GUILD_ID=%s", MAIN_CHANNEL_ID, GUILD_ID)
    log.info("Голос (PyNaCl): %s", "ОК" if _voice_supported() else "НЕТ!")
    log.info("="*50)

    watcher_egoist = Watcher(TOKEN_EGOIST, "Egoist") if TOKEN_EGOIST else None
    watcher_egorik = Watcher(TOKEN_EGORIK, "Egorik") if TOKEN_EGORIK else None

    if watcher_egoist: watcher_egoist.client.event(_on_msg(watcher_egoist.client))
    if watcher_egorik: watcher_egorik.client.event(_on_msg(watcher_egorik.client))

    main_client.event(_on_msg(main_client))
    second_client.event(_on_msg(second_client))
    third_client.event(_on_msg(third_client))
    if reserve_client: reserve_client.event(_on_msg(reserve_client))

    _attach_247_events("main", main_client)
    _attach_247_events("second", second_client)
    _attach_247_events("third", third_client)
    if reserve_client: _attach_247_events("reserve", reserve_client)

    tasks = [start_web_server()]
    if TOKEN_MAIN: tasks.append(main_client.start(TOKEN_MAIN))
    if TOKEN_SECOND: tasks.append(second_client.start(TOKEN_SECOND))
    if TOKEN_THIRD: tasks.append(third_client.start(TOKEN_THIRD))
    if TOKEN_RESERVE and reserve_client: tasks.append(reserve_client.start(TOKEN_RESERVE))
    if watcher_egoist: tasks.append(watcher_egoist.start())
    if watcher_egorik: tasks.append(watcher_egorik.start())

    await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
