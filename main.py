import os
import json
import datetime
from openai import OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, CallbackQueryHandler, filters
)
from telegram.error import Forbidden, BadRequest
import asyncpg

# ---------------- Config ----------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL")
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "0"))
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
DATABASE_URL = os.environ.get("DATABASE_URL")

# ---------------- OpenAI Client ----------------
client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url="https://api.chatanywhere.tech/v1"
)
EXPLAIN_MODEL = "gpt-3.5-turbo"
PROMPT_MODEL = "gpt-3.5-turbo"

# ---------------- Database Utils ----------------
async def get_db():
    return await asyncpg.connect(DATABASE_URL)

async def init_db():
    conn = await get_db()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            joined BOOLEAN DEFAULT FALSE
        )
    """)
    await conn.close()

async def add_user(user_id: int):
    conn = await get_db()
    await conn.execute(
        "INSERT INTO users (id, joined) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
        user_id, False
    )
    await conn.close()

async def update_join(user_id: int):
    conn = await get_db()
    await conn.execute("UPDATE users SET joined = TRUE WHERE id=$1", user_id)
    await conn.close()

async def is_joined(user_id: int):
    conn = await get_db()
    row = await conn.fetchrow("SELECT joined FROM users WHERE id=$1", user_id)
    await conn.close()
    return row["joined"] if row else False

async def get_all_users():
    conn = await get_db()
    rows = await conn.fetch("SELECT id FROM users")
    await conn.close()
    return [r["id"] for r in rows]

# ---------------- Prompt Builders ----------------
def build_explain_prompt(user_text: str) -> str:
    return f"""
You are an expert explainer. Explain the following text at three levels:

🔹 Basic — like for a 5-year-old  
🔸 Intermediate — like for a college student  
🔶 Advanced — for professionals  

Format:

🔹 *Basic explanation:*  
<text>

🔸 *Intermediate explanation:*  
<text>

🔶 *Advanced explanation:*  
<text>

Text:
\"\"\"{user_text}\"\"\""""

def build_prompt_refiner(user_text: str) -> str:
    return f"""
You are a world-class prompt engineer.

Task: Take the raw prompt and transform it into a highly detailed, structured, and optimized prompt.

Follow the rules:
1. Clarify the intent behind the original prompt.
2. Expand with context, constraints, and details.
3. Assign a clear role (expert teacher, senior developer, designer, etc.).
4. Give step-by-step instructions with measurable outcomes.
5. Make it professional, unambiguous, and future-proof.

⚠️ Output format (strictly follow, no extra text, no duplication):

- **Original Prompt**: {user_text}

- **Refined Prompt**: <ultra high-level upgraded version>
"""

# ---------------- Utils ----------------
async def send_log(context: ContextTypes.DEFAULT_TYPE, user, command: str, user_text: str, answer: str):
    if LOG_CHANNEL == 0:
        return
    log_msg = (
        f"👤 User: {user.mention_html() if user else 'Unknown'}\n"
        f"🆔 ID: `{user.id if user else 'N/A'}`\n"
        f"💬 Command: `{command}`\n"
        f"📥 Input:\n{user_text}\n\n"
        f"📤 Output:\n{answer[:3500]}"
    )
    try:
        await context.bot.send_message(chat_id=LOG_CHANNEL, text=log_msg, parse_mode="HTML")
    except Exception as e:
        print(f"⚠️ Log send error: {e}")

async def is_member(bot, user_id):
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Forbidden:
        print("⚠️ Bot is not admin in REQUIRED_CHANNEL!")
        return False
    except BadRequest as e:
        print(f"⚠️ Membership check bad request: {e}")
        return False
    except Exception as e:
        print(f"⚠️ Membership check error: {e}")
        return False

