"""Svitlobot Stats — Telegram-бот: світло по Києву та повітряна тривога.
"""
import asyncio
import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import BotCommand, InputFile, Update
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes

from alerts import (
    format_alert_end,
    format_alert_message,
    get_kyiv_alert,
    get_kyiv_oblast_threats,
    get_nearby_threats,
)
from config import ADMIN_IDS, ALERT_REFRESH_MIN, CHAT_ID, KYIV_TZ, SVITLO_COOLDOWN_MIN, TOKEN
from storage import save_state, set_daily, state
from svitlo import (
    create_stats_image,
    format_districts_full,
    format_svitlo_caption,
    get_svitlobot_stats,
    parse_stats_caption,
)
from timeutils import kyiv_date_str, now_kyiv_str, parse_started_at

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

HELP_TEXT = (
    "🤖 Svitlobot Stats — бот про світло та повітряну тривогу в Києві\n\n"
    "Що вміє:\n"
    "💡 Показує, скільки датчиків Світлобота в Києві зараз зі світлом, "
    "і називає райони, де гірше за все (топ-5).\n"
    "📊 Щодня о 12:05 порівнює світло з учорашнім.\n"
    "🚨 Повідомляє про початок, зміну рівня та відбій повітряної тривоги; "
    "під час тривоги оновлює цілі навколо міста.\n"
    "📌 Закріплює пост про останню тривогу.\n"
    "🔕 Усі повідомлення без звуку.\n\n"
    "Команди:\n"
    "/svitlo — світло по місту\n"
    "/rayony — світло по всіх районах\n"
    "/tryvoga — статус тривоги і цілі поруч\n"
    "/tsili — цілі по Києву та Київській області\n"
    "/dopomoga — ця довідка\n\n"
    "Автоматично: статистика світла о 00:05, 04:05, 08:05, 12:05, 16:05, 20:05; "
    "тривога перевіряється кожні 40 секунд.\n\n"
    "Дякуємо джерелам даних:\n"
    "• NEPTUN (neptun.in.ua) — цілі та тривоги\n"
    "• mapa.ua — запасне джерело цілей\n"
    "• tryvoha.online — запасне джерело тривог\n"
    "• Svitlobot (svitlobot.in.ua) — дані про світло\n\n"
    "Для адмінів: /vidnovyty — відновлення бази порівняння."
)


async def pin_silently(app: Application, message_id: int):
    try:
        await app.bot.pin_chat_message(chat_id=CHAT_ID, message_id=message_id, disable_notification=True)
    except Exception as e:
        logger.warning(f"Не вдалося закріпити: {e}")


async def remove_old_alert_post(app: Application, msg_id):
    ids = set()
    if msg_id:
        ids.add(msg_id)
    try:
        chat = await app.bot.get_chat(CHAT_ID)
        pm = chat.pinned_message
        if pm and pm.from_user and pm.from_user.id == app.bot.id and "| Київ |" in (pm.text or ""):
            ids.add(pm.message_id)
    except Exception:
        pass
    for mid in ids:
        try:
            await app.bot.unpin_chat_message(chat_id=CHAT_ID, message_id=mid)
        except Exception:
            pass
        try:
            await app.bot.delete_message(chat_id=CHAT_ID, message_id=mid)
        except Exception:
            pass


async def edit_or_resend(app: Application, msg_id, text: str):
    if msg_id:
        try:
            await app.bot.edit_message_text(chat_id=CHAT_ID, message_id=msg_id, text=text)
            return msg_id, False
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return msg_id, False
        except Exception:
            pass
    msg = await app.bot.send_message(chat_id=CHAT_ID, text=text, disable_notification=True)
    return msg.message_id, True


async def cmd_svitlo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now_ts = time.time()
    last_ts = state.get("last_svitlo_ts") or 0
    last_mid = state.get("last_svitlo_message_id")
    if last_mid and (now_ts - last_ts) < SVITLO_COOLDOWN_MIN * 60:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=last_mid)
        except Exception:
            pass
    stats = await asyncio.to_thread(get_svitlobot_stats)
    if not stats:
        await update.message.reply_text("Не вдалося отримати дані зі Svitlobot", disable_notification=True)
        return
    msg = await update.message.reply_photo(
        photo=InputFile(create_stats_image(stats), filename="svitlo.png"),
        caption=format_svitlo_caption(stats),
        disable_notification=True,
    )
    state["last_svitlo_message_id"] = msg.message_id
    state["last_svitlo_ts"] = now_ts
    save_state(state)


