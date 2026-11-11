"""Svitlobot Stats — Telegram-бот: світло по Києву та повітряна тривога.
"""
import asyncio
import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

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
    create_24h_image,
    create_week_image,
    create_yasno_image,
    format_districts_full,
    format_svitlo_caption,
    format_24h_caption,
    format_week_caption,
    format_yasno_caption,
    get_svitlobot_stats,
    compute_kyiv_history,
    fetch_yasno_schedule,
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
    "💡 Показує статистику світла по Києву та районах\n"
    "🚨 Повідомляє про повітряну тривогу та цілі біля Києва\n"
    "📌 Закріплює живий пост про ситуацію\n"
    "🔕 Усі повідомлення без звуку\n\n"
    "Команди:\n"
    "/svitlo — світло по місту\n"
    "/rayony — світло по районах\n"
    "/doba — статистика за останні 24 години\n"
    "/tyzhden — тижнева статистика\n"
    "/grafik — графік відключень Yasno\n"
    "/tryvoga — статус тривоги\n"
    "/tsili — цілі по Києву та області\n"
    "/status — створити/оновити закріплений живий пост\n"
    "/dopomoga — ця довідка\n\n"
    "Дякуємо джерелам: NEPTUN, tryvoha.online, mapa.ua, Svitlobot"
)


def alert_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Оновити", callback_data="update_alert")]
    ])


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


async def edit_or_resend(app: Application, msg_id, text: str, keyboard=None):
    if msg_id:
        try:
            await app.bot.edit_message_text(
                chat_id=CHAT_ID,
                message_id=msg_id,
                text=text,
                reply_markup=keyboard,
            )
            return msg_id, False
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return msg_id, False
        except Exception:
            pass
    msg = await app.bot.send_message(
        chat_id=CHAT_ID,
        text=text,
        disable_notification=True,
        reply_markup=keyboard,
    )
    return msg.message_id, True


# ================== КОМАНДИ ==================
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
        text = f"🟢 {now_kyiv_str()} | Київ | Тривоги немає\n\nДані: NEPTUN / tryvoha.online"
    await update.message.reply_text(text, disable_notification=True, reply_markup=alert_keyboard())


