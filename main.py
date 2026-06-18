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
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ChatMember
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# -------- Конфигурация --------
BOT_TOKEN = "8700350538:AAHg9xfB6n_EK77xLoRoOPL6xEHjNZvAWXg"
ADMIN_ID = 8478884644
CHANNEL_LINK = "@LUDO_GMP"  # Канал для подписки
DB_PATH = "gmp.db"

# Настройки
MIN_CHECK_AMOUNT = 0.1
REFERRAL_BONUS = 1
MAX_CHECKS_PER_DAY = 10

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=str(BOT_TOKEN))
dp = Dispatcher()

import aiosqlite

# -------- ИГРЫ --------
GOLD_LEVELS = [
    {"multiplier": 2, "level": 1},
    {"multiplier": 4, "level": 2},
    {"multiplier": 8, "level": 3},
    {"multiplier": 16, "level": 4},
    {"multiplier": 32, "level": 5},
    {"multiplier": 64, "level": 6},
    {"multiplier": 128, "level": 7},
    {"multiplier": 256, "level": 8},
    {"multiplier": 512, "level": 9},
    {"multiplier": 1024, "level": 10},
    {"multiplier": 2048, "level": 11},
    {"multiplier": 4096, "level": 12}
]
active_gold_games = {}

MINES_MULTIPLIERS = {
    1: 1.10, 2: 1.15, 3: 1.25, 4: 1.40, 5: 1.60,
    6: 1.80, 7: 2.00, 8: 2.25, 9: 2.45, 10: 2.85,
    11: 3.00, 12: 3.45, 13: 4.00, 14: 4.75, 15: 5.50,
    16: 6.00, 17: 6.75, 18: 7.00, 19: 8.50, 20: 9.65,
    21: 10.50, 22: 12.50, 23: 15.00, 24: 24.5
}
active_mines_games = {}

TOWER_MULTIPLIERS = {
    1: 1.10, 2: 1.25, 3: 1.50, 4: 1.75, 5: 2.25,
    6: 2.75, 7: 3.35, 8: 4.65, 9: 5.65
}
active_tower_games = {}

# -------- БАЗА ДАННЫХ --------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance REAL DEFAULT 0,
                ref_link TEXT,
                referrer_id INTEGER,
                reg_date TEXT,
                is_admin BOOLEAN DEFAULT 0
            )
        ''')
        
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
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS promo_uses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                promo_id INTEGER,
                user_id INTEGER,
                used_at TEXT,
                UNIQUE(promo_id, user_id)
            )
        ''')
        
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
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        await db.execute('''
            INSERT OR IGNORE INTO users (id, username, balance, is_admin, reg_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (ADMIN_ID, 'admin', 999999999, 1, datetime.now().isoformat()))
        
        await db.commit()
        logger.info('✅ База данных инициализирована')

async def get_user(user_id: int) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM users WHERE id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'username': row[1],
                    'first_name': row[2],
                    'balance': row[3],
                    'ref_link': row[4],
                    'referrer_id': row[5],
                    'reg_date': row[6],
                    'is_admin': row[7] if len(row) > 7 else 0
                }
    return None

async def create_user(user_id: int, username: str = None, first_name: str = None, referrer_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        ref_link = f"ref_{user_id}"
        
        await db.execute('''
            INSERT INTO users (id, username, first_name, ref_link, referrer_id, reg_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username, first_name, ref_link, referrer_id, now))
        
        if referrer_id and referrer_id != user_id:
            await db.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (REFERRAL_BONUS, referrer_id))
            await db.execute('''
                INSERT INTO transactions (user_id, type, amount, description, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (referrer_id, 'referral', REFERRAL_BONUS, f'Реферал {user_id}', now))
        
        await db.commit()

# -------- ПРОВЕРКА ПОДПИСКИ --------
async def check_subscription(user_id: int) -> bool:
    try:
        chat_member = await bot.get_chat_member(chat_id=CHANNEL_LINK, user_id=user_id)
        if chat_member.status in ['member', 'administrator', 'creator']:
            return True
        return False
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
        return False

# -------- КЛАВИАТУРЫ --------
def get_main_keyboard(user_id: int = None) -> ReplyKeyboardMarkup:
    """Главная клавиатура (Reply) - всегда внизу"""
    keyboard = [
        [KeyboardButton(text='🎫 Мои чеки'), KeyboardButton(text='➕ Создать чек')],
        [KeyboardButton(text='💳 Депозит'), KeyboardButton(text='📤 Вывод')],
        [KeyboardButton(text='🎁 Промо'), KeyboardButton(text='👥 Рефка')],
        [KeyboardButton(text='🎮 Мины'), KeyboardButton(text='🎮 Золото')],
        [KeyboardButton(text='🎮 Башня'), KeyboardButton(text='ℹ️ Помощь')]
    ]
    
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton(text='👑 Админ-панель')])
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# -------- ФУНКЦИИ ДЛЯ ЧЕКОВ --------
async def create_check(creator_id: int, amount: float, custom_code: str = None) -> Dict:
    if custom_code:
        code = custom_code.upper()
    else:
        chars = string.ascii_uppercase + string.digits
        code = 'GMP-' + ''.join(random.choices(chars, k=6))
    
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        
        if creator_id != ADMIN_ID:
            await db.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (amount, creator_id))
        
        await db.execute('''
            INSERT INTO checks (code, creator_id, amount, created_at)
            VALUES (?, ?, ?, ?)
        ''', (code, creator_id, amount, now))
        
        await db.execute('''
            INSERT INTO transactions (user_id, type, amount, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (creator_id, 'check_create', -amount, f'Создание чека {code}', now))
        
        await db.commit()
        return {'code': code, 'amount': amount, 'created_at': now}

async def activate_check(code: str, user_id: int) -> Dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM checks WHERE code = ? AND status = "active"', (code,)) as cursor:
            check = await cursor.fetchone()
            if not check:
                return {'success': False, 'error': '❌ Чек не найден или уже использован'}
        
        if check[2] == user_id:
            return {'success': False, 'error': '❌ Нельзя активировать свой чек'}
        
        now = datetime.now().isoformat()
        await db.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (check[3], user_id))
        
        await db.execute('''
            UPDATE checks SET status = "used", activated_by = ?, activated_at = ?
            WHERE code = ?
        ''', (user_id, now, code))
        
        await db.execute('''
            INSERT INTO transactions (user_id, type, amount, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, 'check_activate', check[3], f'Активация чека {code}', now))
        
        await db.commit()
        return {'success': True, 'amount': check[3], 'creator_id': check[2]}

async def delete_check(code: str, user_id: int) -> Dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM checks WHERE code = ? AND status = "active"', (code,)) as cursor:
            check = await cursor.fetchone()
            if not check:
                return {'success': False, 'error': '❌ Чек не найден или уже использован'}
        
        if check[2] != user_id:
            return {'success': False, 'error': '❌ Это не ваш чек'}
        
        now = datetime.now().isoformat()
        if user_id != ADMIN_ID:
            await db.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (check[3], user_id))
        
        await db.execute('UPDATE checks SET status = "deleted" WHERE code = ?', (code,))
        
        await db.execute('''
            INSERT INTO transactions (user_id, type, amount, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, 'check_delete', check[3], f'Удаление чека {code}', now))
        
        await db.commit()
        return {'success': True, 'amount': check[3]}

