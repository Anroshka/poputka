import asyncio
import logging
import sys
import json
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DB_NAME = "carpooling.db"
# URL Вашего Web App
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://your-domain.com/index.html") 

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица пользователей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT
            )
        """)
        # Таблица поездок
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id INTEGER,
                destination TEXT,
                departure_time TEXT,
                seats INTEGER,
                seats_taken INTEGER DEFAULT 0,
                price TEXT,
                comment TEXT,
                is_active BOOLEAN DEFAULT 1
            )
        """)
        # Таблица бронирований
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ride_id INTEGER,
                passenger_id INTEGER,
                FOREIGN KEY(ride_id) REFERENCES rides(id)
            )
        """)
        
        # Миграция: Проверка наличия новых колонок в rides
        cursor = await db.execute("PRAGMA table_info(rides)")
        columns = [row[1] for row in await cursor.fetchall()]
        if 'seats_taken' not in columns:
            await db.execute("ALTER TABLE rides ADD COLUMN seats_taken INTEGER DEFAULT 0")
        if 'is_active' not in columns:
            await db.execute("ALTER TABLE rides ADD COLUMN is_active BOOLEAN DEFAULT 1")
            
        await db.commit()

# --- СОСТОЯНИЯ FSM ---
class RideForm(StatesGroup):
    destination = State()
    departure_time = State()
    seats = State()
    price = State()
    comment = State()

class SearchRide(StatesGroup):
    query = State()

class SupportState(StatesGroup):
    waiting_message = State()
    
class EditRideState(StatesGroup):
    waiting_new_value = State()

class MessageState(StatesGroup):
    target_id = State()
    text = State()

# --- КЛАВИАТУРЫ ---
def main_menu_kb():
    kp = [
        [KeyboardButton(text="✨ Открыть приложение", web_app=WebAppInfo(url=WEB_APP_URL))],
        [KeyboardButton(text="🔍 Найти поездку (Пассажир)"), KeyboardButton(text="➕ Создать поездку (Водитель)")],
        [KeyboardButton(text="📂 Мои поездки"), KeyboardButton(text="🆘 Техподдержка")]
    ]
    return ReplyKeyboardMarkup(keyboard=kp, resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def delete_prev(state: FSMContext, bot: Bot, chat_id: int):
    """Удаляет предыдущее сообщение бота из состояния"""
    data = await state.get_data()
    msg_id = data.get("last_msg_id")
    if msg_id:
        try:
            await bot.delete_message(chat_id, msg_id)
        except:
            pass

async def answer_step(message: Message, state: FSMContext, text: str, kb=None):
    """Отвечает на сообщение и сохраняет ID для последующего удаления"""
    await delete_prev(state, message.bot, message.chat.id)
    new_msg = await message.answer(text, reply_markup=kb)
    await state.update_data(last_msg_id=new_msg.message_id)

# --- ГЛАВНОЕ МЕНЮ И РЕГИСТРАЦИЯ ---
@router.message(CommandStart())
async def command_start(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, full_name) VALUES (?, ?, ?)",
            (message.from_user.id, message.from_user.username, message.from_user.full_name)
        )
        await db.commit()
    
    await message.answer(
        f"Привет, {message.from_user.full_name}! 👋\n"
        "Я бот для совместных поездок. Выберите роль в меню ниже или воспользуйтесь нашим новым приложением:",
        reply_markup=main_menu_kb()
    )

@router.message(F.web_app_data)
async def handle_webapp_data(message: Message, state: FSMContext):
    try:
        data = json.loads(message.web_app_data.data)
        action = data.get("action")
        
        if action == "search":
            # Имитируем нажатие "Найти поездку" с уже введенным городом
            await state.clear()
            # Передаем управление в search_process, имитируя сообщение
            message.text = data.get("query")
            return await search_process(message, state)
            
        elif action == "offer":
            # Сохраняем поездку напрямую из данных приложения
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("""
                    INSERT INTO rides (driver_id, destination, departure_time, seats, price, comment)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (message.from_user.id, data['destination'], data['time'], 
                      data['seats'], data['price'], data['comment']))
                await db.commit()
            
            await message.answer("✅ <b>Поездка успешно опубликована через приложение!</b>", reply_markup=main_menu_kb())
            
    except Exception as e:
        logger.error(f"WebApp Data Error: {e}")
        await message.answer("❌ Произошла ошибка при обработке данных из приложения.")

