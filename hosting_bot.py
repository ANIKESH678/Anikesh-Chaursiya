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
    return "Bot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Flask Keep-Alive server started.")
# --- End Flask Keep Alive ---

# --- Configuration ---
TOKEN = os.environ.get('BOT_TOKEN', '8241319689:AAGeQ_yEwv76AYvvDVvGvUZZuoJLtsSBQX0')
OWNER_ID = 6048094235
ADMIN_ID = 7687242263
YOUR_USERNAME = '@Rexws348'
UPDATE_CHANNEL = 'https://t.me/anikeshxhosting'
DEFAULT_FORCE_JOIN = 'https://t.me/anikeshxhosting'

# Limits
FREE_USER_LIMIT = 3
SUBSCRIBED_USER_LIMIT = 15
ADMIN_LIMIT = 999
OWNER_LIMIT = 999999

# Folder setup
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

# Initialize bot
bot = telebot.TeleBot(TOKEN, parse_mode=None)

# --- Data structures ---
bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False
force_join_channels = []
force_join_enabled = True
user_bot_limits = {}

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Database Setup ---
def init_db():
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
        c.execute('''CREATE TABLE IF NOT EXISTS force_join
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_link TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings
                     (key TEXT PRIMARY KEY, value TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_bot_limits
                     (user_id INTEGER PRIMARY KEY, limit_value INTEGER)''')

        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))

        c.execute('SELECT COUNT(*) FROM force_join')
        if c.fetchone()[0] == 0:
            c.execute('INSERT INTO force_join (channel_link) VALUES (?)', (DEFAULT_FORCE_JOIN,))

        c.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('force_join_enabled', '1'))

        conn.commit()
        conn.close()
        logger.info("Database initialized.")
    except Exception as e:
        logger.error(f"DB init error: {e}", exc_info=True)

def load_data():
    global force_join_channels, force_join_enabled, user_bot_limits
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()

        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            try:
                user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
            except:
                pass

        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for user_id, file_name, file_type in c.fetchall():
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id].append((file_name, file_type))

        c.execute('SELECT user_id FROM active_users')
        active_users.update(row[0] for row in c.fetchall())

        c.execute('SELECT user_id FROM admins')
        admin_ids.update(row[0] for row in c.fetchall())

        c.execute('SELECT channel_link FROM force_join')
        force_join_channels = [row[0] for row in c.fetchall()]

        c.execute("SELECT value FROM settings WHERE key='force_join_enabled'")
        row = c.fetchone()
        if row:
            force_join_enabled = row[0] == '1'

        c.execute('SELECT user_id, limit_value FROM user_bot_limits')
        for user_id, limit_value in c.fetchall():
            user_bot_limits[user_id] = limit_value

        conn.close()
        logger.info(f"Data loaded: {len(active_users)} users, {len(force_join_channels)} force channels.")
    except Exception as e:
        logger.error(f"Data load error: {e}", exc_info=True)

init_db()
load_data()

# --- Helper Functions ---
def get_user_folder(user_id):
    folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(folder, exist_ok=True)
    return folder

def get_user_file_limit(user_id):
    if user_id == OWNER_ID:
        return OWNER_LIMIT
    if user_id in admin_ids:
        return user_bot_limits.get(user_id, ADMIN_LIMIT)
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return user_bot_limits.get(user_id, SUBSCRIBED_USER_LIMIT)
    return user_bot_limits.get(user_id, FREE_USER_LIMIT)

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
                try:
                    if 'log_file' in script_info and not script_info['log_file'].closed:
                        script_info['log_file'].close()
                except:
                    pass
                if script_key in bot_scripts:
                    del bot_scripts[script_key]
            return is_running
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            try:
                if 'log_file' in script_info and not script_info['log_file'].closed:
                    script_info['log_file'].close()
            except:
                pass
            if script_key in bot_scripts:
                del bot_scripts[script_key]
            return False
        except Exception:
            return False
    return False

def kill_process_tree(process_info):
    try:
        if 'log_file' in process_info:
            try:
                if not process_info['log_file'].closed:
                    process_info['log_file'].close()
            except:
                pass

        process = process_info.get('process')
        if process and hasattr(process, 'pid') and process.pid:
            try:
                parent = psutil.Process(process.pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except:
                        pass
                gone, alive = psutil.wait_procs(children, timeout=2)
                for p in alive:
                    try:
                        p.kill()
                    except:
                        pass
                try:
                    parent.terminate()
                    try:
                        parent.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        parent.kill()
                except:
                    try:
                        parent.kill()
                    except:
                        pass
            except psutil.NoSuchProcess:
                pass
            except Exception as e:
                logger.error(f"Kill error: {e}")
    except Exception as e:
        logger.error(f"kill_process_tree error: {e}")

# --- Force Join ---
def extract_channel_username(channel_input):
    if not channel_input:
        return None
    channel_input = channel_input.strip()
    patterns = [
        r't\.me/([a-zA-Z0-9_]+)',
        r'telegram\.me/([a-zA-Z0-9_]+)',
        r'@([a-zA-Z0-9_]+)',
        r'^([a-zA-Z0-9_]+)$'
    ]
    for pattern in patterns:
        match = re.search(pattern, channel_input)
        if match:
            username = match.group(1)
            if username.startswith('+') or 'joinchat' in username.lower():
                return None
            return username
    return None

def check_force_join(user_id):
    if not force_join_enabled:
        return True
    if user_id in admin_ids or user_id == OWNER_ID:
        return True
    if not force_join_channels:
        return True

    for channel in force_join_channels:
        try:
            username = extract_channel_username(channel)
            if not username:
                continue
            try:
                member = bot.get_chat_member(f"@{username}", user_id)
                if member.status in ['left', 'kicked']:
                    return False
            except telebot.apihelper.ApiTelegramException as e:
                err = str(e).lower()
                if "user not found" in err or "participant not found" in err:
                    return False
                logger.warning(f"Force join check failed for @{username}: {e}")
                continue
        except Exception as e:
            logger.error(f"Force join error: {e}")
            continue
    return True

def get_force_join_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    for i, channel in enumerate(force_join_channels):
        if channel.startswith('http'):
            link = channel
        elif channel.startswith('@'):
            link = f"https://t.me/{channel[1:]}"
        else:
            link = f"https://t.me/{channel}"
        markup.add(types.InlineKeyboardButton(f"📢 Join Channel {i+1}", url=link))
    markup.add(types.InlineKeyboardButton("✅ I've Joined - Verify", callback_data="verify_join"))
    return markup

def send_force_join_message(chat_id, user_name):
    channels_text = "\n".join([f"• {ch}" for ch in force_join_channels])
    msg = (f"⚠️ Force Join Required\n\n"
           f"Hello {user_name},\n\n"
           f"To use this bot, you must join:\n\n"
           f"{channels_text}\n\n"
           f"After joining, click Verify button.")
    try:
        bot.send_message(chat_id, msg, reply_markup=get_force_join_markup())
    except Exception as e:
        logger.error(f"Force join msg error: {e}")

# --- Package Installation ---
TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'bs4': 'beautifulsoup4',
    'requests': 'requests',
    'pillow': 'Pillow',
    'PIL': 'Pillow',
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
}

CORE_MODULES = {'asyncio', 'json', 'datetime', 'os', 'sys', 're', 'time', 'math',
                'random', 'logging', 'threading', 'subprocess', 'zipfile',
                'tempfile', 'shutil', 'sqlite3', 'atexit', 'io', 'collections',
                'functools', 'itertools', 'typing', 'traceback', 'warnings',
                'hashlib', 'base64', 'uuid', 'csv', 'glob', 'pathlib', 'pickle'}

