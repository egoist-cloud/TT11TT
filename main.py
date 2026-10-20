import os
import staypresent

# 1. Создаем веб-сервер, чтобы Render видел, что приложение работает
staypresent.web.json({"status": "running", "service": "tt11tt"})

# 2. Пингуем сами себя каждые 4 минуты, чтобы Render не засыпал
# ВАЖНО: Замените ссылку ниже на ту, что написана у вас вверху страницы Render (Available at your primary URL)
staypresent.cron("https://tt11tt-1.onrender.com", interval=240)

# 3. Запускаем bot.py. Если бот упадет, staypresent перезапустит его
staypresent.run(
    "bot.py",
    port=int(os.getenv("PORT", 8080)),
    restart_on_crash=True,
    max_restarts=10,
    restart_delay=3.0,
)
