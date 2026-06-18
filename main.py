import asyncio
import logging
import sqlite3
import random
import string
import os
from datetime import datetime
from typing import Optional, List, Dict
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# -------- Конфигурация --------
BOT_TOKEN = os.getenv('8700350538:AAHg9xfB6n_EK77xLoRoOPL6xEHjNZvAWXg')
ADMIN_ID = int(os.getenv('ADMIN_ID', '8478884644'))
CHANNEL_LINK = os.getenv('CHANNEL_LINK', '@LUDO_GMP')
DB_PATH = 'gmp.db'

# Настройки
MIN_CHECK_AMOUNT = 0.1
REFERRAL_BONUS = 1
MAX_CHECKS_PER_DAY = 10

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# -------- База данных --------
async def init_db():
    """Инициализация базы данных"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица пользователей
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL DEFAULT 0,
                ref_link TEXT,
                referrer_id INTEGER,
                reg_date TEXT
            )
        ''')
        
        # Таблица чеков
        await db.execute('''
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                creator_id INTEGER,
                amount REAL,
                status TEXT DEFAULT 'active',
                created_at TEXT,
                activated_by INTEGER,
                activated_at TEXT
            )
        ''')
        
        # Таблица депозитов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                screenshot TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                confirmed_by INTEGER,
                confirmed_at TEXT
            )
        ''')
        
        # Таблица выводов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                wallet TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                confirmed_by INTEGER,
                confirmed_at TEXT
            )
        ''')
        
        # Таблица промокодов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS promos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE,
                reward REAL,
                uses_left INTEGER,
                created_by INTEGER,
                created_at TEXT
            )
        ''')
        
        # Таблица использований промокодов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS promo_uses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                promo_id INTEGER,
                user_id INTEGER,
                used_at TEXT,
                UNIQUE(promo_id, user_id)
            )
        ''')
        
        # Таблица транзакций
        await db.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT,
                amount REAL,
                description TEXT,
                created_at TEXT
            )
        ''')
        
        await db.commit()

async def get_user(user_id: int) -> Optional[Dict]:
    """Получить пользователя из БД"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM users WHERE id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'username': row[1],
                    'balance': row[2],
                    'ref_link': row[3],
                    'referrer_id': row[4],
                    'reg_date': row[5]
                }
    return None

async def create_user(user_id: int, username: str = None, referrer_id: int = None):
    """Создать нового пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        ref_link = f"ref_{user_id}"
        
        await db.execute('''
            INSERT INTO users (id, username, ref_link, referrer_id, reg_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, ref_link, referrer_id, now))
        
        # Начисляем бонус рефереру
        if referrer_id:
            await db.execute('''
                UPDATE users SET balance = balance + ? WHERE id = ?
            ''', (REFERRAL_BONUS, referrer_id))
            
            await db.execute('''
                INSERT INTO transactions (user_id, type, amount, description, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (referrer_id, 'referral', REFERRAL_BONUS, f'Реферал {user_id}', now))
        
        await db.commit()

async def create_check(creator_id: int, amount: float, custom_code: str = None) -> Dict:
    """Создать новый чек"""
    if custom_code:
        code = custom_code.upper()
    else:
        chars = string.ascii_uppercase + string.digits
        code = 'GMP-' + ''.join(random.choices(chars, k=6))
    
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        
        # Списываем сумму с баланса
        await db.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (amount, creator_id))
        
        # Создаем чек
        await db.execute('''
            INSERT INTO checks (code, creator_id, amount, created_at)
            VALUES (?, ?, ?, ?)
        ''', (code, creator_id, amount, now))
        
        # Транзакция
        await db.execute('''
            INSERT INTO transactions (user_id, type, amount, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (creator_id, 'check_create', -amount, f'Создание чека {code}', now))
        
        await db.commit()
        
        return {'code': code, 'amount': amount, 'created_at': now}

async def activate_check(code: str, user_id: int) -> Dict:
    """Активировать чек"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем чек
        async with db.execute('SELECT * FROM checks WHERE code = ? AND status = "active"', (code,)) as cursor:
            check = await cursor.fetchone()
            if not check:
                return {'success': False, 'error': '❌ Чек не найден или уже использован'}
        
        # Нельзя активировать свой чек
        if check[2] == user_id:
            return {'success': False, 'error': '❌ Нельзя активировать свой собственный чек'}
        
        now = datetime.now().isoformat()
        
        # Начисляем GMP
        await db.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (check[3], user_id))
        
        # Обновляем статус чека
        await db.execute('''
            UPDATE checks SET status = "used", activated_by = ?, activated_at = ?
            WHERE code = ?
        ''', (user_id, now, code))
        
        # Транзакция
        await db.execute('''
            INSERT INTO transactions (user_id, type, amount, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, 'check_activate', check[3], f'Активация чека {code}', now))
        
        await db.commit()
        
        return {'success': True, 'amount': check[3], 'creator_id': check[2]}

async def delete_check(code: str, user_id: int) -> Dict:
    """Удалить чек (возврат GMP создателю)"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM checks WHERE code = ? AND status = "active"', (code,)) as cursor:
            check = await cursor.fetchone()
            if not check:
                return {'success': False, 'error': '❌ Чек не найден или уже использован'}
        
        if check[2] != user_id:
            return {'success': False, 'error': '❌ Это не ваш чек'}
        
        now = datetime.now().isoformat()
        
        # Возвращаем GMP
        await db.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (check[3], user_id))
        
        # Обновляем статус
        await db.execute('UPDATE checks SET status = "deleted" WHERE code = ?', (code,))
        
        # Транзакция
        await db.execute('''
            INSERT INTO transactions (user_id, type, amount, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, 'check_delete', check[3], f'Удаление чека {code}', now))
        
        await db.commit()
        
        return {'success': True, 'amount': check[3]}

