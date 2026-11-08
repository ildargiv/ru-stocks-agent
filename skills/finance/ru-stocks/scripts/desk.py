"""Список наблюдения и папки акций («рабочий стол» аналитика).

  python desk.py add SBER [--note "зачем следим"]   # начать следить: папка + профиль + запись в список
  python desk.py list                               # что под наблюдением
  python desk.py status [SBER ...]                  # что изменилось с последнего разбора (цена, новости, дивиденды)
  python desk.py show SBER                          # содержимое папки и последние записи хронологии
  python desk.py analysed SBER --verdict "Держать" --score 62 [--target 340]   # отметить завершённый разбор
  python desk.py log SBER "Совет директоров рекомендовал дивиденд 34,84 ₽" --source "e-disclosure, 25.03"
  python desk.py remove SBER [--archive]            # перестать следить (папка сохраняется)
  python desk.py path [SBER]                        # путь к хранилищу / папке акции

Хранилище: ~/stocks-desk (или переменная окружения STOCKS_DESK). Структура — references/desk-structure.md.
"""
import argparse
import datetime as dt
import json
import os
import sys

from _iss import (resolve, load_watchlist, save_watchlist, ticker_dir, desk_root, append_journal,
                  watched_or_all, today, date)
import wiki  # база знаний Obsidian: хаб-заметки, index, log (references/wiki.md)

SUBDIRS = ("market", "financials", "reports", "news", "forums", "analyses")

PROFILE = """---
type: profile
entity: {secid}
up: "[[{secid}/{secid}|{secid}]]"
tags: [desk/stocks, type/profile]
created: {date}
updated: {date}
---
# {secid} — {emitent}

> Профиль компании. Создан {date}; агент дополняет его при первом глубоком разборе и обновляет при существенных изменениях.

| Поле | Значение |
|---|---|
| Тикер / ISIN | {secid} / {isin} |
| Эмитент / ИНН | {emitent} / {inn} |
| Режим торгов | {board} |
| Отрасль | _заполнить_ |
| Сайт для инвесторов | _заполнить_ |
| Раскрытие | https://www.e-disclosure.ru (поиск по ИНН {inn}) |
| Карточка на бирже | https://www.moex.com/ru/issue.aspx?code={secid} |
| Smart-Lab | https://smart-lab.ru/q/{secid}/ |
| Зачем следим | {note} |

## Бизнес в двух абзацах
_Чем зарабатывает, сегменты, доли выручки, конкуренты, драйверы._

## Акционеры и корпоративное управление
_Основной акционер, free-float, санкции, квазиказначейские акции, дивидендная политика (формула), байбэки, допэмиссии._

## Ключевые метрики, за которыми следим
_3–6 показателей, которые реально двигают стоимость этой компании (например: NIM и стоимость риска для банка; цена Urals и курс для нефтяника)._

## Календарь
_Даты отчётов, ГОСА, СД по дивидендам, Investor Day._
"""

THESIS = """---
type: thesis
entity: {secid}
up: "[[{secid}/{secid}|{secid}]]"
verdict:
target:
tags: [desk/stocks, type/thesis]
created: {date}
updated: {date}
---
# Инвестиционный тезис: {secid}

> Живой документ. Обновляется после каждого заседания инвесткомитета. Старые версии — в analyses/.

**Статус:** разбор ещё не проводился.

## Тезис (почему это может быть интересно)
## Антитезис (что сломает идею — от адвоката дьявола)
## Оценка и диапазон справедливой стоимости
## Триггеры пересмотра (что должно произойти, чтобы мнение изменилось)
## Что проверить в следующем отчёте
"""


