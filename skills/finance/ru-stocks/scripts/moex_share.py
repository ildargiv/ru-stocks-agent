"""Карточка акции с Мосбиржи: цена, капитализация, ликвидность, доходность за периоды против IMOEX,
52-недельный диапазон, волатильность, просадка, история дивидендов и дивдоходность.

  python moex_share.py SBER
  python moex_share.py "Сбербанк" --json
  python moex_share.py SBER --save        # + обновить папку акции: market/prices.csv, dividends.json, snapshots.jsonl
  python moex_share.py SBER --years 5     # глубина истории (по умолчанию 3 года)

Источник: MOEX ISS (бесплатно, без ключа, задержка ~15 мин).
"""
import argparse
import csv
import datetime as dt
import json
import math
import os

from _iss import get, get_paged, table, resolve, num, date, ticker_dir, today


def quote(secid, board="TQBR"):
    d = get(f"/engines/stock/markets/shares/boards/{board}/securities/{secid}.json",
            **{"iss.only": "securities,marketdata"})
    s = (table(d.get("securities")) or [{}])[0]
    m = (table(d.get("marketdata")) or [{}])[0]
    price = num(m.get("LAST")) or num(m.get("LCURRENTPRICE")) or num(m.get("MARKETPRICE")) or num(s.get("PREVPRICE"))
    return {
        "secid": secid, "board": board, "shortname": s.get("SHORTNAME"), "lot": num(s.get("LOTSIZE")),
        "issuesize": num(s.get("ISSUESIZE")), "currency": s.get("CURRENCYID") or "SUR",
        "price": price, "prevprice": num(s.get("PREVPRICE")), "change_pct": num(m.get("LASTTOPREVPRICE")),
        "open": num(m.get("OPEN")), "high": num(m.get("HIGH")), "low": num(m.get("LOW")),
        "bid": num(m.get("BID")), "offer": num(m.get("OFFER")),
        "value_today": num(m.get("VALTODAY")), "cap": num(m.get("ISSUECAPITALIZATION")),
        "time": m.get("UPDATETIME") or m.get("TIME"), "listlevel": s.get("LISTLEVEL"),
    }


def history(secid, board, since, market="shares"):
    rows = get_paged(f"/history/engines/stock/markets/{market}/boards/{board}/securities/{secid}.json",
                     "history", **{"from": since, "history.columns": "TRADEDATE,OPEN,HIGH,LOW,CLOSE,VOLUME,VALUE"})
    return [r for r in rows if num(r.get("CLOSE"))]