async def get_user_checks(user_id: int, status: str = None) -> List[Dict]:
    """Получить все чеки пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        query = 'SELECT * FROM checks WHERE creator_id = ?'
        params = [user_id]
        
        if status:
            query += ' AND status = ?'
            params.append(status)
        
        query += ' ORDER BY created_at DESC'
        
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            
        return [{
            'id': row[0],
            'code': row[1],
            'creator_id': row[2],
            'amount': row[3],
            'status': row[4],
            'created_at': row[5],
            'activated_by': row[6],
            'activated_at': row[7]
        } for row in rows]

async def create_promo(code: str, reward: float, uses_left: int, admin_id: int) -> bool:
    """Создать промокод"""
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        try:
            await db.execute('''
                INSERT INTO promos (code, reward, uses_left, created_by, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (code.upper(), reward, uses_left, admin_id, now))
            await db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

async def activate_promo(code: str, user_id: int) -> Dict:
    """Активировать промокод"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM promos WHERE code = ? AND uses_left > 0', (code.upper(),)) as cursor:
            promo = await cursor.fetchone()
            if not promo:
                return {'success': False, 'error': '❌ Промокод не найден или истек'}
        
        # Проверяем, не использовал ли пользователь этот промо
        async with db.execute('SELECT * FROM promo_uses WHERE promo_id = ? AND user_id = ?', (promo[0], user_id)) as cursor:
            if await cursor.fetchone():
                return {'success': False, 'error': '❌ Вы уже использовали этот промокод'}
        
        now = datetime.now().isoformat()
        
        # Начисляем награду
        await db.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (promo[2], user_id))
        
        # Уменьшаем количество использований
        await db.execute('UPDATE promos SET uses_left = uses_left - 1 WHERE id = ?', (promo[0],))
        
        # Записываем использование
        await db.execute('''
            INSERT INTO promo_uses (promo_id, user_id, used_at)
            VALUES (?, ?, ?)
        ''', (promo[0], user_id, now))
        
        # Транзакция
        await db.execute('''
            INSERT INTO transactions (user_id, type, amount, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, 'promo', promo[2], f'Активация промокода {code}', now))
        
        await db.commit()
        
        return {'success': True, 'amount': promo[2]}

# -------- Клавиатуры --------
def main_menu(user_id: int = None) -> InlineKeyboardMarkup:
    """Главное меню"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text='🎫 Мои чеки', callback_data='my_checks'),
        InlineKeyboardButton(text='➕ Создать чек', callback_data='create_check')
    )
    builder.row(
        InlineKeyboardButton(text='💳 Депозит', callback_data='deposit'),
        InlineKeyboardButton(text='📤 Вывод', callback_data='withdraw')
    )
    builder.row(
        InlineKeyboardButton(text='👥 Рефка', callback_data='referral'),
        InlineKeyboardButton(text='ℹ️ Помощь', callback_data='help')
    )
    
    if user_id == ADMIN_ID:
        builder.row(
            InlineKeyboardButton(text='👑 Админ-панель', callback_data='admin_panel')
        )
    
    return builder.as_markup()

def check_actions(check_code: str, is_creator: bool = False) -> InlineKeyboardMarkup:
    """Кнопки для чека"""
    builder = InlineKeyboardBuilder()
    
    # Кнопка активации
    builder.row(
        InlineKeyboardButton(
            text='▶️ Активировать',
            url=f'https://t.me/{bot.username}?start=check_{check_code}'
        )
    )
    
    # Кнопки поделиться/переслать/удалить
    buttons = []
    buttons.append(InlineKeyboardButton(
        text='📤 Поделиться',
        switch_inline_query=f'🎫 Чек {check_code}\nСумма: ... GMP\nАктивируй: https://t.me/{bot.username}?start=check_{check_code}'
    ))
    buttons.append(InlineKeyboardButton(
        text='📨 Переслать',
        callback_data=f'forward_{check_code}'
    ))
    
    if is_creator:
        buttons.append(InlineKeyboardButton(
            text='🗑 Удалить',
            callback_data=f'delete_{check_code}'
        ))
    
    builder.row(*buttons)
    
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='back_to_menu')
    )
    
    return builder.as_markup()

def my_checks_menu(active_count: int, used_count: int) -> InlineKeyboardMarkup:
    """Меню моих чеков"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text='📋 Все', callback_data='checks_all'),
        InlineKeyboardButton(text=f'✅ Активные ({active_count})', callback_data='checks_active'),
        InlineKeyboardButton(text=f'❌ Использованные ({used_count})', callback_data='checks_used')
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='back_to_menu')
    )
    
    return builder.as_markup()

def check_list_buttons(checks: list, page: int = 0) -> InlineKeyboardMarkup:
    """Список чеков с пагинацией"""
    builder = InlineKeyboardBuilder()
    
    start = page * 5
    end = start + 5
    checks_page = checks[start:end]
    
    for check in checks_page:
        status_icon = '✅' if check['status'] == 'active' else '❌'
        builder.row(
            InlineKeyboardButton(
                text=f'{status_icon} {check["code"]} | {check["amount"]} GMP',
                callback_data=f'check_{check["code"]}'
            )
        )
    
    # Пагинация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton('◀️', callback_data=f'checks_page_{page-1}'))
    if end < len(checks):
        nav_buttons.append(InlineKeyboardButton('▶️', callback_data=f'checks_page_{page+1}'))
    
    if nav_buttons:
        builder.row(*nav_buttons)
    
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='my_checks')
    )
    
    return builder.as_markup()

def deposit_menu() -> InlineKeyboardMarkup:
    """Меню депозита"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text='📤 Отправить скриншот', callback_data='send_screenshot')
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='back_to_menu')
    )
    
    return builder.as_markup()

def admin_menu() -> InlineKeyboardMarkup:
    """Админ-панель"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text='📥 Пополнения', callback_data='admin_deposits'),
        InlineKeyboardButton(text='📤 Выводы', callback_data='admin_withdrawals')
    )
    builder.row(
        InlineKeyboardButton(text='🎫 Чеки', callback_data='admin_checks'),
        InlineKeyboardButton(text='🎁 Промокоды', callback_data='admin_promos')
    )
    builder.row(
        InlineKeyboardButton(text='📨 Рассылка', callback_data='admin_mailing'),
        InlineKeyboardButton(text='👥 Пользователи', callback_data='admin_users')
    )
    builder.row(
        InlineKeyboardButton(text='⚙️ Настройки', callback_data='admin_settings'),
        InlineKeyboardButton(text='🔙 Назад', callback_data='back_to_menu')
    )
    
    return builder.as_markup()

def admin_confirm_buttons(item_id: int, type: str = 'deposit') -> InlineKeyboardMarkup:
    """Кнопки подтверждения для админа"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text='✅ Подтвердить', callback_data=f'confirm_{type}_{item_id}'),
        InlineKeyboardButton(text='❌ Отклонить', callback_data=f'reject_{type}_{item_id}')
    )
    
    return builder.as_markup()

# -------- Состояния FSM --------
class CheckStates(StatesGroup):
    waiting_amount = State()

class DepositStates(StatesGroup):
    waiting_screenshot = State()

class WithdrawStates(StatesGroup):
    waiting_amount = State()
    waiting_wallet = State()

class AdminStates(StatesGroup):
    waiting_give_user = State()
    waiting_give_amount = State()
    waiting_take_user = State()
    waiting_take_amount = State()
    waiting_promo_code = State()
    waiting_promo_reward = State()
    waiting_promo_uses = State()
    waiting_mailing_text = State()
    waiting_mailing_button = State()

# -------- Обработчики --------
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    username = message.from_user.username
    
    # Проверяем реферала
    args = message.text.split()
    referrer_id = None
    
    if len(args) > 1:
        if args[1].startswith('ref_'):
            referrer_id = int(args[1].replace('ref_', ''))
            if referrer_id == user_id:
                referrer_id = None
        elif args[1].startswith('check_'):
            # Активация чека по ссылке
            code = args[1].replace('check_', '')
            result = await activate_check(code, user_id)
            if result['success']:
                await message.answer(
                    f'✅ Чек активирован!\n'
                    f'Вам зачислено {result["amount"]} GMP'
                )
            else:
                await message.answer(result['error'])
    
    # Проверяем пользователя в БД
    user = await get_user(user_id)
    if not user:
        await create_user(user_id, username, referrer_id)
        
        await message.answer(
            f'👋 Добро пожаловать, {message.from_user.first_name}!\n\n'
            f'💰 Баланс: 0 GMP\n'
            f'📢 Наш канал: {CHANNEL_LINK}\n\n'
            f'Используйте меню для навигации:',
            reply_markup=main_menu(user_id)
        )
    else:
        await message.answer(
            f'🏠 Главное меню\n\n'
            f'💰 Баланс: {user["balance"]} GMP\n'
            f'📢 Наш канал: {CHANNEL_LINK}',
            reply_markup=main_menu(user_id)
        )

@dp.callback_query(F.data == 'back_to_menu')
async def back_to_menu(callback: CallbackQuery):
    """Возврат в главное меню"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    await callback.message.edit_text(
        f'🏠 Главное меню\n\n'
        f'💰 Баланс: {user["balance"]} GMP\n'
        f'📢 Наш канал: {CHANNEL_LINK}',
        reply_markup=main_menu(user_id)
    )
    await callback.answer()

@dp.callback_query(F.data == 'create_check')
async def create_check_start(callback: CallbackQuery, state: FSMContext):
    """Начало создания чека"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    await callback.message.edit_text(
        f'💰 Введите сумму чека:\n'
        f'(мин: 0.1 GMP)\n'
        f'Ваш баланс: {user["balance"]} GMP\n\n'
        f'Чтобы создать чек со своим кодом, введите:\n'
        f'сумма|код (например: 10|MYCODE)',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔙 Назад', callback_data='back_to_menu')]
        ])
    )
    await state.set_state(CheckStates.waiting_amount)
    await callback.answer()

@dp.message(CheckStates.waiting_amount)
async def process_check_amount(message: Message, state: FSMContext):
    """Обработка суммы для чека"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    try:
        text = message.text.strip()
        
        # Проверяем, есть ли кастомный код
        if '|' in text:
            amount_str, custom_code = text.split('|')
            amount = float(amount_str.strip())
            custom_code = custom_code.strip().upper()
            
            if len(custom_code) < 3 or len(custom_code) > 20:
                await message.answer('❌ Код должен быть от 3 до 20 символов')
                return
        else:
            amount = float(text)
            custom_code = None
        
        # Проверяем сумму
        if amount < MIN_CHECK_AMOUNT:
            await message.answer(f'❌ Минимальная сумма: {MIN_CHECK_AMOUNT} GMP')
            return
        
        if amount > user['balance']:
            await message.answer(f'❌ Недостаточно средств. Ваш баланс: {user["balance"]} GMP')
            return
        
        # Проверяем лимит чеков в день
        checks = await get_user_checks(user_id, 'active')
        if len(checks) >= MAX_CHECKS_PER_DAY:
            await message.answer(f'❌ Вы создали максимум {MAX_CHECKS_PER_DAY} активных чеков в день')
            return
        
        # Создаем чек
        result = await create_check(user_id, amount, custom_code)
        
        # Отправляем сообщение с чеком
        check_message = (
            f'🎫 Чек создан!\n\n'
            f'Сумма: {amount} GMP\n'
            f'Код: {result["code"]}\n\n'
            f'🔗 Ссылка для активации:\n'
            f'https://t.me/{bot.username}?start=check_{result["code"]}\n\n'
            f'📢 Наш канал: {CHANNEL_LINK}'
        )
        
        await message.answer(
            check_message,
            reply_markup=check_actions(result['code'], True)
        )
        
        await state.clear()
        
    except ValueError:
        await message.answer('❌ Введите корректную сумму (число)')

@dp.callback_query(F.data == 'my_checks')
async def my_checks(callback: CallbackQuery):
    """Показать мои чеки"""
    user_id = callback.from_user.id
    
    active_checks = await get_user_checks(user_id, 'active')
    used_checks = await get_user_checks(user_id, 'used')
    
    total_active = sum(c['amount'] for c in active_checks)
    total_used = sum(c['amount'] for c in used_checks)
    
    text = (
        f'📊 Мои чеки\n\n'
        f'✅ Активные: {len(active_checks)} чеков на {total_active:.1f} GMP\n'
        f'❌ Использовано: {len(used_checks)} чеков на {total_used:.1f} GMP'
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=my_checks_menu(len(active_checks), len(used_checks))
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('checks_'))
async def show_checks_list(callback: CallbackQuery):
    """Показать список чеков с фильтром"""
    user_id = callback.from_user.id
    filter_type = callback.data.split('_')[1]
    
    if filter_type == 'all':
        checks = await get_user_checks(user_id)
    elif filter_type == 'active':
        checks = await get_user_checks(user_id, 'active')
    elif filter_type == 'used':
        checks = await get_user_checks(user_id, 'used')
    else:
        checks = []
    
    if not checks:
        await callback.answer('❌ Чеков не найдено')
        return
    
    await callback.message.edit_text(
        f'📋 Список чеков:',
        reply_markup=check_list_buttons(checks, 0)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('checks_page_'))
async def checks_page(callback: CallbackQuery):
    """Пагинация списка чеков"""
    user_id = callback.from_user.id
    page = int(callback.data.split('_')[2])
    
    # Получаем все чеки (временное решение, нужно сохранять фильтр)
    checks = await get_user_checks(user_id)
    
    await callback.message.edit_text(
        f'📋 Список чеков:',
        reply_markup=check_list_buttons(checks, page)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('check_'))
async def show_check_details(callback: CallbackQuery):
    """Показать детали чека"""
    code = callback.data.split('_')[1]
    user_id = callback.from_user.id
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM checks WHERE code = ?', (code,)) as cursor:
            check = await cursor.fetchone()
    
    if not check:
        await callback.answer('❌ Чек не найден')
        return
    
    is_creator = check[2] == user_id
    status_icon = '✅ Активен' if check[4] == 'active' else '❌ Использован'
    
    text = (
        f'🎫 Чек {code}\n\n'
        f'Сумма: {check[3]} GMP\n'
        f'Статус: {status_icon}\n'
        f'Создан: {check[5]}'
    )
    
    if check[4] == 'used':
        text += f'\nАктивирован: {check[7]}'
    
    await callback.message.edit_text(
        text,
        reply_markup=check_actions(code, is_creator)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('delete_'))
async def delete_check_callback(callback: CallbackQuery):
    """Удалить чек"""
    code = callback.data.split('_')[1]
    user_id = callback.from_user.id
    
    result = await delete_check(code, user_id)
    
    if result['success']:
        await callback.message.edit_text(
            f'🗑 Чек {code} удален\n'
            f'Вам возвращено {result["amount"]} GMP',
            reply_markup=main_menu(user_id)
        )
    else:
        await callback.answer(result['error'])
    
    await callback.answer()

@dp.callback_query(F.data == 'deposit')
async def deposit_menu_callback(callback: CallbackQuery):
    """Меню депозита"""
    await callback.message.edit_text(
        f'💳 Пополнение GMP\n\n'
        f'Реквизиты для пополнения:\n'
        f'(вставьте свои реквизиты)\n\n'
        f'После перевода нажмите кнопку "Отправить скриншот"',
        reply_markup=deposit_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == 'send_screenshot')
async def send_screenshot(callback: CallbackQuery, state: FSMContext):
    """Запрос скриншота для депозита"""
    await callback.message.edit_text(
        '📸 Отправьте скриншот перевода\n\n'
        'После отправки заявка будет отправлена админу',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔙 Назад', callback_data='deposit')]
        ])
    )
    await state.set_state(DepositStates.waiting_screenshot)
    await callback.answer()

@dp.message(DepositStates.waiting_screenshot)
async def process_screenshot(message: Message, state: FSMContext):
    """Обработка скриншота депозита"""
    if not message.photo:
        await message.answer('❌ Пожалуйста, отправьте фото')
        return
    
    user_id = message.from_user.id
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    
    # Сохраняем заявку в БД
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        cursor = await db.execute('''
            INSERT INTO deposits (user_id, screenshot, created_at)
            VALUES (?, ?, ?)
        ''', (user_id, file.file_id, now))
        await db.commit()
        deposit_id = cursor.lastrowid
    
    # Отправляем админу
    await bot.send_photo(
        ADMIN_ID,
        photo=photo.file_id,
        caption=f'📥 Новая заявка на пополнение\n'
                f'Пользователь: @{message.from_user.username or user_id}\n'
                f'ID: {user_id}\n'
                f'Сумма: (укажите при подтверждении)',
        reply_markup=admin_confirm_buttons(deposit_id, 'deposit')
    )
    
    await message.answer(
        '✅ Скриншот отправлен! Ожидайте подтверждения.',
        reply_markup=main_menu(user_id)
    )
    await state.clear()

@dp.callback_query(F.data == 'withdraw')
async def withdraw_start(callback: CallbackQuery, state: FSMContext):
    """Начало вывода"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    await callback.message.edit_text(
        f'💰 Введите сумму для вывода:\n'
        f'(доступно: {user["balance"]} GMP)',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔙 Назад', callback_data='back_to_menu')]
        ])
    )
    await state.set_state(WithdrawStates.waiting_amount)
    await callback.answer()

@dp.message(WithdrawStates.waiting_amount)
async def process_withdraw_amount(message: Message, state: FSMContext):
    """Обработка суммы вывода"""
    try:
        amount = float(message.text.strip())
        user_id = message.from_user.id
        user = await get_user(user_id)
        
        if amount <= 0:
            await message.answer('❌ Сумма должна быть больше 0')
            return
        
        if amount > user['balance']:
            await message.answer(f'❌ Недостаточно средств. Ваш баланс: {user["balance"]} GMP')
            return
        
        await state.update_data(withdraw_amount=amount)
        
        await message.answer(
            f'📤 Введите ваш кошелек/реквизиты для вывода {amount} GMP:',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='🔙 Назад', callback_data='back_to_menu')]
            ])
        )
        await state.set_state(WithdrawStates.waiting_wallet)
        
    except ValueError:
        await message.answer('❌ Введите корректную сумму (число)')

@dp.message(WithdrawStates.waiting_wallet)
async def process_withdraw_wallet(message: Message, state: FSMContext):
    """Обработка кошелька для вывода"""
    wallet = message.text.strip()
    user_id = message.from_user.id
    data = await state.get_data()
    amount = data.get('withdraw_amount')
    
    if not wallet:
        await message.answer('❌ Введите реквизиты для вывода')
        return
    
    # Создаем заявку на вывод
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        
        # Списываем сумму
        await db.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (amount, user_id))
        
        # Создаем заявку
        cursor = await db.execute('''
            INSERT INTO withdrawals (user_id, amount, wallet, created_at)
            VALUES (?, ?, ?, ?)
        ''', (user_id, amount, wallet, now))
        await db.commit()
        withdraw_id = cursor.lastrowid
        
        # Транзакция
        await db.execute('''
            INSERT INTO transactions (user_id, type, amount, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, 'withdraw', -amount, f'Вывод {amount} GMP на {wallet}', now))
        await db.commit()
    
    # Отправляем админу
    await bot.send_message(
        ADMIN_ID,
        f'📤 Новая заявка на вывод\n'
        f'Пользователь: @{message.from_user.username or user_id}\n'
        f'ID: {user_id}\n'
        f'Сумма: {amount} GMP\n'
        f'Кошелек: {wallet}',
        reply_markup=admin_confirm_buttons(withdraw_id, 'withdraw')
    )
    
    await message.answer(
        f'✅ Заявка на вывод {amount} GMP отправлена!\n'
        f'Ожидайте подтверждения.',
        reply_markup=main_menu(user_id)
    )
    await state.clear()

