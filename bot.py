import asyncio
import logging
import os
from dotenv import load_dotenv

# Firebase & Web
import firebase_admin
from firebase_admin import credentials, firestore
from aiohttp import web

# Aiogram
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton

load_dotenv()

# --- КОНФИГ ---
TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL") # Ссылка на GitHub Pages

# --- FIREBASE ---
cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# --- БОТ ---
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- ФОНОВАЯ ЗАДАЧА: РАССЫЛКА УВЕДОМЛЕНИЙ ---
async def notification_loop():
    """
    Каждые 5 секунд проверяет, есть ли новые брони (notified == false).
    Если есть - шлет сообщение водителю и ставит notified = true.
    """
    print("🚀 Система уведомлений запущена...")
    while True:
        try:
            # 1. Ищем брони, о которых еще не сообщили
            docs = db.collection("bookings").where("notified", "==", False).limit(10).stream()
            
            for doc in docs:
                data = doc.to_dict()
                booking_id = doc.id
                driver_id = data.get('driver_id')
                pass_name = data.get('passenger_name')
                dest = data.get('ride_dest')
                pass_username = data.get('passenger_id') # Это ID, можно сделать ссылку

                if driver_id:
                    # 2. Шлем сообщение водителю
                    try:
                        msg_text = (
                            f"🔔 <b>Новая бронь!</b>\n\n"
                            f"👤 Пассажир: <a href='tg://user?id={pass_username}'>{pass_name}</a>\n"
                            f"📍 Маршрут: {dest}\n"
                            f"<i>Зайдите в раздел «Мои», чтобы увидеть детали.</i>"
                        )
                        await bot.send_message(driver_id, msg_text, parse_mode="HTML")
                        print(f"✅ Уведомление отправлено водителю {driver_id}")
                    except Exception as e:
                        print(f"❌ Не удалось отправить водителю {driver_id}: {e}")

                    # 3. Отмечаем, что уведомление отправлено (чтобы не спамить)
                    db.collection("bookings").document(booking_id).update({"notified": True})
            
        except Exception as e:
            print(f"Ошибка в цикле уведомлений: {e}")
        
        await asyncio.sleep(5) # Пауза 5 секунд

# --- КЛАВИАТУРА ---
def get_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🚗 Открыть Попутчик", web_app=WebAppInfo(url=WEB_APP_URL))]
    ], resize_keyboard=True)

# --- ХЭНДЛЕРЫ ---
@dp.message(CommandStart())
async def start(message: Message):
    # Простое приветствие, вся логика теперь в приложении
    await message.answer(
        "👋 Привет! Это сервис попутчиков.\n"
        "Всё управление происходит внутри приложения.",
        reply_markup=get_kb()
    )

# --- ЗАПУСК ВСЕГО ---
async def main():
    # Запускаем фоновую задачу уведомлений параллельно с ботом
    asyncio.create_task(notification_loop())
    
    # Запускаем бота
    print("🤖 Бот запущен и ждет сообщений...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())