async def get_user_checks(user_id: int, status: str = None) -> List[Dict]:
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
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM promos WHERE code = ? AND uses_left > 0', (code.upper(),)) as cursor:
            promo = await cursor.fetchone()
            if not promo:
                return {'success': False, 'error': '❌ Промокод не найден или истек'}
        
        async with db.execute('SELECT * FROM promo_uses WHERE promo_id = ? AND user_id = ?', (promo[0], user_id)) as cursor:
            if await cursor.fetchone():
                return {'success': False, 'error': '❌ Вы уже использовали этот промокод'}
        
        now = datetime.now().isoformat()
        await db.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (promo[2], user_id))
        await db.execute('UPDATE promos SET uses_left = uses_left - 1 WHERE id = ?', (promo[0],))
        
        await db.execute('''
            INSERT INTO promo_uses (promo_id, user_id, used_at)
            VALUES (?, ?, ?)
        ''', (promo[0], user_id, now))
        
        await db.execute('''
            INSERT INTO transactions (user_id, type, amount, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, 'promo', promo[2], f'Активация промокода {code}', now))
        
        await db.commit()
        return {'success': True, 'amount': promo[2]}

async def delete_promo(code: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM promos WHERE code = ?', (code.upper(),))
        await db.commit()
        return True

async def get_all_promos() -> List[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM promos ORDER BY created_at DESC') as cursor:
            rows = await cursor.fetchall()
            
        return [{
            'id': row[0],
            'code': row[1],
            'reward': row[2],
            'uses_left': row[3],
            'created_by': row[4],
            'created_at': row[5]
        } for row in rows]

# -------- ФУНКЦИИ ДЛЯ ИГР --------
def format_number(num):
    if num >= 1_000_000_000_000:
        return f"{num/1_000_000_000_000:.2f}kkkk".replace(".00", "")
    elif num >= 1_000_000_000:
        return f"{num/1_000_000_000:.2f}kkk".replace(".00", "")
    elif num >= 1_000_000:
        return f"{num/1_000_000:.2f}kk".replace(".00", "")
    elif num >= 1_000:
        return f"{num:,}".replace(",", "'")
    return str(num)

def parse_bet(bet_str):
    bet_str = str(bet_str).lower().strip()
    if bet_str.isdigit():
        return int(bet_str)
    if bet_str.endswith('кккк'):
        return int(float(bet_str[:-4]) * 1_000_000_000_000)
    elif bet_str.endswith('ккк'):
        return int(float(bet_str[:-3]) * 1_000_000_000)
    elif bet_str.endswith('кк'):
        return int(float(bet_str[:-2]) * 1_000_000)
    elif bet_str.endswith('к'):
        return int(float(bet_str[:-1]) * 1_000)
    match = re.match(r'([\d.]+)(кккк|ккк|кк|к)', bet_str)
    if match:
        num = float(match.group(1))
        suffix = match.group(2)
        if suffix == 'кккк':
            return int(num * 1_000_000_000_000)
        elif suffix == 'ккк':
            return int(num * 1_000_000_000)
        elif suffix == 'кк':
            return int(num * 1_000_000)
        elif suffix == 'к':
            return int(num * 1_000)
    return None

# -------- ИГРА МИНЫ --------
def create_mines_board(mines_count: int) -> tuple:
    board = [["❓" for _ in range(5)] for _ in range(5)]
    mines_positions = []
    while len(mines_positions) < mines_count:
        x, y = random.randint(0, 4), random.randint(0, 4)
        if (x, y) not in mines_positions:
            mines_positions.append((x, y))
    return board, mines_positions

def get_mines_keyboard(board: list, game: Dict, show_mines: bool = False, user_id: int = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i in range(5):
        row = []
        for j in range(5):
            if show_mines and (i, j) in game["mines_positions"]:
                text = "💣"
            else:
                cell = board[i][j]
                if cell == "❓":
                    text = "❓"
                elif cell == "💎":
                    text = "💎"
                else:
                    text = "❓"
            row.append(InlineKeyboardButton(text=text, callback_data=f"mines_{user_id}_{i}_{j}"))
        builder.row(*row)
    
    if not show_mines and game["status"] == "playing":
        opened = sum(row.count("💎") for row in board)
        multiplier = MINES_MULTIPLIERS.get(opened, 1.0)
        win_amount = int(game["bet"] * multiplier)
        if win_amount > game["bet"]:
            builder.row(InlineKeyboardButton(text=f"💰 Забрать {format_number(win_amount)} GMP", callback_data=f"mines_cashout_{user_id}"))
    
    return builder.as_markup()

# -------- ИГРА ЗОЛОТО --------
def get_random_position(current_level: int) -> str:
    if random.random() < 0.3:
        if current_level % 3 == 0:
            return random.choice(["left", "right"])
        elif current_level % 2 == 0:
            return "left"
        else:
            return "right"
    else:
        return random.choice(["left", "right"])

def get_gold_table(game: Dict) -> str:
    lines = []
    bet = game["bet"]
    current_level = game["current_level"]

    for i in range(11, -1, -1):
        multiplier = GOLD_LEVELS[i]["multiplier"]
        win_amount = bet * multiplier

        if i < current_level:
            level_result = game["level_results"].get(i)
            if level_result:
                if level_result["correct"] == "left":
                    lines.append(f"|⭐|🧨| {format_number(win_amount)} GMP ({multiplier}x)")
                else:
                    lines.append(f"|🧨|⭐| {format_number(win_amount)} GMP ({multiplier}x)")
        elif i == current_level and game["status"] != "playing":
            if game["status"] == "lose":
                if game["selected_position"] == "left":
                    if game["level_results"][i]["correct"] == "left":
                        lines.append(f"|⭐|🧨| {format_number(win_amount)} GMP ({multiplier}x)")
                    else:
                        lines.append(f"|💥|🧨| {format_number(win_amount)} GMP ({multiplier}x)")
                else:
                    if game["level_results"][i]["correct"] == "right":
                        lines.append(f"|🧨|⭐| {format_number(win_amount)} GMP ({multiplier}x)")
                    else:
                        lines.append(f"|🧨|💥| {format_number(win_amount)} GMP ({multiplier}x)")
            else:
                level_result = game["level_results"].get(i)
                if level_result:
                    if level_result["correct"] == "left":
                        lines.append(f"|⭐|🧨| {format_number(win_amount)} GMP ({multiplier}x)")
                    else:
                        lines.append(f"|🧨|⭐| {format_number(win_amount)} GMP ({multiplier}x)")
        else:
            lines.append(f"|❓|❓| {format_number(win_amount)} GMP ({multiplier}x)")

    return "\n".join(lines)

def get_gold_keyboard(game: Dict, user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    if game["status"] == "playing":
        builder.row(
            InlineKeyboardButton("⬅️", callback_data=f"gold_{user_id}_left_{game['current_level']}"),
            InlineKeyboardButton("➡️", callback_data=f"gold_{user_id}_right_{game['current_level']}")
        )
        
        if game["current_level"] > 0:
            last_multiplier = GOLD_LEVELS[game["current_level"] - 1]["multiplier"]
            win_amount = int(game["bet"] * last_multiplier)
            builder.row(InlineKeyboardButton(text=f"💰 Забрать {format_number(win_amount)}", callback_data=f"gold_cashout_{user_id}"))
    
    return builder.as_markup()

# -------- ИГРА БАШНЯ --------
def get_tower_keyboard(game: Dict, user_id: int, show_mine: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    for row in range(8, -1, -1):
        row_buttons = []
        for col in range(5):
            if row < game["current_level"]:
                if (col, row) == game["opened_position"].get(row):
                    row_buttons.append(InlineKeyboardButton("💎", callback_data="none"))
                else:
                    row_buttons.append(InlineKeyboardButton("❓", callback_data="none"))
            elif row == game["current_level"]:
                if show_mine and (col, row) == game["mine_position"]:
                    row_buttons.append(InlineKeyboardButton("💣", callback_data="none"))
                else:
                    row_buttons.append(InlineKeyboardButton("❓", callback_data=f"tower_{user_id}_{col}_{row}"))
        builder.row(*row_buttons)
    
    if game["status"] == "playing" and game["current_level"] > 0:
        multiplier = TOWER_MULTIPLIERS.get(game["current_level"], 1.0)
        win_amount = int(game["bet"] * multiplier)
        builder.row(InlineKeyboardButton(text=f"💰 Забрать {format_number(win_amount)}", callback_data=f"tower_cashout_{user_id}"))
    
    return builder.as_markup()

# -------- СОСТОЯНИЯ FSM --------
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
    waiting_mailing_text = State()
    waiting_channel_add = State()

class PromoStates(StatesGroup):
    waiting_promo_code = State()

class GameStates(StatesGroup):
    waiting_bet = State()

# -------- ОБРАБОТЧИКИ --------
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Проверяем подписку
    if not await check_subscription(user_id):
        await message.answer(
            f"🔒 <b>Для использования бота необходимо подписаться на канал!</b>\n\n"
            f"📢 <a href='https://t.me/{CHANNEL_LINK.replace('@', '')}'>{CHANNEL_LINK}</a>\n\n"
            f"После подписки нажмите /start",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 ПЕРЕЙТИ В КАНАЛ", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")],
                [InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub")]
            ])
        )
        return
    
    args = message.text.split()
    referrer_id = None
    
    if len(args) > 1:
        if args[1].startswith('ref_'):
            referrer_id = int(args[1].replace('ref_', ''))
            if referrer_id == user_id:
                referrer_id = None
        elif args[1].startswith('check_'):
            code = args[1].replace('check_', '')
            result = await activate_check(code, user_id)
            if result['success']:
                await message.answer(f'✅ Чек активирован! Вам зачислено {result["amount"]} GMP')
                await message.answer(
                    f'🏠 Главное меню\n\n💰 Баланс: обновлен',
                    reply_markup=get_main_keyboard(user_id)
                )
                return
            else:
                await message.answer(result['error'])
                return
    
    user = await get_user(user_id)
    if not user:
        await create_user(user_id, username, first_name, referrer_id)
        
        text = f'👋 Добро пожаловать, {first_name}!\n\n💰 Баланс: 0 GMP\n📢 Наш канал: {CHANNEL_LINK}'
        if referrer_id:
            text += f'\n\n🎉 Вы пришли по реферальной ссылке!\nВаш реферер получил +{REFERRAL_BONUS} GMP'
        
        await message.answer(
            text,
            reply_markup=get_main_keyboard(user_id)
        )
    else:
        await message.answer(
            f'🏠 Главное меню\n\n💰 Баланс: {user["balance"]} GMP\n📢 Наш канал: {CHANNEL_LINK}',
            reply_markup=get_main_keyboard(user_id)
        )

@dp.callback_query(F.data == 'check_sub')
async def check_sub_callback(call: CallbackQuery):
    user_id = call.from_user.id
    
    if await check_subscription(user_id):
        await call.message.delete()
        await call.message.answer(
            f'✅ Подписка подтверждена!\n\n🏠 Главное меню',
            reply_markup=get_main_keyboard(user_id)
        )
    else:
        await call.answer("❌ Вы ещё не подписались на канал!", show_alert=True)

@dp.message(F.text == '🎫 Мои чеки')
async def my_checks(message: Message):
    user_id = message.from_user.id
    
    if not await check_subscription(user_id):
        await message.answer(
            f"🔒 Подпишитесь на канал: {CHANNEL_LINK}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 ПЕРЕЙТИ В КАНАЛ", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")]
            ])
        )
        return
    
    active_checks = await get_user_checks(user_id, 'active')
    used_checks = await get_user_checks(user_id, 'used')
    
    total_active = sum(c['amount'] for c in active_checks)
    total_used = sum(c['amount'] for c in used_checks)
    
    text = (
        f'📊 Мои чеки\n\n'
        f'✅ Активные: {len(active_checks)} чеков на {total_active:.1f} GMP\n'
        f'❌ Использовано: {len(used_checks)} чеков на {total_used:.1f} GMP'
    )
    
    await message.answer(text)

@dp.message(F.text == '➕ Создать чек')
async def create_check_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    if not await check_subscription(user_id):
        await message.answer(
            f"🔒 Подпишитесь на канал: {CHANNEL_LINK}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 ПЕРЕЙТИ В КАНАЛ", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")]
            ])
        )
        return
    
    user = await get_user(user_id)
    
    await message.answer(
        f'💰 Введите сумму чека:\n(мин: {MIN_CHECK_AMOUNT} GMP)\nВаш баланс: {user["balance"]} GMP\n\nЧтобы создать чек со своим кодом, введите:\nсумма|код (например: 10|MYCODE)'
    )
    await state.set_state(CheckStates.waiting_amount)

@dp.message(CheckStates.waiting_amount)
async def process_check_amount(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    try:
        text = message.text.strip()
        
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
        
        if amount < MIN_CHECK_AMOUNT:
            await message.answer(f'❌ Минимальная сумма: {MIN_CHECK_AMOUNT} GMP')
            return
        
        if user_id != ADMIN_ID and amount > user['balance']:
            await message.answer(f'❌ Недостаточно средств. Ваш баланс: {user["balance"]} GMP')
            return
        
        checks = await get_user_checks(user_id, 'active')
        if len(checks) >= MAX_CHECKS_PER_DAY and user_id != ADMIN_ID:
            await message.answer(f'❌ Вы создали максимум {MAX_CHECKS_PER_DAY} активных чеков в день')
            return
        
        result = await create_check(user_id, amount, custom_code)
        
        check_text = f"🎫 Чек на {amount} GMP\nКод: {result['code']}\n\n🔗 https://t.me/GMP_TASKS_BOT?start=check_{result['code']}"
        
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="📤 Поделиться", switch_inline_query=f"🎫 Чек {result['code']} на {amount} GMP"))
        builder.row(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{result['code']}"))
        
        await message.answer(
            check_text,
            reply_markup=builder.as_markup()
        )
        
        await state.clear()
        
    except ValueError:
        await message.answer('❌ Введите корректную сумму (число)')

@dp.message(F.text == '💳 Депозит')
async def deposit_menu(message: Message):
    user_id = message.from_user.id
    
    if not await check_subscription(user_id):
        await message.answer(
            f"🔒 Подпишитесь на канал: {CHANNEL_LINK}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 ПЕРЕЙТИ В КАНАЛ", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")]
            ])
        )
        return
    
    text = f'💳 Пополнение GMP\n\nРеквизиты для пополнения:\nКарта: 1234 5678 9012 3456\nПолучатель: Иванов И.И.\n\nПосле перевода отправьте скриншот в бот'
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📤 Отправить скриншот", callback_data="send_screenshot"))
    
    await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data == 'send_screenshot')
async def send_screenshot(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        '📸 Отправьте скриншот перевода\n\nПосле отправки заявка будет отправлена админу'
    )
    await state.set_state(DepositStates.waiting_screenshot)
    await callback.answer()

@dp.message(DepositStates.waiting_screenshot)
async def process_screenshot(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer('❌ Пожалуйста, отправьте фото')
        return
    
    user_id = message.from_user.id
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        cursor = await db.execute('''
            INSERT INTO deposits (user_id, screenshot, created_at)
            VALUES (?, ?, ?)
        ''', (user_id, file.file_id, now))
        await db.commit()
        deposit_id = cursor.lastrowid
    
    user = await get_user(user_id)
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_deposit_{deposit_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_deposit_{deposit_id}")
    )
    
    await bot.send_photo(
        ADMIN_ID,
        photo=photo.file_id,
        caption=f'📥 Новая заявка на пополнение\nПользователь: @{message.from_user.username or user_id}\nID: {user_id}',
        reply_markup=builder.as_markup()
    )
    
    await message.answer('✅ Скриншот отправлен! Ожидайте подтверждения.')
    await state.clear()

@dp.message(F.text == '📤 Вывод')
async def withdraw_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    if not await check_subscription(user_id):
        await message.answer(
            f"🔒 Подпишитесь на канал: {CHANNEL_LINK}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 ПЕРЕЙТИ В КАНАЛ", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")]
            ])
        )
        return
    
    user = await get_user(user_id)
    await message.answer(f'💰 Введите сумму для вывода:\n(доступно: {user["balance"]} GMP)')
    await state.set_state(WithdrawStates.waiting_amount)

@dp.message(WithdrawStates.waiting_amount)
async def process_withdraw_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        user_id = message.from_user.id
        user = await get_user(user_id)
        
        if amount <= 0:
            await message.answer('❌ Сумма должна быть больше 0')
            return
        
        if amount > user['balance'] and user_id != ADMIN_ID:
            await message.answer(f'❌ Недостаточно средств. Ваш баланс: {user["balance"]} GMP')
            return
        
        await state.update_data(withdraw_amount=amount)
        await message.answer('📤 Введите ваш кошелек/реквизиты для вывода:')
        await state.set_state(WithdrawStates.waiting_wallet)
        
    except ValueError:
        await message.answer('❌ Введите корректную сумму (число)')

@dp.message(WithdrawStates.waiting_wallet)
async def process_withdraw_wallet(message: Message, state: FSMContext):
    wallet = message.text.strip()
    user_id = message.from_user.id
    data = await state.get_data()
    amount = data.get('withdraw_amount')
    
    if not wallet:
        await message.answer('❌ Введите реквизиты для вывода')
        return
    
    user = await get_user(user_id)
    
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        
        if user_id != ADMIN_ID:
            await db.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (amount, user_id))
        
        cursor = await db.execute('''
            INSERT INTO withdrawals (user_id, amount, wallet, created_at)
            VALUES (?, ?, ?, ?)
        ''', (user_id, amount, wallet, now))
        await db.commit()
        withdraw_id = cursor.lastrowid
        
        await db.execute('''
            INSERT INTO transactions (user_id, type, amount, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, 'withdraw', -amount, f'Вывод {amount} GMP на {wallet}', now))
        await db.commit()
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_withdraw_{withdraw_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_withdraw_{withdraw_id}")
    )
    
    await bot.send_message(
        ADMIN_ID,
        f'📤 Новая заявка на вывод\nПользователь: @{message.from_user.username or user_id}\nID: {user_id}\nИмя: {user["first_name"] if user else "Не указано"}\nСумма: {amount} GMP\nКошелек: {wallet}',
        reply_markup=builder.as_markup()
    )
    
    await message.answer(f'✅ Заявка на вывод {amount} GMP отправлена! Ожидайте подтверждения.')
    await state.clear()