@dp.callback_query(F.data == 'referral')
async def referral_menu(callback: CallbackQuery):
    """Реферальное меню"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    # Считаем рефералов
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT COUNT(*) FROM users WHERE referrer_id = ?', (user_id,)) as cursor:
            count = await cursor.fetchone()
            ref_count = count[0] if count else 0
    
    text = (
        f'👥 Реферальная система\n\n'
        f'Ваша реф-ссылка:\n'
        f'https://t.me/{bot.username}?start=ref_{user_id}\n\n'
        f'Приглашено: {ref_count} человек\n'
        f'Бонус за каждого: +{REFERRAL_BONUS} GMP\n\n'
        f'📤 Поделитесь ссылкой с друзьями!'
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text='📤 Поделиться ссылкой',
            switch_inline_query=f'Присоединяйся! Получи бонус: https://t.me/{bot.username}?start=ref_{user_id}'
        )
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='back_to_menu')
    )
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == 'help')
async def help_menu(callback: CallbackQuery):
    """Меню помощи"""
    text = (
        f'ℹ️ Помощь\n\n'
        f'🎫 Чек - создание чека на GMP\n'
        f'▶️ Активировать - активация чека\n'
        f'📤 Поделиться - поделиться чеком\n'
        f'📨 Переслать - переслать чек\n'
        f'🗑 Удалить - удалить свой чек\n\n'
        f'💳 Депозит - пополнение баланса\n'
        f'📤 Вывод - вывод GMP\n'
        f'👥 Рефка - реферальная система\n\n'
        f'📢 Наш канал: {CHANNEL_LINK}'
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔙 Назад', callback_data='back_to_menu')]
        ])
    )
    await callback.answer()

# -------- Админ-панель --------
@dp.callback_query(F.data == 'admin_panel')
async def admin_panel(callback: CallbackQuery):
    """Админ-панель"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    await callback.message.edit_text(
        '👑 Админ-панель\n\n'
        'Выберите раздел:',
        reply_markup=admin_menu()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('confirm_'))
