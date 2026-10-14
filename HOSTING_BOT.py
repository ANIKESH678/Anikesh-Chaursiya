# -*- coding: utf-8 -*-
import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import logging
import threading
import re
import sys
import atexit
import requests

# --- Flask Keep Alive ---
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "I'am Marco File Host - Running"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Flask Keep-Alive server started.")
# --- End Flask Keep Alive ---

# --- Configuration ---
TOKEN = '8974349846:AAG-5_q_fyjLO0-ryvuXYUDbWE_nGwnMzcI'
OWNER_ID = 7687242263
ADMIN_ID = 7687242263
YOUR_USERNAME = '@Rexws348'
UPDATE_CHANNEL = 'https://t.me/anikeshxhosting'
FORCE_JOIN_CHANNEL = '@anikeshxhosting'  # Force join channel username

# Folder setup
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

# File upload limits
FREE_USER_LIMIT = 3          # Free users get 3 bots
REFERRAL_BONUS = 1           # Each referral gives +1 bot
SUBSCRIBED_USER_LIMIT = 15
ADMIN_LIMIT = 999
OWNER_LIMIT = float('inf')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN)

# --- Data structures ---
bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False
user_referrals = {}   # {user_id: count_of_successful_referrals}
user_referred_by = {} # {new_user_id: referrer_id}

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Button Layouts ---
COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 Updates Channel"],
    ["📤 Upload File", "📂 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["🎁 Referral Program", "📞 Contact Owner"]
]
ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 Updates Channel"],
    ["📤 Upload File", "📂 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["💳 Subscriptions", "📢 Broadcast"],
    ["🔒 Lock Bot", "🟢 Running All Code"],
    ["👑 Admin Panel", "🎁 Referral Program"],
    ["📞 Contact Owner"]
]

# --- Database Setup ---
def init_db():
    logger.info(f"Initializing database at: {DATABASE_PATH}")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                     (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_files
                     (user_id INTEGER, file_name TEXT, file_type TEXT,
                      PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS referrals
                     (user_id INTEGER PRIMARY KEY, referral_count INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS referred_users
                     (user_id INTEGER PRIMARY KEY, referrer_id INTEGER)''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}", exc_info=True)

def load_data():
    logger.info("Loading data from database...")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            try:
                user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
            except ValueError:
                logger.warning(f"⚠️ Invalid expiry for {user_id}: {expiry}")
        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for user_id, file_name, file_type in c.fetchall():
            user_files.setdefault(user_id, []).append((file_name, file_type))
        c.execute('SELECT user_id FROM active_users')
        active_users.update(uid for (uid,) in c.fetchall())
        c.execute('SELECT user_id FROM admins')
        admin_ids.update(uid for (uid,) in c.fetchall())
        c.execute('SELECT user_id, referral_count FROM referrals')
        for uid, cnt in c.fetchall():
            user_referrals[uid] = cnt
        c.execute('SELECT user_id, referrer_id FROM referred_users')
        for uid, ref in c.fetchall():
            user_referred_by[uid] = ref
        conn.close()
        logger.info(f"Loaded: {len(active_users)} users, {len(user_subscriptions)} subs, {len(admin_ids)} admins.")
    except Exception as e:
        logger.error(f"❌ Error loading data: {e}", exc_info=True)

init_db()
load_data()

# --- Helper Functions ---
def get_user_folder(user_id):
    folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(folder, exist_ok=True)
    return folder

def get_user_referral_count(user_id):
    return user_referrals.get(user_id, 0)

def get_user_file_limit(user_id):
    if user_id == OWNER_ID:
        return OWNER_LIMIT
    if user_id in admin_ids:
        return ADMIN_LIMIT
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT + get_user_referral_count(user_id) * REFERRAL_BONUS
    # Free user: base 3 + 1 per successful referral
    return FREE_USER_LIMIT + get_user_referral_count(user_id) * REFERRAL_BONUS

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not is_running:
                if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                    try: script_info['log_file'].close()
                    except: pass
                if script_key in bot_scripts: del bot_scripts[script_key]
            return is_running
        except psutil.NoSuchProcess:
            if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                try: script_info['log_file'].close()
                except: pass
            if script_key in bot_scripts: del bot_scripts[script_key]
            return False
        except Exception as e:
            logger.error(f"Error checking process for {script_key}: {e}", exc_info=True)
            return False
    return False

def kill_process_tree(process_info):
    pid = None
    log_file_closed = False
    script_key = process_info.get('script_key', 'N/A')
    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close') and not process_info['log_file'].closed:
            try:
                process_info['log_file'].close()
                log_file_closed = True
            except Exception as log_e:
                logger.error(f"Error closing log file for {script_key}: {log_e}")
        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            pid = process.pid
            if pid:
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        try: child.terminate()
                        except psutil.NoSuchProcess: pass
                        except Exception:
                            try: child.kill()
                            except: pass
                    gone, alive = psutil.wait_procs(children, timeout=1)
                    for p in alive:
                        try: p.kill()
                        except: pass
                    try:
                        parent.terminate()
                        try: parent.wait(timeout=1)
                        except psutil.TimeoutExpired: parent.kill()
                    except psutil.NoSuchProcess: pass
                    except Exception:
                        try: parent.kill()
                        except: pass
                except psutil.NoSuchProcess: pass
    except Exception as e:
        logger.error(f"❌ Error killing process tree for PID {pid}: {e}", exc_info=True)

# --- Force Join Check ---
def check_force_join(user_id):
    """Check if user has joined the force join channel"""
    try:
        member = bot.get_chat_member(f"@{FORCE_JOIN_CHANNEL.replace('@','')}", user_id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
        return False
    except Exception as e:
        logger.warning(f"Force join check failed for {user_id}: {e}")
        # If we can't check (bot not admin in channel), allow user
        return True

def send_force_join_message(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton('📢 Join Updates Channel', url=UPDATE_CHANNEL),
        types.InlineKeyboardButton('✅ I have joined', callback_data='check_join')
    )
    bot.send_message(
        chat_id,
        "⚠️ *Please join our channel to use this bot!*\n\n"
        "After joining, click 'I have joined' button.",
        reply_markup=markup,
        parse_mode='Markdown'
    )

# --- Database Operations ---
DB_LOCK = threading.Lock()

def save_user_file(user_id, file_name, file_type='py'):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
                      (user_id, file_name, file_type))
            conn.commit()
            if user_id not in user_files: user_files[user_id] = []
            user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
            user_files[user_id].append((file_name, file_type))
        except Exception as e:
            logger.error(f"Error saving file: {e}")
        finally:
            conn.close()

def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
            conn.commit()
            if user_id in user_files:
                user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
                if not user_files[user_id]: del user_files[user_id]
        except Exception as e:
            logger.error(f"Error removing file: {e}")
        finally:
            conn.close()

def add_active_user(user_id):
    active_users.add(user_id)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Error adding active user: {e}")
        finally:
            conn.close()

def save_subscription(user_id, expiry):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            expiry_str = expiry.isoformat()
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)', (user_id, expiry_str))
            conn.commit()
            user_subscriptions[user_id] = {'expiry': expiry}
        except Exception as e:
            logger.error(f"Error saving subscription: {e}")
        finally:
            conn.close()

def remove_subscription_db(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            conn.commit()
            if user_id in user_subscriptions: del user_subscriptions[user_id]
        except Exception as e:
            logger.error(f"Error removing subscription: {e}")
        finally:
            conn.close()

def add_admin_db(admin_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            conn.commit()
            admin_ids.add(admin_id)
        except Exception as e:
            logger.error(f"Error adding admin: {e}")
        finally:
            conn.close()

def remove_admin_db(admin_id):
    if admin_id == OWNER_ID: return False
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        removed = False
        try:
            c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
            conn.commit()
            removed = c.rowcount > 0
            if removed: admin_ids.discard(admin_id)
        except Exception as e:
            logger.error(f"Error removing admin: {e}")
        finally:
            conn.close()
        return removed

def save_referral(referrer_id, new_user_id):
    """Save a successful referral"""
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            # Check if new user already referred
            c.execute('SELECT 1 FROM referred_users WHERE user_id = ?', (new_user_id,))
            if c.fetchone():
                return False
            c.execute('INSERT OR REPLACE INTO referred_users (user_id, referrer_id) VALUES (?, ?)',
                      (new_user_id, referrer_id))
            c.execute('INSERT OR IGNORE INTO referrals (user_id, referral_count) VALUES (?, 0)', (referrer_id,))
            c.execute('UPDATE referrals SET referral_count = referral_count + 1 WHERE user_id = ?', (referrer_id,))
            conn.commit()
            user_referrals[referrer_id] = user_referrals.get(referrer_id, 0) + 1
            user_referred_by[new_user_id] = referrer_id
            return True
        except Exception as e:
            logger.error(f"Error saving referral: {e}")
            return False
        finally:
            conn.close()

# --- Menu Creation ---
def create_main_menu_inline(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL),
        types.InlineKeyboardButton('📤 Upload File', callback_data='upload'),
        types.InlineKeyboardButton('📂 Check Files', callback_data='check_files'),
        types.InlineKeyboardButton('⚡ Bot Speed', callback_data='speed'),
        types.InlineKeyboardButton('🎁 Referral Program', callback_data='referral'),
        types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}')
    ]
    if user_id in admin_ids:
        admin_buttons = [
            types.InlineKeyboardButton('💳 Subscriptions', callback_data='subscription'),
            types.InlineKeyboardButton('📊 Statistics', callback_data='stats'),
            types.InlineKeyboardButton('🔒 Lock Bot' if not bot_locked else '🔓 Unlock Bot',
                                     callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
            types.InlineKeyboardButton('📢 Broadcast', callback_data='broadcast'),
            types.InlineKeyboardButton('👑 Admin Panel', callback_data='admin_panel'),
            types.InlineKeyboardButton('🟢 Run All User Scripts', callback_data='run_all_scripts')
        ]
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3], admin_buttons[0])
        markup.add(admin_buttons[1], admin_buttons[3])
        markup.add(admin_buttons[2], admin_buttons[5])
        markup.add(admin_buttons[4])
        markup.add(buttons[4])
        markup.add(buttons[5])
    else:
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3])
        markup.add(types.InlineKeyboardButton('📊 Statistics', callback_data='stats'))
        markup.add(buttons[4])
        markup.add(buttons[5])
    return markup

