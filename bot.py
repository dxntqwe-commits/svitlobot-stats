import os
import json
import logging
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path
from collections import Counter

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle

from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================== НАЛАШТУВАННЯ ==================
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SVITLOBOT_API = "https://api.svitlobot.in.ua/website/getChannelsForMap"
TRYVOHA_KYIV = "https://tryvoha.online/api/v1/alerts/kyiv"
MAPA_NEARBY = "https://mapa.ua/api/v1/nearby"

KYIV_LAT, KYIV_LON = 50.4501, 30.5234
NEARBY_RADIUS_KM = 20
SVITLO_COOLDOWN_MIN = 15

STATE_FILE = Path(os.getenv("STATE_FILE", "data/state.json"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ================== СТАН ==================
def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Не вдалося прочитати state: {e}")
    return {
        "last_alert_active": False,
        "last_alert_level": None,
        "alert_message_id": None,
        "last_svitlo_message_id": None,
        "last_svitlo_ts": 0,
    }


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"Не вдалося зберегти state: {e}")


state = load_state()


# ================== СВІТЛО (тільки Київ) ==================
def get_svitlobot_stats():
    try:
        r = requests.get(SVITLOBOT_API, timeout=15)
        r.raise_for_status()
        text = r.text

        on = off = 0
        for line in text.strip().splitlines():
            parts = line.rstrip(";").split(";&&&;")
            if len(parts) < 8:
                continue
            status = parts[1].strip()
            try:
                lat = float(parts[6])
                lng = float(parts[7])
            except (ValueError, IndexError):
                continue
            if not (50.20 <= lat <= 50.60 and 30.20 <= lng <= 30.85):
                continue
            if status == "1":
                on += 1
            elif status == "2":
                off += 1

        total = on + off
        if total == 0:
            return None
        return {
            "on": on,
            "off": off,
            "total": total,
            "on_pct": round(on / total * 100, 1),
            "off_pct": round(off / total * 100, 1),
        }
    except Exception as e:
        logger.error(f"Svitlobot error: {e}")
        return None


def create_stats_image(stats):
    fig, ax = plt.subplots(figsize=(8, 8), facecolor="#1a1a2e")
    ax.set_facecolor("#1a1a2e")

    sizes = [stats["on"], stats["off"]]
    colors = ["#00c853", "#ff1744"]
    explode = (0.02, 0.02)

    wedges, _ = ax.pie(
        sizes,
        colors=colors,
        startangle=90,
        explode=explode,
        wedgeprops=dict(width=0.35, edgecolor="#1a1a2e", linewidth=3),
    )

    centre_circle = Circle((0, 0), 0.55, fc="#1a1a2e")
    ax.add_patch(centre_circle)

    ax.text(
        0, 0.12, f"{stats['on_pct']}%",
        ha="center", va="center", fontsize=42, fontweight="bold", color="#00c853",
    )
    ax.text(
        0, -0.18, "зі світлом",
        ha="center", va="center", fontsize=16, color="#e0e0e0",
    )
    ax.set_title(
        "Стан світла в Києві", fontsize=20, color="white", pad=20, fontweight="bold"
    )

    legend_labels = [
        f"Світло є  {stats['on_pct']}%",
        f"Без світла  {stats['off_pct']}%",
    ]
    ax.legend(
        wedges, legend_labels,
        loc="lower center", bbox_to_anchor=(0.5, -0.08),
        fontsize=13, frameon=False, labelcolor="white",
    )

    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(
        buf, format="png", dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor()
    )
    buf.seek(0)
    plt.close()
    return buf


def format_svitlo_caption(stats) -> str:
    return (
        f"💡 Світло є: {stats['on_pct']}% ({stats['on']} з {stats['total']})\n"
        f"🌑 Без світла: {stats['off_pct']}% ({stats['off']} з {stats['total']})\n\n"
        f"Київ · svitlobot.in.ua"
    )


# ================== ТРИВОГА + ЦІЛІ ==================
KIND_UA = {
    "drone_piston": "Шахед / БпЛА",
    "drone_jet": "Реактивний БпЛА",
    "drone_fpv": "FPV",
    "missile_cruise": "Крилата ракета",
    "missile_ballistic": "Балістика",
    "missile": "Ракета",
    "kab": "КАБ",
    "aircraft": "Авіація",
    "recon": "Розвідка",
    "decoy": "Приманка",
}


def get_kyiv_alert():
    try:
        r = requests.get(TRYVOHA_KYIV, timeout=10)
        r.raise_for_status()
        data = r.json()
        return {
            "active": bool(data.get("active")),
            "level": data.get("level"),
            "started_at": data.get("started_at"),
            "name": data.get("name_uk", "м. Київ"),
        }
    except Exception as e:
        logger.error(f"Tryvoha error: {e}")
        return None


def get_nearby_threats() -> str:
    try:
        r = requests.get(
            MAPA_NEARBY,
            params={"lat": KYIV_LAT, "lon": KYIV_LON, "radius_km": NEARBY_RADIUS_KM},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        threats = data.get("threats") or data.get("objects") or []
        if not threats:
            return "Цілей у радіусі 20 км не зафіксовано"

        counter = Counter()
        for t in threats:
            if t.get("status") and t.get("status") != "active":
                continue
            kind = t.get("kind") or t.get("subkind") or "unknown"
            amount = int(t.get("amount") or 1)
            counter[kind] += amount

        if not counter:
            return "Цілей у радіусі 20 км не зафіксовано"

        parts = []
        for kind, cnt in counter.most_common():
            label = KIND_UA.get(kind, kind)
            parts.append(f"{cnt}× {label}")
        return "Біля Києва (~20 км): " + ", ".join(parts)
    except Exception as e:
        logger.error(f"MAPA nearby error: {e}")
        return ""


def level_text(level) -> str:
    if level == "red":
        return "червоний"
    if level == "yellow":
        return "жовтий"
    return "невідомий"


def level_emoji(level) -> str:
    if level == "red":
        return "🔴"
    if level == "yellow":
        return "🟡"
    return "🟠"


def now_kyiv_str() -> str:
    return datetime.now(timezone(timedelta(hours=2))).strftime("%H:%M")


def format_alert_start(alert: dict, threats_line: str) -> str:
    lvl = alert.get("level")
    text = (
        f"{level_emoji(lvl)} {now_kyiv_str()} | Київ | Повітряна тривога\n"
        f"Рівень: {level_text(lvl)}"
    )
    if threats_line:
        text += f"\n\n{threats_line}"
    text += "\n\nДжерело: tryvoha.online / mapa.ua"
    return text


def format_alert_end(prev_level) -> str:
    return (
        f"🟢 {now_kyiv_str()} | Київ | Відбій повітряної тривоги\n"
        f"Був рівень: {level_text(prev_level)}\n\n"
        f"Джерело: tryvoha.online"
    )


def format_alert_update(alert: dict, threats_line: str) -> str:
    lvl = alert.get("level")
    text = (
        f"{level_emoji(lvl)} {now_kyiv_str()} | Київ | Зміна рівня тривоги\n"
        f"Рівень: {level_text(lvl)}"
    )
    if threats_line:
        text += f"\n\n{threats_line}"
    text += "\n\nДжерело: tryvoha.online / mapa.ua"
    return text


# ================== КОМАНДИ ==================
async def cmd_svitlo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global state
    chat_id = update.effective_chat.id

    now_ts = datetime.now(timezone.utc).timestamp()
    last_ts = state.get("last_svitlo_ts") or 0
    last_mid = state.get("last_svitlo_message_id")

    if last_mid and (now_ts - last_ts) < SVITLO_COOLDOWN_MIN * 60:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=last_mid)
        except Exception as e:
            logger.info(f"Не вдалося видалити старе /svitlo: {e}")

    stats = get_svitlobot_stats()
    if not stats:
        await update.message.reply_text("Не вдалося отримати дані зі Svitlobot 😔")
        return

    img = create_stats_image(stats)
    caption = format_svitlo_caption(stats)
    msg = await update.message.reply_photo(
        photo=InputFile(img, filename="svitlo.png"),
        caption=caption,
    )

    state["last_svitlo_message_id"] = msg.message_id
    state["last_svitlo_ts"] = now_ts
    save_state(state)


async def cmd_trivoga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    alert = get_kyiv_alert()
    if alert is None:
        await update.message.reply_text("Не вдалося перевірити статус тривоги")
        return

    if alert["active"]:
        threats = get_nearby_threats()
        text = format_alert_start(alert, threats)
    else:
        text = (
            f"🟢 {now_kyiv_str()} | Київ | Тривоги немає\n\n"
            f"Джерело: tryvoha.online"
        )
    await update.message.reply_text(text)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команди:\n"
        "/svitlo — статистика світла в Києві\n"
        "/trivoga — статус повітряної тривоги + цілі поруч\n"
        "/help — ця підказка\n\n"
        "Автоматично:\n"
        "• статистика світла о 00:05, 04:05, 08:05, 12:05, 16:05, 20:05\n"
        "• сповіщення про початок/зміну/відбій тривоги"
    )


