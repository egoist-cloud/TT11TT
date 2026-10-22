"""
TT11TT - точка входа на Render.

Запускает:
  1) HTTP-сервер (нужен, чтобы Render считал сервис живым) + /status + /health
  2) bot.py как дочерний процесс с авто-рестартом при падении
  3) пинг самого себя, чтобы free-план не засыпал
"""
import os
import sys
import logging

import staypresent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("tt11tt.main")

def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("%s='%s' не число - беру %s", name, raw, default)
        return default

PORT = _env_int("PORT", 8080)
HOST = os.getenv("HOST", "0.0.0.0")

def _detect_render_url() -> str:
    for key in ("RENDER_URL", "RENDER_EXTERNAL_URL"):
        value = (os.getenv(key) or "").strip().rstrip("/")
        if value:
            return value
    host = (os.getenv("RENDER_EXTERNAL_HOSTNAME") or "").strip().strip("/")
    if host:
        return "https://" + host
    return ""

RENDER_URL = _detect_render_url()
PING_INTERVAL = _env_int("PING_INTERVAL", 240)
HEARTBEAT_TIMEOUT = _env_int("HEARTBEAT_TIMEOUT", 180)
MAX_RESTARTS = _env_int("MAX_RESTARTS", 50)

log.info("=" * 50)
log.info("TT11TT стартует | port=%s host=%s | render_url=%s", PORT, HOST, RENDER_URL or "НЕ ЗАДАН")
log.info("=" * 50)

staypresent.web.json({"status": "running", "service": "tt11tt", "render_url": RENDER_URL or None})
staypresent.web.json({"status": "ok"}, path="/health")
staypresent.web.status(title="TT11TT - Status", description="Discord 24/7 bots")

if RENDER_URL:
    staypresent.cron(RENDER_URL, interval=PING_INTERVAL)
else:
    log.warning("URL сервиса не определён. Используй Render External URL. Free-инстанс может уснуть.")

staypresent.run(
    "bot.py",
    host=HOST,
    port=PORT,
    restart_on_crash=True,
    max_restarts=MAX_RESTARTS,
    restart_delay=3.0,
    heartbeat_timeout=HEARTBEAT_TIMEOUT,
    services_name="TT11TT Discord Bots",
    services_description="Боты 24/7 в голосовом канале",
)