# ---------------- Handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await add_user(user_id)

    if await is_joined(user_id):
        text = (
            "👋 *Welcome back!*\n\n"
            "I can break down complex text or help you craft better prompts.\n\n"
            "📌 *Commands:*\n"
            "• `/explain <text>` → Multi-level explanations.\n"
            "• `/prompt <idea>` → Get polished prompt.\n"
            "\nMade with ❤️ by [Swayam](https://t.me/regnis)"
        )
        await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)
    else:
        keyboard = [
            [InlineKeyboardButton("👉 Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL.strip('@')}")],
            [InlineKeyboardButton("✅ I've Joined", callback_data="check_join")]
        ]
        await update.message.reply_text(
            "⚠️ Please join our channel to use the bot.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if await is_member(context.bot, user_id):
        await update_join(user_id)
        await query.message.edit_text("✅ Thanks for joining! Type /start again 🎉")
    else:
        await query.answer("❌ You haven't joined yet!", show_alert=True)

async def explain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_joined(user_id):
        keyboard = [
            [InlineKeyboardButton("👉 Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL.strip('@')}")],
            [InlineKeyboardButton("✅ I've Joined", callback_data="check_join")]
        ]
        await update.message.reply_text(
            "⚠️ Please join our channel to use the bot.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    user = update.effective_user
    user_text = " ".join(context.args) if context.args else (getattr(update.message, "text", "") or "")
    if not user_text or user_text.startswith("/explain"):
        await update.message.reply_text("❗ Usage: `/explain your_text_here`", parse_mode="Markdown")
        return

    try:
        prompt = build_explain_prompt(user_text)
        response = client.chat.completions.create(
            model=EXPLAIN_MODEL,
            messages=[
                {"role": "system", "content": "You explain at multiple levels clearly."},
                {"role": "user", "content": prompt}
            ]
        )
        answer = response.choices[0].message.content
        await update.message.reply_text(answer, parse_mode="Markdown")
        await send_log(context, user, "explain", user_text, answer)
    except Exception as e:
        err = f"⚠️ Error: {e}"
        await update.message.reply_text(err)
        await send_log(context, user, "explain", user_text, err)

async def prompt_refiner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_joined(user_id):
        keyboard = [
            [InlineKeyboardButton("👉 Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL.strip('@')}")],
            [InlineKeyboardButton("✅ I've Joined", callback_data="check_join")]
        ]
        await update.message.reply_text(
            "⚠️ Please join our channel to use the bot.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    user = update.effective_user
    user_text = " ".join(context.args)
    if not user_text:
        await update.message.reply_text("❗ Usage: `/prompt your_idea_here`", parse_mode="Markdown")
        return

    try:
        prompt = build_prompt_refiner(user_text)
        response = client.chat.completions.create(
            model=PROMPT_MODEL,
            messages=[
                {"role": "system", "content": "You are a world-class prompt engineer. Always return in the strict format."},
                {"role": "user", "content": prompt}
            ]
        )
        refined = response.choices[0].message.content.strip()
        await update.message.reply_text(refined, parse_mode="Markdown")
        await send_log(context, user, "prompt", user_text, refined)
    except Exception as e:
        err = f"⚠️ Error: {e}"
        await update.message.reply_text(err)
        await send_log(context, user, "prompt", user_text, err)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return
    if not context.args:
        await update.message.reply_text("❗ Usage: `/broadcast your_message`", parse_mode="Markdown")
        return

    message = " ".join(context.args)
    sent, failed = 0, 0
    users = await get_all_users()
    for uid in users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=message)
            sent += 1
        except:
            failed += 1
    await update.message.reply_text(f"📢 Broadcast complete!\n✅ Sent: {sent}\n⚠️ Failed: {failed}")

# ---------------- Global Error Handler ----------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"⚠️ Update error: {context.error}")
    try:
        if LOG_CHANNEL:
            await context.bot.send_message(LOG_CHANNEL, f"⚠️ Error: {context.error}")
    except:
        pass

# ---------------- Main ----------------
import asyncio

async def main():
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("explain", explain))
    app.add_handler(CommandHandler("prompt", prompt_refiner))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(check_join, pattern="check_join"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, explain))
    app.add_error_handler(error_handler)

    # ✅ Webhook mode (Render ke liye)
    PORT = int(os.environ.get("PORT", 8080))
    WEBHOOK_URL = os.environ["WEBHOOK_URL"]

    await app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=os.environ["TELEGRAM_BOT_TOKEN"],
        webhook_url=f"{WEBHOOK_URL}/{os.environ['TELEGRAM_BOT_TOKEN']}"
    )

if __name__ == "__main__":
    asyncio.run(main())
if __name__ == "__main__":
    import asyncio
    asyncio.run(init_db())
    print("🚀 Bot starting with Webhook + PostgreSQL...")
    main()
