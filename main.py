import os
import staypresent

# Health-check эндпоинт для Render
staypresent.web.json({"status": "running", "service": "tt11tt"})

# Самопинг публичного URL (ОБЯЗАТЕЛЬНО замените на ваш реальный адрес!)
staypresent.cron("https://tt11tt-1.onrender.com", interval=240)

# Запуск bot.py с автоматическим перезапуском при падении
staypresent.run(
    "bot.py",
    port=int(os.getenv("PORT", 8080)),
    restart_on_crash=True,
    max_restarts=10,
    restart_delay=3.0,
    restart_reset_after=120.0,
)
