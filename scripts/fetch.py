#!/usr/bin/env python3
"""抓台北、新北、桃園（含中壢）的羽球臨打資料，整理成 data/sessions.js。

來源：
  1. 羽球拼圖 badminton.pintu.tw  /api/teams        → 每週固定的團（程度有數字）
  2. 羽球臨打情報站 farreaching    /api/v1/activities/lite → 近期有日期的場次

只用 Python 標準函式庫。用法：python3 scripts/fetch.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
OUT_JS = ROOT / "data" / "sessions.js"
OUT_JSON = ROOT / "data" / "sessions.json"

UA = "Mozilla/5.0 (Macintosh) badminton-finder/1.0 (personal use)"
CITIES = ["台北市", "新北市", "桃園市"]
PINTU_API = "https://badminton.pintu.tw/api/teams"
FAR_API = "https://badminton-api.farreaching.com.tw/api/v1/activities/lite"

TZ = ZoneInfo("Asia/Taipei")  # GitHub Actions 跑在 UTC，一律用台灣時間
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


def get_json(url: str, retries: int = 3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"抓不到 {url}：{last}")


def region_of(city: str, area: str) -> str | None:
    """把縣市＋區轉成工具用的四個地區。中壢單獨一區。"""
    city = (city or "").replace("臺", "台")
    if city == "台北市":
        return "台北"
    if city == "新北市":
        return "新北"
    if city == "桃園市":
        return "中壢" if (area or "").startswith("中壢") else "桃園"
    return None


def weekday_idx(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"[一二三四五六日天]", text)
    if not m:
        return None
    c = m.group(0)
    return 6 if c in "日天" else WEEKDAYS.index(c)


def hhmm(t: str | None) -> str | None:
    if not t:
        return None
    m = re.match(r"(\d{1,2}):(\d{2})", t)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def to_int(v) -> int | None:
    try:
        n = int(float(str(v).replace("$", "").replace(",", "").strip()))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


# ---------- 程度文字 → 級數 ----------
# 羽球拼圖的文字程度與級數對照（從它自己 level_min/level_max 反推，2026-11 核對）
LV_WORDS = {"新手": (1, 3), "初級": (4, 5), "中下": (6, 7), "中等": (8, 9), "中上": (10, 12), "高級": (13, 18)}


def lv_from_text(text: str) -> tuple[int | None, int | None]:
    """「不限, 初級, 中下, 中等」→ (4, 9)。只寫「不限」就回傳 (None, None)。"""
    hits = [LV_WORDS[w] for w in LV_WORDS if w in (text or "")]
    if not hits:
        return None, None
    return min(h[0] for h in hits), max(h[1] for h in hits)


# ---------- 來源 1：羽球拼圖（每週固定團） ----------

def fetch_pintu() -> list[dict]:
    rows: list[dict] = []
    for city in CITIES:
        q = urllib.parse.urlencode({"page": 1, "pageSize": 5000, "city": city})
        data = get_json(f"{PINTU_API}?{q}")
        items = data.get("items", [])
        print(f"  羽球拼圖 {city}：{len(items)} 團", file=sys.stderr)
        for t in items:
            if t.get("stop") or t.get("hide"):
                continue
            region = region_of(t.get("city"), t.get("area"))
            if not region:
                continue
            lv_min, lv_max = t.get("level_min"), t.get("level_max")
            if lv_min is None or lv_max is None:
                lv_min, lv_max = lv_from_text(t.get("level"))
            slots = []
            for sfx in ("", "2", "3"):
                d = weekday_idx(t.get(f"day{sfx}"))
                s, e = hhmm(t.get(f"start_time{sfx}")), hhmm(t.get(f"end_time{sfx}"))
                if d is not None and s:
                    slots.append({"d": d, "s": s, "e": e})
            if not slots:
                continue
            fee = to_int(t.get("fee_once"))
            for i, sl in enumerate(slots):
                rows.append({
                    "id": f"p{t['id']}" + (f"-{i}" if i else ""),
                    "src": "pintu",
                    "kind": "weekly",
                    "name": (t.get("name") or "").strip(),
                    "region": region,
                    "city": t.get("city"),
                    "area": t.get("area"),
                    "place": (t.get("place") or "").strip(),
                    "address": (t.get("address") or "").strip(),
                    "day": sl["d"],
                    "start": sl["s"],
                    "end": sl["e"],
                    "lvMin": lv_min,
                    "lvMax": lv_max,
                    "lvText": (t.get("level") or "").strip(),
                    "fee": fee,
                    "courts": t.get("field_quantity"),
                    "ball": (t.get("ball_type") or "").strip(),
                    "need": t.get("people_needed"),
                    "full": t.get("full") == "yes",
                    "updated": (t.get("last_update") or "")[:10],
                    "next": t.get("next_play_date"),
                    "url": f"https://badminton.pintu.tw/team/{t['id']}",
                })
        time.sleep(1)
    return rows


# ---------- 來源 2：羽球臨打情報站（近期場次） ----------

LV_RE = re.compile(r"L?(\d{1,2})\s*[-~～至]\s*L?(\d{1,2})")

# 備註裡的個人聯絡方式：存檔前拿掉（資料會放進公開 repo）
_ID = r"[A-Za-z0-9._\-@]{3,}"
REDACT_RES = [
    # LINE 邀請連結、各種網址
    re.compile(r"https?://\S+|line\.me/\S+|lin\.ee/\S+", re.I),
    # 「LINE ID：xxx」「賴：xxx」「ID:xxx」「加line xxx」
    re.compile(r"(?:line|ＬＩＮＥ|賴)\s*(?:id|ＩＤ)?\s*[:：=]?\s*" + _ID, re.I),
    re.compile(r"(?:line|賴)[^A-Za-z0-9\n]{0,3}(?:id|ＩＤ)\s*[:：=]?\s*" + _ID, re.I),
    re.compile(r"(?<![A-Za-z])(?:id|ＩＤ)\s*[:：=]?\s*" + _ID, re.I),
    # 「搜尋 brianhahn」這種只給帳號的
    re.compile(r"搜尋\s*[:：]?\s*[A-Za-z0-9._\-]{4,}"),
    # @開頭的帳號
    re.compile(r"(?<![A-Za-z0-9])@" + _ID),
    # Email
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    # 手機 09xx-xxx-xxx、市話 0x-xxxx-xxxx、+886
    re.compile(r"(?:\+?886[\s-]?|0)9\d{2}[\s-]?\d{3}[\s-]?\d{3}"),
    re.compile(r"\(?0\d{1,2}\)?[\s-]?\d{3,4}[\s-]?\d{4}"),
]


def redact(text: str) -> str:
    for rx in REDACT_RES:
        text = rx.sub("〔已移除聯絡方式〕", text)
    return re.sub(r"(〔已移除聯絡方式〕[\s,，、/]*)+", "〔已移除聯絡方式〕", text)


def fetch_farreaching() -> list[dict]:
    data = get_json(FAR_API)
    acts = data.get("activities", [])
    print(f"  臨打情報站：{len(acts)} 場（全台）", file=sys.stderr)
    rows = []
    for a in acts:
        region = region_of(a.get("city"), a.get("district"))
        if not region:
            continue
        date = (a.get("event_date") or "").replace("/", "-")
        try:
            d = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            continue
        m = LV_RE.search(a.get("rating") or "")
        lv_min, lv_max = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        tm = (a.get("event_time") or "").split("-")
        rows.append({
            "id": f"f{a['id']}",
            "src": "farreaching",
            "kind": "dated",
            "name": (a.get("title") or "").strip(),
            "region": region,
            "city": a.get("city"),
            "area": a.get("district"),
            "place": (a.get("venue_name") or "").strip(),
            "address": (a.get("address") or "").strip(),
            "date": date,
            "day": d.weekday(),
            "start": hhmm(tm[0]) if tm else None,
            "end": hhmm(tm[1]) if len(tm) > 1 else None,
            "lvMin": lv_min,
            "lvMax": lv_max,
            "lvText": a.get("rating") or "",
            "fee": to_int(a.get("cost")) or to_int(a.get("male_cost")),
            "courts": a.get("court_count"),
            "ball": (a.get("shuttle_type") or "").strip(),
            "equip": a.get("equipments") or "",
            "note": redact((a.get("description") or "").strip())[:300],
            "url": a.get("apply_url") or "https://www.farreaching.com.tw/badminton/",
            "pageUrl": "https://www.farreaching.com.tw/badminton/",
        })
    return rows


def dedupe(rows: list[dict], key) -> list[dict]:
    """同一團重複登記時，留最近更新的那筆。"""
    best: dict = {}
    for r in rows:
        k = key(r)
        if k not in best or (r.get("updated") or "") > (best[k].get("updated") or ""):
            best[k] = r
    return list(best.values())


def main() -> int:
    print("開始抓資料…", file=sys.stderr)
    errors = []
    weekly, dated = [], []
    try:
        weekly = fetch_pintu()
    except Exception as e:  # noqa: BLE001
        errors.append(f"羽球拼圖：{e}")
    try:
        dated = fetch_farreaching()
    except Exception as e:  # noqa: BLE001
        errors.append(f"臨打情報站：{e}")

    if not weekly and not dated:
        print("兩個來源都失敗，保留舊資料不覆蓋：", *errors, sep="\n  ", file=sys.stderr)
        return 1

    # 某個來源失敗時，沿用上次的那一份，不讓網頁變空
    if OUT_JSON.exists() and (not weekly or not dated):
        old = json.loads(OUT_JSON.read_text("utf-8"))
        if not weekly:
            weekly = [r for r in old["rows"] if r["kind"] == "weekly"]
        if not dated:
            dated = [r for r in old["rows"] if r["kind"] == "dated"]

    today = datetime.now(TZ).strftime("%Y-%m-%d")
    dated = [r for r in dated if r["date"] >= today]
    weekly = dedupe(weekly, lambda r: (r["name"], r["place"], r["day"], r["start"]))
    dated = dedupe(dated, lambda r: (r["name"], r["place"], r["date"], r["start"]))
    rows = weekly + dated
    geo = {}
    try:
        from geocode import geocode_rows  # 同資料夾
        lim = os.environ.get("GEOCODE_LIMIT")
        geo = geocode_rows(rows, int(lim) if lim else None)
    except Exception as e:  # noqa: BLE001  座標查不到不影響主要資料
        errors.append(f"地理編碼：{e}")
    payload = {
        "generatedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
        "errors": errors,
        "counts": {"weekly": len(weekly), "dated": len(dated)},
        "geo": geo,
        "rows": rows,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
    # file:// 開網頁時不能 fetch JSON，所以另存一份 JS
    OUT_JS.write_text("window.BADMINTON=" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n", "utf-8")
    by = {}
    for r in rows:
        by[(r["kind"], r["region"])] = by.get((r["kind"], r["region"]), 0) + 1
    print(f"完成：每週固定團 {len(weekly)} 筆、近期場次 {len(dated)} 筆", file=sys.stderr)
    for k, v in sorted(by.items()):
        print(f"  {k[0]:7s} {k[1]}：{v}", file=sys.stderr)
    for e in errors:
        print("  ⚠️ " + e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