def cmd_add(a):
    wl = load_watchlist()
    for q in a.tickers:
        info = resolve(q)
        secid = info["secid"]
        if secid in wl["tickers"]:
            print(f"{secid} уже под наблюдением: {ticker_dir(secid)}")
            continue
        root = ticker_dir(secid)
        for s in SUBDIRS:
            os.makedirs(os.path.join(root, s), exist_ok=True)
        fields = {**info, "date": today(), "note": a.note or "_заполнить_"}
        for name, tpl in (("README.md", PROFILE), ("thesis.md", THESIS)):
            p = os.path.join(root, name)
            if not os.path.exists(p):
                with open(p, "w", encoding="utf-8") as f:
                    f.write(tpl.format(**fields))
        if not os.path.exists(os.path.join(root, "journal.md")):
            with open(os.path.join(root, "journal.md"), "w", encoding="utf-8") as f:
                f.write(f"---\ntype: journal\nentity: {secid}\nup: \"[[{secid}/{secid}|{secid}]]\"\n"
                        f"tags: [desk/stocks, type/journal]\n---\n# Хронология: {secid}\n\n")
        append_journal(secid, "Начато наблюдение" + (f": {a.note}" if a.note else ""))
        wl["tickers"][secid] = {**{k: info[k] for k in ("isin", "name", "emitent", "inn", "board")},
                                "added": today(), "note": a.note, "last_analysis": None}
        save_watchlist(wl)
        wiki.safe_log(f"Начато наблюдение: {info['emitent']}" + (f" ({a.note})" if a.note else ""), secid, "источник",
                      "MOEX ISS")
        wiki.safe_sync([secid])
        print(f"✅ {secid} ({info['emitent']}, ИНН {info['inn']}) — под наблюдением. Папка: {root}")
        print("   Дальше: первичный глубокий разбор (SKILL.md, процедуры 0 и A).")


def cmd_list(a):
    wl = load_watchlist()["tickers"]
    if not wl:
        print("Список наблюдения пуст.")
        return
    print(f"Хранилище: {desk_root()}")
    for s, x in sorted(wl.items()):
        la = x.get("last_analysis") or {}
        print(f"  {s:<6} {x.get('emitent') or x.get('name')} | с {x['added']} | "
              f"разбор: {la.get('date', 'не было')} {la.get('verdict', '')} {('скоринг ' + str(la['score'])) if la.get('score') is not None else ''}"
              + (f" | {x['note']}" if x.get("note") else ""))


def count_news_since(secid, since):
    p = os.path.join(ticker_dir(secid, create=False), "news", "news.jsonl")
    if not os.path.exists(p):
        return 0, 0
    total = new = 0
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                n = json.loads(line)
            except ValueError:
                continue
            total += 1
            if not since or (n.get("first_seen") or "") > since:
                new += 1
    return total, new


def cmd_status(a):
    from moex_share import quote, dividends as _divs  # noqa: локальный импорт, чтобы list/add не ходили в сеть лишний раз
    wl = load_watchlist()["tickers"]
    rows = []
    for s in watched_or_all(a.tickers):
        x = wl.get(s)
        if not x:
            print(f"{s}: не под наблюдением (python desk.py add {s})")
            continue
        q = quote(s, x.get("board") or "TQBR")
        la = x.get("last_analysis") or {}
        since = la.get("date")
        chg = (q["price"] / la["price"] - 1) * 100 if la.get("price") and q["price"] else None
        total, new = count_news_since(s, since)
        soon = [d for d in _divs(s)[0] if date(d["date"]) and dt.date.today() <= date(d["date"]) <= dt.date.today() + dt.timedelta(days=60)]
        days = (dt.date.today() - date(since)).days if since else None
        flags = []
        if days is None:
            flags.append("нужен первичный разбор")
        elif days > 90:
            flags.append(f"разбор устарел ({days} дн.)")
        if chg is not None and abs(chg) >= 10:
            flags.append(f"цена {chg:+.1f}% с разбора")
        if new:
            flags.append(f"{new} новых новостей")
        if soon:
            flags.append("отсечка " + ", ".join(f"{d['date']} ({d['value']} ₽)" for d in soon))
        if la.get("target") and q["price"] and q["price"] >= la["target"]:
            flags.append("достигнута оценка справедливой стоимости")
        rows.append((s, q, la, chg, flags))
    for s, q, la, chg, flags in rows:
        print(f"{s:<6} {q['price'] or '—'} ₽ ({q['change_pct'] or 0:+.2f}% день)"
              + (f" | с разбора {la['date']}: {chg:+.1f}%" if chg is not None else "")
              + (f" | вердикт: {la.get('verdict')}" if la.get("verdict") else "")
              + (" | ⚑ " + "; ".join(flags) if flags else " | без изменений"))


