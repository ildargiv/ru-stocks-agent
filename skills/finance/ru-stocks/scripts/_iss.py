"""Общие помощники: MOEX ISS API (iss.moex.com) и папка «рабочего стола» с акциями. Только stdlib."""
import datetime as _dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

ISS = "https://iss.moex.com/iss"
UA = {"User-Agent": "Mozilla/5.0 (ru-stocks-agent)"}

# Режимы торгов акциями: основной — TQBR (Т+1), паи/ETF — TQTF, ДР — TQBR/TQBD.
SHARE_BOARDS = ("TQBR", "TQTF", "TQBD", "TQIF", "TQPI")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------- MOEX ISS ----------
def get(path, **params):
    params.setdefault("iss.meta", "off")
    url = f"{ISS}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=UA)
    last = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # сеть MOEX иногда отваливается — повторяем
            last = e
    raise SystemExit(f"MOEX ISS недоступен ({url}): {last}")


def table(block):
    """ISS-блок {columns, data} -> список dict."""
    if not block:
        return []
    cols = block["columns"]
    return [dict(zip(cols, row)) for row in block["data"]]


def get_paged(path, key, **params):
    """Выкачивает все страницы блока key (ISS отдаёт по 100 строк)."""
    out, start = [], 0
    while True:
        d = get(path, start=start, **params)
        rows = table(d.get(key))
        out += rows
        cur = table(d.get(f"{key}.cursor"))
        if cur:
            c = cur[0]
            start = c["INDEX"] + c["PAGESIZE"]
            if start >= c["TOTAL"]:
                break
        elif len(rows) < 100:
            break
        else:
            start += len(rows)
    return out


def resolve(query):
    """Тикер / ISIN / название -> dict(secid, isin, name, board, emitent, inn, type)."""
    q = query.strip()
    d = get("/securities.json", q=q, **{"iss.only": "securities"})
    rows = [r for r in table(d.get("securities"))
            if (r.get("group") or "") in ("stock_shares", "stock_dr", "stock_ppif", "stock_etf")]
    if not rows:
        raise SystemExit(f"Акция не найдена на MOEX: {q}")
    exact = [r for r in rows if q.upper() in (str(r.get("secid")).upper(), str(r.get("isin")).upper())]
    traded = [r for r in (exact or rows) if r.get("is_traded") in (1, "1", True)]
    r = (traded or exact or rows)[0]
    if not exact and len(rows) > 1:
        print("Найдено несколько бумаг, беру первую. Варианты:", file=sys.stderr)
        for x in rows[:15]:
            print(f"  {x['secid']:<8} {x.get('isin') or '':<14} {x.get('shortname')} ({x.get('emitent_title')})",
                  file=sys.stderr)
    return {
        "secid": r["secid"], "isin": r.get("isin"), "name": r.get("shortname"),
        "board": r.get("primary_boardid") or "TQBR", "emitent": r.get("emitent_title"),
        "inn": r.get("emitent_inn"), "emitent_id": r.get("emitent_id"), "type": r.get("type"),
    }


def num(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def date(x):
    """Безопасный парсинг дат ISS ('0000-00-00', None -> None)."""
    try:
        return _dt.date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


def today():
    return _dt.date.today().isoformat()


# ---------- Рабочий стол (папки акций) ----------
def desk_root():
    """Корень хранилища. Переопределяется переменной STOCKS_DESK."""
    root = os.environ.get("STOCKS_DESK") or os.path.join(os.path.expanduser("~"), "stocks-desk")
    root = os.path.expanduser(root)
    # MSYS/Git-Bash отдаёт нативному python пути вида /c/Users/... — переводим в C:/Users/...
    m = re.match(r"^/([a-zA-Z])/(.*)$", root.replace("\\", "/"))
    if os.name == "nt" and m:
        root = f"{m.group(1).upper()}:/{m.group(2)}"
    os.makedirs(root, exist_ok=True)
    return root


def watchlist_path():
    return os.path.join(desk_root(), "watchlist.json")


def load_watchlist():
    p = watchlist_path()
    if not os.path.exists(p):
        return {"tickers": {}}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_watchlist(wl):
    p = watchlist_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(wl, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def ticker_dir(secid, sub=None, create=True):
    d = os.path.join(desk_root(), secid.upper())
    if sub:
        d = os.path.join(d, sub)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def append_journal(secid, text, source=""):
    """Хронология событий по акции (только добавление)."""
    p = os.path.join(ticker_dir(secid), "journal.md")
    line = f"- {today()} — {text}" + (f" ({source})" if source else "") + "\n"
    with open(p, "a", encoding="utf-8") as f:
        f.write(line)


def watched_or_all(tickers):
    """Список тикеров из аргумента или весь список наблюдения."""
    if tickers:
        return [t.upper() for t in tickers]
    wl = load_watchlist()["tickers"]
    if not wl:
        raise SystemExit("Список наблюдения пуст. Добавьте: python desk.py add SBER")
    return sorted(wl)
