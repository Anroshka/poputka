import asyncio
import logging
import json
import os
from datetime import datetime

# Библиотеки для работы с API и Firebase
from aiohttp import web
import aiohttp_cors
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

# Библиотеки Telegram
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# Загружаем переменные из .env
load_dotenv()

# --- КОНФИГУРАЦИЯ ИЗ .ENV ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
WEB_APP_URL = os.getenv("WEB_APP_URL")
CHAT_ID = "@dubrovitsy_online" # Чат для проверки подписки

# --- ИНИЦИАЛИЗАЦИЯ FIREBASE ---
# Файл должен лежать в той же папке и называться именно так
cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- ПРОВЕРКА ПОДПИСКИ ---
async def is_user_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHAT_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator", "restricted"]
    except Exception as e:
        logger.error(f"Ошибка проверки подписки {user_id}: {e}")
        return False

# --- API ДЛЯ MINI APP ---

async def api_get_rides(request):
    """Сайт запрашивает список поездок"""
    try:
        rides_ref = db.collection("rides").where("is_active", "==", True).stream()
        rides = [ {**r.to_dict(), "id": r.id} for r in rides_ref ]
        return web.json_response(rides)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_offer_ride(request):
    """Сайт отправляет новую поездку"""
    data = await request.json()
    user_id = int(data.get('user_id'))
    
    if not await is_user_subscribed(user_id):
        return web.json_response({"error": "Forbidden"}, status=403)

    new_ride = {
        "driver_id": user_id,
        "driver_name": data.get('driver_name', 'Водитель'),
        "destination": data.get('destination'),
        "time": data.get('time'),
        "seats": int(data.get('seats', 1)),
        "seats_taken": 0,
        "price": data.get('price'),
        "comment": data.get('comment', ''),
        "is_active": True,
        "created_at": datetime.now().isoformat()
    }
    db.collection("rides").add(new_ride)
    return web.json_response({"status": "ok"})

async def setup_api():
    app = web.Application()
    app.router.add_get('/api/rides', api_get_rides)
    app.router.add_post('/api/offer', api_offer_ride)
    
    # Настройка CORS для работы с GitHub Pages
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods=["GET", "POST", "OPTIONS"]
        )
    })
    for route in list(app.router.routes()):
        cors.add(route)
    return app

# --- ЛОГИКА БОТА ---

def main_menu_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🚗 Найти / Создать поездку", web_app=WebAppInfo(url=WEB_APP_URL))],
        [KeyboardButton(text="🆘 Техподдержка")]
    ], resize_keyboard=True)

@dp.message(CommandStart())
async def command_start(message: Message):
    user_id = message.from_user.id
    
    if not await is_user_subscribed(user_id):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Вступить в чат", url="https://t.me/dubrovitsy_online")],
            [InlineKeyboardButton(text="✅ Я вступил", callback_data="check_sub")]
        ])
        await message.answer(
            "🛑 <b>Доступ ограничен!</b>\n\nЭтот бот только для участников чата @dubrovitsy_online.",
            reply_markup=kb
        )
        return

    # Синхронизация юзера с Firebase
    db.collection("users").document(str(user_id)).set({
        "username": message.from_user.username,
        "full_name": message.from_user.full_name,
        "last_active": datetime.now().isoformat()
    }, merge=True)

    await message.answer(
        f"Привет, {message.from_user.full_name}! 👋\nИспользуйте кнопку ниже для работы с поездками:",
        reply_markup=main_menu_kb()
    )

@dp.callback_query(F.data == "check_sub")
async def verify_sub(callback: types.CallbackQuery):
    if await is_user_subscribed(callback.from_user.id):
        await callback.message.answer("✅ Доступ открыт!", reply_markup=main_menu_kb())
        await callback.message.delete()
    else:
        await callback.answer("❌ Вы всё еще не вступили в чат!", show_alert=True)

# --- ЗАПУСК ---
async def main():
    # Запуск API (на порту 8080 для Render/Amvera)
    api_app = await setup_api()
    runner = web.AppRunner(api_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    
    print("Бот и API запущены...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())