@router.message(F.text == "Отмена")
async def cancel_action(message: Message, state: FSMContext):
    await state.clear()
    await delete_prev(state, message.bot, message.chat.id)
    new_msg = await message.answer("Действие отменено.", reply_markup=main_menu_kb())
    await state.update_data(last_msg_id=new_msg.message_id)


# ==========================================
# ============ СЦЕНАРИЙ ВОДИТЕЛЯ ===========
# ==========================================

# 1. Создание поездки
@router.message(F.text == "➕ Создать поездку (Водитель)")
async def create_ride_start(message: Message, state: FSMContext):
    await state.set_state(RideForm.destination)
    await answer_step(message, state, "🚗 <b>Куда едем?</b>\nВведите город или маршрут (например: Москва, Центр)", kb=cancel_kb())

@router.message(RideForm.destination)
async def process_dest(message: Message, state: FSMContext):
    await state.update_data(destination=message.text)
    await state.set_state(RideForm.departure_time)
    await answer_step(message, state, "⏰ <b>Когда выезжаем?</b>\nНапример: Сегодня в 18:00")

@router.message(RideForm.departure_time)
async def process_time(message: Message, state: FSMContext):
    await state.update_data(departure_time=message.text)
    await state.set_state(RideForm.seats)
    await answer_step(message, state, "🔢 <b>Сколько свободных мест?</b>\nВведите число.")

@router.message(RideForm.seats)
async def process_seats(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Пожалуйста, введите число.")
    await state.update_data(seats=int(message.text))
    await state.set_state(RideForm.price)
    await answer_step(message, state, "💰 <b>Цена за место?</b>\nНапример: 100р или Бесплатно")

@router.message(RideForm.price)
async def process_price(message: Message, state: FSMContext):
    await state.update_data(price=message.text)
    await state.set_state(RideForm.comment)
    await answer_step(message, state, "✏️ <b>Комментарий к поездке</b>\nАвто, место встречи и т.д.")

@router.message(RideForm.comment)
async def process_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    await delete_prev(state, message.bot, message.chat.id)
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO rides (driver_id, destination, departure_time, seats, price, comment)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (message.from_user.id, data['destination'], data['departure_time'], 
              data['seats'], data['price'], message.text))
        await db.commit()
    
    await state.clear()
    await message.answer("✅ <b>Поездка успешно создана!</b>", reply_markup=main_menu_kb())