def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    layout = ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC if user_id in admin_ids else COMMAND_BUTTONS_LAYOUT_USER_SPEC
    for row in layout:
        markup.add(*[types.KeyboardButton(text) for text in row])
    return markup

def create_control_buttons(script_owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    markup.add(types.InlineKeyboardButton("🔙 Back to Files", callback_data='check_files'))
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_subscription_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Subscription', callback_data='add_subscription'),
        types.InlineKeyboardButton('➖ Remove Subscription', callback_data='remove_subscription')
    )
    markup.row(types.InlineKeyboardButton('🔍 Check Subscription', callback_data='check_subscription'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

# --- Package Install Helper ---
TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'python_telegram_bot': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'bs4': 'beautifulsoup4',
    'requests': 'requests',
    'pillow': 'Pillow',
    'cv2': 'opencv-python',
    'yaml': 'PyYAML',
    'dotenv': 'python-dotenv',
    'dateutil': 'python-dateutil',
    'pandas': 'pandas',
    'numpy': 'numpy',
    'flask': 'Flask',
    'django': 'Django',
    'sqlalchemy': 'SQLAlchemy',
    'psutil': 'psutil',
    'asyncio': None, 'json': None, 'datetime': None, 'os': None, 'sys': None,
    're': None, 'time': None, 'math': None, 'random': None, 'logging': None,
    'threading': None, 'subprocess': None, 'zipfile': None, 'tempfile': None,
    'shutil': None, 'sqlite3': None, 'atexit': None
}

def attempt_install_pip(module_name, message):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    if package_name is None:
        return False
    try:
        bot.reply_to(message, f"🐍 Installing `{package_name}`...", parse_mode='Markdown')
        command = [sys.executable, '-m', 'pip', 'install', package_name]
        result = subprocess.run(command, capture_output=True, text=True, check=False,
                                encoding='utf-8', errors='ignore')
        if result.returncode == 0:
            bot.reply_to(message, f"✅ Package `{package_name}` installed.", parse_mode='Markdown')
            return True
        else:
            error_msg = f"❌ Failed to install `{package_name}`.\n```\n{(result.stderr or result.stdout)[:500]}\n```"
            bot.reply_to(message, error_msg, parse_mode='Markdown')
            return False
    except Exception as e:
        bot.reply_to(message, f"❌ Error installing `{package_name}`: {e}")
        return False

def attempt_install_npm(module_name, user_folder, message):
    try:
        bot.reply_to(message, f"🟠 Installing Node package `{module_name}`...", parse_mode='Markdown')
        command = ['npm', 'install', module_name]
        result = subprocess.run(command, capture_output=True, text=True, check=False,
                                cwd=user_folder, encoding='utf-8', errors='ignore')
        if result.returncode == 0:
            bot.reply_to(message, f"✅ Node package `{module_name}` installed.", parse_mode='Markdown')
            return True
        else:
            error_msg = f"❌ Failed to install `{module_name}`.\n```\n{(result.stderr or result.stdout)[:500]}\n```"
            bot.reply_to(message, error_msg, parse_mode='Markdown')
            return False
    except FileNotFoundError:
        bot.reply_to(message, "❌ 'npm' not found.")
        return False
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")
        return False

# --- Script Runners ---
def run_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj, f"❌ Failed to run '{file_name}'.")
        return
    script_key = f"{script_owner_id}_{file_name}"
    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj, f"❌ Script '{file_name}' not found!")
            remove_user_file_db(script_owner_id, file_name)
            return
        if attempt == 1:
            check_proc = None
            try:
                check_proc = subprocess.Popen([sys.executable, script_path], cwd=user_folder,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              text=True, encoding='utf-8', errors='ignore')
                stdout, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match_py = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match_py:
                        module_name = match_py.group(1).strip().strip("'\"")
                        if attempt_install_pip(module_name, message_obj):
                            time.sleep(2)
                            threading.Thread(target=run_script,
                                           args=(script_path, script_owner_id, user_folder,
                                                 file_name, message_obj, attempt + 1)).start()
                            return
                        else: return
                    else:
                        bot.reply_to(message_obj, f"❌ Script error:\n```\n{stderr[:500]}\n```", parse_mode='Markdown')
                        return
            except subprocess.TimeoutExpired:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill(); check_proc.communicate()
            except FileNotFoundError:
                bot.reply_to(message_obj, "❌ Python interpreter not found.")
                return
            except Exception as e:
                logger.error(f"Pre-check error: {e}")
            finally:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill(); check_proc.communicate()
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = None
        try: log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            bot.reply_to(message_obj, f"❌ Failed to open log: {e}")
            return
        try:
            startupinfo = None; creationflags = 0
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(
                [sys.executable, script_path], cwd=user_folder, stdout=log_file, stderr=log_file,
                stdin=subprocess.PIPE, startupinfo=startupinfo, creationflags=creationflags,
                encoding='utf-8', errors='ignore'
            )
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj.chat.id, 'script_owner_id': script_owner_id,
                'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'py',
                'script_key': script_key
            }
            bot.reply_to(message_obj, f"✅ Python script '{file_name}' started! (PID: {process.pid})")
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            bot.reply_to(message_obj, f"❌ Error starting: {e}")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}", exc_info=True)
        bot.reply_to(message_obj, f"❌ Error: {e}")

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj, f"❌ Failed to run '{file_name}'.")
        return
    script_key = f"{script_owner_id}_{file_name}"
    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj, f"❌ Script '{file_name}' not found!")
            remove_user_file_db(script_owner_id, file_name)
            return
        if attempt == 1:
            check_proc = None
            try:
                check_proc = subprocess.Popen(['node', script_path], cwd=user_folder,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              text=True, encoding='utf-8', errors='ignore')
                stdout, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match_js = re.search(r"Cannot find module '(.+?)'", stderr)
                    if match_js:
                        module_name = match_js.group(1).strip().strip("'\"")
                        if not module_name.startswith('.') and not module_name.startswith('/'):
                            if attempt_install_npm(module_name, user_folder, message_obj):
                                time.sleep(2)
                                threading.Thread(target=run_js_script,
                                               args=(script_path, script_owner_id, user_folder,
                                                     file_name, message_obj, attempt + 1)).start()
                                return
                    bot.reply_to(message_obj, f"❌ JS error:\n```\n{stderr[:500]}\n```", parse_mode='Markdown')
                    return
            except subprocess.TimeoutExpired:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill(); check_proc.communicate()
            except FileNotFoundError:
                bot.reply_to(message_obj, "❌ Node.js not found.")
                return
            except Exception as e:
                logger.error(f"Pre-check error: {e}")
            finally:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill(); check_proc.communicate()
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = None
        try: log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            bot.reply_to(message_obj, f"❌ Failed to open log: {e}")
            return
        try:
            startupinfo = None; creationflags = 0
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(
                ['node', script_path], cwd=user_folder, stdout=log_file, stderr=log_file,
                stdin=subprocess.PIPE, startupinfo=startupinfo, creationflags=creationflags,
                encoding='utf-8', errors='ignore'
            )
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj.chat.id, 'script_owner_id': script_owner_id,
                'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'js',
                'script_key': script_key
            }
            bot.reply_to(message_obj, f"✅ JS script '{file_name}' started! (PID: {process.pid})")
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            bot.reply_to(message_obj, f"❌ Error starting: {e}")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}", exc_info=True)
        bot.reply_to(message_obj, f"❌ Error: {e}")