async def admin_confirm(callback: CallbackQuery):
    """Подтверждение заявки"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    _, type, item_id = callback.data.split('_')
    item_id = int(item_id)
    
    now = datetime.now().isoformat()
    
    async with aiosqlite.connect(DB_PATH) as db:
        if type == 'deposit':
            # Для депозита - нужно ввести сумму
            await callback.message.answer('📥 Введите сумму депозита в GMP:')
            # Здесь нужно реализовать ввод суммы
            await db.execute('''
                UPDATE deposits SET status = "confirmed", confirmed_by = ?, confirmed_at = ?
                WHERE id = ?
            ''', (ADMIN_ID, now, item_id))
            await db.commit()
            
            await callback.message.edit_text('✅ Депозит подтвержден!')
            
        elif type == 'withdraw':
            # Для вывода - подтверждаем
            async with db.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (item_id,)) as cursor:
                withdraw = await cursor.fetchone()
            
            if withdraw:
                await db.execute('''
                    UPDATE withdrawals SET status = "confirmed", confirmed_by = ?, confirmed_at = ?
                    WHERE id = ?
                ''', (ADMIN_ID, now, item_id))
                await db.commit()
                
                await bot.send_message(
                    withdraw[0],
                    f'✅ Ваш вывод {withdraw[1]} GMP подтвержден и отправлен!'
                )
            
            await callback.message.edit_text('✅ Вывод подтвержден!')
    
    await callback.answer()

@dp.callback_query(F.data.startswith('reject_'))
async def admin_reject(callback: CallbackQuery):
    """Отклонение заявки"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    _, type, item_id = callback.data.split('_')
    item_id = int(item_id)
    
    async with aiosqlite.connect(DB_PATH) as db:
        if type == 'deposit':
            await db.execute('''
                UPDATE deposits SET status = "rejected" WHERE id = ?
            ''', (item_id,))
            await db.commit()
            
        elif type == 'withdraw':
            # Возвращаем деньги пользователю
            async with db.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (item_id,)) as cursor:
                withdraw = await cursor.fetchone()
            
            if withdraw:
                await db.execute('''
                    UPDATE users SET balance = balance + ? WHERE id = ?
                ''', (withdraw[1], withdraw[0]))
                
                await db.execute('''
                    UPDATE withdrawals SET status = "rejected" WHERE id = ?
                ''', (item_id,))
                await db.commit()
                
                await bot.send_message(
                    withdraw[0],
                    f'❌ Ваш вывод {withdraw[1]} GMP отклонен. Средства возвращены на баланс.'
                )
    
    await callback.message.edit_text('❌ Заявка отклонена')
    await callback.answer()

