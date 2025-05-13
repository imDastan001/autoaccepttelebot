from telegram.ext import Application, ChatJoinRequestHandler, ContextTypes
from telegram import Update
import asyncio
import os
from mongo import User_collection
from dotenv import load_dotenv
from telegram.ext import CommandHandler

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))
# CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
ALLOWED_CHAT_IDS = [int(os.getenv("CHANNEL_ID"))]
# In-memory pending request queue
pending_requests = []
pending_lock = asyncio.Lock()

CHECK_INTERVAL = 30  # in seconds


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Hello! I'm a bot that handles join requests.")
# --- HANDLER FOR JOIN REQUEST ---
async def handle_chat_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.chat_join_request.from_user.id
    chat_id = update.chat_join_request.chat.id
    user_name = update.chat_join_request.from_user.full_name
    if chat_id not in ALLOWED_CHAT_IDS:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"❌ Join request from unknown chat {chat_id} was ignored.")
        return
    try:
        if User_collection.find_one({'user_id': str(user_id)}):
            await asyncio.sleep(10)# Convert to string
            await update.chat_join_request.approve()
            await notify_admin(context, user_id=user_id, user_name=user_name)
            return
    except Exception as e:
        await context.bot.send_message(ADMIN_CHAT_ID, f"Error while handling join request for user {user_id}: {e}")
    
    async with pending_lock:
        if not any(p['user_id'] == user_id and p['chat_id'] == chat_id for p in pending_requests):
            pending_requests.append({
                'user_id': user_id,
                'chat_id': chat_id,
                'user_name': user_name,
                'check_count': 0
            })

# --- PERIODIC CHECK ---
async def check_pending_requests(context: ContextTypes.DEFAULT_TYPE):
    global pending_requests

    async with pending_lock:
        current_batch = pending_requests.copy()
        pending_requests = []

    if not current_batch:
        return

    user_ids = [str(req['user_id']) for req in current_batch]  # Convert all to string
    allowed_users = []
    try:
        allowed_users = User_collection.find({'user_id': {'$in': user_ids}})
    except Exception as e:
        await context.bot.send_message(ADMIN_CHAT_ID, f"Error while fetching allowed users: {e}")

    
    
    allowed_ids = [user['user_id'] for user in allowed_users]

    to_approve = []
    to_dismiss = []
    to_retry = []

    for req in current_batch:
        if str(req['user_id']) in allowed_ids:  # Compare as string
            to_approve.append(req)
        else:
            req['check_count'] += 1
            if req['check_count'] >= 3:
                to_dismiss.append(req)

            else:
                to_retry.append(req)


    for req in to_approve:
        await process_approval(context, req)

    for req in to_dismiss:
        await process_dismissal(context, req)

    async with pending_lock:
        pending_requests.extend(to_retry)

# --- APPROVE ---
async def process_approval(context: ContextTypes.DEFAULT_TYPE, req):
    try:
        await asyncio.sleep(10)
        await context.bot.approve_chat_join_request(chat_id=req['chat_id'], user_id=req['user_id'])
        await notify_admin(context, user_id=req['user_id'], user_name=req['user_name'])
    except Exception as e:
        print(f"Approval Error: {e}")

# --- DISMISS ---
async def process_dismissal(context: ContextTypes.DEFAULT_TYPE, req):
    try:
        
        await context.bot.decline_chat_join_request(chat_id=req['chat_id'], user_id=req['user_id'])
    except Exception as e:
        print(f"Dismissal Error: {e}")

# --- NOTIFY ADMIN ---
async def notify_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int, user_name: str = "Unknown"):
    safe_name = user_name if user_name else "Unknown"
    message = (
        "<b>✅ Request Approved</b>\n"
        f"<b>Name:</b> {safe_name}\n"
        f"<b>ID:</b> <code>{user_id}</code>"
    )
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode="HTML")


# --- MAIN ---
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(ChatJoinRequestHandler(handle_chat_join_request))
    application.add_handler(CommandHandler("start", start))
    job_queue = application.job_queue
    job_queue.run_repeating(check_pending_requests, interval=CHECK_INTERVAL, first=10)

    application.run_polling()

if __name__ == '__main__':
    main()