# ================== АВТОМАТИКА ==================
async def send_stats(app: Application):
    stats = get_svitlobot_stats()
    if not stats:
        return
    img = create_stats_image(stats)
    caption = format_svitlo_caption(stats)
    try:
        msg = await app.bot.send_photo(
            chat_id=CHAT_ID,
            photo=InputFile(img, filename="svitlo.png"),
            caption=caption,
        )
        state["last_svitlo_message_id"] = msg.message_id
        state["last_svitlo_ts"] = datetime.now(timezone.utc).timestamp()
        save_state(state)
    except Exception as e:
        logger.error(f"send_stats error: {e}")


async def check_alert(app: Application):
    global state

    alert = get_kyiv_alert()
    if alert is None:
        return

    current_active = alert["active"]
    current_level = alert.get("level")
    prev_active = state.get("last_alert_active", False)
    prev_level = state.get("last_alert_level")
    msg_id = state.get("alert_message_id")

    try:
        if current_active and not prev_active:
            threats = get_nearby_threats()
            text = format_alert_start(alert, threats)
            msg = await app.bot.send_message(chat_id=CHAT_ID, text=text)
            state["alert_message_id"] = msg.message_id

        elif (
            current_active
            and prev_active
            and current_level
            and current_level != prev_level
        ):
            threats = get_nearby_threats()
            text = format_alert_update(alert, threats)
            if msg_id:
                try:
                    await app.bot.edit_message_text(
                        chat_id=CHAT_ID, message_id=msg_id, text=text
                    )
                except Exception:
                    msg = await app.bot.send_message(chat_id=CHAT_ID, text=text)
                    state["alert_message_id"] = msg.message_id
            else:
                msg = await app.bot.send_message(chat_id=CHAT_ID, text=text)
                state["alert_message_id"] = msg.message_id

        elif not current_active and prev_active:
            text = format_alert_end(prev_level)
            if msg_id:
                try:
                    await app.bot.delete_message(chat_id=CHAT_ID, message_id=msg_id)
                except Exception as e:
                    logger.info(f"Не вдалося видалити повідомлення тривоги: {e}")
            await app.bot.send_message(chat_id=CHAT_ID, text=text)
            state["alert_message_id"] = None

    except Exception as e:
        logger.error(f"check_alert error: {e}")

    state["last_alert_active"] = current_active
    state["last_alert_level"] = current_level
    save_state(state)


# ================== ЗАПУСК ==================
def main():
    if not TOKEN or not CHAT_ID:
        logger.error("TELEGRAM_TOKEN або TELEGRAM_CHAT_ID не задані!")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("svitlo", cmd_svitlo))
    app.add_handler(CommandHandler("trivoga", cmd_trivoga))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))

    scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")
    scheduler.add_job(send_stats, "cron", hour="0,4,8,12,16,20", minute=5, args=[app])
    scheduler.add_job(check_alert, "interval", seconds=40, args=[app])
    scheduler.start()

    logger.info("Бот запущено")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
