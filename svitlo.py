"""Світло: дані Світлобота, райони, картинка, підписи, порівняння з учора."""
import logging
import re
import time
from io import BytesIO

import requests
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle

from config import (
    LOW_SAMPLE_POINTS,
    MIN_DISTRICT_POINTS,
    SVITLOBOT_API,
    TOP_DISTRICTS,
)
from districts import EXACT_DISTRICTS, classify_district, rank_districts
from storage import get_daily
from timeutils import kyiv_date_str, ts_kyiv_str

logger = logging.getLogger(__name__)

BG = "#1a1a2e"


# ================== ДАНІ ==================
def get_svitlobot_stats():
    try:
        r = requests.get(SVITLOBOT_API, timeout=15)
        r.raise_for_status()

        on = off = 0
        districts: dict = {}

        for line in r.text.strip().splitlines():
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
            if status not in ("1", "2"):
                continue

            key = "on" if status == "1" else "off"
            if key == "on":
                on += 1
            else:
                off += 1

            district = classify_district(lat, lng)
            if district:
                districts.setdefault(district, {"on": 0, "off": 0})[key] += 1

        total = on + off
        if total == 0:
            return None

        return {
            "on": on,
            "off": off,
            "total": total,
            "on_pct": round(on / total * 100, 1),
            "off_pct": round(off / total * 100, 1),
            "districts": districts,
            "ts": time.time(),  # час запиту = "станом на"
        }
    except Exception as e:
        logger.error(f"Svitlobot error: {e}")
        return None


# ================== КАРТИНКА ==================
def _severity_color(pct: float) -> str:
    if pct >= 60:
        return "#ff1744"
    if pct >= 40:
        return "#ff6d00"
    if pct >= 20:
        return "#ffab00"
    return "#7cb342"


def _draw_donut(ax, stats):
    ax.set_facecolor(BG)
    wedges, _ = ax.pie(
        [stats["on"], stats["off"]],
        colors=["#00c853", "#ff1744"],
        startangle=90,
        explode=(0.02, 0.02),
        wedgeprops=dict(width=0.35, edgecolor=BG, linewidth=3),
    )
    ax.add_patch(Circle((0, 0), 0.55, fc=BG))
    ax.text(0, 0.12, f"{stats['on_pct']}%", ha="center", va="center",
            fontsize=40, fontweight="bold", color="#00c853")
    ax.text(0, -0.18, "зі світлом", ha="center", va="center",
            fontsize=15, color="#e0e0e0")
    ax.legend(
        wedges,
        [f"Світло є {stats['on_pct']}%", f"Без світла {stats['off_pct']}%"],
        loc="lower center", bbox_to_anchor=(0.5, -0.08),
        fontsize=13, frameon=False, labelcolor="white",
    )


def _draw_district_bars(ax, rows):
    ax.set_facecolor(BG)
    n = len(rows)
    ypos = list(range(n - 1, -1, -1))  # перший район — зверху
    for y, r in zip(ypos, rows):
        ax.barh(y, 100, height=0.62, color="#26263f", zorder=1)
        ax.barh(y, max(r["off_pct"], 0.8), height=0.62,
                color=_severity_color(r["off_pct"]), zorder=2)
        star = "*" if r["total"] < LOW_SAMPLE_POINTS else ""
        ax.text(98, y, f"{r['off_pct']:.0f}%  ({r['off']} з {r['total']}){star}",
                ha="right", va="center", color="white",
                fontsize=12, fontweight="bold", zorder=3)
    ax.set_yticks(ypos)
    ax.set_yticklabels([r["name"] for r in rows], color="white", fontsize=14)
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.6, n - 0.4)
    ax.set_xticks([])
    ax.tick_params(left=False)
    for s in ax.spines.values():
        s.set_visible(False)


def _title(fig, x, text):
    fig.text(x, 0.96, text, ha="center", va="bottom", color="white",
             fontsize=20, fontweight="bold")


def create_stats_image(stats):
    reliable, _ = rank_districts(stats)
    top = reliable[:TOP_DISTRICTS]
    stamp = f"Станом на {ts_kyiv_str(stats['ts'])} · svitlobot.in.ua"

    if top:
        fig = plt.figure(figsize=(14, 7), facecolor=BG)
        gs = fig.add_gridspec(1, 2, width_ratios=[1, 1.4], wspace=0.32)
        ax_donut = fig.add_subplot(gs[0, 0])
        ax_bars = fig.add_subplot(gs[0, 1])
        _draw_donut(ax_donut, stats)
        _draw_district_bars(ax_bars, top)
        for ax, text in ((ax_donut, "Стан світла в Києві"),
                         (ax_bars, f"Райони без світла (топ-{len(top)})")):
            pos = ax.get_position()
            _title(fig, pos.x0 + pos.width / 2, text)
        notes = (
            f"{stamp}\n"
            "Відсоток — частка датчиків району без світла; у дужках: без світла з усіх датчиків району. "
            f"* — мало точок (<{LOW_SAMPLE_POINTS}), цифра менш надійна"
        )
        fig.text(0.5, 0.02, notes, ha="center", va="top", color="#9e9e9e", fontsize=11)
    else:
        fig, ax = plt.subplots(figsize=(8, 8), facecolor=BG)
        _draw_donut(ax, stats)
        _title(fig, 0.5, "Стан світла в Києві")
        fig.text(0.5, 0.02, stamp, ha="center", va="top", color="#9e9e9e", fontsize=11)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)
    return buf