def attempt_install_pip(module_name, message):
    if module_name.lower() in CORE_MODULES:
        return False
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    try:
        bot.reply_to(message, f"🐍 Installing {package_name}...")
        cmd = [sys.executable, '-m', 'pip', 'install', package_name]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False,
                                encoding='utf-8', errors='ignore', timeout=180)
        if result.returncode == 0:
            bot.reply_to(message, f"✅ {package_name} installed.")
            return True
        else:
            bot.reply_to(message, f"❌ Failed to install {package_name}.")
            return False
    except Exception as e:
        bot.reply_to(message, f"❌ Install error: {str(e)}")
        return False

def attempt_install_npm(module_name, user_folder, message):
    try:
        bot.reply_to(message, f"🟠 Installing npm: {module_name}...")
        cmd = ['npm', 'install', module_name]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False,
                                cwd=user_folder, encoding='utf-8', errors='ignore', timeout=180)
        if result.returncode == 0:
            bot.reply_to(message, f"✅ {module_name} installed.")
            return True
        else:
            bot.reply_to(message, f"❌ Failed: {module_name}")
            return False
    except FileNotFoundError:
        bot.reply_to(message, "❌ npm not found.")
        return False
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")
        return False

def run_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
    if attempt > 2:
        try:
            bot.reply_to(message_obj, f"❌ Failed to run {file_name}.")
        except:
            pass
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Run py attempt {attempt}: {script_path}")

    try:
        if not os.path.exists(script_path):
            try:
                bot.reply_to(message_obj, f"❌ {file_name} not found.")
            except:
                pass
            remove_user_file_db(script_owner_id, file_name)
            return

        if attempt == 1:
            check_proc = None
            try:
                check_proc = subprocess.Popen(
                    [sys.executable, script_path], cwd=user_folder,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', errors='ignore'
                )
                stdout, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match:
                        module_name = match.group(1).strip().strip("'\"")
                        if module_name.split('.')[0] not in CORE_MODULES:
                            if attempt_install_pip(module_name, message_obj):
                                time.sleep(2)
                                threading.Thread(
                                    target=run_script,
                                    args=(script_path, script_owner_id, user_folder, file_name, message_obj, attempt + 1)
                                ).start()
                                return
                            else:
                                return
                    else:
                        err_text = stderr[:400] if stderr else "Unknown error"
                        try:
                            bot.reply_to(message_obj, f"❌ Error:\n```\n{err_text}\n```")
                        except:
                            bot.reply_to(message_obj, f"❌ Error in {file_name}")
                        return
            except subprocess.TimeoutExpired:
                if check_proc and check_proc.poll() is None:
                    try:
                        check_proc.kill()
                        check_proc.communicate(timeout=2)
                    except:
                        pass
            except FileNotFoundError:
                bot.reply_to(message_obj, "❌ Python not found.")
                return
            except Exception as e:
                logger.error(f"Pre-check error: {e}")
            finally:
                if check_proc and check_proc.poll() is None:
                    try:
                        check_proc.kill()
                    except:
                        pass

        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        try:
            log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            bot.reply_to(message_obj, f"❌ Log file error: {e}")
            return

        try:
            process = subprocess.Popen(
                [sys.executable, script_path], cwd=user_folder,
                stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                encoding='utf-8', errors='ignore'
            )
            bot_scripts[script_key] = {
                'process': process,
                'log_file': log_file,
                'file_name': file_name,
                'chat_id': message_obj.chat.id,
                'script_owner_id': script_owner_id,
                'start_time': datetime.now(),
                'user_folder': user_folder,
                'type': 'py',
                'script_key': script_key
            }
            bot.reply_to(message_obj, f"✅ {file_name} started! (PID: {process.pid})")
        except FileNotFoundError:
            bot.reply_to(message_obj, "❌ Python interpreter not found.")
            try:
                log_file.close()
            except:
                pass
        except Exception as e:
            try:
                log_file.close()
            except:
                pass
            bot.reply_to(message_obj, f"❌ Start error: {str(e)}")
    except Exception as e:
        logger.error(f"run_script error: {e}", exc_info=True)
        try:
            bot.reply_to(message_obj, f"❌ Error: {str(e)}")
        except:
            pass
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
    if attempt > 2:
        try:
            bot.reply_to(message_obj, f"❌ Failed to run {file_name}.")
        except:
            pass
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Run js attempt {attempt}: {script_path}")

    try:
        if not os.path.exists(script_path):
            try:
                bot.reply_to(message_obj, f"❌ {file_name} not found.")
            except:
                pass
            remove_user_file_db(script_owner_id, file_name)
            return

        if attempt == 1:
            check_proc = None
            try:
                check_proc = subprocess.Popen(
                    ['node', script_path], cwd=user_folder,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', errors='ignore'
                )
                stdout, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match = re.search(r"Cannot find module '(.+?)'", stderr)
                    if match:
                        module_name = match.group(1).strip().strip("'\"")
                        if not module_name.startswith('.') and not module_name.startswith('/'):
                            if attempt_install_npm(module_name, user_folder, message_obj):
                                time.sleep(2)
                                threading.Thread(
                                    target=run_js_script,
                                    args=(script_path, script_owner_id, user_folder, file_name, message_obj, attempt + 1)
                                ).start()
                                return
                            else:
                                return
                    err_text = stderr[:400] if stderr else "Unknown error"
                    try:
                        bot.reply_to(message_obj, f"❌ Error:\n```\n{err_text}\n```")
                    except:
                        bot.reply_to(message_obj, f"❌ Error in {file_name}")
                    return
            except subprocess.TimeoutExpired:
                if check_proc and check_proc.poll() is None:
                    try:
                        check_proc.kill()
                        check_proc.communicate(timeout=2)
                    except:
                        pass
            except FileNotFoundError:
                bot.reply_to(message_obj, "❌ node not found.")
                return
            except Exception as e:
                logger.error(f"JS pre-check error: {e}")
            finally:
                if check_proc and check_proc.poll() is None:
                    try:
                        check_proc.kill()
                    except:
                        pass

        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        try:
            log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            bot.reply_to(message_obj, f"❌ Log file error: {e}")
            return

        try:
            process = subprocess.Popen(
                ['node', script_path], cwd=user_folder,
                stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                encoding='utf-8', errors='ignore'
            )
            bot_scripts[script_key] = {
                'process': process,
                'log_file': log_file,
                'file_name': file_name,
                'chat_id': message_obj.chat.id,
                'script_owner_id': script_owner_id,
                'start_time': datetime.now(),
                'user_folder': user_folder,
                'type': 'js',
                'script_key': script_key
            }
            bot.reply_to(message_obj, f"✅ {file_name} started! (PID: {process.pid})")
        except FileNotFoundError:
            bot.reply_to(message_obj, "❌ Node not found.")
            try:
                log_file.close()
            except:
                pass
        except Exception as e:
            try:
                log_file.close()
            except:
                pass
            bot.reply_to(message_obj, f"❌ Start error: {str(e)}")
    except Exception as e:
        logger.error(f"run_js_script error: {e}", exc_info=True)
        try:
            bot.reply_to(message_obj, f"❌ Error: {str(e)}")
        except:
            pass
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

# --- Database Ops ---
DB_LOCK = threading.Lock()