def dividends(secid):
    """Дивиденды: MOEX ISS + ручной файл market/dividends_manual.json (дополняет и приоритетнее).
    Возвращает (список, предупреждения). Пустой ответ ISS — НЕ «дивидендов не было», а «источник недоступен»."""
    warns, out = [], []
    try:
        d = get(f"/securities/{secid}/dividends.json")
        out = [{"date": r.get("registryclosedate"), "value": num(r.get("value")),
                "currency": r.get("currencyid"), "source": "MOEX ISS"} for r in table(d.get("dividends"))]
    except SystemExit as e:
        warns.append(f"MOEX ISS dividends недоступен: {e}")
    if not out:
        warns.append("MOEX ISS не вернул дивиденды (эндпоинт бывает пустым). Это НЕ означает, что выплат не было: "
                     "найдите историю в e-disclosure/на сайте компании/dohod.ru и запишите в market/dividends_manual.json "
                     'в формате [{"date": "2025-07-18", "value": 34.84, "currency": "RUB", "source": "URL"}]')
    mp = os.path.join(ticker_dir(secid, "market", create=False), "dividends_manual.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            manual = json.load(f)
        keys = {(m["date"]) for m in manual}
        out = [x for x in out if x["date"] not in keys] + manual
        if manual:
            warns = [w for w in warns if "не вернул" not in w]
    return sorted(out, key=lambda x: str(x["date"])), warns


def ret(closes_by_date, days):
    """Доходность за N календарных дней по ряду [(date, close)]."""
    if not closes_by_date:
        return None
    last_d, last_c = closes_by_date[-1]
    target = last_d - dt.timedelta(days=days)
    base = next((c for d, c in reversed(closes_by_date) if d <= target), None)
    return (last_c / base - 1) * 100 if base else None


def stats(hist):
    series = [(date(r["TRADEDATE"]), num(r["CLOSE"])) for r in hist]
    series = [(d, c) for d, c in series if d and c]
    if len(series) < 2:
        return {}, series
    closes = [c for _, c in series]
    last_d = series[-1][0]
    year = [c for d, c in series if d >= last_d - dt.timedelta(days=365)]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(max(1, len(closes) - 252), len(closes))]
    vol = (sum((x - sum(rets) / len(rets)) ** 2 for x in rets) / (len(rets) - 1)) ** 0.5 * math.sqrt(252) * 100 \
        if len(rets) > 2 else None
    peak, mdd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    prev_year = [c for d, c in series if d.year < last_d.year]
    ytd_base = prev_year[-1] if prev_year else next(c for d, c in series if d.year == last_d.year)
    vals = [num(r.get("VALUE")) or 0 for r in hist[-20:]]
    return {
        "last_date": last_d.isoformat(), "last_close": closes[-1],
        "ret_1m": ret(series, 30), "ret_3m": ret(series, 91), "ret_6m": ret(series, 182),
        "ret_1y": ret(series, 365), "ret_3y": ret(series, 365 * 3),
        "anomaly": any(abs((ret(series, d) or 0)) > 1000 for d in (365, 365 * 3)),
        "ytd": (closes[-1] / ytd_base - 1) * 100,
        "high_52w": max(year), "low_52w": min(year),
        "from_high_52w": (closes[-1] / max(year) - 1) * 100,
        "vol_1y": vol, "max_drawdown": mdd * 100,
        "avg_value_20d": sum(vals) / len(vals) if vals else None,
    }, series


def div_summary(divs, price):
    today_d = dt.date.today()
    past = [x for x in divs if date(x["date"]) and date(x["date"]) <= today_d and x["value"]]
    future = [x for x in divs if date(x["date"]) and date(x["date"]) > today_d]
    ltm = sum(x["value"] for x in past if date(x["date"]) > today_d - dt.timedelta(days=365))
    by_year = {}
    for x in past:
        by_year[date(x["date"]).year] = by_year.get(date(x["date"]).year, 0) + x["value"]
    return {
        "ltm": ltm, "ltm_yield": ltm / price * 100 if price and ltm else None,
        "by_year": dict(sorted(by_year.items())), "announced": future,
        "years_paid_last5": sum(1 for y in range(today_d.year - 5, today_d.year) if by_year.get(y)),
    }


