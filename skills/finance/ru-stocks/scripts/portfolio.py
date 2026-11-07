"""Портфель пользователя и ежедневный разбор торгов («семейный офис»).

Состав портфеля
  python portfolio.py set SBER LKOH MGNT                 # задать портфель списком (заменяет прежний)
  python portfolio.py set "SBER:100@285.4, LKOH:5@6900"   # с количеством и средней ценой (необязательно)
  python portfolio.py add TATNP:200@610 [--sector нефтегаз] [--target 10]
  python portfolio.py update SBER --qty 150 --avg 279.9 [--sector финансы] [--target 15] [--note "..."]
  python portfolio.py trade SBER buy 50 300.5             # сделка со слов пользователя: пересчёт количества и средней
  python portfolio.py trade SBER sell 30 [320]            # продажа (с ценой — в хронологию пишется результат)
  python portfolio.py remove MGNT                         # убрать из портфеля (папка и наблюдение остаются)
  python portfolio.py import broker.csv                   # CSV: тикер/ISIN; количество; средняя цена
  python portfolio.py list [--offline]                    # позиции, стоимость, P&L

Ежедневная работа
  python portfolio.py session [--save] [--json] [--alerts]   # разбор последней торговой сессии по портфелю
  python portfolio.py review  [--save] [--json]              # недельный обзор: доходность, риск, корреляции, тезисы
  python portfolio.py log "Текст события" [--source ...]      # хронология портфеля
  python portfolio.py path                                   # где лежат файлы портфеля

Хранилище: <STOCKS_DESK>/portfolio.json и <STOCKS_DESK>/_portfolio/ (daily/, briefs/, reviews/, history.csv,
journal.md). Каждая бумага портфеля автоматически ставится на наблюдение (desk.py add) и получает свою папку.
Источник котировок: MOEX ISS (без ключа, задержка ~15 мин). Только stdlib.
"""
import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import re
import sys
import types
import urllib.request
import xml.etree.ElementTree as ET

from _iss import (get, table, num, date, today, desk_root, load_watchlist, ticker_dir, append_journal, UA)

# ---------- пороги флагов (поменяйте под себя) ----------
MOVE_PCT = 3.0          # движение за день, % — существенное
MOVE_SIGMA = 2.0        # или больше N дневных сигм
REL_PP = 2.5            # отклонение от IMOEX за день, п.п.
GAP_PCT = 2.0           # гэп на открытии, %
VOLUME_X = 2.0          # оборот выше среднего за 20 дней в N раз
DIV_DAYS = 30           # показывать отсечки в ближайшие N дней
CONC_WEIGHT = 25.0      # доля одной бумаги выше N% — концентрация
LOSS_FROM_AVG = 15.0    # бумага ниже средней цены покупки на N%
CORR_HIGH = 0.75        # пары с корреляцией выше — «один и тот же риск»

INDICES = {"IMOEX": "Индекс МосБиржи", "RTSI": "РТС ($)", "RGBI": "ОФЗ (RGBI)", "MOEXOG": "нефть и газ",
           "MOEXFN": "финансы", "MOEXMM": "металлы и добыча", "MOEXEU": "электроэнергетика",
           "MOEXTL": "телеком", "MOEXCN": "потребсектор", "MOEXCH": "химия", "MOEXTN": "транспорт",
           "MOEXRE": "строители", "MOEXIT": "IT"}


# ---------- файлы ----------
def pf_path():
    return os.path.join(desk_root(), "portfolio.json")


def pf_dir(sub=None):
    d = os.path.join(desk_root(), "_portfolio", *([sub] if sub else []))
    os.makedirs(d, exist_ok=True)
    return d


def load_pf():
    p = pf_path()
    if not os.path.exists(p):
        return {"positions": {}, "created": today()}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_pf(pf):
    pf["updated"] = today()
    p = pf_path()
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(pf, f, ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)


def pf_journal(text, source=""):
    p = os.path.join(pf_dir(), "journal.md")
    new = not os.path.exists(p)
    with open(p, "a", encoding="utf-8") as f:
        if new:
            f.write("# Хронология портфеля\n\n")
        f.write(f"- {today()} — {text}" + (f" ({source})" if source else "") + "\n")


def need_positions(pf):
    if not pf["positions"]:
        raise SystemExit("Портфель пуст. Задайте: python portfolio.py set SBER LKOH MGNT")
    return pf["positions"]


# ---------- разбор ввода ----------
TOKEN = re.compile(r"^([A-Za-zА-Яа-я0-9._-]+?)(?::([\d\s.,]+?))?(?:@([\d\s.,]+))?$")


def fnum(s):
    if s is None or str(s).strip() == "":
        return None
    s = str(s).replace(" ", "").replace(" ", "").replace(",", ".")
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def parse_tokens(args):
    """['SBER:100@285,4', 'LKOH, MGNT'] -> [(тикер, qty, avg)]."""
    out = []
    for chunk in args:
        for tok in re.split(r"[;\s]+|,(?=\s*[A-Za-zА-Яа-я])", chunk.strip()):
            tok = tok.strip(" ,")
            if not tok:
                continue
            m = TOKEN.match(tok)
            if not m:
                raise SystemExit(f"Не понимаю «{tok}». Формат: ТИКЕР или ТИКЕР:КОЛ-ВО@СРЕДНЯЯ_ЦЕНА")
            out.append((m.group(1).upper(), fnum(m.group(2)), fnum(m.group(3))))
    return out


def ensure_watched(tickers):
    """Ставит бумаги на наблюдение (папка, профиль, ИНН) и возвращает {ввод: secid}."""
    from desk import cmd_add
    wl = load_watchlist()["tickers"]
    missing = [t for t in tickers if t not in wl]
    if missing:
        cmd_add(types.SimpleNamespace(tickers=missing, note="в портфеле"))
    wl = load_watchlist()["tickers"]
    mapping = {}
    for t in tickers:
        if t in wl:
            mapping[t] = t
        else:  # ввели ISIN или название — ищем, во что превратилось
            hit = [s for s, x in wl.items() if t in (str(x.get("isin")).upper(), str(x.get("name")).upper())]
            if not hit:
                raise SystemExit(f"{t}: не удалось поставить на наблюдение")
            mapping[t] = hit[0]
    return mapping, wl