@dp.message(F.text == '🎁 Промо')
async def promo_menu(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    if not await check_subscription(user_id):
        await message.answer(
            f"🔒 Подпишитесь на канал: {CHANNEL_LINK}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 ПЕРЕЙТИ В КАНАЛ", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")]
            ])
        )
        return
    
    await message.answer('🎁 Введите промокод:')
    await state.set_state(PromoStates.waiting_promo_code)

@dp.message(PromoStates.waiting_promo_code)
async def process_promo(message: Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id
    
    result = await activate_promo(code, user_id)
    
    if result['success']:
        await message.answer(f'✅ Промокод активирован!\nВам зачислено {result["amount"]} GMP')
    else:
        await message.answer(result['error'])
    
    await state.clear()

@dp.message(F.text == '👥 Рефка')
async def referral_menu(message: Message):
    user_id = message.from_user.id
    
    if not await check_subscription(user_id):
        await message.answer(
            f"🔒 Подпишитесь на канал: {CHANNEL_LINK}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 ПЕРЕЙТИ В КАНАЛ", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")]
            ])
        )
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT COUNT(*) FROM users WHERE referrer_id = ?', (user_id,)) as cursor:
            count = await cursor.fetchone()
            ref_count = count[0] if count else 0
    
    text = (
        f'👥 Реферальная система\n\n'
        f'Ваша реф-ссылка:\n'
        f'https://t.me/GMP_TASKS_BOT?start=ref_{user_id}\n\n'
        f'Приглашено: {ref_count} человек\n'
        f'Бонус за каждого: +{REFERRAL_BONUS} GMP'
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="📤 Поделиться ссылкой",
        switch_inline_query=f"Присоединяйся к GMP! Получи бонус: https://t.me/GMP_TASKS_BOT?start=ref_{user_id}"
    ))
    
    await message.answer(text, reply_markup=builder.as_markup())

