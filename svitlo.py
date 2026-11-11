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


# ================== ІСТОРІЯ (24г / 7д) ==================
from config import HISTORY_API

def _get_kyiv_channel_keys() -> set:
    """Повертає множину channel_key, які знаходяться в bounding box Києва."""
    try:
        r = requests.get(SVITLOBOT_API, timeout=20)
        r.raise_for_status()
        keys = set()
        for line in r.text.strip().splitlines():
            parts = line.rstrip(";").split(";&&&;")
            if len(parts) < 8:
                continue
            try:
                lat = float(parts[6])
                lng = float(parts[7])
                key = parts[4].strip()
            except (ValueError, IndexError):
                continue
            if 50.20 <= lat <= 50.60 and 30.20 <= lng <= 30.85 and key:
                keys.add(key)
        return keys
    except Exception as e:
        logger.error(f"Kyiv keys error: {e}")
        return set()


def _fetch_history(range_: str) -> dict | None:
    try:
        r = requests.get(f"{HISTORY_API}?range={range_}", timeout=60)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"History {range_} error: {e}")
        return None


def _expand_timeline(arr: list, total_minutes: int) -> list:
    """Перетворює [status, minutes, status, minutes, ...] на список статусів по хвилинах."""
    if not arr:
        return []
    timeline = []
    i = 0
    while i < len(arr) and len(timeline) < total_minutes:
        status = arr[i]
        minutes = arr[i + 1] if i + 1 < len(arr) else (total_minutes - len(timeline))
        minutes = min(int(minutes), total_minutes - len(timeline))
        if minutes > 0:
            timeline.extend([status] * minutes)
        i += 2
    # добиваємо якщо треба
    if len(timeline) < total_minutes and timeline:
        timeline.extend([timeline[-1]] * (total_minutes - len(timeline)))
    return timeline[:total_minutes]


def compute_kyiv_history(range_: str = "24h", sample_every_min: int = 30) -> dict | None:
    """
    Рахує агреговану статистику по Києву за період.
    Повертає dict з samples (список % on), avg/min/max, і для 7d — по днях і годинах.
    """
    kyiv_keys = _get_kyiv_channel_keys()
    if not kyiv_keys:
        return None

    data = _fetch_history(range_)
    if not data or "c" not in data:
        return None

    from_ts = data["from"]
    to_ts = data["to"]
    total_min = int((to_ts - from_ts) / 60)
    if total_min <= 0:
        return None

    # Збираємо таймлайни тільки київських каналів
    timelines = []
    for key, arr in data["c"].items():
        if key not in kyiv_keys:
            continue
        tl = _expand_timeline(arr, total_min)
        if tl:
            timelines.append(tl)

    if not timelines:
        return None

    # Семпли кожні sample_every_min хвилин
    samples = []
    for minute in range(0, total_min, sample_every_min):
        on = 0
        total = 0
        for tl in timelines:
            if minute < len(tl):
                status = tl[minute]
                if status in (1, 2):  # 1=on, 2=off; ігноруємо техперерви
                    total += 1
                    if status == 1:
                        on += 1
        if total > 0:
            samples.append({
                "minute": minute,
                "ts": from_ts + minute * 60,
                "on_pct": round(on / total * 100, 1),
                "total": total,
            })

    if not samples:
        return None

    pcts = [s["on_pct"] for s in samples]
    result = {
        "range": range_,
        "from_ts": from_ts,
        "to_ts": to_ts,
        "samples": samples,
        "avg_pct": round(sum(pcts) / len(pcts), 1),
        "min_pct": min(pcts),
        "max_pct": max(pcts),
        "num_channels": len(timelines),
    }

    # Завжди рахуємо години (для 24h і для 7d)
    from collections import defaultdict
    from datetime import datetime
    from zoneinfo import ZoneInfo
    KYIV_TZ = ZoneInfo("Europe/Kyiv")

    by_hour = defaultdict(list)
    for s in samples:
        dt = datetime.fromtimestamp(s["ts"], tz=KYIV_TZ)
        by_hour[dt.hour].append(s["on_pct"])

    hour_avgs = []
    for h in range(24):
        pcts_h = by_hour.get(h, [])
        hour_avgs.append({
            "hour": h,
            "avg_pct": round(sum(pcts_h) / len(pcts_h), 1) if pcts_h else 0,
        })
    result["hours"] = hour_avgs

    # Для тижня — додатково по днях
    if range_ == "7d":
        by_day = defaultdict(list)
        for s in samples:
            dt = datetime.fromtimestamp(s["ts"], tz=KYIV_TZ)
            day_key = dt.strftime("%Y-%m-%d")
            by_day[day_key].append(s["on_pct"])

        day_avgs = []
        for day, pcts_d in sorted(by_day.items()):
            day_avgs.append({
                "date": day,
                "avg_pct": round(sum(pcts_d) / len(pcts_d), 1),
            })
        result["days"] = day_avgs

        if day_avgs:
            best = max(day_avgs, key=lambda x: x["avg_pct"])
            worst = min(day_avgs, key=lambda x: x["avg_pct"])
            result["best_day"] = best
            result["worst_day"] = worst

    return result