def upsert(pf, items, wl_map, wl, sector=None, target=None, note=None):
    for t, qty, avg in items:
        s = wl_map[t]
        pos = pf["positions"].get(s) or {"added": today()}
        pos["board"] = (wl.get(s) or {}).get("board") or "TQBR"
        pos["name"] = (wl.get(s) or {}).get("emitent") or (wl.get(s) or {}).get("name")
        if qty is not None:
            pos["qty"] = qty
        if avg is not None:
            pos["avg"] = avg
        if sector:
            pos["sector"] = sector
        if target is not None:
            pos["target"] = target
        if note:
            pos["note"] = note
        pf["positions"][s] = pos


def cmd_set(a):
    items = parse_tokens(a.items)
    wl_map, wl = ensure_watched([t for t, _, _ in items])
    pf = load_pf()
    old = set(pf["positions"])
    keep = {wl_map[t] for t, _, _ in items}
    for s in old - keep:
        pf["positions"].pop(s)
    upsert(pf, items, wl_map, wl)
    save_pf(pf)
    pf_journal("Состав портфеля задан: " + ", ".join(sorted(keep))
               + (f"; убраны: {', '.join(sorted(old - keep))}" if old - keep else ""))
    print(f"✅ Портфель ({len(keep)} шт.): {', '.join(sorted(keep))}. Файл: {pf_path()}")
    no_qty = [s for s in sorted(keep) if pf['positions'][s].get('qty') is None]
    if no_qty:
        print("   Без количества: " + ", ".join(no_qty) + " — портфель считается равновзвешенным. "
              "Добавить: portfolio.py update SBER --qty 100 --avg 280")


def cmd_add(a):
    items = parse_tokens(a.items)
    wl_map, wl = ensure_watched([t for t, _, _ in items])
    pf = load_pf()
    upsert(pf, items, wl_map, wl, a.sector, a.target, a.note)
    save_pf(pf)
    added = ", ".join(wl_map[t] for t, _, _ in items)
    pf_journal(f"В портфель: {added}")
    print(f"✅ В портфеле: {added}")


def cmd_update(a):
    pf = load_pf()
    s = a.ticker.upper()
    if s not in pf["positions"]:
        raise SystemExit(f"{s} нет в портфеле (python portfolio.py add {s})")
    pos = pf["positions"][s]
    for k in ("qty", "avg", "sector", "target", "note"):
        v = getattr(a, k)
        if v is not None:
            pos[k] = v
    save_pf(pf)
    pf_journal(f"Позиция {s} обновлена: " + ", ".join(f"{k}={pos.get(k)}" for k in ("qty", "avg", "target") if pos.get(k) is not None))
    print(f"✅ {s}: {pos}")


def cmd_remove(a):
    pf = load_pf()
    for t in a.tickers:
        s = t.upper()
        if pf["positions"].pop(s, None) is None:
            print(f"{s}: нет в портфеле")
            continue
        pf_journal(f"Из портфеля убрана {s}")
        append_journal(s, "Убрана из портфеля пользователя (наблюдение продолжается)")
        print(f"{s} убрана из портфеля; наблюдение и папка остаются (desk.py remove {s}, чтобы перестать следить)")
    save_pf(pf)


def cmd_trade(a):
    pf = load_pf()
    s = a.ticker.upper()
    if a.side == "buy" and s not in pf["positions"]:
        wl_map, wl = ensure_watched([s])
        upsert(pf, [(s, None, None)], wl_map, wl)
        s = wl_map[s]
    if s not in pf["positions"]:
        raise SystemExit(f"{s} нет в портфеле")
    pos = pf["positions"][s]
    q0, a0 = pos.get("qty") or 0, pos.get("avg")
    if a.side == "buy":
        if a.price is None:
            raise SystemExit("Для покупки нужна цена: trade SBER buy 50 300")
        q1 = q0 + a.qty
        pos["avg"] = round((q0 * (a0 or a.price) + a.qty * a.price) / q1, 4) if q0 else a.price
        pos["qty"] = q1
        msg = f"Покупка {s}: {a.qty:g} шт. по {a.price:g} ₽ → {q1:g} шт., средняя {pos['avg']:g} ₽"
    else:
        if a.qty > q0:
            raise SystemExit(f"Продаётся {a.qty:g}, а в позиции {q0:g}")
        pos["qty"] = q0 - a.qty
        res = (", результат " + f"{(a.price - a0) * a.qty:+,.0f}".replace(",", " ") + f" ₽ ({pct(a.price, a0):+.1f}%)") if a.price and a0 else ""
        msg = f"Продажа {s}: {a.qty:g} шт." + (f" по {a.price:g} ₽" if a.price else "") + f" → осталось {pos['qty']:g} шт.{res}"
        if pos["qty"] == 0:
            pf["positions"].pop(s)
            msg += "; позиция закрыта (наблюдение продолжается)"
    save_pf(pf)
    pf_journal(msg, "со слов владельца")
    append_journal(s, msg, "со слов владельца")
    print("✅ " + msg)


HEAD_TICKER = ("ticker", "тикер", "secid", "код", "код бумаги", "инструмент", "symbol", "isin", "код инструмента")
HEAD_QTY = ("qty", "quantity", "количество", "кол-во", "кол-во, шт", "остаток", "количество, шт", "шт", "lots")
HEAD_AVG = ("avg", "average", "средняя", "средняя цена", "ср. цена", "цена покупки", "средняя цена покупки",
            "цена приобретения", "балансовая цена")


def pick(headers, names):
    hs = [h.strip().lower() for h in headers]
    for n in names:
        if n in hs:
            return hs.index(n)
    for i, h in enumerate(hs):
        if any(n in h for n in names if len(n) > 3):
            return i
    return None


