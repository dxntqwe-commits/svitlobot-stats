import asyncio
import json
import logging
import os
from datetime import datetime, time
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
import requests

from telegram import Update, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    Defaults,
)

# -------------------- НАЛАШТУВАННЯ --------------------
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SVITLOBOT_API = "https://api.svitlobot.in.ua/website/getChannelsForMap"
TRYVOHA_API = "https://tryvoha.online/api/v1/alerts"
KYIV_TZ = ZoneInfo("Europe/Kyiv")

# Для постійного збереження стану підключи Railway Volume до /app/data.
# Без Volume бот усе одно працюватиме, але файл може зникнути після перезапуску.
STATE_FILE = Path(os.getenv("STATE_FILE", "data/state.json"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger("svitlobot")

last_alert_state = None
last_alert_message_id = None


# -------------------- ЗБЕРЕЖЕННЯ СТАНУ --------------------
def load_state():
    global last_alert_state, last_alert_message_id
    try:
        if not STATE_FILE.exists():
            return
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        state = data.get("alert_state")
        if isinstance(state, bool):
            last_alert_state = state
        message_id = data.get("last_alert_message_id")
        if isinstance(message_id, int):
            last_alert_message_id = message_id
        logger.info("Стан бота завантажено")
    except Exception:
        logger.exception("Не вдалося завантажити файл стану")


def save_state():
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = STATE_FILE.with_suffix(".tmp")
        temp_file.write_text(
            json.dumps(
                {
                    "alert_state": last_alert_state,
                    "last_alert_message_id": last_alert_message_id,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temp_file.replace(STATE_FILE)
    except Exception:
        logger.exception("Не вдалося зберегти файл стану")


# -------------------- ОТРИМАННЯ ДАНИХ --------------------
def get_svitlobot_stats():
    response = requests.get(SVITLOBOT_API, timeout=20)
    response.raise_for_status()

    # API повертає плоский список полів. Один запис містить 11 полів:
    # [службове ID, службове поле, службове поле, назва, канал,
    #  кількість, широта, довгота, СТАТУС, група, службове поле].
    fields = [item.strip() for item in response.text.strip().split(";&&&;")]
    record_size = 11
    on = off = tech = 0
    kyiv_records = 0
    malformed_records = 0

    for start in range(0, len(fields), record_size):
        record = fields[start:start + record_size]
        if len(record) < record_size:
            malformed_records += 1
            continue

        # Назва об'єкта розташована в полі 3. У статистику Києва
        # включаємо тільки об'єкти, назва яких починається з "Київ".
        object_name = record[3]
        if not object_name.casefold().startswith("київ"):
            continue

        status = record[8]
        if status not in {"0", "1", "2", "3"}:
            continue

        kyiv_records += 1
        if status == "1":
            on += 1
        elif status == "2":
            off += 1
        elif status == "3":
            tech += 1
        # Статус 0 не входить до трьох категорій, які показує графік.

    total = on + off + tech
    logger.info(
        "Svitlobot API: записів Києва=%s, враховано=%s, є=%s, немає=%s, технічний=%s, некоректних=%s",
        kyiv_records, total, on, off, tech, malformed_records
    )

    if total == 0:
        logger.warning(
            "Svitlobot повернув відповідь, але не знайдено придатних записів Києва. "
            "Поля у відповіді: %s",
            len(fields),
        )
        return None

    return {
        "on": on,
        "off": off,
        "tech": tech,
        "total": total,
        "on_pct": round(on / total * 100, 1),
        "off_pct": round(off / total * 100, 1),
        "tech_pct": round(tech / total * 100, 1),
    }


def get_kyiv_alert():
    response = requests.get(TRYVOHA_API, timeout=10)
    response.raise_for_status()
    data = response.json()

    for alert in data.get("alerts", []):
        if alert.get("slug") == "kyiv":
            return {
                "active": True,
                "level": alert.get("level", "unknown"),
                "started_at": alert.get("started_at"),
                "name": alert.get("name_uk", "м. Київ"),
            }

    return {
        "active": False,
        "level": None,
        "started_at": None,
        "name": "м. Київ",
    }


def create_stats_image(stats):
    fig, ax = plt.subplots(figsize=(8, 8), facecolor="#1a1a2e")
    ax.set_facecolor("#1a1a2e")

    sizes = [stats["on"], stats["off"], stats["tech"]]
    colors = ["#00c853", "#ff1744", "#9e9e9e"]
    wedges, _ = ax.pie(
        sizes,
        colors=colors,
        startangle=90,
        explode=(0.02, 0.02, 0.02),
        wedgeprops={"width": 0.35, "edgecolor": "#1a1a2e", "linewidth": 3},
    )

    ax.add_patch(Circle((0, 0), 0.55, fc="#1a1a2e"))
    ax.text(0, 0.12, f"{stats['on_pct']}%", ha="center", va="center",
            fontsize=42, fontweight="bold", color="#00c853")
    ax.text(0, -0.18, "зі світлом", ha="center", va="center",
            fontsize=16, color="#e0e0e0")
    ax.set_title("Стан світла в Києві", fontsize=20, color="white",
                 pad=20, fontweight="bold")

    labels = [
        f"Світло є  {stats['on_pct']}%",
        f"Без світла  {stats['off_pct']}%",
        f"Технічна  {stats['tech_pct']}%",
    ]
    ax.legend(wedges, labels, loc="lower center", bbox_to_anchor=(0.5, -0.08),
              fontsize=13, frameon=False, labelcolor="white")

    plt.tight_layout()
    image_buffer = BytesIO()
    fig.savefig(image_buffer, format="png", dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    image_buffer.seek(0)
    plt.close(fig)
    return image_buffer


def stats_caption(stats):
    return (
        f"💡 Світло є: {stats['on_pct']}%\n"
        f"🌑 Без світла: {stats['off_pct']}%\n\n"
        "Джерело: svitlobot.in.ua"
    )


def format_alert_message(alert, is_start=True):
    now = datetime.now(KYIV_TZ).strftime("%H:%M")
    if not is_start:
        return f"🟢 {now} | Київ | Відбій повітряної тривоги\n\nДжерело: tryvoha.online"

    level = alert.get("level", "unknown")
    if level == "red":
        emoji, level_text = "🔴", "червоний рівень"
    elif level == "yellow":
        emoji, level_text = "🟡", "жовтий рівень"
    else:
        emoji, level_text = "🟠", "тривога"

    return (
        f"{emoji} {now} | Київ | Повітряна тривога\n"
        f"Рівень: {level_text}\n\n"
        "Джерело: tryvoha.online"
    )


# -------------------- КОМАНДИ БОТА --------------------
async def cmd_svitlo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loading = await update.effective_message.reply_text("⏳ Завантажую статистику...")
    try:
        stats = await asyncio.to_thread(get_svitlobot_stats)
        if not stats:
            await loading.edit_text("Не вдалося отримати дані зі Svitlobot 😔")
            return
        image = await asyncio.to_thread(create_stats_image, stats)
        await loading.delete()
        await update.effective_message.reply_photo(
            photo=InputFile(image, filename="svitlo.png"),
            caption=stats_caption(stats),
        )
    except Exception:
        logger.exception("Помилка команди /svitlo")
        try:
            await loading.edit_text("Не вдалося отримати статистику. Спробуй пізніше.")
        except Exception:
            pass


async def cmd_trivoga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        alert = await asyncio.to_thread(get_kyiv_alert)
        now = datetime.now(KYIV_TZ).strftime("%H:%M")
        if alert["active"]:
            level = alert.get("level", "unknown")
            if level == "red":
                emoji, level_text = "🔴", "червоний"
            elif level == "yellow":
                emoji, level_text = "🟡", "жовтий"
            else:
                emoji, level_text = "🟠", "невідомий"
            message = (
                f"{emoji} {now} | Київ | Тривога активна\n"
                f"Рівень: {level_text}\n\nДжерело: tryvoha.online"
            )
        else:
            message = f"🟢 {now} | Київ | Тривоги немає\n\nДжерело: tryvoha.online"
        await update.effective_message.reply_text(message)
    except Exception:
        logger.exception("Помилка команди /trivoga")
        await update.effective_message.reply_text("Не вдалося перевірити статус тривоги. Спробуй пізніше.")


# -------------------- АВТОМАТИЧНІ ЗАВДАННЯ --------------------
async def send_stats(context: ContextTypes.DEFAULT_TYPE):
    try:
        stats = await asyncio.to_thread(get_svitlobot_stats)
        if not stats:
            logger.warning("Svitlobot не повернув статистику")
            return
        image = await asyncio.to_thread(create_stats_image, stats)
        await context.bot.send_photo(
            chat_id=CHAT_ID,
            photo=InputFile(image, filename="svitlo.png"),
            caption=stats_caption(stats),
        )
        logger.info("Автоматичну статистику надіслано")
    except Exception:
        logger.exception("Не вдалося автоматично надіслати статистику")


async def check_alert(context: ContextTypes.DEFAULT_TYPE):
    global last_alert_state, last_alert_message_id
    try:
        alert = await asyncio.to_thread(get_kyiv_alert)
        current_state = alert["active"]

        # Перший запуск без збереженого стану: синхронізуємося з API,
        # але не надсилаємо фальшиве сповіщення про вже активну тривогу.
        if last_alert_state is None:
            last_alert_state = current_state
            save_state()
            logger.info("Початковий стан тривоги: %s", current_state)
            return

        if current_state == last_alert_state:
            return

        message = format_alert_message(alert, is_start=current_state)
        sent = await context.bot.send_message(chat_id=CHAT_ID, text=message)

        old_message_id = last_alert_message_id
        last_alert_message_id = sent.message_id
        last_alert_state = current_state
        save_state()

        if old_message_id:
            try:
                await context.bot.delete_message(chat_id=CHAT_ID, message_id=old_message_id)
            except Exception:
                logger.warning("Не вдалося видалити попереднє повідомлення %s", old_message_id)

        logger.info("Статус тривоги змінився: %s", current_state)
    except Exception:
        logger.exception("Помилка перевірки повітряної тривоги")


async def initialize_alert_state(application: Application):
    global last_alert_state
    load_state()
    if last_alert_state is not None:
        return
    try:
        alert = await asyncio.to_thread(get_kyiv_alert)
        last_alert_state = alert["active"]
        save_state()
        logger.info("Початковий статус тривоги отримано")
    except Exception:
        logger.exception("Не вдалося отримати початковий статус тривоги; перевірка повториться за розкладом")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Необроблена помилка Telegram-бота", exc_info=context.error)


async def post_init(application: Application):
    await initialize_alert_state(application)

    # Перевіряємо тривогу одразу, потім кожні 40 секунд.
    application.job_queue.run_repeating(check_alert, interval=40, first=5, name="check_alert")

    # Статистика щодня о 00:05, 04:05, 08:05, 12:05, 16:05 та 20:05 за Києвом.
    for hour in (0, 4, 8, 12, 16, 20):
        application.job_queue.run_daily(
            send_stats,
            time=time(hour=hour, minute=5, tzinfo=KYIV_TZ),
            name=f"send_stats_{hour:02d}",
        )

    logger.info("Планувальник запущено: тривога кожні 40 секунд, статистика 6 разів на добу")


def main():
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("Не задані змінні TELEGRAM_TOKEN або TELEGRAM_CHAT_ID у Railway Variables")

    load_state()
    application = (
        Application.builder()
        .token(TOKEN)
        .defaults(Defaults(tzinfo=KYIV_TZ))
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("svitlo", cmd_svitlo))
    application.add_handler(CommandHandler("trivoga", cmd_trivoga))
    application.add_error_handler(error_handler)

    logger.info("Запуск Telegram-бота")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