# --- File Handling ---
def handle_zip_file(downloaded_file_content, file_name_zip, message):
    user_id = message.from_user.id
    user_folder = get_user_folder(user_id)
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
        zip_path = os.path.join(temp_dir, file_name_zip)
        with open(zip_path, 'wb') as f: f.write(downloaded_file_content)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for member in zip_ref.infolist():
                member_path = os.path.abspath(os.path.join(temp_dir, member.filename))
                if not member_path.startswith(os.path.abspath(temp_dir)):
                    raise zipfile.BadZipFile(f"Unsafe path: {member.filename}")
            zip_ref.extractall(temp_dir)
        extracted = os.listdir(temp_dir)
        py_files = [f for f in extracted if f.endswith('.py')]
        js_files = [f for f in extracted if f.endswith('.js')]
        req_file = 'requirements.txt' if 'requirements.txt' in extracted else None
        pkg_json = 'package.json' if 'package.json' in extracted else None
        if req_file:
            req_path = os.path.join(temp_dir, req_file)
            bot.reply_to(message, f"🔄 Installing Python deps...")
            try:
                result = subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', req_path],
                                        capture_output=True, text=True, check=True,
                                        encoding='utf-8', errors='ignore')
                bot.reply_to(message, "✅ Python deps installed.")
            except Exception as e:
                bot.reply_to(message, f"❌ Deps failed: {e}"); return
        if pkg_json:
            bot.reply_to(message, f"🔄 Installing Node deps...")
            try:
                result = subprocess.run(['npm', 'install'], capture_output=True, text=True,
                                        check=True, cwd=temp_dir, encoding='utf-8', errors='ignore')
                bot.reply_to(message, "✅ Node deps installed.")
            except FileNotFoundError:
                bot.reply_to(message, "❌ 'npm' not found."); return
            except Exception as e:
                bot.reply_to(message, f"❌ Deps failed: {e}"); return
        main_script_name = None; file_type = None
        for p in ['main.py', 'bot.py', 'app.py']:
            if p in py_files: main_script_name = p; file_type = 'py'; break
        if not main_script_name:
            for p in ['index.js', 'main.js', 'bot.js', 'app.js']:
                if p in js_files: main_script_name = p; file_type = 'js'; break
        if not main_script_name:
            if py_files: main_script_name = py_files[0]; file_type = 'py'
            elif js_files: main_script_name = js_files[0]; file_type = 'js'
        if not main_script_name:
            bot.reply_to(message, "❌ No .py or .js found!"); return
        for item_name in os.listdir(temp_dir):
            src_path = os.path.join(temp_dir, item_name)
            dest_path = os.path.join(user_folder, item_name)
            if os.path.isdir(dest_path): shutil.rmtree(dest_path)
            elif os.path.exists(dest_path): os.remove(dest_path)
            shutil.move(src_path, dest_path)
        save_user_file(user_id, main_script_name, file_type)
        main_script_path = os.path.join(user_folder, main_script_name)
        bot.reply_to(message, f"✅ Files extracted. Starting `{main_script_name}`...", parse_mode='Markdown')
        if file_type == 'py':
            threading.Thread(target=run_script, args=(main_script_path, user_id, user_folder, main_script_name, message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(main_script_path, user_id, user_folder, main_script_name, message)).start()
    except zipfile.BadZipFile as e:
        bot.reply_to(message, f"❌ Invalid ZIP: {e}")
    except Exception as e:
        logger.error(f"❌ Zip error: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try: shutil.rmtree(temp_dir)
            except: pass

def handle_js_file(file_path, script_owner_id, user_folder, file_name, message):
    try:
        save_user_file(script_owner_id, file_name, 'js')
        threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, message)).start()
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

def handle_py_file(file_path, script_owner_id, user_folder, file_name, message):
    try:
        save_user_file(script_owner_id, file_name, 'py')
        threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, message)).start()
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

# --- Logic Functions ---
def _logic_send_welcome(message, check_join=True):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_name = message.from_user.first_name
    user_username = message.from_user.username
    logger.info(f"Welcome from {user_id} (@{user_username})")

    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "⚠️ Bot locked by admin. Try later.")
        return

    # Force join check (skip for admins)
    if check_join and user_id not in admin_ids:
        if not check_force_join(user_id):
            send_force_join_message(chat_id)
            return

    user_bio = "N/A"; photo_file_id = None
    try: user_bio = bot.get_chat(user_id).bio or "No bio"
    except: pass
    try:
        photos = bot.get_user_profile_photos(user_id, limit=1)
        if photos.photos: photo_file_id = photos.photos[0][-1].file_id
    except: pass

    # New user handling
    if user_id not in active_users:
        add_active_user(user_id)
        # Check referral (from /start deep-link)
        try:
            args = message.text.split()
            if len(args) > 1 and args[1].startswith('ref_'):
                referrer_id = int(args[1].replace('ref_', ''))
                if referrer_id != user_id and referrer_id not in admin_ids:
                    if save_referral(referrer_id, user_id):
                        try:
                            ref_count = get_user_referral_count(referrer_id)
                            new_limit = FREE_USER_LIMIT + ref_count * REFERRAL_BONUS
                            bot.send_message(referrer_id,
                                f"🎉 *New Referral!*\n\n"
                                f"User joined via your link!\n"
                                f"Total referrals: *{ref_count}*\n"
                                f"Your new bot limit: *{new_limit}* bots\n\n"
                                f"Keep sharing to earn more!",
                                parse_mode='Markdown')
                        except Exception as e:
                            logger.error(f"Failed to notify referrer {referrer_id}: {e}")
        except Exception as e:
            logger.error(f"Referral processing error: {e}")
        # Notify owner
        try:
            notification = (f"🎉 New User!\n👤 Name: {user_name}\n✳️ @{user_username or 'N/A'}\n"
                            f"🆔 ID: `{user_id}`\n📝 Bio: {user_bio}")
            bot.send_message(OWNER_ID, notification, parse_mode='Markdown')
            if photo_file_id: bot.send_photo(OWNER_ID, photo_file_id, caption=f"New user {user_id}")
        except Exception as e:
            logger.error(f"Owner notify failed: {e}")

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
    ref_count = get_user_referral_count(user_id)
    expiry_info = ""
    if user_id == OWNER_ID: user_status = "👑 Owner"
    elif user_id in admin_ids: user_status = "🛡️ Admin"
    elif user_id in user_subscriptions:
        expiry_date = user_subscriptions[user_id].get('expiry')
        if expiry_date and expiry_date > datetime.now():
            user_status = "⭐ Premium"
            days_left = (expiry_date - datetime.now()).days
            expiry_info = f"\n⏳ Expires in: {days_left} days"
        else:
            user_status = "🆓 Free (Expired Sub)"
            remove_subscription_db(user_id)
    else: user_status = "🆓 Free User"

    welcome_msg = (f"〽️ *Welcome, {user_name}!*\n\n"
                   f"🆔 User ID: `{user_id}`\n"
                   f"✳️ Username: `@{user_username or 'Not set'}`\n"
                   f"🔰 Status: {user_status}{expiry_info}\n"
                   f"📁 Files: {current_files} / {limit_str}\n"
                   f"🎁 Referrals: {ref_count} (each gives +{REFERRAL_BONUS} bot)\n\n"
                   f"🤖 Host Python (`.py`) / JS (`.js`) scripts.\n"
                   f"Upload single files or `.zip` archives.\n\n"
                   f"👇 Use buttons or type commands.")
    main_reply_markup = create_reply_keyboard_main_menu(user_id)
    try:
        if photo_file_id: bot.send_photo(chat_id, photo_file_id)
        bot.send_message(chat_id, welcome_msg, reply_markup=main_reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Welcome send error: {e}")
        try: bot.send_message(chat_id, welcome_msg, reply_markup=main_reply_markup, parse_mode='Markdown')
        except Exception as fe: logger.error(f"Fallback failed: {fe}")

def _logic_updates_channel(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL))
    bot.reply_to(message, "Visit our Updates Channel:", reply_markup=markup)