def cmd_import(a):
    with open(a.file, encoding=a.encoding, errors="replace") as f:
        text = f.read()
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if text.count(";") > text.count(",") else ","
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        raise SystemExit("Файл пуст")
    it, iq, ia = pick(rows[0], HEAD_TICKER), pick(rows[0], HEAD_QTY), pick(rows[0], HEAD_AVG)
    body = rows[1:]
    if it is None:  # без заголовка: тикер;кол-во;цена
        it, iq, ia, body = 0, 1 if len(rows[0]) > 1 else None, 2 if len(rows[0]) > 2 else None, rows
    items = []
    for r in body:
        t = (r[it] if it < len(r) else "").strip().upper()
        if not re.match(r"^[A-Z0-9._-]{2,12}$", t):
            continue
        q = fnum(r[iq]) if iq is not None and iq < len(r) else None
        av = fnum(r[ia]) if ia is not None and ia < len(r) else None
        items.append((t, q, av))
    if not items:
        raise SystemExit("Не нашёл тикеров/ISIN. Ожидаю колонки: тикер (или ISIN); количество; средняя цена")
    print("Распознано: " + ", ".join(f"{t}" + (f":{q:g}" if q else "") + (f"@{av:g}" if av else "") for t, q, av in items))
    a.items = [f"{t}" + (f":{q:g}" if q is not None else "") + (f"@{av:g}" if av is not None else "") for t, q, av in items]
    (cmd_add if a.merge else cmd_set)(a)


# ---------- рыночные данные ----------
def _rows(hist):
    out = []
    for r in hist:
        d, c = date(r.get("TRADEDATE")), num(r.get("CLOSE"))
        if d and c:
            out.append({"date": d, "open": num(r.get("OPEN")), "high": num(r.get("HIGH")), "low": num(r.get("LOW")),
                        "close": c, "value": num(r.get("VALUE")), "volume": num(r.get("VOLUME")), "live": False})
    return out


def share_live(secid, board):
    d = get(f"/engines/stock/markets/shares/boards/{board}/securities/{secid}.json",
            **{"iss.only": "securities,marketdata"})
    s = (table(d.get("securities")) or [{}])[0]
    m = (table(d.get("marketdata")) or [{}])[0]
    return s, m


def merge_live(rows, d, close, open_, high, low, value, volume):
    """Добавляет текущую (ещё не попавшую в историю) сессию к ряду."""
    if not d or not close:
        return rows
    live = {"date": d, "open": open_, "high": high or close, "low": low or close, "close": close,
            "value": value, "volume": volume, "live": True}
    if rows and rows[-1]["date"] == d:
        return rows  # официальные итоги дня уже в истории
    if rows and rows[-1]["date"] > d:
        return rows
    return rows + [live]


def series_for(secid, board, since):
    from moex_share import history
    rows = _rows(history(secid, board, since))
    s, m = share_live(secid, board)
    systime, prevdate = date(m.get("SYSTIME")), date(s.get("PREVDATE"))
    # Текущая сессия ещё не в истории. Если PREVDATE (предыдущий торговый день) не совпадает с последним днём
    # истории, данные marketdata устарели (выходной без торгов) или история отстаёт — тогда берём только историю.
    fresh = prevdate is None or (rows and rows[-1]["date"] == prevdate)
    if num(m.get("VALTODAY")) and systime and fresh:
        close = num(m.get("LAST")) or num(m.get("LCURRENTPRICE")) or num(m.get("MARKETPRICE"))
        rows = merge_live(rows, systime, close, num(m.get("OPEN")), num(m.get("HIGH")), num(m.get("LOW")),
                          num(m.get("VALTODAY")), num(m.get("VOLTODAY")))
    meta = {"lot": num(s.get("LOTSIZE")), "shortname": s.get("SHORTNAME"), "status": m.get("TRADINGSTATUS"),
            "time": m.get("UPDATETIME") or m.get("TIME"), "cap": num(m.get("ISSUECAPITALIZATION")),
            "bid": num(m.get("BID")), "offer": num(m.get("OFFER")), "numtrades": num(m.get("NUMTRADES"))}
    return rows, meta


def index_series(secid, since):
    from moex_share import history
    rows = _rows(history(secid, "SNDX", since, market="index"))
    try:
        d = get(f"/engines/stock/markets/index/securities/{secid}.json", **{"iss.only": "marketdata"})
        m = next((x for x in table(d.get("marketdata")) if num(x.get("CURRENTVALUE"))), None)
        if m:
            rows = merge_live(rows, date(m.get("TRADEDATE")) or date(m.get("SYSTIME")), num(m.get("CURRENTVALUE")),
                              num(m.get("OPENVALUE")), num(m.get("HIGH")), num(m.get("LOW")), num(m.get("VALTODAY")), None)
    except SystemExit:
        pass
    return rows


def market_board():
    """Индексы, юань, официальные курсы ЦБ, Brent — фон дня. Ошибки не роняют разбор."""
    out = {"indices": [], "fx": [], "commodities": [], "errors": []}
    try:
        d = get("/engines/stock/markets/index/securities.json",
                **{"securities": ",".join(INDICES), "iss.only": "marketdata"})
        seen = set()
        for m in table(d.get("marketdata")):
            s = m.get("SECID")
            if s in INDICES and s not in seen and num(m.get("CURRENTVALUE")):
                seen.add(s)
                out["indices"].append({"secid": s, "name": INDICES[s], "value": num(m.get("CURRENTVALUE")),
                                       "change_pct": num(m.get("LASTCHANGEPRC")), "date": m.get("TRADEDATE")})
        out["indices"].sort(key=lambda x: list(INDICES).index(x["secid"]))
    except SystemExit as e:
        out["errors"].append(f"индексы: {e}")
    try:
        d = get("/engines/currency/markets/selt/boards/CETS/securities/CNYRUB_TOM.json", **{"iss.only": "marketdata"})
        m = (table(d.get("marketdata")) or [{}])[0]
        last = num(m.get("LAST")) or num(m.get("MARKETPRICE"))
        if last:
            out["fx"].append({"name": "CNY/RUB (биржа, TOM)", "value": last,
                              "change_pct": num(m.get("LASTTOPREVPRICE")) or num(m.get("LASTCHANGEPRCNT"))})
    except SystemExit as e:
        out["errors"].append(f"юань: {e}")
    try:
        out["fx"] += cbr_rates()
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"ЦБ: {e}")
    try:
        br = brent()
        if br:
            out["commodities"].append(br)
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"Brent: {e}")
    return out


