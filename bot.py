import os
import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, ChatJoinRequestHandler, CommandHandler,
    CallbackQueryHandler, ContextTypes
)
from telegram.request import HTTPXRequest
from telegram.error import RetryAfter

from mongo import User_collection

# ── Logging ───────────────────────────────────────────────────────────────────
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Environment variables ─────────────────────────────────────────────────────
TOKEN         = os.getenv("AUTO_ACCEPT_BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

# ── Bot mode — default is manual on startup ───────────────────────────────────
# "manual" or "auto"
current_mode = "manual"


# ═════════════════════════════════════════════════════════════════════════════
# DB CHECK
# ═════════════════════════════════════════════════════════════════════════════

def is_user_in_db(user_id: str) -> tuple[bool, str]:
    """
    Returns (found, status)
    found: True if user exists in DB
    status: "found" | "not_found" | "db_error"
    """
    try:
        user = User_collection.find_one({"user_id": user_id})
        if user:
            return True, "found"
        return False, "not_found"
    except Exception as e:
        logger.error(f"MongoDB error for user {user_id}: {e}")
        return False, "db_error"


# ═════════════════════════════════════════════════════════════════════════════
# /start
# ═════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    mode_label = "🟢 Auto" if current_mode == "auto" else "🔴 Manual"

    await update.message.reply_text(
        f"🤖 <b>Auto Accept Bot</b>\n\n"
        f"Current Mode: <b>{mode_label}</b>\n\n"
        f"Tap /auto to switch to auto mode\n"
        f"Tap /manual to switch to manual mode",
        parse_mode="HTML"
    )


# ═════════════════════════════════════════════════════════════════════════════
# /manual
# ═════════════════════════════════════════════════════════════════════════════

async def set_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    global current_mode

    if current_mode == "manual":
        await update.message.reply_text("⚠️ Already in manual mode!")
        return

    current_mode = "manual"
    await update.message.reply_text(
        "🔴 <b>Switched to Manual mode!</b>\n\n"
        "All join requests will be sent to you for approval.",
        parse_mode="HTML"
    )


# ═════════════════════════════════════════════════════════════════════════════
# /auto
# ═════════════════════════════════════════════════════════════════════════════

async def set_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    global current_mode

    if current_mode == "auto":
        await update.message.reply_text("⚠️ Already in auto mode!")
        return

    current_mode = "auto"
    await update.message.reply_text(
        "🟢 <b>Switched to Auto mode!</b>\n\n"
        "Bot will automatically accept/decline based on DB.",
        parse_mode="HTML"
    )


# ═════════════════════════════════════════════════════════════════════════════
# JOIN REQUEST HANDLER
# ═════════════════════════════════════════════════════════════════════════════

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    join_request = update.chat_join_request
    user         = join_request.from_user
    user_id      = str(user.id)
    chat_id      = join_request.chat.id
    username     = f"@{user.username}" if user.username else "(@none)"
    first_name   = user.first_name or "Unknown"

    logger.info(f"Join request from {first_name} ({user_id}) — mode: {current_mode}")

    if current_mode == "manual":
        await _handle_manual(context, user_id, username, first_name, chat_id)
    else:
        asyncio.create_task(
            _handle_auto(context, user_id, username, first_name, chat_id)
        )


# ═════════════════════════════════════════════════════════════════════════════
# MANUAL MODE HANDLER
# ═════════════════════════════════════════════════════════════════════════════

async def _handle_manual(context, user_id, username, first_name, chat_id):
    """Send admin a notification with Accept/Decline buttons."""
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Accept",
            callback_data=f"accept_{chat_id}_{user_id}"
        ),
        InlineKeyboardButton(
            "❌ Decline",
            callback_data=f"decline_{chat_id}_{user_id}"
        )
    ]])

    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=(
            f"📥 <b>New Join Request!</b>\n\n"
            f"👤 Name: {first_name}\n"
            f"🔗 Username: {username}\n"
            f"🆔 User ID: <code>{user_id}</code>\n\n"
            f"Tap below to accept or decline:"
        ),
        parse_mode="HTML",
        reply_markup=keyboard
    )


# ═════════════════════════════════════════════════════════════════════════════
# AUTO MODE HANDLER
# ═════════════════════════════════════════════════════════════════════════════

