import os
import staypresent

# Предоставляем health-check эндпоинт для Render
staypresent.web.json({"status": "running", "service": "tt11tt"})

# Ключевой момент: пингуем публичный URL для предотвращения засыпания (обязательно указываем реальный адрес onrender.com)
staypresent.cron("https://tt11tt-1.onrender.com", interval=240)

# Запускаем ваш bot.py с автоматическим перезапуском при сбое
staypresent.run(
    "bot.py",
    port=int(os.getenv("PORT", 8080)),
    restart_on_crash=True,
    max_restarts=5,
    restart_delay=3.0,
    restart_reset_after=90.0,
)