@dp.message(F.text == '🎮 Мины')
async def mines_menu(message: Message):
    user_id = message.from_user.id
    
    if not await check_subscription(user_id):
        await message.answer(
            f"🔒 Подпишитесь на канал: {CHANNEL_LINK}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 ПЕРЕЙТИ В КАНАЛ", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")]
            ])
        )
        return
    
    await message.answer(
        f'🎮 <b>Игра Мины</b>\n\nПравила:\n• Открывай клетки на поле 5x5\n• Не наступи на мину! 💣\n• Чем больше клеток открыл - тем выше множитель\n• Множитель: x1.10 - x24.5\n\n📝 Введите ставку:\n<code>мины 100</code>',
        parse_mode="HTML"
    )

@dp.message(F.text == '🎮 Золото')
async def gold_menu(message: Message):
    user_id = message.from_user.id
    
    if not await check_subscription(user_id):
        await message.answer(
            f"🔒 Подпишитесь на канал: {CHANNEL_LINK}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 ПЕРЕЙТИ В КАНАЛ", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")]
            ])
        )
        return
    
    await message.answer(
        f'🎮 <b>Игра Золото</b>\n\nПравила:\n• Угадывай сторону (⬅️ или ➡️)\n• 12 уровней с множителями x2 - x4096\n• Неправильный выбор - проигрыш\n\n📝 Введите ставку:\n<code>золото 100</code>',
        parse_mode="HTML"
    )

