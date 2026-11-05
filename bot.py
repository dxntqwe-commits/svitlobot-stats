import os
import logging
import requests
from datetime import datetime, timezone, timedelta
from io import BytesIO

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle

from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================== НАЛАШТУВАННЯ ==================
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SVITLOBOT_API = "https://api.svitlobot.in.ua/website/getChannelsForMap"
TRYVOHA_API = "https://tryvoha.online/api/v1/alerts"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Стан тривоги
last_alert_state = None
last_alert_message_id = None   # щоб видаляти попереднє повідомлення

# ================== СТАТИСТИКА СВІТЛА ==================
def get_svitlobot_stats():
    try:
        r = requests.get(SVITLOBOT_API, timeout=15)
        r.raise_for_status()
        text = r.text

        on = off = tech = 0
        for line in text.split(";&&&;"):
            parts = line.strip().split(";")
            if len(parts) < 2:
                continue
            status = parts[1]
            if status == "1":
                on += 1
            elif status == "2":
                off += 1
            elif status == "3":
                tech += 1

        total = on + off + tech
        if total == 0:
            return None

        return {
            "on": on,
            "off": off,
            "tech": tech,
            "total": total,
            "on_pct": round(on / total * 100, 1),
            "off_pct": round(off / total * 100, 1),
            "tech_pct": round(tech / total * 100, 1)
        }
    except Exception as e:
        logger.error(f"Svitlobot error: {e}")
        return None


def create_stats_image(stats):
    fig, ax = plt.subplots(figsize=(8, 8), facecolor="#1a1a2e")
    ax.set_facecolor("#1a1a2e")

    sizes = [stats["on"], stats["off"], stats["tech"]]
    colors = ["#00c853", "#ff1744", "#9e9e9e"]
    explode = (0.02, 0.02, 0.02)

    wedges, _ = ax.pie(
        sizes,
        colors=colors,
        startangle=90,
        explode=explode,
        wedgeprops=dict(width=0.35, edgecolor="#1a1a2e", linewidth=3)
    )

    centre_circle = Circle((0, 0), 0.55, fc="#1a1a2e")
    ax.add_patch(centre_circle)

    ax.text(0, 0.12, f"{stats['on_pct']}%", 
            ha="center", va="center", fontsize=42, fontweight="bold", color="#00c853")
    ax.text(0, -0.18, "зі світлом", 
            ha="center", va="center", fontsize=16, color="#e0e0e0")

    ax.set_title("Стан світла в Києві", fontsize=20, color="white", pad=20, fontweight="bold")

    legend_labels = [
        f"Світло є  {stats['on_pct']}%",
        f"Без світла  {stats['off_pct']}%",
        f"Технічна  {stats['tech_pct']}%"
    ]
    ax.legend(wedges, legend_labels, loc="lower center", 
              bbox_to_anchor=(0.5, -0.08), fontsize=13,
              frameon=False, labelcolor="white")

    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close()
    return buf


# ================== ТРИВОГА ==================
def get_kyiv_alert():
    try:
        r = requests.get(TRYVOHA_API, timeout=10)
        r.raise_for_status()
        data = r.json()

        for alert in data.get("alerts", []):
            if alert.get("slug") == "kyiv":
                return {
                    "active": True,
                    "level": alert.get("level", "unknown"),
                    "started_at": alert.get("started_at"),
                    "name": alert.get("name_uk", "м. Київ")
                }
        return {"active": False, "level": None, "started_at": None, "name": "м. Київ"}
    except Exception as e:
        logger.error(f"Tryvoha error: {e}")
        return None


def format_alert_message(alert, is_start=True):
    now = datetime.now(timezone(timedelta(hours=2))).strftime("%H:%M")
    
    if is_start:
        level = alert.get("level", "unknown")
        if level == "red":
            emoji = "🔴"
            level_text = "червоний рівень"
        elif level == "yellow":
            emoji = "🟡"
            level_text = "жовтий рівень"
        else:
            emoji = "🟠"
            level_text = "тривога"
        
        return (
            f"{emoji} {now} | Київ | Повітряна тривога\n"
            f"Рівень: {level_text}\n\n"
            f"Джерело: tryvoha.online"
        )
    else:
        return (
            f"🟢 {now} | Київ | Відбій повітряної тривоги\n\n"
            f"Джерело: tryvoha.online"
        )