def _cbr(d=None):
    url = "https://www.cbr.ru/scripts/XML_daily.asp" + (f"?date_req={d:%d/%m/%Y}" if d else "")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        root = ET.fromstring(r.read())
    rates = {}
    for v in root.iter("Valute"):
        code = v.findtext("CharCode")
        if code in ("USD", "EUR", "CNY"):
            rates[code] = fnum(v.findtext("Value")) / (fnum(v.findtext("Nominal")) or 1)
    return root.get("Date"), rates


def cbr_rates():
    on, now = _cbr()
    prev_day = dt.datetime.strptime(on, "%d.%m.%Y").date() - dt.timedelta(days=1)
    _, prev = _cbr(prev_day)
    return [{"name": f"{c}/RUB (ЦБ на {on})", "value": v,
             "change_pct": (v / prev[c] - 1) * 100 if prev.get(c) else None} for c, v in now.items()]


def brent():
    d = get("/engines/futures/markets/forts/securities.json", **{"iss.only": "securities,marketdata"})
    secs = {r["SECID"]: r for r in table(d.get("securities")) if r.get("ASSETCODE") == "BR"}
    live = [r for r in secs.values() if date(r.get("LASTTRADEDATE")) and date(r.get("LASTTRADEDATE")) >= dt.date.today()]
    if not live:
        return None
    near = min(live, key=lambda r: r["LASTTRADEDATE"])
    m = next((x for x in table(d.get("marketdata")) if x.get("SECID") == near["SECID"]), {})
    last = num(m.get("LAST")) or num(near.get("PREVSETTLEPRICE"))
    prev = num(near.get("PREVSETTLEPRICE"))
    return {"name": f"Brent, фьючерс {near['SECID']} ($)", "value": last,
            "change_pct": (last / prev - 1) * 100 if last and prev else None}


# ---------- расчёты ----------
def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def pct(a, b):
    return (a / b - 1) * 100 if a and b else None


def ret_n(rows, n):
    return pct(rows[-1]["close"], rows[-1 - n]["close"]) if len(rows) > n else None


def ret_days(rows, days):
    target = rows[-1]["date"] - dt.timedelta(days=days)
    base = next((r["close"] for r in reversed(rows) if r["date"] <= target), None)
    return pct(rows[-1]["close"], base)


def day_metrics(rows):
    if len(rows) < 2:
        return None
    last, prev = rows[-1], rows[-2]
    closes = [r["close"] for r in rows]
    lr = [math.log(closes[i] / closes[i - 1]) for i in range(max(1, len(closes) - 61), len(closes) - 1)]
    sigma = (sum((x - sum(lr) / len(lr)) ** 2 for x in lr) / (len(lr) - 1)) ** 0.5 if len(lr) > 5 else None
    vals20 = [r["value"] for r in rows[-21:-1] if r.get("value")]
    avg20 = sum(vals20) / len(vals20) if vals20 else None
    year = [r for r in rows[:-1] if r["date"] >= last["date"] - dt.timedelta(days=365)]
    hi = max((r["close"] for r in year), default=None)
    lo = min((r["close"] for r in year), default=None)
    s200, s200p = sma(closes, 200), sma(closes[:-1], 200)
    rng = (last["high"] - last["low"]) if last.get("high") and last.get("low") else None
    prev_year = [r for r in rows if r["date"].year < last["date"].year]
    return {
        "date": last["date"].isoformat(), "live": last["live"], "close": last["close"], "prev_close": prev["close"],
        "day": pct(last["close"], prev["close"]),
        "sigma_day": sigma * 100 if sigma else None,
        "move_sigma": (math.log(last["close"] / prev["close"]) / sigma) if sigma else None,
        "gap": pct(last.get("open"), prev["close"]) if last.get("open") else None,
        "open": last.get("open"), "high": last.get("high"), "low": last.get("low"),
        "range_pos": ((last["close"] - last["low"]) / rng * 100) if rng else None,
        "value": last.get("value"), "avg_value_20d": avg20,
        "value_x": (last["value"] / avg20) if last.get("value") and avg20 else None,
        "ret_5d": ret_n(rows, 5), "ret_1m": ret_days(rows, 30), "ret_3m": ret_days(rows, 91),
        "ytd": pct(last["close"], prev_year[-1]["close"]) if prev_year else None,
        "high_52w": hi, "low_52w": lo, "from_high_52w": pct(last["close"], hi),
        "new_high_52w": bool(hi and last["close"] > hi and len(year) > 120),
        "new_low_52w": bool(lo and last["close"] < lo and len(year) > 120),
        "sma50": sma(closes, 50), "sma200": s200,
        "vs_sma200": pct(last["close"], s200),
        "cross_200": ("вверх" if s200 and s200p and prev["close"] < s200p and last["close"] >= s200 else
                      "вниз" if s200 and s200p and prev["close"] > s200p and last["close"] <= s200 else None),
    }


def prev_weekday(d):
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def upcoming_divs(secid):
    from moex_share import dividends
    out = []
    for x in dividends(secid):
        d = date(x["date"])
        if d and dt.date.today() <= d <= dt.date.today() + dt.timedelta(days=DIV_DAYS):
            out.append({**x, "last_buy_day": prev_weekday(d).isoformat()})  # T+1: купить не позже рабочего дня до отсечки
    return out


def cur(c):
    return "₽" if c in (None, "", "SUR", "RUB") else c