@dp.message(F.text == '🎮 Башня')
async def tower_menu(message: Message):
    user_id = message.from_user.id
    
    if not await check_subscription(user_id):
        await message.answer(
            f"🔒 Подпишитесь на канал: {CHANNEL_LINK}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 ПЕРЕЙТИ В КАНАЛ", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")]
            ])
        )
        return
    
    await message.answer(
        f'🎮 <b>Игра Башня</b>\n\nПравила:\n• Поднимайся на башню из 9 уровней\n• На каждом уровне 5 клеток, одна с миной\n• Чем выше - тем больше множитель\n• Множитель: x1.10 - x5.65\n\n📝 Введите ставку:\n<code>башня 100</code>',
        parse_mode="HTML"
    )

@dp.message(F.text == 'ℹ️ Помощь')
async def help_menu(message: Message):
    user_id = message.from_user.id
    
    if not await check_subscription(user_id):
        await message.answer(
            f"🔒 Подпишитесь на канал: {CHANNEL_LINK}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 ПЕРЕЙТИ В КАНАЛ", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")]
            ])
        )
        return
    
    text = (
        f'ℹ️ <b>Помощь</b>\n\n'
        f'🎫 <b>Мои чеки</b> - список ваших чеков\n'
        f'➕ <b>Создать чек</b> - создать новый чек\n'
        f'💳 <b>Депозит</b> - пополнение баланса\n'
        f'📤 <b>Вывод</b> - вывод GMP\n'
        f'🎁 <b>Промо</b> - активация промокода\n'
        f'👥 <b>Рефка</b> - реферальная система\n'
        f'🎮 <b>Мины</b> - игра Мины\n'
        f'🎮 <b>Золото</b> - игра Золото\n'
        f'🎮 <b>Башня</b> - игра Башня\n\n'
        f'📢 Наш канал: {CHANNEL_LINK}'
    )
    
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == '👑 Админ-панель')
async def admin_panel(message: Message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await message.answer('❌ Доступ запрещен')
        return
    
    if not await check_subscription(user_id):
        await message.answer(
            f"🔒 Подпишитесь на канал: {CHANNEL_LINK}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 ПЕРЕЙТИ В КАНАЛ", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")]
            ])
        )
        return
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📥 Пополнения", callback_data="admin_deposits"),
        InlineKeyboardButton(text="📤 Выводы", callback_data="admin_withdrawals")
    )
    builder.row(
        InlineKeyboardButton(text="🎁 Промокоды", callback_data="admin_promos"),
        InlineKeyboardButton(text="📨 Рассылка", callback_data="admin_mailing")
    )
    builder.row(
        InlineKeyboardButton(text="💰 Выдать GMP", callback_data="admin_give"),
        InlineKeyboardButton(text="💰 Забрать GMP", callback_data="admin_take")
    )
    builder.row(
        InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users"),
        InlineKeyboardButton(text="📢 Канал", callback_data="admin_channel")
    )
    
    await message.answer('👑 Админ-панель\n\nВыберите раздел:', reply_markup=builder.as_markup())

