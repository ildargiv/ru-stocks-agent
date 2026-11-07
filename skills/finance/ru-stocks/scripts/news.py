"""Сбор новостей по акциям из списка наблюдения в их папки (news/news.jsonl, без дублей).

  python news.py                      # все акции из списка, показать только новое с прошлого запуска
  python news.py SBER GAZP            # выбранные
  python news.py SBER --days 30 --all # показать всё за 30 дней, а не только новое
  python news.py SBER --query '"Сбербанк" OR "Сбер" акции'   # свой поисковый запрос (запоминается)
  python news.py SBER --add-feed https://example.ru/rss.xml  # свой RSS (запоминается)

Источники: Google News RSS (агрегатор: Интерфакс, РБК, Коммерсантъ, Ведомости, Frank Media, Smart-Lab и др.)
+ RSS-ленты, добавленные для акции. Скрипт только СОБИРАЕТ заголовки и ссылки; значимые новости агент
обязан открыть и проверить по первоисточнику (e-disclosure, сайт компании, cbr.ru) — см. references/news-forums.md.
"""
import argparse
import datetime as dt
import email.utils
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from _iss import load_watchlist, save_watchlist, ticker_dir, watched_or_all, append_journal

UA = {"User-Agent": "Mozilla/5.0 (ru-stocks-agent)"}
GNEWS = "https://news.google.com/rss/search?{}"

# Слова в заголовке, которые почти всегда означают существенное событие — помечаем ⚑
IMPORTANT = re.compile(
    r"дивиденд|байбэк|buyback|выкуп акц|допэмисс|SPO|IPO|делистинг|санкц|отчет|отчёт|МСФО|РСБУ|прибыл|убыт|"
    r"выручк|EBITDA|прогноз|гайденс|guidance|совет директоров|ГОСА|собрани|сделк|поглощ|M&A|продаж[аи] доли|"
    r"иск|суд|банкрот|штраф|ФАС|национализ|изъят|сплит|консолидац|рейтинг|CEO|гендиректор|отставк|арест|"
    r"обыск|авари|пожар|налог|НДПИ|экспортн\w+ пошлин", re.I)


def default_query(x, secid):
    name = (x.get("emitent") or x.get("name") or secid)
    # «ПАО "Сбербанк России"» -> «Сбербанк России»; убираем ОПФ и кавычки
    name = re.sub(r'\b(МКПАО|ПАО|ОАО|АО)\b', "", name)
    name = re.sub(r'[«»"\']', "", name).strip(" ,.-")
    short = x.get("name") or secid
    parts = [f'"{name}"'] if name else []
    if short and short.lower() != name.lower():
        parts.append(f'"{short}"')
    parts.append(secid)
    return "(" + " OR ".join(parts) + ")"


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def parse_rss(raw, feed):
    items = []
    root = ET.fromstring(raw)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src = it.find("source")
        source = src.text.strip() if src is not None and src.text else urllib.parse.urlparse(link).netloc
        pub = it.findtext("pubDate")
        try:
            pub = email.utils.parsedate_to_datetime(pub).date().isoformat() if pub else None
        except (TypeError, ValueError):
            pub = None
        if title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        items.append({"title": title, "link": link, "source": source, "published": pub, "feed": feed})
    # Atom
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for e in root.findall(".//a:entry", ns):
        link_el = e.find("a:link", ns)
        items.append({"title": (e.findtext("a:title", "", ns) or "").strip(),
                      "link": link_el.get("href") if link_el is not None else "",
                      "source": urllib.parse.urlparse(feed).netloc,
                      "published": (e.findtext("a:updated", "", ns) or "")[:10] or None, "feed": feed})
    return items


def key(item):
    norm = re.sub(r"\W+", " ", item["title"].lower()).strip()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def stems(x, secid):
    """Основы названий для проверки релевантности: «Сбербанк России» -> сбербан, росси; тикер."""
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]{3,}", f"{x.get('emitent') or ''} {x.get('name') or ''}")
    stop = {"пао", "оао", "мкпао", "акции", "группа", "компания", "холдинг", "россии", "российские"}
    out = {w.lower()[:6] for w in words if w.lower() not in stop}
    out.add(secid.lower())
    return out


