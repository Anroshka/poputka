import asyncio
import os
from dotenv import load_dotenv

import firebase_admin
from firebase_admin import credentials, firestore
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL") 
CHAT_ID = "@dubrovitsy_online"

# Подключаем Firebase
cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- ПРОВЕРКА ПОДПИСКИ ---
async def check_sub(user_id):
    try:
        m = await bot.get_chat_member(CHAT_ID, user_id)
        return m.status in ["member", "administrator", "creator"]
    except:
        return False

# --- СЛЕЖКА ЗА БАЗОЙ (Real-time уведомления) ---
async def watch_bookings():
    """Эта функция срабатывает САМА, когда в базе что-то меняется"""
    print("👀 Слежу за новыми бронями...")
    
    # Callback-функция, которая запускается при изменениях
    def on_snapshot(col_snapshot, changes, read_time):
        for change in changes:
            if change.type.name == 'ADDED': # Только новые брони
                data = change.document.to_dict()
                # Если водителю еще не сообщили (поле notified нет или False)
                if not data.get('notified'):
                    asyncio.create_task(notify_driver(change.document.id, data))

    # Ставим слушатель на коллекцию bookings
    db.collection("bookings").on_snapshot(on_snapshot)

async def notify_driver(doc_id, data):
    driver_id = data.get('driver_id')
    pass_name = data.get('passenger_name')
    dest = data.get('ride_dest')
    
    try:
        # Шлем сообщение в Телеграм
        await bot.send_message(
            driver_id, 
            f"🔔 <b>Новый пассажир!</b>\n"
            f"👤 {pass_name} забронировал место\n"
            f"📍 Маршрут: {dest}", 
            parse_mode="HTML"
        )
        # Отмечаем в базе, что уведомление ушло
        db.collection("bookings").document(doc_id).update({"notified": True})
    except Exception as e:
        print(f"Не удалось отправить водителю {driver_id}: {e}")

# --- ОБЫЧНЫЙ БОТ ---
@dp.message(CommandStart())
async def start(message: Message):
    if not await check_sub(message.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Вступить в чат", url=f"https://t.me/{CHAT_ID.replace('@','')}")]])
        return await message.answer("🛑 Сначала вступите в чат!", reply_markup=kb)

    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🚗 Попутчик (Открыть)", web_app=WebAppInfo(url=WEB_APP_URL))]
    ], resize_keyboard=True)
    await message.answer("Добро пожаловать! Жми кнопку 👇", reply_markup=kb)

async def main():
    # Запускаем в отдельном потоке слушатель базы (чтобы не блокировать бота)
    # В Python firebase-admin watch работает в фоне, нам достаточно инициализировать
    watch_bookings() 
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())