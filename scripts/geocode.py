"""場館地址 → 經緯度，結果快取在 data/geocache.json。

用 OpenStreetMap Nominatim（https://operations.osmfoundation.org/policies/nominatim/）：
每秒最多 1 個請求、帶可辨識的 User-Agent、查過的結果一律快取，不重複查。

查詢順序（第一個查到、而且落在台北／新北／桃園範圍內的就用）：
  1. 場館名稱＋縣市             → precision = "venue"
  2. 地址截到「路／街／段」為止  → precision = "road"
  3. 縣市＋行政區中心            → precision = "district"（地圖上標「大概位置」）
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "geocache.json"
UA = "badminton-finder/1.0 (+https://github.com/knowmad-tw/badminton-finder)"
API = "https://nominatim.openstreetmap.org/search"
# 台北、新北、桃園的外框（緯度 24.55–25.35、經度 120.95–122.05）
VIEWBOX = "120.95,25.35,122.05,24.55"

_last = 0.0


def _search(q: str, city: str = "") -> tuple[float, float] | None:
    global _last
    wait = 1.1 - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    params = urllib.parse.urlencode({
        "format": "jsonv2", "q": q, "countrycodes": "tw", "limit": 5,
        "viewbox": VIEWBOX, "bounded": 1, "accept-language": "zh-TW",
    })
    req = urllib.request.Request(f"{API}?{params}", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"    查詢失敗 {q}：{e}", file=sys.stderr)
        return None
    # 同名場館很多（例如各縣市都有博愛國小），只接受地址裡有同一個縣市、座標也在該縣市範圍內的結果
    want = city.replace("臺", "台")
    for hit in data:
        name = (hit.get("display_name") or "").replace("臺", "台")
        lat, lng = float(hit["lat"]), float(hit["lon"])
        if want and want not in name:
            continue
        if want and not in_city(want, lat, lng):
            continue
        return round(lat, 6), round(lng, 6)
    return None


# 各縣市的大致範圍（用來擋掉同名但在別縣市的結果）
CITY_BOX = {
    "台北市": (24.96, 25.22, 121.45, 121.67),
    "新北市": (24.67, 25.31, 121.28, 122.02),
    "桃園市": (24.58, 25.13, 120.97, 121.50),
}


def in_city(city: str, lat: float, lng: float) -> bool:
    b = CITY_BOX.get(city.replace("臺", "台"))
    return not b or (b[0] <= lat <= b[1] and b[2] <= lng <= b[3])


def _clean_place(place: str) -> str:
    p = re.sub(r"[（(].*?[)）]", "", place or "")      # 拿掉括號附註
    p = re.sub(r"\d+\s*[Ff樓].*$", "", p)              # 拿掉「5F-羽1羽2」這類樓層
    p = re.sub(r"[♡★☆~～\-–—].*$", "", p)
    return p.strip()


def _road_of(address: str) -> str | None:
    a = re.sub(r"^\d{3,6}", "", (address or "").strip()).replace("臺", "台")
    m = re.match(r"(.*?[市縣].*?[區市鎮鄉].*?(?:路|街|大道)(?:[一二三四五六七八九十]段)?)", a)
    return m.group(1) if m else None


def key_of(row: dict) -> str:
    return f"{row.get('city') or ''}|{(row.get('place') or '').strip()}"


def load_cache() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text("utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True), "utf-8")


def geocode_rows(rows: list[dict], limit: int | None = None) -> dict:
    """在每個 row 加上 lat / lng / geo。limit＝這次最多新查幾個場館（Actions 用來控時間）。"""
    cache = load_cache()
    for k, v in list(cache.items()):
        if v.get("lat") is not None and not in_city(k.split("|")[0], v["lat"], v["lng"]):
            del cache[k]  # 舊版沒檢查縣市，落在別縣市的重查
    todo: dict[str, dict] = {}
    for r in rows:
        k = key_of(r)
        if k not in cache and k not in todo and r.get("place"):
            todo[k] = r
    todo_items = list(todo.items())[: limit] if limit is not None else list(todo.items())
    if todo_items:
        print(f"  地理編碼：新場館 {len(todo_items)} 個（全部未查 {len(todo)}）", file=sys.stderr)
    for i, (k, r) in enumerate(todo_items, 1):
        city, area = r.get("city") or "", r.get("area") or ""
        tries = []
        place = _clean_place(r.get("place", ""))
        if place:
            tries.append(("venue", f"{place} {city}"))
        # 「欣動羽球 (自強國中)」括號裡常常才是真正的場地
        for inner in re.findall(r"[（(]([^)）]{2,})[)）]", r.get("place", "")):
            tries.append(("venue", f"{inner.strip()} {city}"))
        road = _road_of(r.get("address", ""))
        if road:
            tries.append(("road", road))
        if area and area.endswith(("區", "市", "鎮", "鄉")):
            tries.append(("district", f"{city}{area}"))
        hit = None
        for prec, q in tries:
            ll = _search(q, city)
            if ll:
                hit = {"lat": ll[0], "lng": ll[1], "geo": prec, "q": q}
                break
        cache[k] = hit or {"lat": None, "lng": None, "geo": None}
        cache[k]["at"] = datetime.now().strftime("%Y-%m-%d")
        if i % 20 == 0:
            save_cache(cache)
            print(f"    {i}/{len(todo_items)}", file=sys.stderr)
    save_cache(cache)
    for r in rows:
        c = cache.get(key_of(r)) or {}
        if c.get("lat") is not None:
            r["lat"], r["lng"], r["geo"] = c["lat"], c["lng"], c["geo"]
    found = sum(1 for v in cache.values() if v.get("lat") is not None)
    return {"venues": len(cache), "located": found}


if __name__ == "__main__":
    # 單獨跑：python3 scripts/geocode.py → 把 data/sessions.json 的場館全部查一遍
    src = ROOT / "data" / "sessions.json"
    payload = json.loads(src.read_text("utf-8"))
    print(geocode_rows(payload["rows"]), file=sys.stderr)