# 2. Мои поездки (Управление)
@router.message(F.text == "📂 Мои поездки")
async def my_rides(message: Message, state: FSMContext):
    await delete_prev(state, message.bot, message.chat.id)
    async with aiosqlite.connect(DB_NAME) as db:
        # Получаем активные поездки водителя
        cursor = await db.execute("""
            SELECT id, destination, departure_time, seats, seats_taken, price 
            FROM rides WHERE driver_id = ? AND is_active = 1
        """, (message.from_user.id,))
        rows = await cursor.fetchall()
        
        # Получаем бронирования пассажира
        cursor_book = await db.execute("""
            SELECT r.id, r.destination, r.departure_time, r.price, r.driver_id
            FROM bookings b
            JOIN rides r ON b.ride_id = r.id
            WHERE b.passenger_id = ? AND r.is_active = 1
        """, (message.from_user.id,))
        rows_booked = await cursor_book.fetchall()

    if not rows and not rows_booked:
        await message.answer(
            "📭 <b>У вас пока нет активных поездок.</b>\n\n"
            "Вы можете создать свою поездку как водитель или найти подходящую как пассажир.",
            reply_markup=main_menu_kb()
        )
        return

    # Показываем созданные поездки
    if rows:
        await message.answer("<b>🚗 ВАШИ ПРЕДЛОЖЕНИЯ (Водитель):</b>")
        for row in rows:
            r_id, dest, time, seats, taken, price = row
            text = (
                f"🆔 <b>Поездка #{r_id}</b>\n"
                f"📍 <b>Куда:</b> {dest}\n"
                f"⏰ <b>Время:</b> {time}\n"
                f"💰 <b>Цена:</b> {price}\n"
                f"💺 <b>Места:</b> {taken} из {seats} занято"
            )
            
            kb = InlineKeyboardBuilder()
            kb.button(text="👥 Пассажиры", callback_data=f"view_passengers_{r_id}")
            kb.button(text="⏰ Время", callback_data=f"edit_time_{r_id}")
            kb.button(text="📍 Трасса", callback_data=f"edit_dest_{r_id}")
            kb.button(text="💰 Цена", callback_data=f"edit_price_{r_id}")
            kb.button(text="💺 Места", callback_data=f"edit_seats_{r_id}")
            kb.button(text="❌ Отменить", callback_data=f"cxl_ride_{r_id}")
            kb.adjust(1, 2, 2, 1)
            
            await message.answer(text, reply_markup=kb.as_markup())

    # Показываем брони
    if rows_booked:
        await message.answer("<b>🎒 ВЫ ЗАБРОНИРОВАЛИ (Пассажир):</b>")
        for row in rows_booked:
            r_id, dest, time, price, driver_id = row
            # Получаем ник водителя
            async with aiosqlite.connect(DB_NAME) as db:
                async with db.execute("SELECT full_name, username FROM users WHERE id = ?", (driver_id,)) as cur:
                   drv = await cur.fetchone()
                   drv_name = drv[0] if drv else "Водитель"
                   drv_user = f"@{drv[1]}" if drv and drv[1] else "Без ника"

            kb = InlineKeyboardBuilder()
            kb.button(text="💬 Написать водителю", callback_data=f"chat_{driver_id}")
            kb.button(text="❌ Отменить бронь", callback_data=f"unbook_{r_id}")
            kb.adjust(1)
            
            text = (
                f"🆔 <b>Бронь #{r_id}</b>\n"
                f"📍 <b>Маршрут:</b> {dest}\n"
                f"⏰ <b>Время:</b> {time}\n"
                f"💰 <b>Цена:</b> {price}\n"
                f"👨‍✈️ <b>Водитель:</b> {drv_name} ({drv_user})"
            )
            await message.answer(text, reply_markup=kb.as_markup())

# Логика редактирования/отмены (Callback)
@router.callback_query(F.data.startswith("cxl_ride_"))
async def cancel_ride_confirm(callback: CallbackQuery):
    ride_id = int(callback.data.split("_")[2])
    
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, отменить", callback_data=f"really_cxl_{ride_id}")
    kb.button(text="Нет", callback_data="cancel_edit")
    kb.adjust(1)
    
    await callback.message.edit_text(
        f"⚠️ <b>Вы уверены, что хотите отменить поездку #{ride_id}?</b>\n"
        "Это действие нельзя отменить, все пассажиры будут уведомлены.",
        reply_markup=kb.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data == "cancel_edit")