# -------- АДМИН КОЛБЭКИ --------
@dp.callback_query(F.data.startswith('confirm_'))
async def admin_confirm(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    _, type, item_id = callback.data.split('_')
    item_id = int(item_id)
    now = datetime.now().isoformat()
    
    async with aiosqlite.connect(DB_PATH) as db:
        if type == 'deposit':
            await db.execute('''
                UPDATE deposits SET status = "confirmed", confirmed_by = ?, confirmed_at = ?
                WHERE id = ?
            ''', (ADMIN_ID, now, item_id))
            await db.commit()
            await callback.message.edit_text('✅ Депозит подтвержден!')
            
        elif type == 'withdraw':
            async with db.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (item_id,)) as cursor:
                withdraw = await cursor.fetchone()
            
            if withdraw:
                await db.execute('''
                    UPDATE withdrawals SET status = "confirmed", confirmed_by = ?, confirmed_at = ?
                    WHERE id = ?
                ''', (ADMIN_ID, now, item_id))
                await db.commit()
                await bot.send_message(withdraw[0], f'✅ Ваш вывод {withdraw[1]} GMP подтвержден!')
            
            await callback.message.edit_text('✅ Вывод подтвержден!')
    
    await callback.answer()

@dp.callback_query(F.data.startswith('reject_'))
async def admin_reject(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    _, type, item_id = callback.data.split('_')
    item_id = int(item_id)
    
    async with aiosqlite.connect(DB_PATH) as db:
        if type == 'deposit':
            await db.execute('UPDATE deposits SET status = "rejected" WHERE id = ?', (item_id,))
            await db.commit()
            
        elif type == 'withdraw':
            async with db.execute('SELECT user_id, amount FROM withdrawals WHERE id = ?', (item_id,)) as cursor:
                withdraw = await cursor.fetchone()
            
            if withdraw:
                await db.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (withdraw[1], withdraw[0]))
                await db.execute('UPDATE withdrawals SET status = "rejected" WHERE id = ?', (item_id,))
                await db.commit()
                await bot.send_message(withdraw[0], f'❌ Ваш вывод {withdraw[1]} GMP отклонен. Средства возвращены.')
    
    await callback.message.edit_text('❌ Заявка отклонена')
    await callback.answer()