def save_user_file(user_id, file_name, file_type='py'):
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
                      (user_id, file_name, file_type))
            conn.commit()
            conn.close()
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
            user_files[user_id].append((file_name, file_type))
        except Exception as e:
            logger.error(f"save_user_file error: {e}")

def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
            conn.commit()
            conn.close()
            if user_id in user_files:
                user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
                if not user_files[user_id]:
                    del user_files[user_id]
        except Exception as e:
            logger.error(f"remove_user_file_db error: {e}")

def add_active_user(user_id):
    active_users.add(user_id)
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"add_active_user error: {e}")

def save_subscription(user_id, expiry):
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)',
                      (user_id, expiry.isoformat()))
            conn.commit()
            conn.close()
            user_subscriptions[user_id] = {'expiry': expiry}
        except Exception as e:
            logger.error(f"save_subscription error: {e}")

def remove_subscription_db(user_id):
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            if user_id in user_subscriptions:
                del user_subscriptions[user_id]
        except Exception as e:
            logger.error(f"remove_subscription error: {e}")

def add_admin_db(admin_id):
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            conn.commit()
            conn.close()
            admin_ids.add(admin_id)
        except Exception as e:
            logger.error(f"add_admin error: {e}")

def remove_admin_db(admin_id):
    if admin_id == OWNER_ID:
        return False
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
            conn.commit()
            conn.close()
            admin_ids.discard(admin_id)
            return True
        except Exception as e:
            logger.error(f"remove_admin error: {e}")
            return False

def save_setting(key, value):
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"save_setting error: {e}")

def add_force_join_channel(channel_link):
    global force_join_channels
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT INTO force_join (channel_link) VALUES (?)', (channel_link,))
            conn.commit()
            conn.close()
            force_join_channels.append(channel_link)
            return True
        except Exception as e:
            logger.error(f"add_force_join error: {e}")
            return False

def remove_force_join_channel(channel_link):
    global force_join_channels
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM force_join WHERE channel_link = ?', (channel_link,))
            conn.commit()
            conn.close()
            if channel_link in force_join_channels:
                force_join_channels.remove(channel_link)
            return True
        except Exception as e:
            logger.error(f"remove_force_join error: {e}")
            return False

def clear_force_join_channels():
    global force_join_channels
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM force_join')
            conn.commit()
            conn.close()
            force_join_channels = []
            return True
        except Exception as e:
            logger.error(f"clear_force_join error: {e}")
            return False

def set_user_bot_limit(user_id, limit):
    global user_bot_limits
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO user_bot_limits (user_id, limit_value) VALUES (?, ?)',
                      (user_id, limit))
            conn.commit()
            conn.close()
            user_bot_limits[user_id] = limit
            return True
        except Exception as e:
            logger.error(f"set_user_bot_limit error: {e}")
            return False

def remove_user_bot_limit(user_id):
    global user_bot_limits
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM user_bot_limits WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            if user_id in user_bot_limits:
                del user_bot_limits[user_id]
            return True
        except Exception as e:
            logger.error(f"remove_user_bot_limit error: {e}")
            return False

# --- Keyboards ---
def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if user_id in admin_ids:
        layout = [
            ["📢 Updates Channel"],
            ["📤 Upload File", "📂 Check Files"],
            ["⚡ Bot Speed", "📊 Statistics"],
            ["💳 Subscriptions", "📢 Broadcast"],
            ["🔒 Lock Bot", "🟢 Running All Code"],
            ["📣 Force Join", "🔔 Notification"],
            ["⚙️ User Limits", "👑 Admin Panel"],
            ["📞 Contact Owner"]
        ]
    else:
        layout = [
            ["📢 Updates Channel"],
            ["📤 Upload File", "📂 Check Files"],
            ["⚡ Bot Speed", "📊 Statistics"],
            ["📞 Contact Owner"]
        ]
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