def relevant(item, st):
    t = item["title"].lower()
    return any(s in t for s in st)


def near_dup(title, seen_prefixes):
    """Перепечатки/SEO-клоны: совпадение первых 35 символов заголовка без знаков."""
    p = re.sub(r"\W+", "", title.lower())[:35]
    if p in seen_prefixes:
        return True
    seen_prefixes.add(p)
    return False


def collect(secid, x, days):
    q = x.get("news_query") or default_query(x, secid)
    feeds = [GNEWS.format(urllib.parse.urlencode({"q": f"{q} when:{days}d", "hl": "ru", "gl": "RU", "ceid": "RU:ru"}))]
    feeds += x.get("feeds") or []
    items, errors = [], []
    for url in feeds:
        try:
            items += parse_rss(fetch(url), url)
        except Exception as e:  # одна лента не должна ронять сбор
            errors.append(f"{urllib.parse.urlparse(url).netloc}: {e}")
    return q, items, errors


def store(secid, items, st=None, strict=True):
    p = os.path.join(ticker_dir(secid, "news"), "news.jsonl")
    seen = set()
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["id"])
                except (ValueError, KeyError):
                    pass
    new = []
    today = dt.date.today().isoformat()
    prefixes = set()
    with open(p, "a", encoding="utf-8") as f:
        for it in items:
            k = key(it)
            if k in seen or not it["title"]:
                continue
            if strict and st and it.get("feed", "").startswith("https://news.google.com") and not relevant(it, st):
                continue  # заголовок не про эту компанию (шум Google News)
            if near_dup(it["title"], prefixes):
                continue
            seen.add(k)
            rec = {"id": k, "first_seen": today, **it, "important": bool(IMPORTANT.search(it["title"]))}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            new.append(rec)
    return p, new


def recent(path, days):
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if (r.get("published") or r.get("first_seen") or "") >= since:
                out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--days", type=int, default=14, help="окно поиска, дней")
    ap.add_argument("--all", action="store_true", help="показать все новости за окно, а не только новые")
    ap.add_argument("--query", help="свой поисковый запрос для Google News (сохраняется для акции)")
    ap.add_argument("--add-feed", help="добавить RSS-ленту для акции")
    ap.add_argument("--no-filter", action="store_true", help="не отсеивать заголовки без названия компании")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    wl = load_watchlist()
    out = {}
    for s in watched_or_all(a.tickers):
        x = wl["tickers"].get(s)
        if x is None:
            print(f"{s}: не под наблюдением (python desk.py add {s})", file=sys.stderr)
            continue
        if a.query:
            x["news_query"] = a.query
        if a.add_feed:
            x.setdefault("feeds", []).append(a.add_feed)
        if a.query or a.add_feed:
            save_watchlist(wl)
        q, items, errors = collect(s, x, a.days)
        path, new = store(s, items, stems(x, s), strict=not a.no_filter)
        if new:
            imp = sum(1 for n in new if n["important"])
            append_journal(s, f"Новости: +{len(new)} (существенных по заголовку: {imp})", "news/news.jsonl")
        shown = recent(path, a.days) if a.all else new
        shown.sort(key=lambda r: r.get("published") or "", reverse=True)
        out[s] = {"query": q, "new": len(new), "errors": errors, "items": shown, "file": path}

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    for s, r in out.items():
        print(f"\n=== {s}: новых {r['new']} | запрос: {r['query']} | {r['file']} ===")
        for e in r["errors"]:
            print(f"  ! лента недоступна: {e}")
        for n in r["items"][:40]:
            print(f"  {'⚑' if n['important'] else ' '} {n.get('published') or '????-??-??'} [{n['source']}] {n['title']}\n      {n['link']}")
        if not r["items"]:
            print("  нет " + ("новостей за окно" if a.all else "новых новостей"))


if __name__ == "__main__":
    main()