@dp.callback_query(F.data == 'admin_deposits')
async def admin_deposits(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM deposits WHERE status = "pending" ORDER BY created_at DESC') as cursor:
            deposits = await cursor.fetchall()
    
    if not deposits:
        await callback.message.edit_text('📭 Нет новых заявок на пополнение')
    else:
        text = '📥 Заявки на пополнение:\n\n'
        for dep in deposits[:10]:
            text += f'#{dep[0]} | Пользователь: {dep[1]}\n'
        await callback.message.edit_text(text)
    
    await callback.answer()

@dp.callback_query(F.data == 'admin_withdrawals')
async def admin_withdrawals(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM withdrawals WHERE status = "pending" ORDER BY created_at DESC') as cursor:
            withdrawals = await cursor.fetchall()
    
    if not withdrawals:
        await callback.message.edit_text('📭 Нет новых заявок на вывод')
    else:
        text = '📤 Заявки на вывод:\n\n'
        for wd in withdrawals[:10]:
            text += f'#{wd[0]} | {wd[1]} | {wd[2]} GMP\n'
        await callback.message.edit_text(text)
    
    await callback.answer()

@dp.callback_query(F.data == 'admin_promos')
async def admin_promos(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    promos = await get_all_promos()
    text = '🎁 Управление промокодами\n\n'
    if promos:
        for promo in promos[:10]:
            text += f'• {promo["code"]} | {promo["reward"]} GMP | осталось: {promo["uses_left"]}\n'
    else:
        text += '📭 Нет активных промокодов\n'
    
    text += '\n📝 Создать: создать|КОД|СУММА|ЛИМИТ\n🗑 Удалить: удалить|КОД'
    
    await callback.message.edit_text(text)
    await state.set_state(AdminStates.waiting_promo_code)
    await callback.answer()

@dp.message(AdminStates.waiting_promo_code)
async def process_promo_command(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    text = message.text.strip()
    
    if text.startswith('создать|'):
        parts = text.split('|')
        if len(parts) == 4:
            _, code, reward, uses = parts
            try:
                reward = float(reward)
                uses = int(uses)
                success = await create_promo(code, reward, uses, ADMIN_ID)
                if success:
                    await message.answer(f'✅ Промокод {code} создан!')
                else:
                    await message.answer('❌ Промокод уже существует')
            except ValueError:
                await message.answer('❌ Неверный формат')
        else:
            await message.answer('❌ Формат: создать|код|сумма|лимит')
    
    elif text.startswith('удалить|'):
        code = text.split('|')[1].strip()
        await delete_promo(code)
        await message.answer(f'✅ Промокод {code} удален')
    
    else:
        await message.answer('❌ Неизвестная команда')
    
    await state.clear()

@dp.callback_query(F.data == 'admin_mailing')
async def admin_mailing(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    await callback.message.edit_text('📨 Отправьте текст для рассылки:')
    await state.set_state(AdminStates.waiting_mailing_text)
    await callback.answer()

@dp.message(AdminStates.waiting_mailing_text)
async def process_mailing(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    text = message.text.strip()
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id FROM users') as cursor:
            users = await cursor.fetchall()
    
    if not users:
        await message.answer('📭 Нет пользователей')
        await state.clear()
        return
    
    await message.answer(f'📨 Начинаю рассылку {len(users)} пользователям...')
    
    count = 0
    for user in users:
        try:
            await bot.send_message(user[0], text)
            count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    
    await message.answer(f'✅ Рассылка завершена! Отправлено {count} сообщений')
    await state.clear()

@dp.callback_query(F.data == 'admin_give')
async def admin_give(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    await callback.message.edit_text('💰 Введите ID или @username пользователя:')
    await state.set_state(AdminStates.waiting_give_user)
    await callback.answer()

@dp.message(AdminStates.waiting_give_user)
async def process_give_user(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    user_input = message.text.strip()
    user_id = None
    
    if user_input.startswith('@'):
        username = user_input[1:]
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT id FROM users WHERE username = ?', (username,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    user_id = row[0]
    else:
        try:
            user_id = int(user_input)
        except:
            pass
    
    if not user_id:
        await message.answer('❌ Пользователь не найден')
        await state.clear()
        return
    
    await state.update_data(give_user_id=user_id)
    await message.answer('💰 Введите сумму для выдачи:')
    await state.set_state(AdminStates.waiting_give_amount)

@dp.message(AdminStates.waiting_give_amount)
async def process_give_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        amount = float(message.text.strip())
        data = await state.get_data()
        user_id = data.get('give_user_id')
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (amount, user_id))
            now = datetime.now().isoformat()
            await db.execute('''
                INSERT INTO transactions (user_id, type, amount, description, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, 'admin_give', amount, f'Выдано админом {amount} GMP', now))
            await db.commit()
        
        await message.answer(f'✅ Пользователю выдано {amount} GMP')
        await bot.send_message(user_id, f'💰 Вам начислено {amount} GMP от администратора!')
        
    except ValueError:
        await message.answer('❌ Введите корректную сумму')
        return
    
    await state.clear()

@dp.callback_query(F.data == 'admin_take')
async def admin_take(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    await callback.message.edit_text('💰 Введите ID или @username пользователя:')
    await state.set_state(AdminStates.waiting_take_user)
    await callback.answer()

@dp.message(AdminStates.waiting_take_user)
async def process_take_user(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    user_input = message.text.strip()
    user_id = None
    
    if user_input.startswith('@'):
        username = user_input[1:]
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT id FROM users WHERE username = ?', (username,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    user_id = row[0]
    else:
        try:
            user_id = int(user_input)
        except:
            pass
    
    if not user_id:
        await message.answer('❌ Пользователь не найден')
        await state.clear()
        return
    
    await state.update_data(take_user_id=user_id)
    await message.answer('💰 Введите сумму для списания:')
    await state.set_state(AdminStates.waiting_take_amount)

@dp.message(AdminStates.waiting_take_amount)
async def process_take_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        amount = float(message.text.strip())
        data = await state.get_data()
        user_id = data.get('take_user_id')
        
        user = await get_user(user_id)
        if amount > user['balance']:
            await message.answer(f'❌ У пользователя {user["balance"]} GMP, нельзя списать больше')
            return
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (amount, user_id))
            now = datetime.now().isoformat()
            await db.execute('''
                INSERT INTO transactions (user_id, type, amount, description, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, 'admin_take', -amount, f'Списано админом {amount} GMP', now))
            await db.commit()
        
        await message.answer(f'✅ У пользователя списано {amount} GMP')
        await bot.send_message(user_id, f'❌ С вашего баланса списано {amount} GMP администратором')
        
    except ValueError:
        await message.answer('❌ Введите корректную сумму')
        return
    
    await state.clear()

@dp.callback_query(F.data == 'admin_users')
async def admin_users(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT COUNT(*) FROM users') as cursor:
            count = await cursor.fetchone()
            total_users = count[0] if count else 0
        
        async with db.execute('SELECT SUM(balance) FROM users') as cursor:
            total_balance = await cursor.fetchone()
            total_gmp = total_balance[0] if total_balance and total_balance[0] else 0
    
    await callback.message.edit_text(
        f'👥 Статистика\n\nВсего пользователей: {total_users}\nОбщий баланс GMP: {total_gmp:.1f}'
    )
    await callback.answer()

@dp.callback_query(F.data == 'admin_channel')
async def admin_channel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer('❌ Доступ запрещен')
        return
    
    await callback.message.edit_text(
        f'📢 Текущий канал: {CHANNEL_LINK}\n\n'
        f'Чтобы изменить канал, отредактируйте CHANNEL_LINK в коде'
    )
    await callback.answer()

# -------- ЗАПУСК --------
async def main():
    await init_db()
    logger.info('🚀 Бот запущен!')
    logger.info(f'👑 Админ ID: {ADMIN_ID}')
    logger.info(f'📢 Канал: {CHANNEL_LINK}')
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