# ================== КОМАНДИ ==================
async def cmd_svitlo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Спочатку повідомлення "Завантажую..."
    loading_msg = await update.message.reply_text("⏳ Завантажую статистику...")

    stats = get_svitlobot_stats()
    
    if not stats:
        await loading_msg.edit_text("Не вдалося отримати дані зі Svitlobot 😔")
        return

    img = create_stats_image(stats)
    caption = (
        f"💡 Світло є: {stats['on_pct']}%\n"
        f"🌑 Без світла: {stats['off_pct']}%\n\n"
        f"Джерело: svitlobot.in.ua"
    )

    # Видаляємо "Завантажую..." і кидаємо фото
    try:
        await loading_msg.delete()
    except:
        pass

    await update.message.reply_photo(
        photo=InputFile(img, filename="svitlo.png"),
        caption=caption
    )


async def cmd_trivoga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    alert = get_kyiv_alert()
    if alert is None:
        await update.message.reply_text("Не вдалося перевірити статус тривоги")
        return

    now = datetime.now(timezone(timedelta(hours=2))).strftime("%H:%M")
    
    if alert["active"]:
        level = alert.get("level", "unknown")
        if level == "red":
            emoji = "🔴"
            level_text = "червоний"
        elif level == "yellow":
            emoji = "🟡"
            level_text = "жовтий"
        else:
            emoji = "🟠"
            level_text = "невідомий"
        
        text = (
            f"{emoji} {now} | Київ | Тривога активна\n"
            f"Рівень: {level_text}\n\n"
            f"Джерело: tryvoha.online"
        )
    else:
        text = (
            f"🟢 {now} | Київ | Тривоги немає\n\n"
            f"Джерело: tryvoha.online"
        )
    
    await update.message.reply_text(text)


# ================== АВТОМАТИЧНІ ЗАВДАННЯ ==================
async def send_stats(context: ContextTypes.DEFAULT_TYPE):
    stats = get_svitlobot_stats()
    if not stats:
        return

    img = create_stats_image(stats)
    caption = (
        f"💡 Світло є: {stats['on_pct']}%\n"
        f"🌑 Без світла: {stats['off_pct']}%\n\n"
        f"Джерело: svitlobot.in.ua"
    )
    await context.bot.send_photo(
        chat_id=CHAT_ID,
        photo=InputFile(img, filename="svitlo.png"),
        caption=caption
    )


async def check_alert(context: ContextTypes.DEFAULT_TYPE):
    global last_alert_state, last_alert_message_id
    
    alert = get_kyiv_alert()
    if alert is None:
        return

    current_state = alert["active"]

    # Тривога тільки що почалася
    if current_state and not last_alert_state:
        # Видаляємо попереднє повідомлення (якщо було)
        if last_alert_message_id:
            try:
                await context.bot.delete_message(chat_id=CHAT_ID, message_id=last_alert_message_id)
            except:
                pass

        msg = format_alert_message(alert, is_start=True)
        sent = await context.bot.send_message(chat_id=CHAT_ID, text=msg)
        last_alert_message_id = sent.message_id

    # Відбій
    elif not current_state and last_alert_state:
        if last_alert_message_id:
            try:
                await context.bot.delete_message(chat_id=CHAT_ID, message_id=last_alert_message_id)
            except:
                pass

        msg = format_alert_message(alert, is_start=False)
        sent = await context.bot.send_message(chat_id=CHAT_ID, text=msg)
        last_alert_message_id = sent.message_id

    last_alert_state = current_state


# ================== ЗАПУСК ==================
def main():
    if not TOKEN or not CHAT_ID:
        logger.error("TELEGRAM_TOKEN або TELEGRAM_CHAT_ID не задані!")
        return

    async def post_init(application):
        # Виконується всередині запущеного event loop
        scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")
        # Статистика кожні 4 години
        scheduler.add_job(send_stats, "cron", hour="0,4,8,12,16,20", minute=5, args=[application])
        # Перевірка тривоги кожні 40 секунд
        scheduler.add_job(check_alert, "interval", seconds=40, args=[application])
        scheduler.start()
        application.bot_data["scheduler"] = scheduler

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("svitlo", cmd_svitlo))
    app.add_handler(CommandHandler("trivoga", cmd_trivoga))

    logger.info("Бот запущено")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