async def cancel_edit_cb(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer("Действие отменено.")

@router.callback_query(F.data.startswith("really_cxl_"))
async def cancel_ride_handler(callback: CallbackQuery):
    ride_id = int(callback.data.split("_")[2])
    
    # Уведомляем пассажиров
    passengers = await get_ride_passengers(ride_id)
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Получаем инфо перед удалением
        async with db.execute("SELECT destination FROM rides WHERE id = ?", (ride_id,)) as cur:
            res = await cur.fetchone()
            dest = res[0] if res else "неизвестно"

        await db.execute("UPDATE rides SET is_active = 0 WHERE id = ?", (ride_id,))
        await db.execute("DELETE FROM bookings WHERE ride_id = ?", (ride_id,))
        await db.commit()

    for p_id in passengers:
        try:
            await bot.send_message(p_id, f"⚠️ <b>Поездка отменена!</b>\nВодитель отменил поездку в <b>{dest}</b> (ID: {ride_id}).")
        except: pass
    
    await callback.message.edit_text(f"✅ Поездка {ride_id} в {dest} успешно отменена.")
    await callback.answer()

@router.callback_query(F.data.startswith("view_passengers_"))
async def view_passengers_handler(callback: CallbackQuery):
    ride_id = int(callback.data.split("_")[2])
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT u.full_name, u.username, u.id
            FROM bookings b
            JOIN users u ON b.passenger_id = u.id
            WHERE b.ride_id = ?
        """, (ride_id,))
        passengers = await cursor.fetchall()

    if not passengers:
        await callback.answer("На эту поездку пока никто не записался.", show_alert=True)
        return

    text = f"👥 <b>Пассажиры на поездку #{ride_id}:</b>\n\n"
    kb = InlineKeyboardBuilder()
    for idx, (name, username, p_id) in enumerate(passengers, 1):
        user_link = f"@{username}" if username else f"ID: {p_id}"
        text += f"{idx}. {name} ({user_link})\n"
        kb.button(text=f"💬 Написать {name}", callback_data=f"chat_{p_id}")
    
    kb.adjust(1)
    await callback.message.answer(text, reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("edit_"))
async def edit_ride_start(callback: CallbackQuery, state: FSMContext):
    try:
        parts = callback.data.split("_")
        field = parts[1]
        ride_id = int(parts[2])
    except (ValueError, IndexError):
        return await callback.answer("Ошибка данных.")

    # Получаем текущее значение
    db_fields = {"time": "departure_time", "dest": "destination", "price": "price", "seats": "seats"}
    db_field = db_fields.get(field)
    current_val = "неизвестно"
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(f"SELECT {db_field} FROM rides WHERE id = ?", (ride_id,)) as cur:
            res = await cur.fetchone()
            if res: current_val = res[0]

    await state.update_data(edit_ride_id=ride_id, edit_field=field)
    await state.set_state(EditRideState.waiting_new_value)
    
    labels = {
        "time": "новое время отправления (например, 15:00)",
        "dest": "новое место назначения",
        "price": "новую цену",
        "seats": "общее количество мест"
    }
    label = labels.get(field, "новое значение")
    
    await answer_step(
        callback.message, state, 
        f"📝 <b>Редактирование:</b>\n"
        f"Текущее значение: <code>{current_val}</code>\n\n"
        f"Введите {label}:",
        kb=cancel_kb()
    )
    await callback.answer()

@router.message(EditRideState.waiting_new_value)
async def edit_ride_finish(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await delete_prev(state, message.bot, message.chat.id)
        await state.clear()
        return await message.answer("Редактирование отменено.", reply_markup=main_menu_kb())

    data = await state.get_data()
    await delete_prev(state, message.bot, message.chat.id)
    ride_id = data.get('edit_ride_id')
    field = data.get('edit_field')
    new_val = message.text.strip()
    
    db_fields = {
        "time": "departure_time",
        "dest": "destination",
        "price": "price",
        "seats": "seats"
    }
    friendly_names = {
        "time": "⏰ Время отправления",
        "dest": "📍 Маршрут",
        "price": "💰 Цена",
        "seats": "👥 Количество мест"
    }
    
    db_field = db_fields.get(field)
    if not db_field:
        await state.clear()
        return await message.answer("Произошла ошибка. Попробуйте снова.", reply_markup=main_menu_kb())

    # Валидация для числовых полей
    if field in ["price", "seats"]:
        if not new_val.isdigit():
            return await message.answer("Пожалуйста, введите число.")
        new_val = int(new_val)
        
        if field == "seats":
            # Проверка, чтобы мест не стало меньше, чем уже занято
            async with aiosqlite.connect(DB_NAME) as db:
                cursor = await db.execute("SELECT seats_taken FROM rides WHERE id = ?", (ride_id,))
                row = await cursor.fetchone()
                if row and new_val < row[0]:
                    return await message.answer(f"❌ Нельзя установить мест меньше, чем уже забронировано ({row[0]}).")

    async with aiosqlite.connect(DB_NAME) as db:
        # Получаем данные о поездке для уведомления
        cursor = await db.execute("SELECT destination FROM rides WHERE id = ?", (ride_id,))
        ride_data = await cursor.fetchone()
        dest_name = ride_data[0] if ride_data else "неизвестно"

        await db.execute(f"UPDATE rides SET {db_field} = ? WHERE id = ?", (new_val, ride_id))
        await db.commit()
    
    # Уведомления пассажирам
    passengers = await get_ride_passengers(ride_id)
    msg_text = (
        f"✏️ <b>Изменение в вашей поездке!</b>\n"
        f"📍 <b>Маршрут:</b> {dest_name}\n\n"
        f"Водитель обновил информацию:\n"
        f"➡️ <b>{friendly_names.get(field)}:</b> {new_val}\n\n"
        f"Проверьте актуальные данные в разделе «📂 Мои поездки»."
    )
    
    count = 0
    for p_id in passengers:
        try:
            await bot.send_message(p_id, msg_text)
            count += 1
        except Exception as e:
            logger.error(f"Failed to notify passenger {p_id}: {e}")
        
    await state.clear()
    await delete_prev(state, message.bot, message.chat.id)
    success_msg = f"✅ <b>Поездка успешно обновлена!</b>\n"
    if count > 0:
        success_msg += f"📢 Уведомление отправлено {count} пассажирам."
        
    new_msg = await message.answer(success_msg, reply_markup=main_menu_kb())
    await state.update_data(last_msg_id=new_msg.message_id)

# Helper для пассажиров
async def get_ride_passengers(ride_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT passenger_id FROM bookings WHERE ride_id = ?", (ride_id,))
        return [row[0] for row in await cursor.fetchall()]

# --- СИСТЕМА СООБЩЕНИЙ ---
@router.callback_query(F.data.startswith("chat_"))
async def start_chat(callback: CallbackQuery, state: FSMContext):
    target_id = int(callback.data.split("_")[1])
    if target_id == callback.from_user.id:
        return await callback.answer("Вы не можете написать самому себе.")
    
    await state.update_data(chat_target_id=target_id)
    await state.set_state(MessageState.text)
    
    await answer_step(
        callback.message, state,
        "📝 <b>Напишите ваше сообщение:</b>\n"
        "Оно будет доставлено пользователю напрямую от имени бота.",
        kb=cancel_kb()
    )
    await callback.answer()

@router.message(MessageState.text)
async def send_internal_message(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await delete_prev(state, message.bot, message.chat.id)
        await state.clear()
        return await message.answer("Отправка отменена.", reply_markup=main_menu_kb())
        
    data = await state.get_data()
    await delete_prev(state, message.bot, message.chat.id)
    target_id = data.get("chat_target_id")
    
    if not target_id:
        await state.clear()
        return await message.answer("Ошибка: адресат не найден.")

    # Кнопка для быстрого ответа
    kb = InlineKeyboardBuilder()
    kb.button(text="↩️ Ответить", callback_data=f"chat_{message.from_user.id}")
    
    try:
        sender_name = message.from_user.full_name
        sender_user = f"@{message.from_user.username}" if message.from_user.username else f"ID: {message.from_user.id}"
        
        await bot.send_message(
            target_id,
            f"📩 <b>Новое сообщение!</b>\n"
            f"От: {sender_name} ({sender_user})\n\n"
            f"{message.text}",
            reply_markup=kb.as_markup()
        )
        new_msg = await message.answer("✅ Сообщение успешно доставлено!", reply_markup=main_menu_kb())
        await state.update_data(last_msg_id=new_msg.message_id)
    except Exception as e:
        new_msg = await message.answer(f"❌ Не удалось доставить сообщение: {e}", reply_markup=main_menu_kb())
        await state.update_data(last_msg_id=new_msg.message_id)
    
    await state.clear()

# ==========================================
# ============ СЦЕНАРИЙ ПАССАЖИРА ==========
# ==========================================

@router.message(F.text == "🔍 Найти поездку (Пассажир)")
async def search_start(message: Message, state: FSMContext):
    await state.set_state(SearchRide.query)
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Показать все")],
        [KeyboardButton(text="Отмена")]
    ], resize_keyboard=True)
    await answer_step(message, state, "🔍 <b>Ищем поездку</b>\nВведите название города или выберите «Показать все»:", kb=kb)

@router.message(SearchRide.query)
async def search_process(message: Message, state: FSMContext):
    query_text = message.text.strip()
    await delete_prev(state, message.bot, message.chat.id)
    
    sql = "SELECT id, driver_id, destination, departure_time, seats, seats_taken, price, comment FROM rides WHERE is_active = 1 AND seats_taken < seats"
    params = ()

    if query_text != "Показать все":
        sql += " AND destination LIKE ?"
        params = (f"%{query_text}%",)
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(sql, params)
        rides = await cursor.fetchall()
    
    await state.clear()
    
    if not rides:
        new_msg = await message.answer("😔 Поездок не найдено.", reply_markup=main_menu_kb())
        await state.update_data(last_msg_id=new_msg.message_id)
        return

    new_msg = await message.answer(f"🔎 <b>Найдено вариантов: {len(rides)}</b>", reply_markup=main_menu_kb())
    await state.update_data(last_msg_id=new_msg.message_id)
    
    for ride in rides:
        r_id, drv_id, dest, time, seats, taken, price, comm = ride
        
        # Получаем имя водителя
        async with aiosqlite.connect(DB_NAME) as db:
             async with db.execute("SELECT full_name, username FROM users WHERE id = ?", (drv_id,)) as cur:
                 drv_data = await cur.fetchone()
                 drv_name = drv_data[0] if drv_data else "Водитель"
                 drv_user = f"@{drv_data[1]}" if drv_data and drv_data[1] else "Без ника"

        # Проверка, не забронировал ли уже
        already_booked = False
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT 1 FROM bookings WHERE ride_id = ? AND passenger_id = ?", (r_id, message.from_user.id)) as cur:
                if await cur.fetchone():
                    already_booked = True

        info = (
            f"📍 <b>Маршрут: {dest}</b>\n\n"
            f"⏰ <b>Когда:</b> {time}\n"
            f"💰 <b>Цена:</b> {price}\n"
            f"💺 <b>Мест:</b> {seats - taken} свободно\n"
            f"👨‍✈️ <b>Водитель:</b> {drv_name} ({drv_user})\n"
            f"💬 <b>Инфо:</b> {comm or 'Нет описания'}"
        )
        
        kb = InlineKeyboardBuilder()
        if already_booked:
            kb.button(text="❌ Отменить бронь", callback_data=f"unbook_{r_id}")
        elif message.from_user.id == drv_id:
             kb.button(text="🔒 Это ваша поездка", callback_data="ignore")
        else:
            kb.button(text="✅ Забронировать", callback_data=f"book_{r_id}")
        
        # Добавляем кнопку сообщения если это не сам водитель
        if message.from_user.id != drv_id:
            kb.button(text="💬 Написать водителю", callback_data=f"chat_{drv_id}")
            
        kb.adjust(1)
        await message.answer(info, reply_markup=kb.as_markup())

# Бронирование
@router.callback_query(F.data.startswith("book_"))
async def book_ride(callback: CallbackQuery):
    ride_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Проверка мест
        async with db.execute("SELECT seats, seats_taken, driver_id, destination FROM rides WHERE id = ?", (ride_id,)) as cur:
            ride = await cur.fetchone()
            if not ride:
                return await callback.answer("Поездка не найдена", show_alert=True)
            seats, taken, driver_id, dest = ride
            
        if taken >= seats:
            return await callback.answer("Места закончились!", show_alert=True)
            
        # Запись
        try:
            await db.execute("INSERT INTO bookings (ride_id, passenger_id) VALUES (?, ?)", (ride_id, user_id))
            await db.execute("UPDATE rides SET seats_taken = seats_taken + 1 WHERE id = ?", (ride_id,))
            await db.commit()
        except Exception as e:
            return await callback.answer("Ошибка или вы уже записаны", show_alert=True)

    await callback.answer("Успешно забронировано!", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None) # Удаляем кнопку
    await callback.message.answer("✅ Вы забронировали место! Свяжитесь с водителем при необходимости.")

    # Уведомление водителю
    try:
        username = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.full_name
        msg = (
            f"🎉 <b>У вас новый пассажир!</b>\n\n"
            f"📍 <b>В город:</b> {dest}\n"
            f"👤 <b>Пассажир:</b> {callback.from_user.full_name} ({username})\n"
            f"👉 Проверьте список пассажиров в «📂 Мои поездки»"
        )
        # Кнопка для быстрой связи с пассажиром
        kb = InlineKeyboardBuilder()
        kb.button(text=f"💬 Написать пассажиру", callback_data=f"chat_{callback.from_user.id}")

        await bot.send_message(driver_id, msg, reply_markup=kb.as_markup())
    except: pass

# Отмена брони
@router.callback_query(F.data.startswith("unbook_"))
async def unbook_ride(callback: CallbackQuery):
    ride_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Проверка владельца и получение данных
        async with db.execute("SELECT driver_id FROM rides WHERE id = ?", (ride_id,)) as cur:
            res = await cur.fetchone()
            driver_id = res[0] if res else None

        await db.execute("DELETE FROM bookings WHERE ride_id = ? AND passenger_id = ?", (ride_id, user_id))
        await db.execute("UPDATE rides SET seats_taken = seats_taken - 1 WHERE id = ?", (ride_id,))
        await db.commit()
        
    await callback.answer("Бронь отменена")
    await callback.message.delete()
    
    # Уведомление водителю
    if driver_id:
        try:
            username = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.full_name
            await bot.send_message(driver_id, f"⚠️ <b>Отмена брони!</b>\nПассажир {username} отказался от поездки.")
        except: pass


# ==========================================
# ============ ТЕХПОДДЕРЖКА ================
# ==========================================

@router.message(F.text == "🆘 Техподдержка")
async def support_start(message: Message, state: FSMContext):
    await state.set_state(SupportState.waiting_message)
    await answer_step(message, state, "🆘 <b>Техподдержка</b>\n\nНапишите ваше сообщение администратору:", kb=cancel_kb())

@router.message(SupportState.waiting_message)
async def support_send(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await delete_prev(state, message.bot, message.chat.id)
        await state.clear()
        return await message.answer("Действие отменено.", reply_markup=main_menu_kb())

    await delete_prev(state, message.bot, message.chat.id)
    # Пересылка админу
    info_text = f"🆘 <b>Обращение от пользователя</b>\nID: <code>{message.from_user.id}</code>\n@{message.from_user.username}"
    try:
        await bot.send_message(ADMIN_ID, info_text)
        await message.forward(ADMIN_ID)
        new_msg = await message.answer("✅ Сообщение отправлено администрации.", reply_markup=main_menu_kb())
        await state.update_data(last_msg_id=new_msg.message_id)
    except Exception as e:
        new_msg = await message.answer(f"Ошибка отправки: {e}", reply_markup=main_menu_kb())
        await state.update_data(last_msg_id=new_msg.message_id)
    await state.clear()

# Ответ админа (/answer ID текст)
@router.message(Command("answer"), F.from_user.id == ADMIN_ID)
async def admin_answer(message: Message, command: CommandObject):
    if command.args is None:
        await message.answer("Ошибка: введите /answer user_id текст")
        return
    
    try:
        user_id_str, text = command.args.split(" ", 1)
        user_id = int(user_id_str)
        await bot.send_message(user_id, f"📨 <b>Ответ от поддержки:</b>\n{text}")
        await message.answer("✅ Ответ отправлен.")
    except ValueError:
        await message.answer("Неверный формат ID или текста.")
    except Exception as e:
        await message.answer(f"Не удалось отправить: {e}")

# ==========================================
# ============ ЗАПУСК ======================
# ==========================================

async def main():
    await init_db()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    if TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("ОШИБКА: Укажите токен бота в файле!")
        sys.exit(1)
        
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")
