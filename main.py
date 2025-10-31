import os
import time
import logging
import asyncio
import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Load environment variables from .env file
load_dotenv()

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
EXCHANGE_KEY = os.getenv('EXCHANGE_API_KEY')
WEATHER_KEY = os.getenv('WEATHER_API_KEY')

# Проверка переменных окружения
if not TOKEN or not EXCHANGE_KEY or not WEATHER_KEY:
    raise RuntimeError("Ошибка: отсутствуют переменные окружения в .env")

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

CACHE_TTL = 120  # seconds
_cache = {'rates': None, 'rates_time': 0, 'weather': None, 'weather_time': 0}
_cache_lock = asyncio.Lock()


async def _send_text(update: Update, text: str, **kwargs) -> None:
    if update.message:
        await update.message.reply_text(text, **kwargs)
    elif update.effective_chat:
        await update.effective_chat.send_message(text, **kwargs)
    else:
        logging.warning("Не удалось отправить сообщение: отсутствует чат")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_text(
        update,
        "Привет, я Стамбульский Помощник! 🕌\n"
        "💱 /currency — курс валют\n"
        "🌤 /weather — погода в Стамбуле"
    )
    
async def get_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    async with _cache_lock:
        cached_rates = _cache['rates']
        cached_time = _cache['rates_time']

    if cached_rates and now - cached_time < CACHE_TTL:
        rates = cached_rates
        rates_time = cached_time
    else:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                usd = await client.get(f"https://v6.exchangerate-api.com/v6/{EXCHANGE_KEY}/latest/USD")
                eur = await client.get(f"https://v6.exchangerate-api.com/v6/{EXCHANGE_KEY}/latest/EUR")
                lira = await client.get(f"https://v6.exchangerate-api.com/v6/{EXCHANGE_KEY}/latest/TRY")
                usd.raise_for_status()
                eur.raise_for_status()
                lira.raise_for_status()

            usd_try = usd.json()['conversion_rates']['TRY']
            eur_try = eur.json()['conversion_rates']['TRY']
            try_rub = lira.json()['conversion_rates']['RUB']

            rates = {'usd_try': usd_try, 'eur_try': eur_try, 'try_rub': try_rub}
            rates_time = time.time()
            async with _cache_lock:
                _cache['rates'] = rates
                _cache['rates_time'] = rates_time
        except Exception as e:
            logging.exception("Ошибка получения курса валют: %s", e)
            await _send_text(update, "Не удалось получить курс валют. Попробуй позже.")
            return

    formatted_time = time.strftime('%H:%M:%S', time.gmtime(rates_time))
    message = (f"💱 Курсы валют (обновлено {formatted_time} UTC):\n"
               f"1 USD = {rates['usd_try']:.2f} TRY\n"
               f"1 EUR = {rates['eur_try']:.2f} TRY\n"
               f"1 TRY = {rates['try_rub']:.2f} RUB")
    await _send_text(update, message)

async def get_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    async with _cache_lock:
        cached_weather = _cache['weather']
        cached_time = _cache['weather_time']

    if cached_weather and now - cached_time < CACHE_TTL:
        data = cached_weather
    else:
        try:
            lat, lon = 41.0082, 28.9784  # Istanbul coordinates
            url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_KEY}&units=metric&lang=ru"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            data = resp.json()
            async with _cache_lock:
                _cache['weather'] = data
                _cache['weather_time'] = time.time()
        except Exception as e:
            logging.exception("Ошибка получения погоды: %s", e)
            await _send_text(update, "Не удалось получить погоду. Попробуй позже.")
            return

    temp = data['main']['temp']
    feels = data['main']['feels_like']
    desc = data['weather'][0]['description']
    await _send_text(
        update,
        f"🌤 Погода в Стамбуле:\n"
        f"Температура: {temp:.1f}°C\n"
        f"Ощущается как: {feels:.1f}°C\n"
        f"{desc.capitalize()}"
    )

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message and update.effective_message.text:
        await _send_text(update, update.effective_message.text)
    else:
        logging.warning("Эхо-команда вызвана без текстового сообщения")

if __name__ == '__main__':
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('currency', get_currency))
    app.add_handler(CommandHandler('weather', get_weather))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    print("Бот запущен. Нажмите Ctrl+C для выхода.")
    app.run_polling()