def flags_for(m, imoex_day, pos, weight, divs):
    f = []
    if m["day"] is not None and (abs(m["day"]) >= MOVE_PCT or (m["move_sigma"] and abs(m["move_sigma"]) >= MOVE_SIGMA)):
        f.append(f"сильное движение {m['day']:+.1f}%" + (f" ({m['move_sigma']:+.1f}σ)" if m["move_sigma"] else ""))
    if m["day"] is not None and imoex_day is not None and abs(m["day"] - imoex_day) >= REL_PP:
        f.append(f"{'лучше' if m['day'] > imoex_day else 'хуже'} рынка на {abs(m['day'] - imoex_day):.1f} п.п.")
    if m["gap"] is not None and abs(m["gap"]) >= GAP_PCT:
        f.append(f"гэп {m['gap']:+.1f}%")
    if m["value_x"] and m["value_x"] >= VOLUME_X:
        f.append(f"оборот ×{m['value_x']:.1f} к среднему")
    if m["new_high_52w"]:
        f.append("новый 52-нед. максимум")
    if m["new_low_52w"]:
        f.append("новый 52-нед. минимум")
    if m["cross_200"]:
        f.append(f"пересечение 200-дн. средней {m['cross_200']}")
    for d in divs:
        f.append(f"отсечка {d['date']} ({d['value']} {cur(d.get('currency'))}, купить до {d['last_buy_day']} вкл.)")
    if weight is not None and weight >= CONC_WEIGHT:
        f.append(f"концентрация {weight:.0f}% портфеля")
    if pos.get("avg") and m["close"] and pct(m["close"], pos["avg"]) <= -LOSS_FROM_AVG:
        f.append(f"ниже средней цены покупки на {abs(pct(m['close'], pos['avg'])):.0f}%")
    if pos.get("target") is not None and weight is not None and abs(weight - pos["target"]) >= 5:
        f.append(f"доля {weight:.0f}% против целевой {pos['target']:.0f}%")
    return f


def thesis_flags(s, m, wl):
    la = (wl.get(s) or {}).get("last_analysis") or {}
    f = []
    if not la.get("date"):
        f.append("комитет ещё не разбирал")
    else:
        days = (dt.date.today() - date(la["date"])).days
        if days > 90:
            f.append(f"разбор устарел ({days} дн.)")
        chg = pct(m["close"], la.get("price"))
        if chg is not None and abs(chg) >= 15:
            f.append(f"цена {chg:+.0f}% с разбора {la['date']}")
        if la.get("target") and m["close"] >= la["target"]:
            f.append("достигнута справедливая стоимость комитета")
    return la, f


def collect(pf, days=400):
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    wl = load_watchlist()["tickers"]
    imoex = index_series("IMOEX", since)
    im = day_metrics(imoex) or {}
    data = {}
    for s, pos in sorted(pf["positions"].items()):
        try:
            rows, meta = series_for(s, pos.get("board") or "TQBR", since)
        except SystemExit as e:
            data[s] = {"error": str(e)}
            continue
        m = day_metrics(rows)
        if not m:
            data[s] = {"error": "нет истории торгов"}
            continue
        data[s] = {"rows": rows, "m": m, "meta": meta, "pos": pos}
    return wl, imoex, im, data


def weigh(data):
    ok = {s: x for s, x in data.items() if "m" in x}
    mode = "qty" if ok and all(x["pos"].get("qty") for x in ok.values()) else "equal"
    for s, x in ok.items():
        q = x["pos"].get("qty")
        x["value"] = q * x["m"]["close"] if q else None
        x["prev_value"] = q * x["m"]["prev_close"] if q else None
    if mode == "qty":
        tot, totp = sum(x["value"] for x in ok.values()), sum(x["prev_value"] for x in ok.values())
        for x in ok.values():
            x["weight"], x["w_prev"] = x["value"] / tot * 100, x["prev_value"] / totp
    else:
        for x in ok.values():
            x["weight"], x["w_prev"] = 100 / len(ok), 1 / len(ok)
    return ok, mode


# ---------- session ----------
def cmd_session(a):
    pf = load_pf()
    need_positions(pf)
    wl, imoex, im, data = collect(pf)
    ok, mode = weigh(data)
    sd = max((x["m"]["date"] for x in ok.values()), default=None)
    if sd and im.get("date") != sd:  # сравниваем с индексом за тот же день, что и бумаги
        cut = [r for r in imoex if r["date"].isoformat() <= sd]
        im = day_metrics(cut) or im
    port_day = sum(x["w_prev"] * (x["m"]["day"] or 0) for x in ok.values())
    rep = {"generated": dt.datetime.now().isoformat(timespec="minutes"), "source": "MOEX ISS",
           "session_date": sd,
           "weighting": "по стоимости позиций" if mode == "qty" else "равные веса (количество не задано)",
           "imoex": {k: im.get(k) for k in ("date", "close", "day", "ret_5d", "ret_1m", "ytd", "live")},
           "portfolio": {"day": port_day, "vs_imoex_pp": port_day - im["day"] if im.get("day") is not None else None},
           "positions": {}, "errors": {s: x["error"] for s, x in data.items() if "error" in x}}
    if mode == "qty":
        tv = sum(x["value"] for x in ok.values())
        rep["portfolio"].update({"value": tv, "pnl_day": tv - sum(x["prev_value"] for x in ok.values())})
        cost = [x["pos"]["qty"] * x["pos"]["avg"] for x in ok.values() if x["pos"].get("avg")]
        if len(cost) == len(ok):
            rep["portfolio"].update({"cost": sum(cost), "pnl_total": tv - sum(cost), "pnl_total_pct": pct(tv, sum(cost))})
    ws = [x["weight"] / 100 for x in ok.values()]
    rep["portfolio"]["effective_n"] = 1 / sum(w * w for w in ws) if ws else None
    rep["portfolio"]["top3_weight"] = sum(sorted((x["weight"] for x in ok.values()), reverse=True)[:3])
    sectors = {}
    for x in ok.values():
        sectors[x["pos"].get("sector") or "не указан"] = sectors.get(x["pos"].get("sector") or "не указан", 0) + x["weight"]
    rep["portfolio"]["sectors"] = dict(sorted(sectors.items(), key=lambda kv: -kv[1]))
    for s, x in ok.items():
        m, pos = x["m"], x["pos"]
        divs = upcoming_divs(s)
        la, tf = thesis_flags(s, m, wl)
        flags = flags_for(m, im.get("day"), pos, x["weight"] if mode == "qty" else None, divs)
        p = {"name": pos.get("name"), "metrics": m, "meta": x["meta"], "weight": x["weight"],
             "contribution_pp": x["w_prev"] * (m["day"] or 0), "dividends": divs, "flags": flags,
             "thesis": {"verdict": la.get("verdict"), "date": la.get("date"), "target": la.get("target"), "flags": tf},
             "folder": ticker_dir(s, create=False)}
        if pos.get("qty"):
            p.update({"qty": pos["qty"], "value": x["value"], "pnl_day": x["value"] - x["prev_value"]})
            if pos.get("avg"):
                p.update({"avg": pos["avg"], "pnl_total": (m["close"] - pos["avg"]) * pos["qty"],
                          "pnl_total_pct": pct(m["close"], pos["avg"])})
        rep["positions"][s] = p
    rep["market"] = market_board() if not a.no_market else None
    if a.save:
        save_session(rep, ok)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        return
    print_session(rep, a.alerts)