async def cmd_rayony(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = await asyncio.to_thread(get_svitlobot_stats)
    if not stats:
        await update.message.reply_text("Не вдалося отримати дані зі Svitlobot", disable_notification=True)
        return
    await update.message.reply_text(format_districts_full(stats), disable_notification=True)


async def cmd_tryvoga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    alert = await asyncio.to_thread(get_kyiv_alert)
    if alert is None:
        await update.message.reply_text("Не вдалося перевірити статус тривоги", disable_notification=True)
        return
    if alert["active"]:
        threats = await asyncio.to_thread(get_nearby_threats)
        started_ts = state.get("alert_started_ts") or parse_started_at(alert.get("started_at"))
        text = format_alert_message(alert, threats, started_ts, "status")
    else:
        text = f"{now_kyiv_str()} | Київ | Тривоги немає\n\nДані: NEPTUN / tryvoha.online"
    await update.message.reply_text(text, disable_notification=True)


async def cmd_tsili(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = await asyncio.to_thread(get_kyiv_oblast_threats)
    if not result:
        await update.message.reply_text("Не вдалося отримати дані з NEPTUN", disable_notification=True)
        return
    text = result["summary"]
    if result.get("details"):
        text += "\n" + "\n".join(result["details"])
    text += "\n\nДані: NEPTUN (neptun.in.ua)"
    await update.message.reply_text(text, disable_notification=True)


async def cmd_dopomoga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, disable_notification=True)


async def cmd_vidnovyty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_IDS and update.effective_user.id not in ADMIN_IDS:
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Відповідай на пост зі статистикою о 12:05.")
        return
    src = update.message.reply_to_message
    parsed = parse_stats_caption(src.caption or src.text or "")
    if not parsed:
        await update.message.reply_text("У цьому повідомленні немає статистики світла.")
        return
    dt = src.date.astimezone(KYIV_TZ)
    if not (dt.hour == 12 and dt.minute < 30):
        await update.message.reply_text("Потрібен пост о 12:05.")
        return
    day = dt.strftime("%Y-%m-%d")
    set_daily(day, parsed["on"], parsed["off"])
    total = parsed["on"] + parsed["off"]
    await update.message.reply_text(
        f"✅ Збережено базу за {day}: світло було у {parsed['on']} з {total} ({parsed['on'] / total * 100:.1f}%)."
    )


async def send_stats(app: Application, daily: bool = False):
    stats = await asyncio.to_thread(get_svitlobot_stats)
    if not stats:
        return
    try:
        caption = format_svitlo_caption(stats, with_daily=daily)
        msg = await app.bot.send_photo(
            chat_id=CHAT_ID,
            photo=InputFile(create_stats_image(stats), filename="svitlo.png"),
            caption=caption,
            disable_notification=True,
        )
        state["last_svitlo_message_id"] = msg.message_id
        state["last_svitlo_ts"] = time.time()
        if daily:
            set_daily(kyiv_date_str(stats["ts"]), stats["on"], stats["off"])
        else:
            save_state(state)
    except Exception as e:
        logger.error(f"send_stats error: {e}")


async def check_alert(app: Application):
    alert = await asyncio.to_thread(get_kyiv_alert)
    if alert is None:
        return
    now_ts = time.time()
    active = alert["active"]
    level = alert.get("level")
    prev_active = state.get("last_alert_active", False)
    prev_level = state.get("last_alert_level")
    msg_id = state.get("alert_message_id")
    started_ts = state.get("alert_started_ts")
    try:
        if active and not prev_active:
            started_ts = parse_started_at(alert.get("started_at")) or now_ts
            await remove_old_alert_post(app, msg_id)
            threats = await asyncio.to_thread(get_nearby_threats)
            text = format_alert_message(alert, threats, started_ts, "start")
            msg = await app.bot.send_message(chat_id=CHAT_ID, text=text, disable_notification=True)
            state["alert_message_id"] = msg.message_id
            state["alert_started_ts"] = started_ts
            state["alert_last_refresh_ts"] = now_ts
            await pin_silently(app, msg.message_id)
        elif active and prev_active:
            if not started_ts:
                started_ts = parse_started_at(alert.get("started_at")) or now_ts
                state["alert_started_ts"] = started_ts
            level_changed = bool(level) and level != prev_level
            last_refresh = state.get("alert_last_refresh_ts") or 0
            refresh_due = (now_ts - last_refresh) >= ALERT_REFRESH_MIN * 60
            if level_changed or refresh_due:
                threats = await asyncio.to_thread(get_nearby_threats)
                kind = "update" if level_changed else "refresh"
                text = format_alert_message(alert, threats, started_ts, kind)
                new_id, created = await edit_or_resend(app, msg_id, text)
                state["alert_message_id"] = new_id
                state["alert_last_refresh_ts"] = now_ts
                if created:
                    await pin_silently(app, new_id)
        elif not active and prev_active:
            text = format_alert_end(prev_level, started_ts, now_ts)
            new_id, created = await edit_or_resend(app, msg_id, text)
            state["alert_message_id"] = new_id
            state["alert_started_ts"] = None
            state["alert_last_refresh_ts"] = 0
            if created:
                await pin_silently(app, new_id)
    except Exception as e:
        logger.error(f"check_alert error: {e}")
        save_state(state)
        return
    state["last_alert_active"] = active
    state["last_alert_level"] = level
    save_state(state)


async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("svitlo", "Світло в Києві: діаграма і топ-5 районів"),
        BotCommand("rayony", "Світло по всіх районах"),
        BotCommand("tryvoga", "Статус повітряної тривоги"),
        BotCommand("tsili", "Цілі по Києву та Київській області"),
        BotCommand("dopomoga", "Що вміє бот"),
    ])
    scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")
    scheduler.add_job(send_stats, "cron", hour="0,4,8,16,20", minute=5, args=[app, False], max_instances=1, coalesce=True)
    scheduler.add_job(send_stats, "cron", hour=12, minute=5, args=[app, True], max_instances=1, coalesce=True)
    scheduler.add_job(check_alert, "interval", seconds=40, args=[app], max_instances=1, coalesce=True)
    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    logger.info("Планувальник запущено")


def main():
    if not TOKEN or not CHAT_ID:
        logger.error("TELEGRAM_TOKEN або TELEGRAM_CHAT_ID не задані!")
        return
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("svitlo", cmd_svitlo))
    app.add_handler(CommandHandler("rayony", cmd_rayony))
    app.add_handler(CommandHandler(["tryvoga", "trivoga", "trevoga"], cmd_tryvoga))
    app.add_handler(CommandHandler("tsili", cmd_tsili))
    app.add_handler(CommandHandler(["dopomoga", "start"], cmd_dopomoga))
    app.add_handler(CommandHandler("vidnovyty", cmd_vidnovyty))
    logger.info("Бот запущено")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