def create_24h_image(hist: dict) -> BytesIO:
    samples = hist["samples"]
    fig = plt.figure(figsize=(12, 7), facecolor=BG)
    gs = fig.add_gridspec(2, 1, height_ratios=[1.2, 1], hspace=0.35)

    # Верх — динаміка за 24 год
    ax1 = fig.add_subplot(gs[0])
    ax1.set_facecolor(BG)
    xs = [s["minute"] / 60 for s in samples]
    ys = [s["on_pct"] for s in samples]
    ax1.fill_between(xs, ys, color="#00c853", alpha=0.35)
    ax1.plot(xs, ys, color="#00c853", linewidth=2.2)
    ax1.axhline(hist["avg_pct"], color="#ffab00", linestyle="--", linewidth=1, alpha=0.8)
    ax1.set_xlim(0, 24)
    ax1.set_ylim(0, 100)
    ax1.set_xlabel("Години тому", color="white", fontsize=11)
    ax1.set_ylabel("% зі світлом", color="white", fontsize=11)
    ax1.tick_params(colors="white")
    for spine in ax1.spines.values():
        spine.set_color("#444")
    ax1.set_title("Динаміка за останні 24 години", color="white", fontsize=13)

    # Низ — по годинах доби
    ax2 = fig.add_subplot(gs[1])
    ax2.set_facecolor(BG)
    hours = hist.get("hours", [])
    if hours:
        hs = [h["hour"] for h in hours]
        hp = [h["avg_pct"] for h in hours]
        colors = ["#00c853" if p >= hist["avg_pct"] else "#ff1744" for p in hp]
        ax2.bar(hs, hp, color=colors, width=0.7, alpha=0.9)
        ax2.set_xlim(-0.5, 23.5)
        ax2.set_ylim(0, 100)
        ax2.set_xlabel("Година доби (Київ)", color="white", fontsize=11)
        ax2.set_ylabel("% зі світлом", color="white", fontsize=11)
        ax2.set_xticks(range(0, 24, 2))
        ax2.tick_params(colors="white")
        for spine in ax2.spines.values():
            spine.set_color("#444")
        ax2.set_title("Коли більше / менше світла (середнє по годинах)", color="white", fontsize=13)

    title = f"Київ · 24 год · середнє {hist['avg_pct']}% (мін {hist['min_pct']}% / макс {hist['max_pct']}%)"
    fig.text(0.5, 0.98, title, ha="center", va="top", color="white", fontsize=14, fontweight="bold")
    fig.text(0.5, 0.01, "svitlobot.in.ua · тільки датчики в Києві", ha="center", color="#9e9e9e", fontsize=10)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)
    return buf