async def _handle_auto(context, user_id, username, first_name, chat_id):
    """Staggered sleep then check DB and accept/decline."""

    # Stagger: spread users over 0–18 extra seconds to avoid flood
    stagger = (int(user_id) % 10) * 2
    await asyncio.sleep(10 + stagger)

    found, status = is_user_in_db(user_id)

    if status == "db_error":
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"🚨 <b>MongoDB Error!</b>\n\n"
                    f"Could not process join request.\n\n"
                    f"👤 Name: {first_name}\n"
                    f"🔗 Username: {username}\n"
                    f"🆔 User ID: <code>{user_id}</code>\n\n"
                    f"Please handle manually in the channel lobby."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to notify admin about DB error: {e}")
        return

    if found:
        # Accept
        try:
            await context.bot.approve_chat_join_request(
                chat_id=chat_id,
                user_id=int(user_id)
            )
            logger.info(f"Auto accepted: {first_name} ({user_id})")

            # ── Notify admin about auto acceptance ──
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=(
                        f"✅ <b>Auto Accepted</b>\n\n"
                        f"👤 Name: {first_name}\n"
                        f"🔗 Username: {username}\n"
                        f"🆔 User ID: <code>{user_id}</code>"
                    ),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to notify admin about acceptance: {e}")

        except RetryAfter as e:
            # Telegram flood limit hit — wait and retry once
            logger.warning(f"Flood limit hit for {user_id}, retrying after {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            try:
                await context.bot.approve_chat_join_request(
                    chat_id=chat_id,
                    user_id=int(user_id)
                )
                logger.info(f"Auto accepted after retry: {first_name} ({user_id})")

                # ── Notify admin after retry acceptance ──
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=(
                            f"✅ <b>Auto Accepted (after retry)</b>\n\n"
                            f"👤 Name: {first_name}\n"
                            f"🔗 Username: {username}\n"
                            f"🆔 User ID: <code>{user_id}</code>"
                        ),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify admin after retry: {e}")

            except Exception as e:
                logger.info(f"Could not accept {user_id} after retry — may have cancelled: {e}")

        except Exception as e:
            # User may have cancelled request already — ignore silently
            logger.info(f"Could not accept {user_id} — may have cancelled: {e}")

    else:
        # Decline
        try:
            await context.bot.decline_chat_join_request(
                chat_id=chat_id,
                user_id=int(user_id)
            )
            logger.info(f"Auto declined: {first_name} ({user_id})")
        except RetryAfter as e:
            # Telegram flood limit hit — wait and retry once
            logger.warning(f"Flood limit hit on decline for {user_id}, retrying after {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            try:
                await context.bot.decline_chat_join_request(
                    chat_id=chat_id,
                    user_id=int(user_id)
                )
                logger.info(f"Auto declined after retry: {first_name} ({user_id})")
            except Exception as e:
                logger.info(f"Could not decline {user_id} after retry — may have cancelled: {e}")
        except Exception as e:
            # User may have cancelled request already — ignore silently
            logger.info(f"Could not decline {user_id} — may have cancelled: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# BUTTON CALLBACK — manual mode accept/decline
# ═════════════════════════════════════════════════════════════════════════════

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    data   = query.data

    if update.effective_user.id != ADMIN_CHAT_ID:
        await query.answer("❌ Not authorized.", show_alert=True)
        return

    await query.answer()

    parts   = data.split("_")
    action  = parts[0]           # "accept" or "decline"
    chat_id = int(parts[1])
    user_id = int(parts[2])

    if action == "accept":
        try:
            await context.bot.approve_chat_join_request(
                chat_id=chat_id,
                user_id=user_id
            )
            await query.edit_message_text(
                query.message.text + "\n\n✅ <b>Accepted!</b>",
                parse_mode="HTML"
            )
        except Exception:
            # Request no longer in lobby — user cancelled
            await query.edit_message_text(
                query.message.text + "\n\n⚠️ <b>Could not accept — request no longer pending.</b>",
                parse_mode="HTML"
            )

    elif action == "decline":
        try:
            await context.bot.decline_chat_join_request(
                chat_id=chat_id,
                user_id=user_id
            )
            await query.edit_message_text(
                query.message.text + "\n\n❌ <b>Declined!</b>",
                parse_mode="HTML"
            )
        except Exception:
            # Request no longer in lobby — user cancelled
            await query.edit_message_text(
                query.message.text + "\n\n⚠️ <b>Could not decline — request no longer pending.</b>",
                parse_mode="HTML"
            )


# ═════════════════════════════════════════════════════════════════════════════
# BOT STARTUP
# ═════════════════════════════════════════════════════════════════════════════

async def post_init(application: Application):
    try:
        await application.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                "🤖 <b>Auto Accept Bot Started!</b>\n\n"
                "Current Mode: 🔴 <b>Manual</b>\n\n"
                "Tap /auto to switch to auto mode\n"
                "Tap /manual to switch to manual mode"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Could not send startup message: {e}")


def main():
    if not TOKEN:
        logger.error("AUTO_ACCEPT_BOT_TOKEN is not set!")
        return

    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=60.0,
        write_timeout=60.0,
    )

    application = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start",  start))
    application.add_handler(CommandHandler("manual", set_manual))
    application.add_handler(CommandHandler("auto",   set_auto))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(ChatJoinRequestHandler(handle_join_request))

    logger.info("Auto Accept Bot is running...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    application.run_polling(
        allowed_updates=["chat_join_request", "message", "callback_query"],
        close_loop=False
    )


if __name__ == "__main__":
    main()