def save_session(rep, ok):
    d = rep["session_date"] or today()
    p = os.path.join(pf_dir("daily"), f"{d}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    rep["saved_to"] = p
    # история портфеля (одна строка на дату)
    hp = os.path.join(pf_dir(), "history.csv")
    hist = {}
    if os.path.exists(hp):
        with open(hp, encoding="utf-8") as f:
            hist = {r["date"]: r for r in csv.DictReader(f)}
    hist[d] = {"date": d, "value": round(rep["portfolio"]["value"], 2) if rep["portfolio"].get("value") else "", "day_pct": round(rep["portfolio"]["day"], 4),
               "imoex_day_pct": round(rep["imoex"]["day"], 4) if rep["imoex"].get("day") is not None else "",
               "positions": len(ok)}
    with open(hp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "value", "day_pct", "imoex_day_pct", "positions"])
        w.writeheader()
        for k in sorted(hist):
            w.writerow(hist[k])
    # официальные дневные цены — в папки акций
    for s, x in ok.items():
        upsert_prices(s, [r for r in x["rows"][-30:] if not r["live"]])


def upsert_prices(secid, rows):
    p = os.path.join(ticker_dir(secid, "market"), "prices.csv")
    cols = ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "VALUE"]
    have = {}
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            have = {r["TRADEDATE"]: r for r in csv.DictReader(f)}
    for r in rows:
        have[r["date"].isoformat()] = dict(zip(cols, [r["date"].isoformat(), r["open"], r["high"], r["low"],
                                                      r["close"], r["volume"], r["value"]]))
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for k in sorted(have):
            w.writerow(have[k])


def f_(x, n=1, sign=False):
    if x is None:
        return "—"
    s = f"{x:+,.{n}f}" if sign else f"{x:,.{n}f}"
    return s.replace(",", " ")


def rub(x):
    if x is None:
        return "—"
    if abs(x) >= 1e9:
        return f"{x/1e9:,.2f} млрд ₽".replace(",", " ")
    if abs(x) >= 1e6:
        return f"{x/1e6:,.1f} млн ₽".replace(",", " ")
    return f"{x:,.0f} ₽".replace(",", " ")


def print_session(rep, alerts_only=False):
    im, pt = rep["imoex"], rep["portfolio"]
    live = " (сессия идёт, данные предварительные)" if any(p["metrics"]["live"] for p in rep["positions"].values()) else ""
    print(f"=== Торговая сессия {rep['session_date']}{live} | {rep['source']}, сформировано {rep['generated']} ===")
    line = f"Портфель {f_(pt['day'], 2, True)}% за день | IMOEX {f_(im.get('day'), 2, True)}% ({f_(im.get('close'), 2)})"
    if pt.get("vs_imoex_pp") is not None:
        line += f" | против индекса {f_(pt['vs_imoex_pp'], 2, True)} п.п."
    print(line + f" | веса: {rep['weighting']}")
    if pt.get("value"):
        print(f"Стоимость {rub(pt['value'])} | за день {rub(pt['pnl_day'])}"
              + (f" | всего {rub(pt['pnl_total'])} ({f_(pt['pnl_total_pct'], 1, True)}%)" if pt.get("pnl_total") is not None else ""))
    print(f"Диверсификация: эффективное число бумаг {f_(pt['effective_n'])}, топ-3 = {f_(pt['top3_weight'], 0)}% | сектора: "
          + ", ".join(f"{k} {v:.0f}%" for k, v in pt["sectors"].items()))
    pos = sorted(rep["positions"].items(), key=lambda kv: -(kv[1]["contribution_pp"]))
    if not alerts_only:
        print("\nТикер   Цена        День     vs IMOEX  Гэп     Оборот/ср20  Позиция в дне  5д      1м      От 52н макс  Доля   Вклад, п.п.")
        for s, p in pos:
            m = p["metrics"]
            rel = m["day"] - im["day"] if m["day"] is not None and im.get("day") is not None else None
            print(f"{s:<7} {f_(m['close'], 2):>10}  {f_(m['day'], 2, True):>6}%  {f_(rel, 1, True):>6}    {f_(m['gap'], 1, True):>5}%  "
                  f"{('×' + f_(m['value_x'], 1)) if m['value_x'] else '—':>10}  {f_(m['range_pos'], 0):>9}%     "
                  f"{f_(m['ret_5d'], 1, True):>6}% {f_(m['ret_1m'], 1, True):>6}%  {f_(m['from_high_52w'], 1, True):>9}%  "
                  f"{f_(p['weight'], 1):>5}%  {f_(p['contribution_pp'], 2, True):>6}")
    print("\nСигналы:")
    any_flag = False
    for s, p in pos:
        fl = p["flags"] + [f"тезис: {t}" for t in p["thesis"]["flags"]]
        if fl:
            any_flag = True
            v = p["thesis"]
            print(f"  ⚑ {s}: " + "; ".join(fl) + (f" | вердикт комитета: {v['verdict']} ({v['date']})" if v.get("verdict") else ""))
    if not any_flag:
        print("  нет — обычный день по всем бумагам")
    for s, e in rep["errors"].items():
        print(f"  ! {s}: {e}")
    mk = rep.get("market")
    if mk and not alerts_only:
        print("\nФон рынка:")
        if mk["indices"]:
            print("  " + " | ".join(f"{x['secid']} ({x['name']}) {f_(x['value'], 2)} {f_(x['change_pct'], 2, True)}%" for x in mk["indices"]))
        for x in mk["fx"] + mk["commodities"]:
            print(f"  {x['name']}: {f_(x['value'], 4 if x['value'] and x['value'] < 20 else 2)} ({f_(x['change_pct'], 2, True)}%)")
        for e in mk["errors"]:
            print(f"  ! {e}")
    if rep.get("saved_to"):
        print(f"\nСохранено: {rep['saved_to']}")
    print(f"Брифинг сохранить в: {os.path.join(pf_dir('briefs'), (rep['session_date'] or today()) + '.md')}")


