import os
import io
import requests
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def get_stats():
    url = "https://api.svitlobot.in.ua/website/getChannelsForMap"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    
    on = off = tech = 0
    for line in r.text.strip().split("\n"):
        parts = line.split(";&&&;")
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
        "tech_pct": round(tech / total * 100, 1),
    }

def create_image(stats):
    fig, ax = plt.subplots(figsize=(6, 6), facecolor="#1a1a1a")
    ax.set_facecolor("#1a1a1a")
    
    sizes = [stats["on"], stats["off"], stats["tech"]]
    colors = ["#22c55e", "#ef4444", "#9ca3af"]
    explode = (0.02, 0.02, 0.02)
    
    ax.pie(
        sizes,
        colors=colors,
        startangle=90,
        explode=explode,
        wedgeprops=dict(width=0.35, edgecolor="#1a1a1a", linewidth=3)
    )
    
    centre = Circle((0, 0), 0.55, fc="#1a1a1a")
    ax.add_patch(centre)
    
    ax.text(0, 0.12, f"{stats['on_pct']}%", 
            ha="center", va="center", fontsize=42, fontweight="bold", color="#22c55e")
    ax.text(0, -0.18, "світло є", 
            ha="center", va="center", fontsize=16, color="#e5e7eb")
    
    ax.set_aspect("equal")
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="#1a1a1a")
    buf.seek(0)
    plt.close()
    return buf

async def send_stats(context: ContextTypes.DEFAULT_TYPE, chat_id=None):
    stats = get_stats()
    if not stats:
        return
    
    image = create_image(stats)
    
    caption = (
        f"🟢 <b>Світло є:</b> {stats['on_pct']}%\n"
        f"🔴 <b>Світла немає:</b> {stats['off_pct']}%\n"
        f"⚪️ <b>Техперерва:</b> {stats['tech_pct']}%"
    )
    
    target = chat_id or CHAT_ID
    await context.bot.send_photo(
        chat_id=target,
        photo=image,
        caption=caption,
        parse_mode="HTML"
    )

async def svitlo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Завантажую...")
    await send_stats(context, chat_id=update.effective_chat.id)

async def hourly_job(context: ContextTypes.DEFAULT_TYPE):
    await send_stats(context)

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("svitlo", svitlo_command))
    
    scheduler = AsyncIOScheduler(timezone=pytz.timezone("Europe/Kyiv"))
    scheduler.add_job(hourly_job, "cron", minute=0, args=[app])
    scheduler.start()
    
    print("Бот запущено")
    app.run_polling()

if __name__ == "__main__":
    main()