def _logic_upload_file(message):
    user_id = message.from_user.id
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked."); return
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ Limit reached ({current_files}/{limit_str}). Delete files or use referrals!")
        return
    bot.reply_to(message, "📤 Send `.py`, `.js`, or `.zip` file.")

def _logic_check_files(message):
    user_id = message.from_user.id
    files_list = user_files.get(user_id, [])
    if not files_list:
        bot.reply_to(message, "📂 Your files:\n\n(No files uploaded yet)"); return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for fname, ftype in sorted(files_list):
        is_running = is_bot_running(user_id, fname)
        icon = "🟢 Running" if is_running else "🔴 Stopped"
        markup.add(types.InlineKeyboardButton(f"{fname} ({ftype}) - {icon}",
                                              callback_data=f'file_{user_id}_{fname}'))
    bot.reply_to(message, "📂 Your files:\nClick to manage.", reply_markup=markup)

def _logic_bot_speed(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    start = time.time()
    wait_msg = bot.reply_to(message, "🏃 Testing speed...")
    try:
        bot.send_chat_action(chat_id, 'typing')
        response_time = round((time.time() - start) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        if user_id == OWNER_ID: level = "👑 Owner"
        elif user_id in admin_ids: level = "🛡️ Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now():
            level = "⭐ Premium"
        else: level = "🆓 Free User"
        msg = (f"⚡ *Bot Speed & Status:*\n\n"
               f"⏱️ Response: {response_time} ms\n"
               f"🚦 Status: {status}\n"
               f"👤 Level: {level}")
        bot.edit_message_text(msg, chat_id, wait_msg.message_id, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Speed error: {e}")
        bot.edit_message_text("❌ Error in speed test.", chat_id, wait_msg.message_id)

def _logic_contact_owner(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    bot.reply_to(message, "Click to contact Owner:", reply_markup=markup)

def _logic_referral_program(message):
    user_id = message.from_user.id
    bot_info = bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    ref_count = get_user_referral_count(user_id)
    base = FREE_USER_LIMIT
    total = get_user_file_limit(user_id)
    total_str = str(total) if total != float('inf') else "Unlimited"
    msg = (f"🎁 *Referral Program*\n\n"
           f"🔗 Your Referral Link:\n`{ref_link}`\n\n"
           f"📊 Your Referrals: *{ref_count}*\n"
           f"📁 Your Bot Limit: *{total_str}*\n\n"
           f"💡 *How it works:*\n"
           f"• Base limit: *{base}* bots\n"
           f"• Each referral: *+{REFERRAL_BONUS}* bot\n"
           f"• Share link & invite friends!\n\n"
           f"👥 Referred Users: {len([u for u,r in user_referred_by.items() if r == user_id])}")
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📤 Share Link",
        url=f"https://t.me/share/url?url={ref_link}&text=Check%20out%20this%20awesome%20bot%20hosting%20service!"))
    bot.reply_to(message, msg, reply_markup=markup, parse_mode='Markdown')

# --- Admin Logic ---
def _logic_subscriptions_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin required."); return
    bot.reply_to(message, "💳 Subscription Management",
                 reply_markup=create_subscription_menu())

def _logic_statistics(message):
    user_id = message.from_user.id
    total_users = len(active_users)
    total_files = sum(len(f) for f in user_files.values())
    running = 0
    user_running = 0
    for key, info in list(bot_scripts.items()):
        owner_id_str, _ = key.split('_', 1)
        if is_bot_running(int(owner_id_str), info['file_name']):
            running += 1
            if int(owner_id_str) == user_id: user_running += 1
    base = (f"📊 *Bot Statistics:*\n\n"
            f"👥 Total Users: {total_users}\n"
            f"📂 Total File Records: {total_files}\n"
            f"🟢 Active Bots: {running}\n")
    if user_id in admin_ids:
        base += (f"🔒 Bot Status: {'🔴 Locked' if bot_locked else '🟢 Unlocked'}\n"
                 f"🤖 Your Running: {user_running}")
    else:
        base += f"🤖 Your Running: {user_running}"
    bot.reply_to(message, base, parse_mode='Markdown')

def _logic_broadcast_init(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin required."); return
    msg = bot.reply_to(message, "📢 Send message to broadcast.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def _logic_toggle_lock_bot(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin required."); return
    global bot_locked
    bot_locked = not bot_locked
    status = "locked" if bot_locked else "unlocked"
    bot.reply_to(message, f"🔒 Bot {status}.")

def _logic_admin_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin required."); return
    bot.reply_to(message, "👑 Admin Panel", reply_markup=create_admin_panel())

def _logic_run_all_scripts(message_or_call):
    if isinstance(message_or_call, telebot.types.Message):
        admin_id = message_or_call.from_user.id
        chat_id = message_or_call.chat.id
        reply = lambda text, **kw: bot.reply_to(message_or_call, text, **kw)
        msg_obj = message_or_call
    elif isinstance(message_or_call, telebot.types.CallbackQuery):
        admin_id = message_or_call.from_user.id
        chat_id = message_or_call.message.chat.id
        bot.answer_callback_query(message_or_call.id)
        reply = lambda text, **kw: bot.send_message(chat_id, text, **kw)
        msg_obj = message_or_call.message
    else:
        return
    if admin_id not in admin_ids:
        reply("⚠️ Admin required."); return
    reply("⏳ Starting all user scripts...")
    started = 0; skipped = 0; errors = []
    snapshot = dict(user_files)
    for target_id, files in snapshot.items():
        if not files: continue
        folder = get_user_folder(target_id)
        for fname, ftype in files:
            if not is_bot_running(target_id, fname):
                path = os.path.join(folder, fname)
                if os.path.exists(path):
                    try:
                        if ftype == 'py':
                            threading.Thread(target=run_script, args=(path, target_id, folder, fname, msg_obj)).start()
                            started += 1
                        elif ftype == 'js':
                            threading.Thread(target=run_js_script, args=(path, target_id, folder, fname, msg_obj)).start()
                            started += 1
                        time.sleep(0.5)
                    except Exception as e:
                        errors.append(f"{fname} ({target_id}): {e}")
                        skipped += 1
                else:
                    errors.append(f"{fname} ({target_id}): not found")
                    skipped += 1
    summary = f"✅ Started: {started}\n⚠️ Skipped: {skipped}"
    if errors: summary += f"\nErrors (first 5):\n" + "\n".join(errors[:5])
    reply(summary)

# --- Broadcast handlers ---
def process_broadcast_message(message):
    user_id = message.from_user.id
    if user_id not in admin_ids: bot.reply_to(message, "⚠️ Not auth."); return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled."); return
    target = len(active_users)
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_broadcast_{message.message_id}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")
    )
    preview = (message.text[:1000].strip() if message.text else "(Media message)")
    bot.reply_to(message,
        f"⚠️ Confirm Broadcast to *{target}* users:\n\n```\n{preview}\n```",
        reply_markup=markup, parse_mode='Markdown')

def handle_confirm_broadcast(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    if user_id not in admin_ids: bot.answer_callback_query(call.id, "Admin only.", show_alert=True); return
    try:
        orig = call.message.reply_to_message
        if not orig: raise ValueError("No original msg")
        text = orig.text if orig.text else None
        photo = orig.photo[-1].file_id if orig.photo else None
        video = orig.video.file_id if orig.video else None
        caption = orig.caption if (photo or video) else None
        if not (text or photo or video): raise ValueError("No content")
        bot.answer_callback_query(call.id, "Starting...")
        bot.edit_message_text(f"📢 Broadcasting to {len(active_users)} users...",
                              chat_id, call.message.message_id, reply_markup=None)
        threading.Thread(target=execute_broadcast, args=(text, photo, video, caption, chat_id)).start()
    except Exception as e:
        logger.error(f"Broadcast confirm error: {e}")
        bot.edit_message_text(f"❌ {e}", chat_id, call.message.message_id, reply_markup=None)

def handle_cancel_broadcast(call):
    bot.answer_callback_query(call.id, "Cancelled.")
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass

def execute_broadcast(text, photo, video, caption, admin_chat):
    sent = 0; failed = 0; blocked = 0
    start = time.time()
    users = list(active_users)
    total = len(users)
    for i, uid in enumerate(users):
        try:
            if text: bot.send_message(uid, text, parse_mode='Markdown')
            elif photo: bot.send_photo(uid, photo, caption=caption, parse_mode='Markdown' if caption else None)
            elif video: bot.send_video(uid, video, caption=caption, parse_mode='Markdown' if caption else None)
            sent += 1
        except telebot.apihelper.ApiTelegramException as e:
            err = str(e).lower()
            if any(s in err for s in ["blocked", "deactivated", "chat not found", "kicked", "restricted"]):
                blocked += 1
            elif "flood" in err or "too many" in err:
                m = re.search(r"retry after (\d+)", err)
                wait = int(m.group(1)) + 1 if m else 5
                time.sleep(wait)
                try:
                    if text: bot.send_message(uid, text, parse_mode='Markdown')
                    elif photo: bot.send_photo(uid, photo, caption=caption)
                    elif video: bot.send_video(uid, video, caption=caption)
                    sent += 1
                except: failed += 1
            else:
                failed += 1
        except Exception:
            failed += 1
        if (i + 1) % 25 == 0 and i < total - 1: time.sleep(1.5)
        elif i % 5 == 0: time.sleep(0.2)
    dur = round(time.time() - start, 2)
    result = (f"📢 Broadcast Done!\n\n✅ Sent: {sent}\n❌ Failed: {failed}\n"
              f"🚫 Blocked: {blocked}\n👥 Targets: {total}\n⏱️ {dur}s")
    try: bot.send_message(admin_chat, result)
    except: pass

# --- Command Handlers ---
@bot.message_handler(commands=['start', 'help'])
def cmd_start(message):
    _logic_send_welcome(message)

@bot.message_handler(commands=['status'])
def cmd_status(message): _logic_statistics(message)

BUTTON_TEXT_TO_LOGIC = {
    "📢 Updates Channel": _logic_updates_channel,
    "📤 Upload File": _logic_upload_file,
    "📂 Check Files": _logic_check_files,
    "⚡ Bot Speed": _logic_bot_speed,
    "📞 Contact Owner": _logic_contact_owner,
    "📊 Statistics": _logic_statistics,
    "🎁 Referral Program": _logic_referral_program,
    "💳 Subscriptions": _logic_subscriptions_panel,
    "📢 Broadcast": _logic_broadcast_init,
    "🔒 Lock Bot": _logic_toggle_lock_bot,
    "🟢 Running All Code": _logic_run_all_scripts,
    "👑 Admin Panel": _logic_admin_panel,
}

@bot.message_handler(func=lambda m: m.text in BUTTON_TEXT_TO_LOGIC)
def handle_button_text(message):
    fn = BUTTON_TEXT_TO_LOGIC.get(message.text)
    if fn: fn(message)

@bot.message_handler(commands=['referral', 'ref'])
def cmd_referral(message): _logic_referral_program(message)

@bot.message_handler(commands=['updateschannel'])
def cmd_updates(message): _logic_updates_channel(message)

@bot.message_handler(commands=['uploadfile'])
def cmd_upload(message): _logic_upload_file(message)

@bot.message_handler(commands=['checkfiles'])
def cmd_checkfiles(message): _logic_check_files(message)

@bot.message_handler(commands=['botspeed'])
def cmd_speed(message): _logic_bot_speed(message)

@bot.message_handler(commands=['contactowner'])
def cmd_contact(message): _logic_contact_owner(message)

@bot.message_handler(commands=['subscriptions'])
def cmd_subs(message): _logic_subscriptions_panel(message)

@bot.message_handler(commands=['statistics'])
def cmd_stats(message): _logic_statistics(message)

@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(message): _logic_broadcast_init(message)

@bot.message_handler(commands=['lockbot'])
def cmd_lock(message): _logic_toggle_lock_bot(message)

@bot.message_handler(commands=['adminpanel'])
def cmd_admin(message): _logic_admin_panel(message)

@bot.message_handler(commands=['runningallcode'])
def cmd_runall(message): _logic_run_all_scripts(message)

@bot.message_handler(commands=['ping'])
def cmd_ping(message):
    start = time.time()
    msg = bot.reply_to(message, "Pong!")
    latency = round((time.time() - start) * 1000, 2)
    bot.edit_message_text(f"Pong! {latency} ms", message.chat.id, msg.message_id)

# --- Document Handler ---
@bot.message_handler(content_types=['document'])
def handle_file_upload(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    doc = message.document
    logger.info(f"Doc from {user_id}: {doc.file_name}")
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked."); return
    if user_id not in admin_ids and not check_force_join(user_id):
        send_force_join_message(chat_id); return
    limit = get_user_file_limit(user_id)
    count = get_user_file_count(user_id)
    if count >= limit:
        l_str = str(limit) if limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ Limit reached ({count}/{l_str}). Use referrals for more!")
        return
    file_name = doc.file_name
    if not file_name: bot.reply_to(message, "⚠️ No filename."); return
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "⚠️ Only .py, .js, .zip allowed!"); return
    if doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(message, "⚠️ Max 20 MB."); return
    try:
        try:
            bot.forward_message(OWNER_ID, chat_id, message.message_id)
            bot.send_message(OWNER_ID, f"⬆️ {file_name} from {message.from_user.first_name} (`{user_id}`)",
                            parse_mode='Markdown')
        except Exception as e: logger.error(f"Forward failed: {e}")
        wait_msg = bot.reply_to(message, f"⏳ Downloading `{file_name}`...", parse_mode='Markdown')
        file_info = bot.get_file(doc.file_id)
        content = bot.download_file(file_info.file_path)
        bot.edit_message_text(f"✅ Downloaded. Processing...", chat_id, wait_msg.message_id)
        folder = get_user_folder(user_id)
        if ext == '.zip':
            handle_zip_file(content, file_name, message)
        else:
            path = os.path.join(folder, file_name)
            with open(path, 'wb') as f: f.write(content)
            if ext == '.js': handle_js_file(path, user_id, folder, file_name, message)
            elif ext == '.py': handle_py_file(path, user_id, folder, file_name, message)
    except telebot.apihelper.ApiTelegramException as e:
        logger.error(f"API error: {e}")
        bot.reply_to(message, f"❌ API Error: {str(e)[:200]}")
    except Exception as e:
        logger.error(f"File error: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")

# --- Callback Handlers ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data
    logger.info(f"Callback: {user_id} -> {data}")
    if bot_locked and user_id not in admin_ids and data not in ['back_to_main', 'speed', 'stats', 'check_join']:
        bot.answer_callback_query(call.id, "⚠️ Bot locked.", show_alert=True); return
    # Force join check
    if user_id not in admin_ids and data not in ['check_join', 'back_to_main', 'referral']:
        if not check_force_join(user_id):
            bot.answer_callback_query(call.id, "⚠️ Please join the channel first!", show_alert=True)
            send_force_join_message(call.message.chat.id)
            return
    try:
        if data == 'check_join':
            if check_force_join(user_id):
                bot.answer_callback_query(call.id, "✅ Verified! Use /start", show_alert=True)
                try: bot.delete_message(call.message.chat.id, call.message.message_id)
                except: pass
            else:
                bot.answer_callback_query(call.id, "❌ Not joined yet!", show_alert=True)
        elif data == 'upload': upload_callback(call)
        elif data == 'check_files': check_files_callback(call)
        elif data == 'referral': _logic_referral_program(call.message)
        elif data.startswith('file_'): file_control_callback(call)
        elif data.startswith('start_'): start_bot_callback(call)
        elif data.startswith('stop_'): stop_bot_callback(call)
        elif data.startswith('restart_'): restart_bot_callback(call)
        elif data.startswith('delete_'): delete_bot_callback(call)
        elif data.startswith('logs_'): logs_bot_callback(call)
        elif data == 'speed': speed_callback(call)
        elif data == 'back_to_main': back_to_main_callback(call)
        elif data.startswith('confirm_broadcast_'): handle_confirm_broadcast(call)
        elif data == 'cancel_broadcast': handle_cancel_broadcast(call)
        elif data == 'subscription': admin_required_callback(call, subscription_management_callback)
        elif data == 'stats': stats_callback(call)
        elif data == 'lock_bot': admin_required_callback(call, lock_bot_callback)
        elif data == 'unlock_bot': admin_required_callback(call, unlock_bot_callback)
        elif data == 'run_all_scripts': admin_required_callback(call, run_all_scripts_callback)
        elif data == 'broadcast': admin_required_callback(call, broadcast_init_callback)
        elif data == 'admin_panel': admin_required_callback(call, admin_panel_callback)
        elif data == 'add_admin': owner_required_callback(call, add_admin_init_callback)
        elif data == 'remove_admin': owner_required_callback(call, remove_admin_init_callback)
        elif data == 'list_admins': admin_required_callback(call, list_admins_callback)
        elif data == 'add_subscription': admin_required_callback(call, add_subscription_init_callback)
        elif data == 'remove_subscription': admin_required_callback(call, remove_subscription_init_callback)
        elif data == 'check_subscription': admin_required_callback(call, check_subscription_init_callback)
        else:
            bot.answer_callback_query(call.id, "Unknown action.")
    except Exception as e:
        logger.error(f"Callback error '{data}': {e}", exc_info=True)
        try: bot.answer_callback_query(call.id, "Error.", show_alert=True)
        except: pass

def admin_required_callback(call, fn):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin required.", show_alert=True); return
    fn(call)

def owner_required_callback(call, fn):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner required.", show_alert=True); return
    fn(call)

def upload_callback(call):
    user_id = call.from_user.id
    limit = get_user_file_limit(user_id)
    count = get_user_file_count(user_id)
    if count >= limit:
        l_str = str(limit) if limit != float('inf') else "Unlimited"
        bot.answer_callback_query(call.id, f"⚠️ Limit reached ({count}/{l_str}).", show_alert=True); return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "📤 Send `.py`, `.js`, or `.zip` file.")

def check_files_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    files = user_files.get(user_id, [])
    if not files:
        bot.answer_callback_query(call.id, "No files.", show_alert=True)
        try:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_main'))
            bot.edit_message_text("📂 No files uploaded yet.", chat_id, call.message.message_id, reply_markup=markup)
        except: pass
        return
    bot.answer_callback_query(call.id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    for fname, ftype in sorted(files):
        running = is_bot_running(user_id, fname)
        icon = "🟢 Running" if running else "🔴 Stopped"
        markup.add(types.InlineKeyboardButton(f"{fname} ({ftype}) - {icon}",
                                              callback_data=f'file_{user_id}_{fname}'))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_main'))
    try:
        bot.edit_message_text("📂 Your files:", chat_id, call.message.message_id, reply_markup=markup)
    except Exception as e:
        if "not modified" not in str(e).lower(): logger.error(f"Edit error: {e}")

def file_control_callback(call):
    try:
        _, owner_str, fname = call.data.split('_', 2)
        owner_id = int(owner_str)
        uid = call.from_user.id
        if not (uid == owner_id or uid in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ No permission.", show_alert=True)
            check_files_callback(call); return
        files = user_files.get(owner_id, [])
        if not any(f[0] == fname for f in files):
            bot.answer_callback_query(call.id, "File not found.", show_alert=True)
            check_files_callback(call); return
        bot.answer_callback_query(call.id)
        running = is_bot_running(owner_id, fname)
        ftype = next((f[1] for f in files if f[0] == fname), '?')
        try:
            bot.edit_message_text(
                f"⚙️ `{fname}` ({ftype}) - User `{owner_id}`\nStatus: {'🟢' if running else '🔴'}",
                call.message.chat.id, call.message.message_id,
                reply_markup=create_control_buttons(owner_id, fname, running),
                parse_mode='Markdown')
        except Exception as e:
            if "not modified" not in str(e).lower(): raise
    except Exception as e:
        logger.error(f"File control error: {e}")
        bot.answer_callback_query(call.id, "Error.", show_alert=True)

def start_bot_callback(call):
    try:
        _, owner_str, fname = call.data.split('_', 2)
        owner_id = int(owner_str)
        uid = call.from_user.id
        chat_id = call.message.chat.id
        if not (uid == owner_id or uid in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ No permission.", show_alert=True); return
        files = user_files.get(owner_id, [])
        info = next((f for f in files if f[0] == fname), None)
        if not info:
            bot.answer_callback_query(call.id, "File not found.", show_alert=True); return
        ftype = info[1]
        folder = get_user_folder(owner_id)
        path = os.path.join(folder, fname)
        if not os.path.exists(path):
            bot.answer_callback_query(call.id, "File missing!", show_alert=True)
            remove_user_file_db(owner_id, fname); check_files_callback(call); return
        if is_bot_running(owner_id, fname):
            bot.answer_callback_query(call.id, "Already running.", show_alert=True); return
        bot.answer_callback_query(call.id, "Starting...")
        if ftype == 'py':
            threading.Thread(target=run_script, args=(path, owner_id, folder, fname, call.message)).start()
        elif ftype == 'js':
            threading.Thread(target=run_js_script, args=(path, owner_id, folder, fname, call.message)).start()
        time.sleep(1.5)
        running = is_bot_running(owner_id, fname)
        try:
            bot.edit_message_text(
                f"⚙️ `{fname}` ({ftype}) - User `{owner_id}`\nStatus: {'🟢' if running else '🟡'}",
                chat_id, call.message.message_id,
                reply_markup=create_control_buttons(owner_id, fname, running),
                parse_mode='Markdown')
        except Exception as e:
            if "not modified" not in str(e).lower(): raise
    except Exception as e:
        logger.error(f"Start error: {e}")
        bot.answer_callback_query(call.id, "Error.", show_alert=True)

def stop_bot_callback(call):
    try:
        _, owner_str, fname = call.data.split('_', 2)
        owner_id = int(owner_str)
        uid = call.from_user.id
        chat_id = call.message.chat.id
        if not (uid == owner_id or uid in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ No permission.", show_alert=True); return
        files = user_files.get(owner_id, [])
        info = next((f for f in files if f[0] == fname), None)
        if not info:
            bot.answer_callback_query(call.id, "File not found.", show_alert=True); return
        ftype = info[1]
        key = f"{owner_id}_{fname}"
        if not is_bot_running(owner_id, fname):
            bot.answer_callback_query(call.id, "Already stopped.", show_alert=True); return
        bot.answer_callback_query(call.id, "Stopping...")
        pi = bot_scripts.get(key)
        if pi: kill_process_tree(pi); del bot_scripts[key]
        try:
            bot.edit_message_text(
                f"⚙️ `{fname}` ({ftype}) - User `{owner_id}`\nStatus: 🔴 Stopped",
                chat_id, call.message.message_id,
                reply_markup=create_control_buttons(owner_id, fname, False),
                parse_mode='Markdown')
        except Exception as e:
            if "not modified" not in str(e).lower(): raise
    except Exception as e:
        logger.error(f"Stop error: {e}")
        bot.answer_callback_query(call.id, "Error.", show_alert=True)

def restart_bot_callback(call):
    try:
        _, owner_str, fname = call.data.split('_', 2)
        owner_id = int(owner_str)
        uid = call.from_user.id
        chat_id = call.message.chat.id
        if not (uid == owner_id or uid in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ No permission.", show_alert=True); return
        files = user_files.get(owner_id, [])
        info = next((f for f in files if f[0] == fname), None)
        if not info:
            bot.answer_callback_query(call.id, "File not found.", show_alert=True); return
        ftype = info[1]
        folder = get_user_folder(owner_id)
        path = os.path.join(folder, fname)
        key = f"{owner_id}_{fname}"
        if not os.path.exists(path):
            bot.answer_callback_query(call.id, "File missing!", show_alert=True)
            remove_user_file_db(owner_id, fname); check_files_callback(call); return
        bot.answer_callback_query(call.id, "Restarting...")
        if is_bot_running(owner_id, fname):
            pi = bot_scripts.get(key)
            if pi: kill_process_tree(pi)
            if key in bot_scripts: del bot_scripts[key]
            time.sleep(1.5)
        if ftype == 'py':
            threading.Thread(target=run_script, args=(path, owner_id, folder, fname, call.message)).start()
        elif ftype == 'js':
            threading.Thread(target=run_js_script, args=(path, owner_id, folder, fname, call.message)).start()
        time.sleep(1.5)
        running = is_bot_running(owner_id, fname)
        try:
            bot.edit_message_text(
                f"⚙️ `{fname}` ({ftype}) - User `{owner_id}`\nStatus: {'🟢' if running else '🟡'}",
                chat_id, call.message.message_id,
                reply_markup=create_control_buttons(owner_id, fname, running),
                parse_mode='Markdown')
        except Exception as e:
            if "not modified" not in str(e).lower(): raise
    except Exception as e:
        logger.error(f"Restart error: {e}")
        bot.answer_callback_query(call.id, "Error.", show_alert=True)

def delete_bot_callback(call):
    try:
        _, owner_str, fname = call.data.split('_', 2)
        owner_id = int(owner_str)
        uid = call.from_user.id
        chat_id = call.message.chat.id
        if not (uid == owner_id or uid in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ No permission.", show_alert=True); return
        files = user_files.get(owner_id, [])
        if not any(f[0] == fname for f in files):
            bot.answer_callback_query(call.id, "File not found.", show_alert=True); return
        bot.answer_callback_query(call.id, "Deleting...")
        key = f"{owner_id}_{fname}"
        if is_bot_running(owner_id, fname):
            pi = bot_scripts.get(key)
            if pi: kill_process_tree(pi)
            if key in bot_scripts: del bot_scripts[key]
            time.sleep(0.5)
        folder = get_user_folder(owner_id)
        fpath = os.path.join(folder, fname)
        lpath = os.path.join(folder, f"{os.path.splitext(fname)[0]}.log")
        deleted = []
        if os.path.exists(fpath):
            try: os.remove(fpath); deleted.append(fname)
            except: pass
        if os.path.exists(lpath):
            try: os.remove(lpath); deleted.append(os.path.basename(lpath))
            except: pass
        remove_user_file_db(owner_id, fname)
        try:
            bot.edit_message_text(
                f"🗑️ Deleted `{fname}` for User `{owner_id}`",
                chat_id, call.message.message_id, parse_mode='Markdown')
        except: pass
    except Exception as e:
        logger.error(f"Delete error: {e}")
        bot.answer_callback_query(call.id, "Error.", show_alert=True)

def logs_bot_callback(call):
    try:
        _, owner_str, fname = call.data.split('_', 2)
        owner_id = int(owner_str)
        uid = call.from_user.id
        chat_id = call.message.chat.id
        if not (uid == owner_id or uid in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ No permission.", show_alert=True); return
        files = user_files.get(owner_id, [])
        if not any(f[0] == fname for f in files):
            bot.answer_callback_query(call.id, "File not found.", show_alert=True); return
        folder = get_user_folder(owner_id)
        lpath = os.path.join(folder, f"{os.path.splitext(fname)[0]}.log")
        if not os.path.exists(lpath):
            bot.answer_callback_query(call.id, "No logs.", show_alert=True); return
        bot.answer_callback_query(call.id)
        try:
            content = ""
            size = os.path.getsize(lpath)
            if size == 0: content = "(Log empty)"
            elif size > 100 * 1024:
                with open(lpath, 'rb') as f:
                    f.seek(-100 * 1024, os.SEEK_END)
                    content = "(Last 100 KB)\n...\n" + f.read().decode('utf-8', errors='ignore')
            else:
                with open(lpath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            if len(content) > 4096:
                content = content[-4096:]
                nl = content.find('\n')
                if nl != -1: content = "...\n" + content[nl+1:]
            if not content.strip(): content = "(No content)"
            bot.send_message(chat_id, f"📜 Logs for `{fname}`:\n```\n{content}\n```", parse_mode='Markdown')
        except Exception as e:
            bot.send_message(chat_id, f"❌ Error reading log: {e}")
    except Exception as e:
        logger.error(f"Logs error: {e}")
        bot.answer_callback_query(call.id, "Error.", show_alert=True)

def speed_callback(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    start = time.time()
    try:
        bot.edit_message_text("🏃 Testing...", chat_id, call.message.message_id)
        bot.send_chat_action(chat_id, 'typing')
        rt = round((time.time() - start) * 1000, 2)
        status = "🔓" if not bot_locked else "🔒"
        if uid == OWNER_ID: lvl = "👑 Owner"
        elif uid in admin_ids: lvl = "🛡️ Admin"
        elif uid in user_subscriptions and user_subscriptions[uid].get('expiry', datetime.min) > datetime.now(): lvl = "⭐ Premium"
        else: lvl = "🆓 Free"
        msg = f"⚡ *Bot Speed:*\n\n⏱️ {rt} ms\n🚦 Status: {status}\n👤 Level: {lvl}"
        bot.answer_callback_query(call.id)
        bot.edit_message_text(msg, chat_id, call.message.message_id,
                              reply_markup=create_main_menu_inline(uid), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Speed error: {e}")
        try: bot.answer_callback_query(call.id, "Error.", show_alert=True)
        except: pass

def back_to_main_callback(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    limit = get_user_file_limit(uid)
    count = get_user_file_count(uid)
    l_str = str(limit) if limit != float('inf') else "Unlimited"
    ref = get_user_referral_count(uid)
    if uid == OWNER_ID: st = "👑 Owner"
    elif uid in admin_ids: st = "🛡️ Admin"
    elif uid in user_subscriptions:
        exp = user_subscriptions[uid].get('expiry')
        if exp and exp > datetime.now(): st = "⭐ Premium"
        else: st = "🆓 Free (Expired)"
    else: st = "🆓 Free User"
    text = (f"〽️ *Welcome back, {call.from_user.first_name}!*\n\n"
            f"🆔 ID: `{uid}`\n🔰 Status: {st}\n"
            f"📁 Files: {count} / {l_str}\n🎁 Referrals: {ref}\n\n"
            f"👇 Use buttons or commands.")
    try:
        bot.answer_callback_query(call.id)
        bot.edit_message_text(text, chat_id, call.message.message_id,
                              reply_markup=create_main_menu_inline(uid), parse_mode='Markdown')
    except Exception as e:
        if "not modified" not in str(e).lower(): logger.error(f"Back error: {e}")

# --- Admin Callbacks ---
def subscription_management_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("💳 Subscription Management:",
                              call.message.chat.id, call.message.message_id,
                              reply_markup=create_subscription_menu())
    except: pass

def stats_callback(call):
    bot.answer_callback_query(call.id)
    _logic_statistics(call.message)
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                      reply_markup=create_main_menu_inline(call.from_user.id))
    except: pass

def lock_bot_callback(call):
    global bot_locked; bot_locked = True
    bot.answer_callback_query(call.id, "🔒 Locked.")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                        reply_markup=create_main_menu_inline(call.from_user.id))
    except: pass

def unlock_bot_callback(call):
    global bot_locked; bot_locked = False
    bot.answer_callback_query(call.id, "🔓 Unlocked.")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                        reply_markup=create_main_menu_inline(call.from_user.id))
    except: pass

def run_all_scripts_callback(call):
    _logic_run_all_scripts(call)

def broadcast_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📢 Send broadcast message.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def admin_panel_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("👑 Admin Panel:", call.message.chat.id, call.message.message_id,
                              reply_markup=create_admin_panel())
    except: pass

def add_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Send User ID to promote.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_add_admin_id)

def process_add_admin_id(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "⚠️ Owner only."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        new_id = int(message.text.strip())
        if new_id <= 0: raise ValueError
        if new_id == OWNER_ID: bot.reply_to(message, "Owner already."); return
        if new_id in admin_ids: bot.reply_to(message, "Already admin."); return
        add_admin_db(new_id)
        bot.reply_to(message, f"✅ `{new_id}` is now Admin.", parse_mode='Markdown')
        try: bot.send_message(new_id, "🎉 You are now an Admin!")
        except: pass
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID.")
        msg = bot.send_message(message.chat.id, "Send ID or /cancel.")
        bot.register_next_step_handler(msg, process_add_admin_id)

def remove_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Send User ID to demote.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_remove_admin_id)

def process_remove_admin_id(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "⚠️ Owner only."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        aid = int(message.text.strip())
        if aid <= 0: raise ValueError
        if aid == OWNER_ID: bot.reply_to(message, "Can't remove Owner."); return
        if aid not in admin_ids: bot.reply_to(message, "Not admin."); return
        if remove_admin_db(aid):
            bot.reply_to(message, f"✅ `{aid}` removed.", parse_mode='Markdown')
            try: bot.send_message(aid, "ℹ️ You are no longer Admin.")
            except: pass
        else: bot.reply_to(message, "❌ Failed.")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID.")
        msg = bot.send_message(message.chat.id, "Send ID or /cancel.")
        bot.register_next_step_handler(msg, process_remove_admin_id)

def list_admins_callback(call):
    bot.answer_callback_query(call.id)
    try:
        lst = "\n".join(f"- `{aid}` {'(Owner)' if aid == OWNER_ID else ''}" for aid in sorted(admin_ids))
        bot.edit_message_text(f"👑 Admins:\n\n{lst}", call.message.chat.id, call.message.message_id,
                              reply_markup=create_admin_panel(), parse_mode='Markdown')
    except: pass

def add_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Send `UserID days` (e.g., `123 30`).\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_add_subscription_details)

def process_add_subscription_details(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Not auth."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        parts = message.text.split()
        if len(parts) != 2: raise ValueError
        uid = int(parts[0]); days = int(parts[1])
        if uid <= 0 or days <= 0: raise ValueError
        current = user_subscriptions.get(uid, {}).get('expiry')
        start = datetime.now()
        if current and current > start: start = current
        new_exp = start + timedelta(days=days)
        save_subscription(uid, new_exp)
        bot.reply_to(message, f"✅ Sub added for `{uid}` by {days} days.\nExpires: {new_exp:%Y-%m-%d}",
                     parse_mode='Markdown')
        try: bot.send_message(uid, f"🎉 Sub activated! Expires: {new_exp:%Y-%m-%d}")
        except: pass
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid. Format: `ID days`.")
        msg = bot.send_message(message.chat.id, "Send `ID days` or /cancel.")
        bot.register_next_step_handler(msg, process_add_subscription_details)

def remove_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Send User ID to remove sub.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_remove_subscription_id)

def process_remove_subscription_id(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Not auth."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        uid = int(message.text.strip())
        if uid <= 0: raise ValueError
        if uid not in user_subscriptions:
            bot.reply_to(message, "⚠️ No active sub."); return
        remove_subscription_db(uid)
        bot.reply_to(message, f"✅ Sub removed for `{uid}`.", parse_mode='Markdown')
        try: bot.send_message(uid, "ℹ️ Your subscription removed.")
        except: pass
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID.")
        msg = bot.send_message(message.chat.id, "Send ID or /cancel.")
        bot.register_next_step_handler(msg, process_remove_subscription_id)

def check_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Send User ID to check.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_check_subscription_id)

def process_check_subscription_id(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Not auth."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        uid = int(message.text.strip())
        if uid <= 0: raise ValueError
        if uid in user_subscriptions:
            exp = user_subscriptions[uid].get('expiry')
            if exp and exp > datetime.now():
                days = (exp - datetime.now()).days
                bot.reply_to(message, f"✅ Active: Expires {exp:%Y-%m-%d} ({days} days)")
            else:
                bot.reply_to(message, f"⚠️ Expired: {exp}")
                remove_subscription_db(uid)
        else: bot.reply_to(message, "ℹ️ No sub.")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID.")
        msg = bot.send_message(message.chat.id, "Send ID or /cancel.")
        bot.register_next_step_handler(msg, process_check_subscription_id)

# --- Cleanup ---
def cleanup():
    logger.warning("Shutting down. Stopping processes...")
    for key in list(bot_scripts.keys()):
        if key in bot_scripts:
            kill_process_tree(bot_scripts[key])
    logger.warning("Cleanup done.")

atexit.register(cleanup)

# --- Main ---
if __name__ == '__main__':
    logger.info("="*40)
    logger.info("🤖 Bot Starting...")
    logger.info(f"🐍 Python: {sys.version.split()[0]}")
    logger.info(f"📁 Base: {BASE_DIR}")
    logger.info(f"🔑 Owner: {OWNER_ID}")
    logger.info(f"📢 Channel: {UPDATE_CHANNEL}")
    logger.info("="*40)
    keep_alive()
    logger.info("🚀 Polling started...")
    while True:
        try:
            bot.infinity_polling(logger_level=logging.INFO, timeout=60, long_polling_timeout=30)
        except requests.exceptions.ReadTimeout:
            logger.warning("ReadTimeout. Restarting in 5s..."); time.sleep(5)
        except requests.exceptions.ConnectionError as ce:
            logger.error(f"ConnectionError: {ce}. Retry 15s..."); time.sleep(15)
        except Exception as e:
            logger.critical(f"💥 Critical: {e}", exc_info=True)
            logger.info("Restart in 30s..."); time.sleep(30)