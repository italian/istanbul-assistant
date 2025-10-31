import os
import time
import logging
import asyncio
import httpx
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
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
_cache = {'rates': None, 'rates_time': 0, 'weather': {}}
_cache_lock = asyncio.Lock()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=("Привет, я Стамбульский Помощник! 🕌\n"
              "💱 /currency — курс валют\n"
              "🌤 /weather — погода в Стамбуле")
        )
    
async def get_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with _cache_lock:
        if _cache['rates'] and time.time() - _cache['rates_time'] < CACHE_TTL:
            rates = _cache['rates']
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
                _cache['rates'] = rates
                _cache['rates_time'] = time.time()
            except Exception as e:
                logging.exception("Ошибка получения курса валют: %s", e)
                await update.message.reply_text("Не удалось получить курс валют. Попробуй позже.")
                return
            
    message = (f"💱 Курсы валют (обновленно {time.strftime('%H:%M:%S', time.gmtime())} UTC):\n"
               f"1 USD = {rates['usd_try']:.2f} TRY\n"
               f"1 EUR = {rates['eur_try']:.2f} TRY\n"
               f"1 TRY = {rates['try_rub']:.2f} RUB")
    await update.message.reply_text(message)

def _format_weather_message(data: dict) -> str:
    location_name = data.get('name') or 'вашего местоположения'
    temp = data['main']['temp']
    feels = data['main']['feels_like']
    desc = data['weather'][0]['description']
    return (
        f"🌍 Погода для {location_name}:\n"
        f"🌡 Температура: {temp:.1f}°C\n"
        f"🤗 Ощущается как: {feels:.1f}°C\n"
        f"☁️ {desc.capitalize()}"
    )


async def _fetch_weather(lat: float, lon: float) -> dict:
    now = time.time()
    async with _cache_lock:
        stale_keys = [coords for coords, payload in _cache['weather'].items() if now - payload['time'] >= CACHE_TTL]
        for key in stale_keys:
            del _cache['weather'][key]

        cached_entry = _cache['weather'].get((lat, lon))
        if cached_entry and now - cached_entry['time'] < CACHE_TTL:
            return cached_entry['data']

    try:
        url = (
            "https://api.openweathermap.org/data/2.5/weather"
            f"?lat={lat}&lon={lon}&appid={WEATHER_KEY}&units=metric&lang=ru"
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        data = resp.json()
    except Exception:
        logging.exception("Ошибка получения погоды для координат (%s, %s)", lat, lon)
        raise

    async with _cache_lock:
        _cache['weather'][(lat, lon)] = {'data': data, 'time': time.time()}

    return data


async def get_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    if message.location:
        try:
            data = await _fetch_weather(message.location.latitude, message.location.longitude)
        except Exception:
            await message.reply_text("Не удалось получить погоду. Попробуй позже.")
            return

        await message.reply_text(_format_weather_message(data), reply_markup=ReplyKeyboardRemove())
        return

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("Отправить геопозицию", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.reply_text(
        "Отправь свою геопозицию, чтобы я подсказал погоду рядом с тобой ☀️",
        reply_markup=keyboard,
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.location:
        return

    location = update.message.location
    try:
        data = await _fetch_weather(location.latitude, location.longitude)
    except Exception:
        await update.message.reply_text("Не удалось получить погоду. Попробуй позже.")
        return

    await update.message.reply_text(
        _format_weather_message(data),
        reply_markup=ReplyKeyboardRemove(),
    )

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(update.message.text)

if __name__ == '__main__':
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('currency', get_currency))
    app.add_handler(CommandHandler('weather', get_weather))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    print("Бот запущен. Нажмите Ctrl+C для выхода.")
    app.run_polling()