@dp.callback_query(F.data == 'admin_promos')
async def admin_promos(callback: CallbackQuery, state: FSMContext):
    """Управление промокодами"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    await callback.message.edit_text(
        '🎁 Управление промокодами\n\n'
        'Введите команду:\n'
        'создать|код|сумма|лимит\n'
        'например: создать|WELCOME|10|100\n\n'
        'Или отправьте "список" для просмотра',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔙 Назад', callback_data='admin_panel')]
        ])
    )
    await state.set_state(AdminStates.waiting_promo_code)
    await callback.answer()

@dp.message(AdminStates.waiting_promo_code)
async def process_promo_command(message: Message, state: FSMContext):
    """Обработка команд промокодов"""
    if message.from_user.id != ADMIN_ID:
        return
    
    text = message.text.strip()
    
    if text.lower() == 'список':
        # Показываем список промокодов
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT * FROM promos') as cursor:
                promos = await cursor.fetchall()
        
        if not promos:
            await message.answer('📭 Нет активных промокодов')
        else:
            text = '🎁 Список промокодов:\n\n'
            for promo in promos:
                text += f'• {promo[1]} | {promo[2]} GMP | осталось: {promo[3]}\n'
            
            await message.answer(text)
        return
    
    if text.startswith('создать|'):
        parts = text.split('|')
        if len(parts) == 4:
            _, code, reward, uses = parts
            try:
                reward = float(reward)
                uses = int(uses)
                
                success = await create_promo(code, reward, uses, ADMIN_ID)
                if success:
                    await message.answer(f'✅ Промокод {code} создан!\n{reward} GMP | {uses} использований')
                else:
                    await message.answer('❌ Промокод с таким кодом уже существует')
            except ValueError:
                await message.answer('❌ Неверный формат суммы или лимита')
        else:
            await message.answer('❌ Формат: создать|код|сумма|лимит')
    else:
        await message.answer('❌ Неизвестная команда')
    
    await state.clear()

@dp.callback_query(F.data == 'admin_mailing')
async def admin_mailing(callback: CallbackQuery, state: FSMContext):
    """Рассылка"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    await callback.message.edit_text(
        '📨 Рассылка\n\n'
        'Отправьте текст для рассылки.\n'
        'Для добавления кнопки напишите:\n'
        'текст|кнопка|ссылка',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔙 Назад', callback_data='admin_panel')]
        ])
    )
    await state.set_state(AdminStates.waiting_mailing_text)
    await callback.answer()