def create_week_image(hist: dict) -> BytesIO:
    fig = plt.figure(figsize=(14, 7), facecolor=BG)
    gs = fig.add_gridspec(2, 1, height_ratios=[1, 1], hspace=0.35)

    # Верх — по днях
    ax1 = fig.add_subplot(gs[0])
    ax1.set_facecolor(BG)
    days = hist.get("days", [])
    if days:
        labels = [d["date"][5:] for d in days]  # MM-DD
        pcts = [d["avg_pct"] for d in days]
        colors = ["#00c853" if p >= hist["avg_pct"] else "#ff1744" for p in pcts]
        ax1.bar(labels, pcts, color=colors, width=0.6)
        ax1.axhline(hist["avg_pct"], color="#ffab00", linestyle="--", linewidth=1)
        ax1.set_ylim(0, 100)
        ax1.set_ylabel("% зі світлом", color="white")
        ax1.tick_params(colors="white")
        for spine in ax1.spines.values():
            spine.set_color("#444")
        ax1.set_title("Середній % світла по днях", color="white", fontsize=13)

    # Низ — по годинах
    ax2 = fig.add_subplot(gs[1])
    ax2.set_facecolor(BG)
    hours = hist.get("hours", [])
    if hours:
        hs = [h["hour"] for h in hours]
        hp = [h["avg_pct"] for h in hours]
        ax2.bar(hs, hp, color="#00c853", width=0.7, alpha=0.85)
        ax2.set_xlim(-0.5, 23.5)
        ax2.set_ylim(0, 100)
        ax2.set_xlabel("Година доби (Київ)", color="white")
        ax2.set_ylabel("% зі світлом", color="white")
        ax2.set_xticks(range(0, 24, 2))
        ax2.tick_params(colors="white")
        for spine in ax2.spines.values():
            spine.set_color("#444")
        ax2.set_title("Коли в Києві частіше є світло (середнє за тиждень)", color="white", fontsize=13)

    best = hist.get("best_day", {})
    worst = hist.get("worst_day", {})
    footer = f"Найкращий день: {best.get('date', '?')} ({best.get('avg_pct', 0)}%) · Найгірший: {worst.get('date', '?')} ({worst.get('avg_pct', 0)}%) · svitlobot.in.ua"
    fig.text(0.5, 0.01, footer, ha="center", color="#9e9e9e", fontsize=10)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)
    return buf


def format_24h_caption(hist: dict) -> str:
    hours = hist.get("hours", [])
    best_hours = sorted([h for h in hours if h["avg_pct"] > 0], key=lambda x: x["avg_pct"], reverse=True)[:3]
    worst_hours = sorted([h for h in hours if h["avg_pct"] > 0], key=lambda x: x["avg_pct"])[:3]

    lines = [
        "📊 Київ · останні 24 години",
        "",
        f"💡 Середнє: {hist['avg_pct']}% датчиків зі світлом",
        f"📈 Максимум: {hist['max_pct']}%",
        f"📉 Мінімум: {hist['min_pct']}%",
        f"🔢 Датчиків: {hist['num_channels']}",
        "",
        "🕐 Коли світла було більше:",
    ]
    for h in best_hours:
        lines.append(f"  • {h['hour']:02d}:00 — {h['avg_pct']}%")
    lines.append("")
    lines.append("🌑 Коли світла було менше:")
    for h in worst_hours:
        lines.append(f"  • {h['hour']:02d}:00 — {h['avg_pct']}%")
    lines.append("")
    lines.append(f"🕐 {ts_kyiv_str(hist['to_ts'])} · svitlobot.in.ua")
    return "\n".join(lines)


def format_week_caption(hist: dict) -> str:
    best = hist.get("best_day", {})
    worst = hist.get("worst_day", {})
    hours = hist.get("hours", [])
    best_hours = sorted(hours, key=lambda x: x["avg_pct"], reverse=True)[:3]
    worst_hours = sorted(hours, key=lambda x: x["avg_pct"])[:3]

    lines = [
        "📊 Київ · тижнева статистика",
        "",
        f"💡 Середнє за тиждень: {hist['avg_pct']}%",
        f"✅ Найкращий день: {best.get('date', '?')} — {best.get('avg_pct', 0)}%",
        f"❌ Найгірший день: {worst.get('date', '?')} — {worst.get('avg_pct', 0)}%",
        "",
        "🕐 Години, коли світло є частіше:",
    ]
    for h in best_hours:
        lines.append(f"  • {h['hour']:02d}:00 — {h['avg_pct']}%")
    lines.append("")
    lines.append("🌑 Години, коли світла частіше немає:")
    for h in worst_hours:
        lines.append(f"  • {h['hour']:02d}:00 — {h['avg_pct']}%")
    lines.append("")
    lines.append(f"🕐 {ts_kyiv_str(hist['to_ts'])} · svitlobot.in.ua")
    return "\n".join(lines)


# ================== ГРАФІКИ YASNO ==================
from config import YASNO_SCHEDULE_API