# ================== ТЕКСТ ==================
def format_districts_block(stats: dict) -> str:
    reliable, _ = rank_districts(stats)
    if not reliable:
        return ""
    top = reliable[:TOP_DISTRICTS]
    worst = top[0]
    if worst["off_pct"] == 0:
        return f"✅ У районах (від {MIN_DISTRICT_POINTS} точок) світло є скрізь"

    worst_mark = " ⚠️ мало точок" if worst["total"] < LOW_SAMPLE_POINTS else ""
    lines = [
        f"📍 Найменше світла: {worst['name']} — {worst['off_pct']}% без світла "
        f"({worst['off']} з {worst['total']}){worst_mark}",
        "",
        f"🔻 Райони без світла (топ-{len(top)}):",
    ]
    for i, r in enumerate(top, 1):
        mark = " ⚠️ мало точок" if r["total"] < LOW_SAMPLE_POINTS else ""
        lines.append(f"{i}. {r['name']} — {r['off_pct']}% ({r['off']} з {r['total']}){mark}")
    return "\n".join(lines)


def format_daily_delta(stats: dict) -> str:
    """Порівняння з учорашнім заміром о 12:05 (береться зі state)."""
    yesterday = kyiv_date_str(stats["ts"] - 86400)
    prev = get_daily(yesterday)
    if not prev or not prev.get("total"):
        return "📊 Порівняння з учора: даних немає (бот ще не мав вчорашнього заміру о 12:05)"

    prev_pct = prev["on"] / prev["total"] * 100
    diff = stats["on"] / stats["total"] * 100 - prev_pct
    if diff > 0.05:
        return f"📊 ▲ Світла на {abs(diff):.1f} п.п. більше, ніж учора о 12:05 (було {prev_pct:.1f}%)"
    if diff < -0.05:
        return f"📊 ▼ Світла на {abs(diff):.1f} п.п. менше, ніж учора о 12:05 (було {prev_pct:.1f}%)"
    return f"📊 ▬ Без змін порівняно з учора о 12:05 ({prev_pct:.1f}%)"


def format_svitlo_caption(stats, with_daily: bool = False) -> str:
    head = (
        f"💡 Світло є: {stats['on_pct']}% ({stats['on']} з {stats['total']})\n"
        f"🌑 Без світла: {stats['off_pct']}% ({stats['off']} з {stats['total']})"
    )
    footer = f"🕐 Станом на {ts_kyiv_str(stats['ts'])} · Київ · svitlobot.in.ua"
    block = format_districts_block(stats)
    note = (
        "ℹ️ У районах різна кількість датчиків (дивіться «з N»): рейтинг це враховує, "
        "тож район з кількома точками не потрапить нагору випадково. "
        f"Райони з менше ніж {MIN_DISTRICT_POINTS} точок не враховані."
    )

    sections = [head]
    if with_daily:
        sections.append(format_daily_delta(stats))
    if block:
        sections.append(block)

    full = "\n\n".join(sections + ([note] if block else []) + [footer])
    if len(full) > 1024:  # ліміт підпису до фото в Telegram
        full = "\n\n".join(sections + [footer])
    return full[:1024]


def format_districts_full(stats: dict) -> str:
    reliable, small = rank_districts(stats)
    if not reliable and not small:
        return "Не вдалося визначити райони для точок Svitlobot 😔"

    lines = ["🏙 Світло по районах Києва", "Найгірші зверху (з поправкою на кількість датчиків)", ""]
    for i, r in enumerate(reliable, 1):
        mark = " ⚠️" if r["total"] < LOW_SAMPLE_POINTS else ""
        lines.append(f"{i}. {r['name']} — {r['off_pct']}% без світла ({r['off']} з {r['total']}){mark}")
    if small:
        lines += ["", f"❔ Замало даних (менше {MIN_DISTRICT_POINTS} точок):"]
        for r in small:
            lines.append(f"• {r['name']} — {r['off']} з {r['total']} без світла")
    lines += [
        "",
        "ℹ️ У районах різна кількість датчиків; ⚠️ — мало точок, цифра менш надійна.",
        f"🕐 Станом на {ts_kyiv_str(stats['ts'])} · svitlobot.in.ua",
    ]
    if not EXACT_DISTRICTS:
        lines.append("ℹ️ Райони визначено наближено (за найближчим центром).")
    return "\n".join(lines)


# ================== ВІДНОВЛЕННЯ З ПОВІДОМЛЕННЯ ==================
_CAPTION_RE = re.compile(r"Світло є:\s*([\d.]+)%\s*\((\d+)\s*з\s*(\d+)\)")


def parse_stats_caption(text: str):
    """Дістає on/off з підпису попереднього поста бота. None, якщо це не пост зі світлом."""
    m = _CAPTION_RE.search(text or "")
    if not m:
        return None
    on, total = int(m.group(2)), int(m.group(3))
    if total <= 0 or on > total:
        return None
    return {"on": on, "off": total - on}