async def cmd_tsili(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = await asyncio.to_thread(get_kyiv_oblast_threats)
    if not result:
        await update.message.reply_text("Не вдалося отримати дані з NEPTUN", disable_notification=True)
        return
    text = result["summary"]
    if result.get("details"):
        text += "\n" + "\n".join(result["details"])
    text += "\n\n📡 Дані: NEPTUN (neptun.in.ua)"
    await update.message.reply_text(text, disable_notification=True)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Створює або оновлює закріплений живий пост про ситуацію."""
    alert = await asyncio.to_thread(get_kyiv_alert)
    if alert is None:
        await update.message.reply_text("Не вдалося перевірити тривогу", disable_notification=True)
        return

    msg_id = state.get("alert_message_id")
    started_ts = state.get("alert_started_ts")

    if alert["active"]:
        threats = await asyncio.to_thread(get_nearby_threats)
        text = format_alert_message(alert, threats, started_ts, "status")
    else:
        text = (
            f"🟢 {now_kyiv_str()} | Київ | Тривоги немає\n\n"
            "Живий статус оновлюється автоматично.\n"
            "Дані: NEPTUN / tryvoha.online"
        )

    new_id, created = await edit_or_resend(context.application, msg_id, text, alert_keyboard())
    state["alert_message_id"] = new_id
    if alert["active"] and not started_ts:
        state["alert_started_ts"] = parse_started_at(alert.get("started_at")) or time.time()
    save_state(state)

    if created:
        await pin_silently(context.application, new_id)
        await update.message.reply_text("📌 Закріплено живий пост", disable_notification=True)
    else:
        await update.message.reply_text("✅ Оновлено", disable_notification=True)


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


# ================== КНОПКА ОНОВЛЕННЯ ==================
async def on_update_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    alert = await asyncio.to_thread(get_kyiv_alert)
    if alert is None:
        await query.answer("Не вдалося перевірити", show_alert=True)
        return

    started_ts = state.get("alert_started_ts")
    if alert["active"]:
        threats = await asyncio.to_thread(get_nearby_threats)
        text = format_alert_message(alert, threats, started_ts, "refresh")
    else:
        text = f"🟢 {now_kyiv_str()} | Київ | Тривоги немає\n\nДані: NEPTUN / tryvoha.online"

    try:
        await query.edit_message_text(text=text, reply_markup=alert_keyboard())
        state["alert_last_refresh_ts"] = time.time()
        save_state(state)
    except Exception as e:
        logger.error(f"update button: {e}")
        await query.answer("Помилка оновлення", show_alert=True)


# ================== АВТОМАТИКА ==================
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


async def send_24h_stats(app: Application):
    """Щоденний підсумок за 24 години о 11:00."""
    hist = await asyncio.to_thread(compute_kyiv_history, "24h", 30)
    if not hist:
        return
    try:
        caption = format_24h_caption(hist)
        await app.bot.send_photo(
            chat_id=CHAT_ID,
            photo=InputFile(create_24h_image(hist), filename="svitlo_24h.png"),
            caption=caption[:1024],
            disable_notification=True,
        )
    except Exception as e:
        logger.error(f"send_24h_stats error: {e}")


async def send_week_stats(app: Application):
    """Тижневий підсумок о 11:30 по понеділках."""
    hist = await asyncio.to_thread(compute_kyiv_history, "7d", 60)
    if not hist:
        return
    try:
        caption = format_week_caption(hist)
        await app.bot.send_photo(
            chat_id=CHAT_ID,
            photo=InputFile(create_week_image(hist), filename="svitlo_week.png"),
            caption=caption[:1024],
            disable_notification=True,
        )
    except Exception as e:
        logger.error(f"send_week_stats error: {e}")


async def cmd_doba(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /doba — статистика за останні 24 год."""
    hist = await asyncio.to_thread(compute_kyiv_history, "24h", 30)
    if not hist:
        await update.message.reply_text("Не вдалося отримати історію 😔", disable_notification=True)
        return
    try:
        await update.message.reply_photo(
            photo=InputFile(create_24h_image(hist), filename="svitlo_24h.png"),
            caption=format_24h_caption(hist)[:1024],
            disable_notification=True,
        )
    except Exception as e:
        logger.error(f"cmd_doba error: {e}")
        await update.message.reply_text("Помилка при формуванні статистики за 24 год 😔", disable_notification=True)


async def cmd_tyzhden(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /tyzhden — статистика за останні 7 днів."""
    hist = await asyncio.to_thread(compute_kyiv_history, "7d", 60)
    if not hist:
        await update.message.reply_text("Не вдалося отримати історію 😔", disable_notification=True)
        return
    try:
        await update.message.reply_photo(
            photo=InputFile(create_week_image(hist), filename="svitlo_week.png"),
            caption=format_week_caption(hist)[:1024],
            disable_notification=True,
        )
    except Exception as e:
        logger.error(f"cmd_tyzhden error: {e}")
        await update.message.reply_text("Помилка при формуванні тижневої статистики 😔", disable_notification=True)


async def cmd_grafik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /grafik — актуальний графік відключень Yasno."""
    schedule = await asyncio.to_thread(fetch_yasno_schedule)
    if not schedule:
        await update.message.reply_text("Не вдалося отримати графік Yasno 😔", disable_notification=True)
        return
    try:
        # Спочатку today, якщо є дані
        day = "today"
        first = next(iter(schedule.values()), {})
        if not first.get("today", {}).get("slots"):
            day = "tomorrow"
        img = await asyncio.to_thread(create_yasno_image, schedule, day)
        caption = format_yasno_caption(schedule, day)
        await update.message.reply_photo(
            photo=InputFile(img, filename="yasno.png"),
            caption=caption[:1024],
            disable_notification=True,
        )
    except Exception as e:
        logger.error(f"cmd_grafik error: {e}")
        await update.message.reply_text("Помилка при формуванні графіка 😔", disable_notification=True)


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
            msg = await app.bot.send_message(
                chat_id=CHAT_ID,
                text=text,
                disable_notification=True,
                reply_markup=alert_keyboard(),
            )
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
                new_id, created = await edit_or_resend(app, msg_id, text, alert_keyboard())
                state["alert_message_id"] = new_id
                state["alert_last_refresh_ts"] = now_ts
                if created:
                    await pin_silently(app, new_id)

        elif not active and prev_active:
            text = format_alert_end(prev_level, started_ts, now_ts)
            new_id, created = await edit_or_resend(app, msg_id, text, alert_keyboard())
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


async def check_yasno_schedule(app: Application):
    """Перевіряє оновлення графіків Yasno і надсилає картинку (зі звуком)."""
    schedule = await asyncio.to_thread(fetch_yasno_schedule)
    if not schedule:
        return

    # Беремо updatedOn з першої групи
    first_group = next(iter(schedule.values()), {})
    updated = first_group.get("updatedOn")
    if not updated:
        return

    last_updated = state.get("last_yasno_updated")
    tomorrow_info = next((g.get("tomorrow", {}) for g in schedule.values() if g.get("tomorrow")), {})
    tomorrow_date = tomorrow_info.get("date", "")[:10] if tomorrow_info else None
    tomorrow_status = tomorrow_info.get("status", "")
    last_tomorrow = state.get("last_yasno_tomorrow_date")

    # Надсилаємо якщо:
    # 1) з'явився графік на завтра (статус не WaitingForSchedule)
    # 2) або оновився поточний графік і ми ще не надсилали сьогодні
    should_send = False
    day_to_send = "today"
    reason = ""

    if tomorrow_status and tomorrow_status != "WaitingForSchedule" and tomorrow_date and tomorrow_date != last_tomorrow:
        should_send = True
        day_to_send = "tomorrow"
        reason = f"графік на завтра ({tomorrow_date})"
    elif updated != last_updated:
        should_send = True
        day_to_send = "today"
        reason = "оновлення графіка"

    if not should_send:
        return

    try:
        img = await asyncio.to_thread(create_yasno_image, schedule, day_to_send)
        caption = format_yasno_caption(schedule, day_to_send)
        await app.bot.send_photo(
            chat_id=CHAT_ID,
            photo=InputFile(img, filename=f"yasno_{day_to_send}.png"),
            caption=caption[:1024],
            disable_notification=False,  # зі звуком
        )
        state["last_yasno_updated"] = updated
        if day_to_send == "tomorrow" and tomorrow_date:
            state["last_yasno_tomorrow_date"] = tomorrow_date
        save_state(state)
        logger.info(f"Sent Yasno schedule: {reason}")
    except Exception as e:
        logger.error(f"check_yasno_schedule error: {e}")


# ================== ЗАПУСК ==================
async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("svitlo", "Світло в Києві"),
        BotCommand("rayony", "Світло по районах"),
        BotCommand("doba", "Статистика за 24 години"),
        BotCommand("tyzhden", "Тижнева статистика"),
        BotCommand("grafik", "Графік відключень Yasno"),
        BotCommand("tryvoga", "Статус тривоги"),
        BotCommand("tsili", "Цілі по Києву та області"),
        BotCommand("status", "Живий закріплений пост"),
        BotCommand("dopomoga", "Довідка"),
    ])

    scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")
    scheduler.add_job(send_stats, "cron", hour="0,4,8,16,20", minute=5, args=[app, False], max_instances=1, coalesce=True)
    scheduler.add_job(send_stats, "cron", hour=12, minute=5, args=[app, True], max_instances=1, coalesce=True)
    # Нове: щоденний підсумок за 24г о 11:00
    scheduler.add_job(send_24h_stats, "cron", hour=11, minute=0, args=[app], max_instances=1, coalesce=True)
    # Тижневий підсумок по понеділках о 11:30
    scheduler.add_job(send_week_stats, "cron", day_of_week="mon", hour=11, minute=30, args=[app], max_instances=1, coalesce=True)
    scheduler.add_job(check_yasno_schedule, "interval", minutes=3, args=[app], max_instances=1, coalesce=True)
    scheduler.add_job(check_alert, "interval", seconds=18, args=[app], max_instances=1, coalesce=True)
    scheduler.start()
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
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler(["dopomoga", "start"], cmd_dopomoga))
    app.add_handler(CommandHandler("vidnovyty", cmd_vidnovyty))
    app.add_handler(CommandHandler("doba", cmd_doba))
    app.add_handler(CommandHandler("tyzhden", cmd_tyzhden))
    app.add_handler(CommandHandler("grafik", cmd_grafik))
    app.add_handler(CallbackQueryHandler(on_update_button, pattern="^update_alert$"))

    logger.info("Бот запущено")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