def cmd_show(a):
    s = a.ticker.upper()
    root = ticker_dir(s, create=False)
    if not os.path.isdir(root):
        raise SystemExit(f"Папки {root} нет. Сначала: python desk.py add {s}")
    print(f"Папка: {root}")
    for dirpath, _, files in sorted(os.walk(root)):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if rel != ".":
            print("  " * depth + f"{os.path.basename(dirpath)}/")
        for fn in sorted(files)[-12:]:
            print("  " * (depth + 1) + fn)
    j = os.path.join(root, "journal.md")
    if os.path.exists(j):
        with open(j, encoding="utf-8") as f:
            lines = [ln.rstrip() for ln in f if ln.startswith("- ")]
        print("\nПоследние записи хронологии:")
        print("\n".join(lines[-15:]))


def cmd_analysed(a):
    from moex_share import quote
    wl = load_watchlist()
    s = a.ticker.upper()
    if s not in wl["tickers"]:
        raise SystemExit(f"{s} не под наблюдением")
    q = quote(s, wl["tickers"][s].get("board") or "TQBR")
    wl["tickers"][s]["last_analysis"] = {"date": today(), "price": q["price"], "verdict": a.verdict,
                                         "score": a.score, "target": a.target}
    save_watchlist(wl)
    adir = ticker_dir(s, "analyses")
    proto = a.protocol
    if not proto:
        files = sorted(f for f in os.listdir(adir) if f.endswith(".md"))
        proto = files[-1] if files else None
    if proto and not os.path.exists(os.path.join(adir, proto)):
        print(f"⚠ протокол analyses/{proto} не найден — ссылка в журнале будет без файла", file=sys.stderr)
        proto = None
    append_journal(s, f"Инвесткомитет: {a.verdict}" + (f", скоринг {a.score}" if a.score is not None else "")
                   + (f", справедливая стоимость {a.target} ₽" if a.target else "") + f" при цене {q['price']} ₽",
                   f"analyses/{proto}" if proto else "протокол не сохранён")
    wiki.safe_log(f"Инвесткомитет: {a.verdict}" + (f", скоринг {a.score}" if a.score is not None else "")
                  + (f", справедливая стоимость {a.target} ₽" if a.target else ""), s, "вывод",
                  f"analyses/{proto}" if proto else "")
    wiki.safe_sync([s])
    print(f"Записано: {s} {a.verdict} @ {q['price']} ₽")


def cmd_log(a):
    append_journal(a.ticker.upper(), a.text, a.source or "")
    wiki.safe_sync([a.ticker.upper()])
    print("Записано в journal.md")


def cmd_remove(a):
    wl = load_watchlist()
    s = a.ticker.upper()
    if wl["tickers"].pop(s, None) is None:
        raise SystemExit(f"{s} не под наблюдением")
    save_watchlist(wl)
    append_journal(s, "Наблюдение прекращено")
    root = ticker_dir(s, create=False)
    if a.archive and os.path.isdir(root):
        dst = os.path.join(desk_root(), "_archive", s)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(root, dst)
        root = dst
    wiki.safe_sync()
    print(f"{s} снят с наблюдения; данные сохранены в {root}")


def cmd_path(a):
    print(ticker_dir(a.ticker, create=False) if a.ticker else desk_root())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("add"); p.add_argument("tickers", nargs="+"); p.add_argument("--note"); p.set_defaults(fn=cmd_add)  # noqa: E702
    p = sp.add_parser("list"); p.set_defaults(fn=cmd_list)  # noqa: E702
    p = sp.add_parser("status"); p.add_argument("tickers", nargs="*"); p.set_defaults(fn=cmd_status)  # noqa: E702
    p = sp.add_parser("show"); p.add_argument("ticker"); p.set_defaults(fn=cmd_show)  # noqa: E702
    p = sp.add_parser("analysed"); p.add_argument("ticker"); p.add_argument("--verdict", required=True)  # noqa: E702
    p.add_argument("--score", type=int); p.add_argument("--target", type=float)  # noqa: E702
    p.add_argument("--protocol", help="имя файла протокола в analyses/ (по умолчанию — самый свежий)")
    p.set_defaults(fn=cmd_analysed)
    p = sp.add_parser("log"); p.add_argument("ticker"); p.add_argument("text"); p.add_argument("--source")  # noqa: E702
    p.set_defaults(fn=cmd_log)
    p = sp.add_parser("remove"); p.add_argument("ticker"); p.add_argument("--archive", action="store_true")  # noqa: E702
    p.set_defaults(fn=cmd_remove)
    p = sp.add_parser("path"); p.add_argument("ticker", nargs="?"); p.set_defaults(fn=cmd_path)  # noqa: E702
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