def fetch_yasno_schedule() -> dict | None:
    """Завантажує графіки відключень Yasno для Києва (60 груп)."""
    try:
        r = requests.get(YASNO_SCHEDULE_API, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict) or not data:
            return None
        return data
    except Exception as e:
        logger.error(f"Yasno schedule error: {e}")
        return None


def _minutes_to_hhmm(m: int) -> str:
    h, mm = divmod(m, 60)
    return f"{h:02d}:{mm:02d}"


def create_yasno_image(schedule: dict, day: str = "today") -> BytesIO:
    """Генерує чисту картинку з графіками всіх груп."""
    groups = sorted(schedule.keys(), key=lambda x: (int(x.split(".")[0]), x))
    n = len(groups)

    # Більше місця по висоті, щоб текст не наїжджав
    fig, ax = plt.subplots(figsize=(16, max(16, n * 0.55)), facecolor=BG)
    ax.set_facecolor(BG)

    day_label = "Сьогодні" if day == "today" else "Завтра"
    fig.text(0.5, 0.99, f"Графік відключень · Київ · {day_label}",
             ha="center", va="top", color="white", fontsize=20, fontweight="bold")

    y_positions = list(range(n - 1, -1, -1))

    for y, grp in zip(y_positions, groups):
        info = schedule[grp].get(day, {})
        slots = info.get("slots", [])

        # Фон (світло є)
        ax.barh(y, 24, height=0.42, left=0, color="#2a2a45", zorder=1)

        # Відключення
        intervals = []
        for s in slots:
            if s.get("type") == "Definite":
                start_h = s["start"] / 60.0
                end_h = s["end"] / 60.0
                ax.barh(y, end_h - start_h, height=0.42, left=start_h,
                        color="#e53935", zorder=2)
                intervals.append(f"{_minutes_to_hhmm(s['start'])}–{_minutes_to_hhmm(s['end'])}")

        # Група зліва
        ax.text(-1.8, y, grp, ha="right", va="center", color="white",
                fontsize=11, fontweight="bold")

        # Інтервали справа від бару, рівно
        if intervals:
            text = " · ".join(intervals)
            ax.text(24.6, y, text, ha="left", va="center",
                    color="#b0bec5", fontsize=8.5)

        # Години без світла ще правіше
        off_min = sum(s["end"] - s["start"] for s in slots if s.get("type") == "Definite")
        off_h = round(off_min / 60, 1)
        ax.text(38.5, y, f"{off_h} год", ha="left", va="center",
                color="#90a4ae", fontsize=9)

    ax.set_xlim(-3.5, 42)
    ax.set_ylim(-0.6, n - 0.4)
    ax.set_yticks([])
    ax.set_xticks(range(0, 25, 3))
    ax.set_xticklabels([f"{h:02d}" for h in range(0, 25, 3)], color="#cfd8dc", fontsize=11)
    ax.tick_params(axis="x", colors="#546e7a", length=0, pad=6)
    for spine in ax.spines.values():
        spine.set_visible(False)

    fig.text(0.5, 0.005, "Дякуємо Yasno · yasno.com.ua · тільки Київ",
             ha="center", color="#78909c", fontsize=11)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)
    return buf


def format_yasno_caption(schedule: dict, day: str = "today") -> str:
    groups = sorted(schedule.keys(), key=lambda x: (int(x.split(".")[0]), x))
    day_label = "сьогодні" if day == "today" else "завтра"

    lines = [
        f"⚡️ Графік відключень · Київ · {day_label}",
        "",
    ]

    # Короткий підсумок
    total_off = 0
    for grp in groups:
        slots = schedule[grp].get(day, {}).get("slots", [])
        total_off += sum(s["end"] - s["start"] for s in slots if s.get("type") == "Definite")
    avg_off = round(total_off / len(groups) / 60, 1) if groups else 0
    lines.append(f"📊 Середнє без світла: ~{avg_off} год на групу")
    lines.append(f"🔢 Груп: {len(groups)}")
    lines.append("")
    lines.append("🔴 Червоні смуги — заплановані відключення")
    lines.append("")
    lines.append("Дякуємо Yasno за дані 🙏")
    lines.append("yasno.com.ua")

    return "\n".join(lines)