def create_force_join_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    status = "✅ Enabled" if force_join_enabled else "❌ Disabled"
    markup.add(types.InlineKeyboardButton(f'🔄 Toggle ({status})', callback_data='toggle_force_join'))
    markup.add(types.InlineKeyboardButton('➕ Add Channel', callback_data='add_force_channel'))
    markup.add(types.InlineKeyboardButton('➖ Remove Channel', callback_data='remove_force_channel'))
    markup.add(types.InlineKeyboardButton('📋 List Channels', callback_data='list_force_channels'))
    markup.add(types.InlineKeyboardButton('🗑️ Clear All', callback_data='clear_force_channels'))
    markup.add(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

# --- Logic Functions ---
def _logic_send_welcome(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_name = message.from_user.first_name
    user_username = message.from_user.username

    if bot_locked and user_id not in admin_ids:
        try:
            bot.send_message(chat_id, "⚠️ Bot locked by admin. Try later.")
        except:
            pass
        return

    if not check_force_join(user_id):
        send_force_join_message(chat_id, user_name)
        return

    if user_id not in active_users:
        add_active_user(user_id)
        try:
            bot.send_message(OWNER_ID,
                f"🎉 New user!\n👤 {user_name}\n✳️ @{user_username or 'N/A'}\n🆔 `{user_id}`")
        except:
            pass

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit < 999999 else "Unlimited"
    expiry_info = ""

    if user_id == OWNER_ID:
        user_status = "👑 Owner"
    elif user_id in admin_ids:
        user_status = "🛡️ Admin"
    elif user_id in user_subscriptions:
        expiry_date = user_subscriptions[user_id].get('expiry')
        if expiry_date and expiry_date > datetime.now():
            user_status = "⭐ Premium"
            days_left = (expiry_date - datetime.now()).days
            expiry_info = f"\n⏳ Expires in: {days_left} days"
        else:
            user_status = "🆓 Free User"
            remove_subscription_db(user_id)
    else:
        user_status = "🆓 Free User"

    welcome_msg = (f"〽️ Welcome, {user_name}!\n\n"
                   f"🆔 User ID: `{user_id}`\n"
                   f"✳️ Username: `@{user_username or 'Not set'}`\n"
                   f"🔰 Status: {user_status}{expiry_info}\n"
                   f"📁 Files: {current_files} / {limit_str}\n\n"
                   f"🤖 Host Python (.py) or JS (.js) scripts.\n"
                   f"Upload single file or .zip archive.\n\n"
                   f"👇 Use buttons below.")

    try:
        bot.send_message(chat_id, welcome_msg, reply_markup=create_reply_keyboard_main_menu(user_id))
    except Exception as e:
        logger.error(f"Welcome error: {e}")

def _logic_updates_channel(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL))
    bot.reply_to(message, "Visit our Updates Channel:", reply_markup=markup)

def _logic_upload_file(message):
    user_id = message.from_user.id
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked by admin.")
        return
    if not check_force_join(user_id):
        send_force_join_message(message.chat.id, message.from_user.first_name)
        return
    file_limit = get_user_file_limit(user_id)
    if get_user_file_count(user_id) >= file_limit:
        limit_str = str(file_limit) if file_limit < 999999 else "Unlimited"
        bot.reply_to(message, f"⚠️ File limit reached ({file_limit}).")
        return
    bot.reply_to(message, "📤 Send .py, .js, or .zip file.")

def _logic_check_files(message):
    user_id = message.from_user.id
    if not check_force_join(user_id):
        send_force_join_message(message.chat.id, message.from_user.first_name)
        return
    files = user_files.get(user_id, [])
    if not files:
        bot.reply_to(message, "📂 No files uploaded yet.")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for fn, ft in sorted(files):
        running = is_bot_running(user_id, fn)
        icon = "🟢" if running else "🔴"
        markup.add(types.InlineKeyboardButton(f"{icon} {fn} ({ft})", callback_data=f'file_{user_id}_{fn}'))
    bot.reply_to(message, "📂 Your files:", reply_markup=markup)

def _logic_bot_speed(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    start = time.time()
    wait = bot.reply_to(message, "🏃 Testing...")
    try:
        bot.send_chat_action(chat_id, 'typing')
        rt = round((time.time() - start) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        if user_id == OWNER_ID:
            lvl = "👑 Owner"
        elif user_id in admin_ids:
            lvl = "🛡️ Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now():
            lvl = "⭐ Premium"
        else:
            lvl = "🆓 Free"
        bot.edit_message_text(
            f"⚡ Speed Test:\n\n⏱️ {rt} ms\n🚦 {status}\n👤 {lvl}",
            chat_id, wait.message_id
        )
    except Exception as e:
        logger.error(f"Speed error: {e}")

def _logic_contact_owner(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📞 Contact', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    bot.reply_to(message, "Click to contact:", reply_markup=markup)

def _logic_statistics(message):
    user_id = message.from_user.id
    total_users = len(active_users)
    total_files = sum(len(f) for f in user_files.values())
    running = 0
    user_running = 0
    for key, info in list(bot_scripts.items()):
        try:
            owner = int(key.split('_', 1)[0])
            if is_bot_running(owner, info['file_name']):
                running += 1
                if owner == user_id:
                    user_running += 1
        except:
            pass
    base = f"📊 Statistics:\n\n👥 Users: {total_users}\n📂 Files: {total_files}\n🟢 Running: {running}\n"
    if user_id in admin_ids:
        extra = f"🔒 Bot: {'Locked' if bot_locked else 'Unlocked'}\n📣 Force Join: {'ON' if force_join_enabled else 'OFF'}\n"
        base += extra
    base += f"🤖 Your Bots: {user_running}"
    bot.reply_to(message, base)

def _logic_broadcast_init(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    msg = bot.reply_to(message, "📢 Send message to broadcast.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def _logic_toggle_lock_bot(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    global bot_locked
    bot_locked = not bot_locked
    bot.reply_to(message, f"🔒 Bot {'locked' if bot_locked else 'unlocked'}.")

def _logic_admin_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    bot.reply_to(message, "👑 Admin Panel", reply_markup=create_admin_panel())

def _logic_force_join_menu(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    status = "✅ Enabled" if force_join_enabled else "❌ Disabled"
    channels = "\n".join([f"• {ch}" for ch in force_join_channels]) if force_join_channels else "(none)"
    bot.reply_to(message, f"📣 Force Join: {status}\n\n{channels}", reply_markup=create_force_join_menu())

def _logic_add_notification(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    msg = bot.reply_to(message, "🔔 Send notification to all users.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_notification_message)

def _logic_user_bot_limits(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin only.")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton('➕ Set User Limit', callback_data='set_user_limit'))
    markup.add(types.InlineKeyboardButton('➖ Remove User Limit', callback_data='remove_user_limit'))
    markup.add(types.InlineKeyboardButton('📋 List Limits', callback_data='list_user_limits'))
    markup.add(types.InlineKeyboardButton('🔙 Back', callback_data='back_to_main'))
    bot.reply_to(message, "⚙️ User Bot Limits", reply_markup=markup)

def _logic_run_all_scripts(message_or_call):
    if isinstance(message_or_call, telebot.types.Message):
        admin_id = message_or_call.from_user.id
        chat_id = message_or_call.chat.id
        reply = lambda t, **k: bot.reply_to(message_or_call, t, **k)
        msg_obj = message_or_call
    elif isinstance(message_or_call, telebot.types.CallbackQuery):
        admin_id = message_or_call.from_user.id
        chat_id = message_or_call.message.chat.id
        try:
            bot.answer_callback_query(message_or_call.id)
        except:
            pass
        reply = lambda t, **k: bot.send_message(chat_id, t, **k)
        msg_obj = message_or_call.message
    else:
        return

    if admin_id not in admin_ids:
        reply("⚠️ Admin only.")
        return

    reply("⏳ Starting all user scripts...")
    started = 0
    skipped = 0
    for target_uid, files in dict(user_files).items():
        if not files:
            continue
        folder = get_user_folder(target_uid)
        for fn, ft in files:
            if not is_bot_running(target_uid, fn):
                fp = os.path.join(folder, fn)
                if os.path.exists(fp):
                    try:
                        if ft == 'py':
                            threading.Thread(target=run_script, args=(fp, target_uid, folder, fn, msg_obj)).start()
                            started += 1
                        elif ft == 'js':
                            threading.Thread(target=run_js_script, args=(fp, target_uid, folder, fn, msg_obj)).start()
                            started += 1
                        time.sleep(0.5)
                    except:
                        skipped += 1
                else:
                    skipped += 1
    reply(f"✅ Started: {started}\n⚠️ Skipped: {skipped}")

# --- Button Mapping ---
BUTTON_TEXT_TO_LOGIC = {
    "📢 Updates Channel": _logic_updates_channel,
    "📤 Upload File": _logic_upload_file,
    "📂 Check Files": _logic_check_files,
    "⚡ Bot Speed": _logic_bot_speed,
    "📞 Contact Owner": _logic_contact_owner,
    "📊 Statistics": _logic_statistics,
    "💳 Subscriptions": lambda m: bot.reply_to(m, "💳 Subscriptions", reply_markup=create_subscription_menu()) if m.from_user.id in admin_ids else bot.reply_to(m, "⚠️ Admin only."),
    "📢 Broadcast": _logic_broadcast_init,
    "🔒 Lock Bot": _logic_toggle_lock_bot,
    "🟢 Running All Code": _logic_run_all_scripts,
    "👑 Admin Panel": _logic_admin_panel,
    "📣 Force Join": _logic_force_join_menu,
    "🔔 Notification": _logic_add_notification,
    "⚙️ User Limits": _logic_user_bot_limits,
}

# --- Handlers ---
@bot.message_handler(commands=['start', 'help'])
def cmd_start(message):
    try:
        _logic_send_welcome(message)
    except Exception as e:
        logger.error(f"Start error: {e}", exc_info=True)

@bot.message_handler(commands=['status', 'statistics'])
def cmd_status(message):
    try:
        _logic_statistics(message)
    except Exception as e:
        logger.error(f"Stats error: {e}")

@bot.message_handler(commands=['ping'])
def cmd_ping(message):
    try:
        start = time.time()
        msg = bot.reply_to(message, "Pong!")
        latency = round((time.time() - start) * 1000, 2)
        bot.edit_message_text(f"Pong! {latency} ms", message.chat.id, msg.message_id)
    except:
        pass

@bot.message_handler(func=lambda m: m.text in BUTTON_TEXT_TO_LOGIC if m.text else False)
def handle_button_text(message):
    try:
        fn = BUTTON_TEXT_TO_LOGIC.get(message.text)
        if fn:
            fn(message)
    except Exception as e:
        logger.error(f"Button error: {e}", exc_info=True)

@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    doc = message.document

    try:
        if bot_locked and user_id not in admin_ids:
            bot.reply_to(message, "⚠️ Bot locked.")
            return

        if not check_force_join(user_id):
            send_force_join_message(chat_id, message.from_user.first_name)
            return

        file_limit = get_user_file_limit(user_id)
        if get_user_file_count(user_id) >= file_limit:
            bot.reply_to(message, f"⚠️ File limit reached.")
            return

        file_name = doc.file_name
        if not file_name:
            bot.reply_to(message, "⚠️ No file name.")
            return

        ext = os.path.splitext(file_name)[1].lower()
        if ext not in ['.py', '.js', '.zip']:
            bot.reply_to(message, "⚠️ Only .py, .js, .zip allowed.")
            return

        if doc.file_size > 20 * 1024 * 1024:
            bot.reply_to(message, "⚠️ File too large (Max 20MB).")
            return

        try:
            bot.forward_message(OWNER_ID, chat_id, message.message_id)
        except:
            pass

        wait = bot.reply_to(message, f"⏳ Downloading {file_name}...")
        file_info = bot.get_file(doc.file_id)
        content = bot.download_file(file_info.file_path)
        bot.edit_message_text(f"✅ Downloaded. Processing...", chat_id, wait.message_id)

        folder = get_user_folder(user_id)

        if ext == '.zip':
            handle_zip_file(content, file_name, message)
        else:
            fp = os.path.join(folder, file_name)
            with open(fp, 'wb') as f:
                f.write(content)
            if ext == '.js':
                save_user_file(user_id, file_name, 'js')
                threading.Thread(target=run_js_script, args=(fp, user_id, folder, file_name, message)).start()
            elif ext == '.py':
                save_user_file(user_id, file_name, 'py')
                threading.Thread(target=run_script, args=(fp, user_id, folder, file_name, message)).start()

    except telebot.apihelper.ApiTelegramException as e:
        if "file is too big" in str(e).lower():
            bot.reply_to(message, "❌ File too large to download.")
        else:
            bot.reply_to(message, f"❌ API Error: {str(e)}")
    except Exception as e:
        logger.error(f"Doc error: {e}", exc_info=True)
        try:
            bot.reply_to(message, f"❌ Error: {str(e)}")
        except:
            pass

def handle_zip_file(content, file_name_zip, message):
    user_id = message.from_user.id
    folder = get_user_folder(user_id)
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_")
        zip_path = os.path.join(temp_dir, file_name_zip)
        with open(zip_path, 'wb') as f:
            f.write(content)

        with zipfile.ZipFile(zip_path, 'r') as zf:
            for member in zf.infolist():
                mp = os.path.abspath(os.path.join(temp_dir, member.filename))
                if not mp.startswith(os.path.abspath(temp_dir)):
                    raise zipfile.BadZipFile("Unsafe path")
            zf.extractall(temp_dir)

        items = os.listdir(temp_dir)
        py_files = [f for f in items if f.endswith('.py')]
        js_files = [f for f in items if f.endswith('.js')]

        if 'requirements.txt' in items:
            req = os.path.join(temp_dir, 'requirements.txt')
            bot.reply_to(message, "🔄 Installing Python deps...")
            try:
                r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', req],
                                   capture_output=True, text=True, timeout=300,
                                   encoding='utf-8', errors='ignore')
                if r.returncode == 0:
                    bot.reply_to(message, "✅ Python deps installed.")
                else:
                    bot.reply_to(message, "⚠️ Some Python deps failed.")
            except Exception as e:
                bot.reply_to(message, f"⚠️ Deps error: {e}")

        if 'package.json' in items:
            bot.reply_to(message, "🔄 Installing Node deps...")
            try:
                r = subprocess.run(['npm', 'install'], capture_output=True, text=True,
                                   check=False, cwd=temp_dir, timeout=300,
                                   encoding='utf-8', errors='ignore')
                if r.returncode == 0:
                    bot.reply_to(message, "✅ Node deps installed.")
            except FileNotFoundError:
                bot.reply_to(message, "⚠️ npm not found.")
            except Exception as e:
                bot.reply_to(message, f"⚠️ Node deps error: {e}")

        main_file = None
        ftype = None
        for p in ['main.py', 'bot.py', 'app.py']:
            if p in py_files:
                main_file = p
                ftype = 'py'
                break
        if not main_file:
            for p in ['index.js', 'main.js', 'bot.js', 'app.js']:
                if p in js_files:
                    main_file = p
                    ftype = 'js'
                    break
        if not main_file:
            if py_files:
                main_file = py_files[0]
                ftype = 'py'
            elif js_files:
                main_file = js_files[0]
                ftype = 'js'

        if not main_file:
            bot.reply_to(message, "❌ No .py or .js found in zip!")
            return

        for item in os.listdir(temp_dir):
            src = os.path.join(temp_dir, item)
            dst = os.path.join(folder, item)
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            elif os.path.exists(dst):
                os.remove(dst)
            shutil.move(src, dst)

        save_user_file(user_id, main_file, ftype)
        fp = os.path.join(folder, main_file)
        bot.reply_to(message, f"✅ Extracted. Starting {main_file}...")

        if ftype == 'py':
            threading.Thread(target=run_script, args=(fp, user_id, folder, main_file, message)).start()
        else:
            threading.Thread(target=run_js_script, args=(fp, user_id, folder, main_file, message)).start()

    except zipfile.BadZipFile as e:
        bot.reply_to(message, f"❌ Invalid ZIP: {e}")
    except Exception as e:
        logger.error(f"Zip error: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {str(e)}")
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except:
                pass

# --- Callbacks ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    try:
        user_id = call.from_user.id
        data = call.data

        if bot_locked and user_id not in admin_ids and data not in ['back_to_main', 'speed', 'stats', 'verify_join']:
            bot.answer_callback_query(call.id, "⚠️ Bot locked.", show_alert=True)
            return

        if data == 'upload':
            cb_upload(call)
        elif data == 'check_files':
            cb_check_files(call)
        elif data.startswith('file_'):
            cb_file_control(call)
        elif data.startswith('start_'):
            cb_start(call)
        elif data.startswith('stop_'):
            cb_stop(call)
        elif data.startswith('restart_'):
            cb_restart(call)
        elif data.startswith('delete_'):
            cb_delete(call)
        elif data.startswith('logs_'):
            cb_logs(call)
        elif data == 'speed':
            cb_speed(call)
        elif data == 'back_to_main':
            cb_back_main(call)
        elif data == 'verify_join':
            cb_verify_join(call)
        elif data == 'subscription':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                try:
                    bot.edit_message_text("💳 Subscriptions", call.message.chat.id,
                                          call.message.message_id, reply_markup=create_subscription_menu())
                except:
                    pass
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        elif data == 'stats':
            bot.answer_callback_query(call.id)
            _logic_statistics(call.message)
        elif data == 'lock_bot':
            if user_id in admin_ids:
                global bot_locked
                bot_locked = True
                bot.answer_callback_query(call.id, "🔒 Locked")
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'unlock_bot':
            if user_id in admin_ids:
                bot_locked = False
                bot.answer_callback_query(call.id, "🔓 Unlocked")
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'run_all_scripts':
            if user_id in admin_ids:
                _logic_run_all_scripts(call)
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'broadcast':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                msg = bot.send_message(call.message.chat.id, "📢 Send message.\n/cancel to abort.")
                bot.register_next_step_handler(msg, process_broadcast_message)
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'admin_panel':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                try:
                    bot.edit_message_text("👑 Admin Panel", call.message.chat.id,
                                          call.message.message_id, reply_markup=create_admin_panel())
                except:
                    pass
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'add_admin':
            if user_id == OWNER_ID:
                bot.answer_callback_query(call.id)
                msg = bot.send_message(call.message.chat.id, "Enter User ID to promote.\n/cancel")
                bot.register_next_step_handler(msg, process_add_admin)
            else:
                bot.answer_callback_query(call.id, "⚠️ Owner only", show_alert=True)
        elif data == 'remove_admin':
            if user_id == OWNER_ID:
                bot.answer_callback_query(call.id)
                msg = bot.send_message(call.message.chat.id, "Enter Admin ID to remove.\n/cancel")
                bot.register_next_step_handler(msg, process_remove_admin)
            else:
                bot.answer_callback_query(call.id, "⚠️ Owner only", show_alert=True)
        elif data == 'list_admins':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                lst = "\n".join([f"- `{a}` {'(Owner)' if a == OWNER_ID else ''}" for a in sorted(admin_ids)])
                try:
                    bot.edit_message_text(f"👑 Admins:\n\n{lst}", call.message.chat.id,
                                          call.message.message_id, reply_markup=create_admin_panel())
                except:
                    pass
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'add_subscription':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                msg = bot.send_message(call.message.chat.id, "Enter: USER_ID DAYS\nExample: 12345678 30\n/cancel")
                bot.register_next_step_handler(msg, process_add_sub)
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'remove_subscription':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                msg = bot.send_message(call.message.chat.id, "Enter User ID to remove sub.\n/cancel")
                bot.register_next_step_handler(msg, process_remove_sub)
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'check_subscription':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                msg = bot.send_message(call.message.chat.id, "Enter User ID to check.\n/cancel")
                bot.register_next_step_handler(msg, process_check_sub)
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'force_join_settings':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                _show_force_join_menu(call)
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'toggle_force_join':
            if user_id in admin_ids:
                global force_join_enabled
                force_join_enabled = not force_join_enabled
                save_setting('force_join_enabled', '1' if force_join_enabled else '0')
                bot.answer_callback_query(call.id, f"Force Join: {'ON' if force_join_enabled else 'OFF'}")
                _show_force_join_menu(call)
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'add_force_channel':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                msg = bot.send_message(call.message.chat.id,
                    "Send channel link/username.\n\n"
                    "Formats:\n"
                    "• https://t.me/channelname\n"
                    "• @channelname\n"
                    "• channelname\n\n/cancel")
                bot.register_next_step_handler(msg, process_add_force_channel)
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'remove_force_channel':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                if not force_join_channels:
                    bot.send_message(call.message.chat.id, "⚠️ No channels.")
                    return
                ch_list = "\n".join([f"{i+1}. {ch}" for i, ch in enumerate(force_join_channels)])
                msg = bot.send_message(call.message.chat.id,
                    f"Current:\n{ch_list}\n\nSend channel to remove.\n/cancel")
                bot.register_next_step_handler(msg, process_remove_force_channel)
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'list_force_channels':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                if not force_join_channels:
                    txt = "(No channels)"
                else:
                    txt = "\n".join([f"{i+1}. {ch}" for i, ch in enumerate(force_join_channels)])
                try:
                    bot.edit_message_text(f"📣 Force Join Channels:\n\n{txt}",
                                          call.message.chat.id, call.message.message_id,
                                          reply_markup=create_force_join_menu())
                except:
                    pass
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'clear_force_channels':
            if user_id in admin_ids:
                clear_force_join_channels()
                bot.answer_callback_query(call.id, "✅ Cleared")
                _show_force_join_menu(call)
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'add_notification':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                msg = bot.send_message(call.message.chat.id, "🔔 Send notification message.\n/cancel")
                bot.register_next_step_handler(msg, process_notification_message)
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'user_bot_limits':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(types.InlineKeyboardButton('➕ Set User Limit', callback_data='set_user_limit'))
                markup.add(types.InlineKeyboardButton('➖ Remove User Limit', callback_data='remove_user_limit'))
                markup.add(types.InlineKeyboardButton('📋 List Limits', callback_data='list_user_limits'))
                markup.add(types.InlineKeyboardButton('🔙 Back', callback_data='back_to_main'))
                try:
                    bot.edit_message_text("⚙️ User Bot Limits", call.message.chat.id,
                                          call.message.message_id, reply_markup=markup)
                except:
                    pass
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'set_user_limit':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                msg = bot.send_message(call.message.chat.id,
                    "Send: USER_ID LIMIT\nExample: `12345678 10`\n\n0 = unlimited\n/cancel")
                bot.register_next_step_handler(msg, process_set_user_limit)
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'remove_user_limit':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                msg = bot.send_message(call.message.chat.id, "Send User ID.\n/cancel")
                bot.register_next_step_handler(msg, process_remove_user_limit)
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        elif data == 'list_user_limits':
            if user_id in admin_ids:
                bot.answer_callback_query(call.id)
                if not user_bot_limits:
                    txt = "(No custom limits)"
                else:
                    txt = "\n".join([f"• `{u}`: {'Unlimited' if l >= 999999 else l}" for u, l in user_bot_limits.items()])
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton('🔙 Back', callback_data='user_bot_limits'))
                try:
                    bot.edit_message_text(f"⚙️ User Limits:\n\n{txt}", call.message.chat.id,
                                          call.message.message_id, reply_markup=markup)
                except:
                    pass
            else:
                bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "Unknown action.")
    except Exception as e:
        logger.error(f"Callback error [{data}]: {e}", exc_info=True)
        try:
            bot.answer_callback_query(call.id, "Error processing.", show_alert=True)
        except:
            pass

def _show_force_join_menu(call):
    status = "✅ Enabled" if force_join_enabled else "❌ Disabled"
    channels = "\n".join([f"• {ch}" for ch in force_join_channels]) if force_join_channels else "(none)"
    try:
        bot.edit_message_text(f"📣 Force Join: {status}\n\n{channels}",
                              call.message.chat.id, call.message.message_id,
                              reply_markup=create_force_join_menu())
    except Exception as e:
        logger.error(f"Show force join error: {e}")

def cb_upload(call):
    user_id = call.from_user.id
    if get_user_file_count(user_id) >= get_user_file_limit(user_id):
        bot.answer_callback_query(call.id, "⚠️ Limit reached.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "📤 Send .py, .js, or .zip")

def cb_check_files(call):
    user_id = call.from_user.id
    files = user_files.get(user_id, [])
    if not files:
        bot.answer_callback_query(call.id, "⚠️ No files.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    for fn, ft in sorted(files):
        running = is_bot_running(user_id, fn)
        icon = "🟢" if running else "🔴"
        markup.add(types.InlineKeyboardButton(f"{icon} {fn} ({ft})", callback_data=f'file_{user_id}_{fn}'))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_main'))
    try:
        bot.edit_message_text("📂 Your files:", call.message.chat.id, call.message.message_id, reply_markup=markup)
    except:
        pass

def cb_file_control(call):
    try:
        parts = call.data.split('_', 2)
        if len(parts) < 3:
            bot.answer_callback_query(call.id, "Invalid.")
            return
        _, owner_str, fn = parts
        owner = int(owner_str)
        req = call.from_user.id

        if not (req == owner or req in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True)
            return

        files = user_files.get(owner, [])
        if not any(f[0] == fn for f in files):
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        running = is_bot_running(owner, fn)
        status = '🟢 Running' if running else '🔴 Stopped'
        ftype = next((f[1] for f in files if f[0] == fn), '?')
        bot.edit_message_text(
            f"⚙️ {fn} ({ftype})\nStatus: {status}",
            call.message.chat.id, call.message.message_id,
            reply_markup=create_control_buttons(owner, fn, running)
        )
    except Exception as e:
        logger.error(f"File control error: {e}")

def cb_start(call):
    try:
        _, owner_str, fn = call.data.split('_', 2)
        owner = int(owner_str)
        req = call.from_user.id
        if not (req == owner or req in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Denied.", show_alert=True)
            return

        files = user_files.get(owner, [])
        info = next((f for f in files if f[0] == fn), None)
        if not info:
            bot.answer_callback_query(call.id, "⚠️ Not found.", show_alert=True)
            return

        ft = info[1]
        folder = get_user_folder(owner)
        fp = os.path.join(folder, fn)

        if not os.path.exists(fp):
            bot.answer_callback_query(call.id, "⚠️ File missing.", show_alert=True)
            remove_user_file_db(owner, fn)
            return

        if is_bot_running(owner, fn):
            bot.answer_callback_query(call.id, "Already running.", show_alert=True)
            return

        bot.answer_callback_query(call.id, "Starting...")
        if ft == 'py':
            threading.Thread(target=run_script, args=(fp, owner, folder, fn, call.message)).start()
        else:
            threading.Thread(target=run_js_script, args=(fp, owner, folder, fn, call.message)).start()

        time.sleep(1.5)
        running = is_bot_running(owner, fn)
        status = '🟢 Running' if running else '🟡 Starting...'
        try:
            bot.edit_message_text(
                f"⚙️ {fn} ({ft})\nStatus: {status}",
                call.message.chat.id, call.message.message_id,
                reply_markup=create_control_buttons(owner, fn, running)
            )
        except:
            pass
    except Exception as e:
        logger.error(f"Start error: {e}")

def cb_stop(call):
    try:
        _, owner_str, fn = call.data.split('_', 2)
        owner = int(owner_str)
        req = call.from_user.id
        if not (req == owner or req in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Denied.", show_alert=True)
            return

        files = user_files.get(owner, [])
        info = next((f for f in files if f[0] == fn), None)
        if not info:
            bot.answer_callback_query(call.id, "⚠️ Not found.", show_alert=True)
            return

        ft = info[1]
        key = f"{owner}_{fn}"

        if not is_bot_running(owner, fn):
            bot.answer_callback_query(call.id, "Already stopped.", show_alert=True)
            return

        bot.answer_callback_query(call.id, "Stopping...")
        pi = bot_scripts.get(key)
        if pi:
            kill_process_tree(pi)
            if key in bot_scripts:
                del bot_scripts[key]

        try:
            bot.edit_message_text(
                f"⚙️ {fn} ({ft})\nStatus: 🔴 Stopped",
                call.message.chat.id, call.message.message_id,
                reply_markup=create_control_buttons(owner, fn, False)
            )
        except:
            pass
    except Exception as e:
        logger.error(f"Stop error: {e}")

def cb_restart(call):
    try:
        _, owner_str, fn = call.data.split('_', 2)
        owner = int(owner_str)
        req = call.from_user.id
        if not (req == owner or req in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Denied.", show_alert=True)
            return

        files = user_files.get(owner, [])
        info = next((f for f in files if f[0] == fn), None)
        if not info:
            bot.answer_callback_query(call.id, "⚠️ Not found.", show_alert=True)
            return

        ft = info[1]
        folder = get_user_folder(owner)
        fp = os.path.join(folder, fn)
        key = f"{owner}_{fn}"

        if not os.path.exists(fp):
            bot.answer_callback_query(call.id, "⚠️ File missing.", show_alert=True)
            return

        bot.answer_callback_query(call.id, "Restarting...")
        if is_bot_running(owner, fn):
            pi = bot_scripts.get(key)
            if pi:
                kill_process_tree(pi)
            if key in bot_scripts:
                del bot_scripts[key]
            time.sleep(1)

        if ft == 'py':
            threading.Thread(target=run_script, args=(fp, owner, folder, fn, call.message)).start()
        else:
            threading.Thread(target=run_js_script, args=(fp, owner, folder, fn, call.message)).start()

        time.sleep(1.5)
        running = is_bot_running(owner, fn)
        status = '🟢 Running' if running else '🟡 Starting...'
        try:
            bot.edit_message_text(
                f"⚙️ {fn} ({ft})\nStatus: {status}",
                call.message.chat.id, call.message.message_id,
                reply_markup=create_control_buttons(owner, fn, running)
            )
        except:
            pass
    except Exception as e:
        logger.error(f"Restart error: {e}")

def cb_delete(call):
    try:
        _, owner_str, fn = call.data.split('_', 2)
        owner = int(owner_str)
        req = call.from_user.id
        if not (req == owner or req in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Denied.", show_alert=True)
            return

        bot.answer_callback_query(call.id, "Deleting...")
        key = f"{owner}_{fn}"

        if is_bot_running(owner, fn):
            pi = bot_scripts.get(key)
            if pi:
                kill_process_tree(pi)
            if key in bot_scripts:
                del bot_scripts[key]

        folder = get_user_folder(owner)
        fp = os.path.join(folder, fn)
        lp = os.path.join(folder, f"{os.path.splitext(fn)[0]}.log")

        for p in [fp, lp]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except:
                    pass

        remove_user_file_db(owner, fn)
        try:
            bot.edit_message_text(f"🗑️ {fn} deleted!", call.message.chat.id,
                                  call.message.message_id, reply_markup=None)
        except:
            pass
    except Exception as e:
        logger.error(f"Delete error: {e}")

def cb_logs(call):
    try:
        _, owner_str, fn = call.data.split('_', 2)
        owner = int(owner_str)
        req = call.from_user.id
        if not (req == owner or req in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Denied.", show_alert=True)
            return

        folder = get_user_folder(owner)
        lp = os.path.join(folder, f"{os.path.splitext(fn)[0]}.log")

        if not os.path.exists(lp):
            bot.answer_callback_query(call.id, "⚠️ No logs.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        try:
            with open(lp, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            if len(content) > 4000:
                content = content[-4000:]
            if not content.strip():
                content = "(empty)"
            bot.send_message(call.message.chat.id, f"📜 Logs for {fn}:\n```\n{content}\n```")
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ Log error: {e}")
    except Exception as e:
        logger.error(f"Logs error: {e}")

def cb_speed(call):
    user_id = call.from_user.id
    try:
        start = time.time()
        bot.answer_callback_query(call.id)
        bot.send_chat_action(call.message.chat.id, 'typing')
        rt = round((time.time() - start) * 1000, 2)
        status = "🔓" if not bot_locked else "🔒"
        if user_id == OWNER_ID:
            lvl = "👑 Owner"
        elif user_id in admin_ids:
            lvl = "🛡️ Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now():
            lvl = "⭐ Premium"
        else:
            lvl = "🆓 Free"
        bot.edit_message_text(
            f"⚡ Speed:\n\n⏱️ {rt} ms\n🚦 {status}\n👤 {lvl}",
            call.message.chat.id, call.message.message_id
        )
    except Exception as e:
        logger.error(f"Speed cb error: {e}")

def cb_back_main(call):
    user_id = call.from_user.id
    try:
        if not check_force_join(user_id):
            send_force_join_message(call.message.chat.id, call.from_user.first_name)
            return
        file_limit = get_user_file_limit(user_id)
        current = get_user_file_count(user_id)
        limit_str = str(file_limit) if file_limit < 999999 else "Unlimited"
        if user_id == OWNER_ID:
            st = "👑 Owner"
        elif user_id in admin_ids:
            st = "🛡️ Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now():
            st = "⭐ Premium"
        else:
            st = "🆓 Free"
        txt = (f"〽️ Welcome, {call.from_user.first_name}!\n\n"
               f"🆔 `{user_id}`\n🔰 {st}\n📁 {current}/{limit_str}\n\n👇 Use buttons.")
        bot.answer_callback_query(call.id)
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.error(f"Back main error: {e}")

def cb_verify_join(call):
    user_id = call.from_user.id
    try:
        if check_force_join(user_id):
            bot.answer_callback_query(call.id, "✅ Verified!", show_alert=True)
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            _logic_send_welcome(call.message)
        else:
            bot.answer_callback_query(call.id, "❌ Join all channels first!", show_alert=True)
    except Exception as e:
        logger.error(f"Verify error: {e}")

# --- Process Functions ---
def process_broadcast_message(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    if not message.text:
        bot.reply_to(message, "⚠️ Text only.")
        return

    text = message.text
    count = len(active_users)
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_bc_{message.message_id}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_bc")
    )
    bot.reply_to(message, f"⚠️ Confirm broadcast to {count} users?\n\n{text[:200]}", reply_markup=markup)

def process_notification_message(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    if not message.text:
        bot.reply_to(message, "⚠️ Text only.")
        return

    sent = 0
    failed = 0
    for uid in list(active_users):
        try:
            bot.send_message(uid, f"🔔 Notification:\n\n{message.text}")
            sent += 1
            time.sleep(0.05)
        except:
            failed += 1
    bot.reply_to(message, f"✅ Sent: {sent}\n❌ Failed: {failed}")

def process_add_admin(message):
    if message.from_user.id != OWNER_ID:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        aid = int(message.text.strip())
        if aid <= 0:
            raise ValueError
        if aid == OWNER_ID:
            bot.reply_to(message, "Already Owner.")
            return
        if aid in admin_ids:
            bot.reply_to(message, "Already Admin.")
            return
        add_admin_db(aid)
        bot.reply_to(message, f"✅ `{aid}` is now Admin.")
        try:
            bot.send_message(aid, "🎉 You are now Admin!")
        except:
            pass
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")

def process_remove_admin(message):
    if message.from_user.id != OWNER_ID:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        aid = int(message.text.strip())
        if aid == OWNER_ID:
            bot.reply_to(message, "Cannot remove Owner.")
            return
        if aid not in admin_ids:
            bot.reply_to(message, "Not an Admin.")
            return
        if remove_admin_db(aid):
            bot.reply_to(message, f"✅ Removed Admin `{aid}`.")
            try:
                bot.send_message(aid, "ℹ️ You are no longer Admin.")
            except:
                pass
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")

def process_add_sub(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        parts = message.text.split()
        if len(parts) != 2:
            raise ValueError
        uid = int(parts[0])
        days = int(parts[1])
        if uid <= 0 or days <= 0:
            raise ValueError
        cur = user_subscriptions.get(uid, {}).get('expiry')
        start = datetime.now()
        if cur and cur > start:
            start = cur
        new_exp = start + timedelta(days=days)
        save_subscription(uid, new_exp)
        bot.reply_to(message, f"✅ Sub for `{uid}` by {days} days.\nExpires: {new_exp:%Y-%m-%d}")
        try:
            bot.send_message(uid, f"🎉 Subscription active till {new_exp:%Y-%m-%d}!")
        except:
            pass
    except:
        bot.reply_to(message, "⚠️ Format: `USER_ID DAYS`")

def process_remove_sub(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        uid = int(message.text.strip())
        if uid not in user_subscriptions:
            bot.reply_to(message, "No active sub.")
            return
        remove_subscription_db(uid)
        bot.reply_to(message, f"✅ Removed sub for `{uid}`.")
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")

def process_check_sub(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        uid = int(message.text.strip())
        if uid in user_subscriptions:
            exp = user_subscriptions[uid].get('expiry')
            if exp and exp > datetime.now():
                days = (exp - datetime.now()).days
                bot.reply_to(message, f"✅ Active till {exp:%Y-%m-%d} ({days} days left).")
            else:
                bot.reply_to(message, "⚠️ Expired.")
                remove_subscription_db(uid)
        else:
            bot.reply_to(message, "No active sub.")
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")

def process_add_force_channel(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    ch = message.text.strip()
    if add_force_join_channel(ch):
        bot.reply_to(message, f"✅ Added: `{ch}`")
    else:
        bot.reply_to(message, "❌ Failed to add.")

def process_remove_force_channel(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    ch = message.text.strip()
    if ch in force_join_channels:
        if remove_force_join_channel(ch):
            bot.reply_to(message, f"✅ Removed: `{ch}`")
        else:
            bot.reply_to(message, "❌ Failed.")
    else:
        bot.reply_to(message, "⚠️ Not found.")

def process_set_user_limit(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        parts = message.text.split()
        if len(parts) != 2:
            raise ValueError
        uid = int(parts[0])
        lim = int(parts[1])
        if uid <= 0 or lim < 0:
            raise ValueError
        val = 999999 if lim == 0 else lim
        if set_user_bot_limit(uid, val):
            bot.reply_to(message, f"✅ User `{uid}` limit: {'Unlimited' if lim == 0 else lim}")
        else:
            bot.reply_to(message, "❌ Failed.")
    except:
        bot.reply_to(message, "⚠️ Format: `USER_ID LIMIT`")

def process_remove_user_limit(message):
    if message.from_user.id not in admin_ids:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "Cancelled.")
        return
    try:
        uid = int(message.text.strip())
        if remove_user_bot_limit(uid):
            bot.reply_to(message, f"✅ Custom limit removed for `{uid}`.")
        else:
            bot.reply_to(message, f"⚠️ No custom limit for `{uid}`.")
    except:
        bot.reply_to(message, "⚠️ Invalid ID.")

# --- Broadcast Confirm Handlers ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_bc_') or call.data == 'cancel_bc')
def handle_broadcast_confirm(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only", show_alert=True)
        return

    if call.data == 'cancel_bc':
        bot.answer_callback_query(call.id, "Cancelled.")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        return

    try:
        original = call.message.reply_to_message
        if not original or not original.text:
            raise ValueError("No message")
        text = original.text
        bot.answer_callback_query(call.id, "Broadcasting...")
        bot.edit_message_text(f"📢 Sending to {len(active_users)} users...",
                              call.message.chat.id, call.message.message_id)

        def do_broadcast():
            sent = 0
            failed = 0
            for uid in list(active_users):
                try:
                    bot.send_message(uid, text)
                    sent += 1
                except:
                    failed += 1
                if sent % 25 == 0:
                    time.sleep(1.5)
                else:
                    time.sleep(0.05)
            try:
                bot.send_message(call.message.chat.id,
                    f"📢 Done!\n✅ Sent: {sent}\n❌ Failed: {failed}")
            except:
                pass

        threading.Thread(target=do_broadcast).start()
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}", call.message.chat.id, call.message.message_id)

# --- Cleanup ---
def cleanup():
    logger.warning("Shutdown. Cleaning processes...")
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
    logger.info(f"🔑 Owner: {OWNER_ID}")
    logger.info(f"🛡️ Admins: {admin_ids}")
    logger.info("="*40)

    keep_alive()

    logger.info("🚀 Polling started...")
    while True:
        try:
            bot.infinity_polling(logger_level=logging.INFO, timeout=60, long_polling_timeout=30)
        except requests.exceptions.ReadTimeout:
            logger.warning("ReadTimeout. Retry 5s...")
            time.sleep(5)
        except requests.exceptions.ConnectionError as ce:
            logger.error(f"ConnectionError: {ce}. Retry 15s...")
            time.sleep(15)
        except Exception as e:
            logger.critical(f"💥 Error: {e}", exc_info=True)
            time.sleep(30)