def save(secid, card, hist, divs):
    mdir = ticker_dir(secid, "market")
    p = os.path.join(mdir, "prices.csv")
    have = {}
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                have[r["TRADEDATE"]] = r
    for r in hist:
        have[r["TRADEDATE"]] = {k: r.get(k) for k in ("TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "VALUE")}
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "VALUE"])
        w.writeheader()
        for k in sorted(have):
            w.writerow(have[k])
    if divs:  # пустой ответ не затирает ранее сохранённую историю
        with open(os.path.join(mdir, "dividends.json"), "w", encoding="utf-8") as f:
            json.dump(divs, f, ensure_ascii=False, indent=1)
    snap = {"date": today(), **{k: card[k] for k in ("price", "cap", "value_today")},
            **{k: card["stats"].get(k) for k in ("ret_1m", "ret_1y", "vol_1y", "from_high_52w")},
            "div_ltm_yield": card["div"].get("ltm_yield")}
    with open(os.path.join(mdir, "snapshots.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    return mdir


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", help="тикер / ISIN / название")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--save", action="store_true", help="сохранить в папку акции")
    a = ap.parse_args()

    info = resolve(a.query)
    secid, board = info["secid"], info["board"]
    q = quote(secid, board)
    since = (dt.date.today() - dt.timedelta(days=365 * a.years + 10)).isoformat()
    hist = history(secid, board, since)
    st, series = stats(hist)
    idx = history("IMOEX", "SNDX", since, market="index")
    ist, _ = stats(idx)
    price = q["price"] or st.get("last_close")
    divs, dwarn = dividends(secid)
    ds = div_summary(divs, price)
    ds["known"] = bool(divs)
    ds["warnings"] = dwarn
    card = {**info, **q, "price": price, "stats": st, "imoex": ist, "div": ds,
            "source": f"MOEX ISS, {today()}", "url": f"https://www.moex.com/ru/issue.aspx?code={secid}"}
    if a.save:
        card["saved_to"] = save(secid, card, hist, divs)
    if a.json:
        print(json.dumps(card, ensure_ascii=False, indent=1, default=str))
        return

    f = lambda x, n=1: "—" if x is None else f"{x:,.{n}f}".replace(",", " ")  # noqa: E731
    bn = lambda x: "—" if not x else f"{x/1e9:,.1f} млрд ₽".replace(",", " ")  # noqa: E731
    print(f"=== {secid} | {info['name']} | {info['emitent']} | ИНН {info['inn']} | {board} | уровень листинга {q['listlevel']} ===")
    print(f"Цена {f(price, 2)} ₽ ({f(q['change_pct'], 2)}% за день, {q['time']}) | лот {f(q['lot'], 0)} | "
          f"bid/ask {f(q['bid'], 2)}/{f(q['offer'], 2)}")
    print(f"Капитализация {bn(q['cap'])} | акций в выпуске {f(q['issuesize'], 0)} | оборот сегодня {bn(q['value_today'])} | "
          f"ср. оборот 20д {bn(st.get('avg_value_20d'))}")
    if st:
        print(f"Доходность: 1м {f(st['ret_1m'])}% | 3м {f(st['ret_3m'])}% | 6м {f(st['ret_6m'])}% | YTD {f(st['ytd'])}% | "
              f"1г {f(st['ret_1y'])}% | 3г {f(st['ret_3y'])}%")
        if ist:
            print(f"  IMOEX:   1м {f(ist['ret_1m'])}% | 3м {f(ist['ret_3m'])}% | 6м {f(ist['ret_6m'])}% | YTD {f(ist['ytd'])}% | "
                  f"1г {f(ist['ret_1y'])}% | 3г {f(ist['ret_3y'])}%")
        print(f"52 нед.: {f(st['low_52w'], 2)} – {f(st['high_52w'], 2)} (от максимума {f(st['from_high_52w'])}%) | "
              f"волатильность 1г {f(st['vol_1y'])}% | макс. просадка за период {f(st['max_drawdown'])}%")
    if ds["known"]:
        print(f"Дивиденды LTM {f(ds['ltm'], 2)} ₽ → доходность {f(ds['ltm_yield'], 2)}% | выплаты в {ds['years_paid_last5']} из 5 последних лет")
    else:
        print("Дивиденды: ДАННЫХ НЕТ (не «ноль»!)")
    for w in ds["warnings"]:
        print(f"  ⚠ {w}")
    for k in ("ret_1y", "ret_3y"):
        if st.get(k) is not None and abs(st[k]) > 1000:
            print(f"  ⚠ {k}: {st[k]:,.0f}% — вероятно сплит/деноминация/смена бумаги; цены не скорректированы, доверять нельзя".replace(",", " "))
    if ds["by_year"]:
        print("  По годам (дата отсечки): " + " | ".join(f"{y}: {f(v, 2)}" for y, v in list(ds["by_year"].items())[-7:]))
    for x in ds["announced"]:
        print(f"  Объявлено/рекомендовано: {x['value']} {x['currency']} — отсечка {x['date']}")
    print(f"Источник: {card['source']} | {card['url']}")
    if a.save:
        print(f"Сохранено: {card['saved_to']}")


if __name__ == "__main__":
    main()
