import aiosqlite
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
import config
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import logging
import random
from datetime import datetime, timedelta

# Логирование для отладки
logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# Состояния для FSM
class ApplicationForm(StatesGroup):
    waiting_for_answer = State()
    waiting_for_acceptance = State()

# Хранение заявок
applications = {}
user_ids = set()  # Хранение уникальных ID пользователей
statistics = {
    "daily": 0,
    "weekly": 0,
    "monthly": 0,
    "all_time": 0,
}

# Функция для создания клавиатуры с кнопкой "Каналы Тимы"
def get_channels_button():
    keyboard = InlineKeyboardMarkup()
    channels_button = InlineKeyboardButton(text="Каналы Тимы", callback_data="show_channels")
    keyboard.add(channels_button)
    return keyboard

# Функция для админ-панели
def get_admin_panel_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton(text="Статистика", callback_data="show_statistics"))
    keyboard.add(InlineKeyboardButton(text="Рассылка", callback_data="send_broadcast"))
    keyboard.add(InlineKeyboardButton(text="Заявки", callback_data="show_applications"))  # Кнопка для заявок
    return keyboard

# Инициализация базы данных
async def init_db():
    async with aiosqlite.connect("users.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY
            )
        """)
        await db.commit()

async def load_users():
    async with aiosqlite.connect("users.db") as db:
        async with db.execute("SELECT id FROM users") as cursor:
            async for row in cursor:
                user_ids.add(row[0])  # Добавляем ID пользователя в множество

async def save_user(user_id):
    async with aiosqlite.connect("users.db") as db:
        await db.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (user_id,))
        await db.commit()

# Стартовое сообщение
@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.reply("Привет! Отправь заявку командой /apply")

# Пользователь отправляет заявку
@dp.message_handler(commands=['apply'])
async def apply_for_team(message: types.Message, state: FSMContext):
    logging.info(f"Пользователь {message.from_user.id} начал заявку.")
    
    # Добавляем пользователя в уникальный список и сохраняем в базе
    user_ids.add(message.from_user.id)
    await save_user(message.from_user.id)
    logging.info(f"Добавлен пользователь {message.from_user.id} в список заявок.")

    await state.update_data(answers=[])  
    await state.update_data(question_index=0)  
    await ask_next_question(message, state)

# Функция для задавания следующего вопроса
async def ask_next_question(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    question_index = user_data.get('question_index', 0)

    if question_index < len(config.QUESTIONS):
        question = config.QUESTIONS[question_index]
        logging.info(f"Задаем вопрос: {question}")
        await message.reply(question)
        await state.update_data(question_index=question_index + 1)  
        await ApplicationForm.waiting_for_answer.set()  
        logging.info(f"Состояние установлено: waiting_for_answer")
    else:
        answers = user_data.get('answers', [])
        application_id = random.randint(1000, 9999)  
        application_text = "\n".join([f"{config.QUESTIONS[i]}: {answer}" for i, answer in enumerate(answers)])  

        # Сохраняем заявку
        applications[application_id] = {
            'user_id': message.from_user.id,
            'application_text': application_text,
            'timestamp': datetime.now(),
        }

        for admin_id in config.ADMIN_IDS:
            await bot.send_message(admin_id, f"Новая заявка #{application_id} от {message.from_user.full_name}:\n\n{application_text}")

        # Увеличиваем статистику
        update_statistics()
        
        await message.reply("Ваша заявка отправлена на рассмотрение. Ваш ID заявки: " + str(application_id))
        await state.finish()
        logging.info(f"Пользователь {message.from_user.id} завершил заявку с ID {application_id}.")

# Обработка ответа на вопрос
@dp.message_handler(state=ApplicationForm.waiting_for_answer)
async def process_answer(message: types.Message, state: FSMContext):
    logging.info(f"Получен ответ от пользователя {message.from_user.id}: {message.text}")

    user_data = await state.get_data()
    answers = user_data.get('answers', [])

    answers.append(message.text)
    await state.update_data(answers=answers)

    await ask_next_question(message, state)

# Проверка на права администратора
def is_admin(user_id):
    return user_id in config.ADMIN_IDS

# Команда для администраторов
@dp.message_handler(commands=['admin'])
async def admin_panel(message: types.Message):
    if is_admin(message.from_user.id):
        await message.reply("Вы в админ-панели.", reply_markup=get_admin_panel_keyboard())
    else:
        await message.reply("У вас нет прав доступа к админ-панели.")

# Админ принимает или отклоняет заявку
@dp.message_handler(lambda message: is_admin(message.chat.id), commands=['accept', 'reject'])
async def handle_application(message: types.Message):
    command, *args = message.text.split()
    
    if len(args) == 0:
        await message.reply("Пожалуйста, укажите ID заявки.")
        return

    try:
        application_id = int(args[0])
        application_data = applications.get(application_id)
    except ValueError:
        await message.reply("ID заявки должен быть числом.")
        return

    if not application_data:
        await message.reply("Нет доступной заявки с таким ID.")
        return

    user_id = application_data['user_id']
    application_text = application_data['application_text']

    if command == "/accept":
        await bot.send_message(user_id, f"Ваша заявка #{application_id} принята!", reply_markup=get_channels_button())
        await message.reply(f"Вы приняли заявку #{application_id}.")
        logging.info(f"Администратор {message.from_user.id} принял заявку #{application_id}.")  # Логируем принятие заявки
    elif command == "/reject":
        await bot.send_message(user_id, f"Ваша заявка #{application_id} отклонена.")
        await message.reply(f"Вы отклонили заявку #{application_id}.")
        logging.info(f"Администратор {message.from_user.id} отклонил заявку #{application_id}.")  # Логируем отклонение заявки

    del applications[application_id]
    logging.info(f"Заявка #{application_id} обработана и удалена.")

# Функция для обновления статистики
def update_statistics():
    statistics["all_time"] += 1
    now = datetime.now()
    
    # Обновление статистики по дням, неделям и месяцам
    if now.date() == (now - timedelta(days=1)).date():
        statistics["daily"] = 1
    else:
        statistics["daily"] += 1
        
    if now.date() >= (now - timedelta(weeks=1)).date():
        statistics["weekly"] += 1
    if now.month == (now - timedelta(days=30)).month:
        statistics["monthly"] += 1

@dp.callback_query_handler(lambda c: c.data == "show_channels")
async def show_channels(callback_query: types.CallbackQuery):
    links_text = "Каналы Тимы:\n"
    for link in config.CHANNEL_LINKS:
        links_text += f"{link}\n"

    await bot.send_message(callback_query.from_user.id, links_text)
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "show_statistics")
async def show_statistics(callback_query: types.CallbackQuery):
    user_count = len(user_ids)  # Количество уникальных пользователей
    stats_message = (f"Статистика проекта:\n"
                     f"Проверенные заявки за день: {statistics['daily']}\n"
                     f"Проверенные заявки за неделю: {statistics['weekly']}\n"
                     f"Проверенные заявки за месяц: {statistics['monthly']}\n"
                     f"Проверенные заявки за все время: {statistics['all_time']}\n"
                     f"Количество пользователей: {user_count}")  # Количество пользователей
    
    await bot.send_message(callback_query.from_user.id, stats_message)
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "send_broadcast")
async def send_broadcast(callback_query: types.CallbackQuery):
    await bot.send_message(callback_query.from_user.id, "Введите сообщение для рассылки.")
    await ApplicationForm.waiting_for_acceptance.set()

# Отправка рассылки всем пользователям
@dp.message_handler(state=ApplicationForm.waiting_for_acceptance)
async def process_broadcast(message: types.Message, state: FSMContext):
    for user_id in user_ids:
        try:
            await bot.send_message(user_id, message.text)
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")

    await message.reply("Рассылка завершена!")
    await state.finish()

# Обработка показа всех заявок
@dp.callback_query_handler(lambda c: c.data == "show_applications")
async def show_applications(callback_query: types.CallbackQuery):
    if applications:
        applications_text = "Нерассмотренные заявки:\n\n"
        for app_id, app_data in applications.items():
            applications_text += (f"Заявка #{app_id} от {app_data['user_id']}:\n"
                                  f"{app_data['application_text']}\n\n")
        await bot.send_message(callback_query.from_user.id, applications_text)
    else:
        await bot.send_message(callback_query.from_user.id, "Нет нерассмотренных заявок.")
    await callback_query.answer()

# Запуск бота
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    loop.run_until_complete(load_users())
    executor.start_polling(dp, skip_updates=True)