@dp.message(AdminStates.waiting_mailing_text)
async def process_mailing(message: Message, state: FSMContext):
    """Обработка рассылки"""
    if message.from_user.id != ADMIN_ID:
        return
    
    text = message.text.strip()
    
    # Проверяем, есть ли кнопка
    if '|' in text:
        parts = text.split('|')
        if len(parts) == 3:
            msg_text, btn_text, btn_url = parts
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=btn_text.strip(), url=btn_url.strip())]
            ])
        else:
            await message.answer('❌ Формат: текст|кнопка|ссылка')
            return
    else:
        msg_text = text
        markup = None
    
    # Получаем всех пользователей
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id FROM users') as cursor:
            users = await cursor.fetchall()
    
    if not users:
        await message.answer('📭 Нет пользователей для рассылки')
        await state.clear()
        return
    
    await message.answer(f'📨 Начинаю рассылку {len(users)} пользователям...')
    
    count = 0
    for user in users:
        try:
            await bot.send_message(user[0], msg_text, reply_markup=markup)
            count += 1
            await asyncio.sleep(0.05)  # Защита от лимитов
        except:
            pass
    
    await message.answer(f'✅ Рассылка завершена! Отправлено {count} сообщений')
    await state.clear()

# -------- Запуск --------
async def main():
    """Запуск бота"""
    # Импортируем aiosqlite
    global aiosqlite
    import aiosqlite
    
    await init_db()
    logger.info('🚀 Бот запущен!')
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