# ---------- review ----------
def daily_returns(rows):
    return {rows[i]["date"]: rows[i]["close"] / rows[i - 1]["close"] - 1 for i in range(1, len(rows))}


def corr(a, b):
    n = len(a)
    if n < 30:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va and vb else None


def cmd_review(a):
    pf = load_pf()
    need_positions(pf)
    wl, imoex, im, data = collect(pf)
    ok, mode = weigh(data)
    rets = {s: daily_returns(x["rows"]) for s, x in ok.items()}
    iret = daily_returns(imoex)
    last_year = dt.date.today() - dt.timedelta(days=365)
    dates = sorted(d for d in iret if d >= last_year and all(d in r for r in rets.values()))
    w = {s: x["weight"] / 100 for s, x in ok.items()}  # текущие веса, «как если бы держали весь период»
    port = [sum(w[s] * rets[s][d] for s in ok) for d in dates]
    ind = [iret[d] for d in dates]

    def cum(seq):
        v = 1.0
        for r in seq:
            v *= 1 + r
        return (v - 1) * 100

    def window(days):
        idx = [i for i, d in enumerate(dates) if d > dates[-1] - dt.timedelta(days=days)] if dates else []
        return (cum([port[i] for i in idx]), cum([ind[i] for i in idx])) if idx else (None, None)

    periods = {"неделя": 7, "месяц": 30, "3 месяца": 91, "год": 365}
    perf = {k: window(v) for k, v in periods.items()}
    ytd_idx = [i for i, d in enumerate(dates) if d.year == dt.date.today().year]
    perf["с начала года"] = (cum([port[i] for i in ytd_idx]), cum([ind[i] for i in ytd_idx])) if ytd_idx else (None, None)
    vol = (sum((x - sum(port) / len(port)) ** 2 for x in port) / (len(port) - 1)) ** 0.5 * math.sqrt(252) * 100 if len(port) > 30 else None
    beta = None
    if len(port) > 30:
        mi, mp = sum(ind) / len(ind), sum(port) / len(port)
        var_i = sum((x - mi) ** 2 for x in ind)
        beta = sum((x - mi) * (y - mp) for x, y in zip(ind, port)) / var_i if var_i else None
    peak, mdd, v = 1.0, 0.0, 1.0
    for r in port:
        v *= 1 + r
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    pairs = []
    names = sorted(ok)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            c = corr([rets[names[i]][d] for d in dates], [rets[names[j]][d] for d in dates])
            if c is not None:
                pairs.append((names[i], names[j], c))
    pairs.sort(key=lambda t: -t[2])
    pos_rows = []
    for s, x in ok.items():
        m = x["m"]
        la, tf = thesis_flags(s, m, wl)
        pos_rows.append({"ticker": s, "weight": x["weight"], "target": x["pos"].get("target"),
                         "ret_5d": m["ret_5d"], "ret_1m": m["ret_1m"], "ytd": m["ytd"],
                         "contrib_week_pp": w[s] * (m["ret_5d"] or 0), "verdict": la.get("verdict"),
                         "analysis_date": la.get("date"), "thesis_flags": tf, "dividends": upcoming_divs(s)})
    pos_rows.sort(key=lambda r: -r["contrib_week_pp"])
    rep = {"generated": dt.datetime.now().isoformat(timespec="minutes"), "weighting": mode, "days_in_sample": len(dates),
           "performance": {k: {"portfolio": p, "imoex": i} for k, (p, i) in perf.items()},
           "risk": {"vol_1y": vol, "beta": beta, "max_drawdown_1y": mdd * 100,
                    "effective_n": 1 / sum(v * v for v in w.values()) if w else None,
                    "high_corr_pairs": [p for p in pairs if p[2] >= CORR_HIGH][:10]},
           "positions": pos_rows, "errors": {s: x["error"] for s, x in data.items() if "error" in x}}
    if a.save:
        p = os.path.join(pf_dir("reviews"), f"{today()}-data.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
        rep["saved_to"] = p
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        return
    print(f"=== Обзор портфеля на {today()} (текущий состав и веса, MOEX ISS; {len(dates)} общих торговых дней) ===")
    print("Доходность:   " + " | ".join(f"{k}: {f_(p, 1, True)}% (IMOEX {f_(i, 1, True)}%)" for k, (p, i) in perf.items()))
    print(f"Риск: волатильность {f_(vol)}% год. | бета к IMOEX {f_(beta, 2)} | макс. просадка за год {f_(mdd * 100, 1)}% | "
          f"эффективное число бумаг {f_(rep['risk']['effective_n'])}")
    if rep["risk"]["high_corr_pairs"]:
        print("Сильно связанные пары (один и тот же риск): " + ", ".join(f"{a_}–{b_} {c:.2f}" for a_, b_, c in rep["risk"]["high_corr_pairs"]))
    print("\nТикер   Доля   Цель   Неделя   Месяц    YTD      Вклад нед., п.п.  Вердикт (дата)              Тезис")
    for r in pos_rows:
        print(f"{r['ticker']:<7} {f_(r['weight'], 1):>5}% {f_(r['target'], 0):>5}  {f_(r['ret_5d'], 1, True):>6}% "
              f"{f_(r['ret_1m'], 1, True):>6}% {f_(r['ytd'], 1, True):>6}%  {f_(r['contrib_week_pp'], 2, True):>8}          "
              f"{(r['verdict'] or '—') + ' (' + (r['analysis_date'] or '—') + ')':<27} {'; '.join(r['thesis_flags']) or 'в порядке'}")
    cal = [(r["ticker"], d) for r in pos_rows for d in r["dividends"]]
    if cal:
        print("\nОтсечки в ближайшие 30 дней: " + "; ".join(f"{t} {d['date']} ({d['value']} {cur(d.get('currency'))}, купить до {d['last_buy_day']})" for t, d in sorted(cal, key=lambda t: t[1]["date"])))
    for s, e in rep["errors"].items():
        print(f"  ! {s}: {e}")
    if rep.get("saved_to"):
        print(f"\nСохранено: {rep['saved_to']}")
    print(f"Обзор сохранить в: {os.path.join(pf_dir('reviews'), today() + '.md')}")


# ---------- list / log / path ----------
def cmd_list(a):
    pf = load_pf()
    pos = need_positions(pf)
    print(f"Портфель: {len(pos)} шт., файл {pf_path()}")
    if a.offline:
        for s, p in sorted(pos.items()):
            print(f"  {s:<7} {p.get('name') or ''} | кол-во {f_(p.get('qty'), 0)} | средняя {f_(p.get('avg'), 2)} | "
                  f"сектор {p.get('sector') or '—'} | цель {f_(p.get('target'), 0)}%")
        return
    from moex_share import quote
    rows, total = [], 0
    for s, p in sorted(pos.items()):
        q = quote(s, p.get("board") or "TQBR")
        val = p["qty"] * q["price"] if p.get("qty") and q["price"] else None
        total += val or 0
        rows.append((s, p, q, val))
    for s, p, q, val in rows:
        pnl = (q["price"] - p["avg"]) * p["qty"] if p.get("avg") and p.get("qty") and q["price"] else None
        print(f"  {s:<7} {f_(q['price'], 2):>10} ₽ ({f_(q['change_pct'], 2, True)}%) | кол-во {f_(p.get('qty'), 0)} | "
              f"средняя {f_(p.get('avg'), 2)} | стоимость {rub(val)}" + (f" ({val / total * 100:.1f}%)" if val and total else "")
              + (f" | P&L {rub(pnl)} ({f_(pct(q['price'], p['avg']), 1, True)}%)" if pnl is not None else ""))
    if total:
        print(f"Итого: {rub(total)}")


def cmd_log(a):
    pf_journal(a.text, a.source or "")
    print(f"Записано: {os.path.join(pf_dir(), 'journal.md')}")


def cmd_path(a):
    print(f"Портфель: {pf_path()}\nПапка офиса: {pf_dir()}\n  брифинги: {pf_dir('briefs')}\n  обзоры: {pf_dir('reviews')}\n"
          f"  данные сессий: {pf_dir('daily')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("set", help="задать портфель (заменяет)"); p.add_argument("items", nargs="+"); p.set_defaults(fn=cmd_set)  # noqa: E702
    p = sp.add_parser("add", help="добавить бумаги"); p.add_argument("items", nargs="+")  # noqa: E702
    p.add_argument("--sector"); p.add_argument("--target", type=float); p.add_argument("--note"); p.set_defaults(fn=cmd_add)  # noqa: E702
    p = sp.add_parser("update"); p.add_argument("ticker"); p.add_argument("--qty", type=float); p.add_argument("--avg", type=float)  # noqa: E702
    p.add_argument("--sector"); p.add_argument("--target", type=float); p.add_argument("--note"); p.set_defaults(fn=cmd_update)  # noqa: E702
    p = sp.add_parser("trade"); p.add_argument("ticker"); p.add_argument("side", choices=["buy", "sell"])  # noqa: E702
    p.add_argument("qty", type=float); p.add_argument("price", type=float, nargs="?"); p.set_defaults(fn=cmd_trade)  # noqa: E702
    p = sp.add_parser("remove"); p.add_argument("tickers", nargs="+"); p.set_defaults(fn=cmd_remove)  # noqa: E702
    p = sp.add_parser("import"); p.add_argument("file"); p.add_argument("--encoding", default="utf-8-sig")  # noqa: E702
    p.add_argument("--merge", action="store_true", help="добавить к портфелю, а не заменить")
    p.add_argument("--sector"); p.add_argument("--target", type=float); p.add_argument("--note"); p.set_defaults(fn=cmd_import)  # noqa: E702
    p = sp.add_parser("list"); p.add_argument("--offline", action="store_true"); p.set_defaults(fn=cmd_list)  # noqa: E702
    p = sp.add_parser("session"); p.add_argument("--save", action="store_true"); p.add_argument("--json", action="store_true")  # noqa: E702
    p.add_argument("--alerts", action="store_true", help="только сигналы"); p.add_argument("--no-market", action="store_true")  # noqa: E702
    p.set_defaults(fn=cmd_session)
    p = sp.add_parser("review"); p.add_argument("--save", action="store_true"); p.add_argument("--json", action="store_true")  # noqa: E702
    p.set_defaults(fn=cmd_review)
    p = sp.add_parser("log"); p.add_argument("text"); p.add_argument("--source"); p.set_defaults(fn=cmd_log)  # noqa: E702
    p = sp.add_parser("path"); p.set_defaults(fn=cmd_path)  # noqa: E702
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
