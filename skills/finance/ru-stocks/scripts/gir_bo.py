"""Бухгалтерская отчётность (РСБУ) компании из ГИР БО ФНС (bo.nalog.gov.ru) + метрики прибыльности и долга.

  python gir_bo.py 7736050003            # по ИНН
  python gir_bo.py "Северсталь"          # поиск по названию (покажет варианты)
  python gir_bo.py 3528000597 --json
  python gir_bo.py 3528000597 --save CHMF   # сохранить в папку акции: financials/rsbu.json

Для акционера РСБУ материнской компании важна, когда дивиденды считаются от неё (многие госкомпании,
Транснефть, Сургутнефтегаз) или когда холдинг живёт дивидендами «дочек».

Важно: ГИР БО — это РСБУ ОДНОГО юрлица (не группы). Для холдингов смотрите МСФО группы
(сайт эмитента / e-disclosure.ru). Банков и НФО в ГИР БО нет — для банков формы 101/102/123/135 на cbr.ru.
Строки: 1100 внеоборот., 1200 оборот., 1250 деньги, 1230 дебиторка, 1210 запасы, 1300 капитал,
1410 долг.займы, 1510 кратк.займы, 1520 кредиторка, 1600 баланс, 2110 выручка, 2120 себест.,
2200 прибыль от продаж (≈EBIT), 2320 проценты к получ., 2330 проценты к уплате, 2300 до налога,
2400 чистая прибыль; ОДДС: 4100 операц., 4200 инвест., 4300 финанс.
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://bo.nalog.gov.ru"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def get(path):
    req = urllib.request.Request(BASE + path, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def find_org(query):
    d = get("/advanced-search/organizations/search?" + urllib.parse.urlencode({"query": query, "page": 0}))
    orgs = d.get("content", [])
    if not orgs:
        raise SystemExit(f"Не найдено в ГИР БО: {query}. Банки и НФО в ГИР БО отсутствуют — для банков берите МСФО и формы 101/102/123/135 на cbr.ru")
    clean = lambda s: (s or "").replace("<strong>", "").replace("</strong>", "")  # noqa: E731
    if len(orgs) > 1:
        print("Найдено несколько организаций, беру первую:", file=sys.stderr)
        for o in orgs[:10]:
            print(f"  id={o['id']} ИНН {clean(o.get('inn'))} {clean(o.get('shortName'))} "
                  f"статус {o.get('statusCode')}", file=sys.stderr)
    o = orgs[0]
    return o["id"], clean(o.get("inn")), clean(o.get("shortName"))


def v(section, line, col="current"):
    if not section:
        return None
    x = section.get(f"{col}{line}")
    return float(x) if x is not None else None


def metrics(period, corr):
    b, f = corr.get("balance") or {}, corr.get("financialResult") or {}
    cf = corr.get("fundsMovement") or corr.get("fundMovement") or {}
    rev, ebit = v(f, 2110), v(f, 2200)
    int_paid, int_recv = v(f, 2330), v(f, 2320)
    np_ = v(f, 2400)
    debt = (v(b, 1410) or 0) + (v(b, 1510) or 0)
    cash = v(b, 1250) or 0
    eq, assets = v(b, 1300), v(b, 1600)
    cur_a, cur_l = v(b, 1200), v(b, 1500)
    # Амортизация в РСБУ отдельной строкой не раскрыта — EBITDA только из пояснений/МСФО.
    m = {
        "period": period, "unit": "тыс. ₽",
        "выручка_2110": rev, "прибыль_от_продаж_2200": ebit, "проценты_к_уплате_2330": int_paid,
        "проценты_к_получению_2320": int_recv, "чистая_прибыль_2400": np_,
        "долг_1410+1510": debt, "деньги_1250": cash, "чистый_долг": debt - cash,
        "капитал_1300": eq, "активы_1600": assets,
        "операционный_CF_4100": v(cf, 4100), "инвест_CF_4200": v(cf, 4200), "фин_CF_4300": v(cf, 4300),
    }
    r = {}
    if ebit and int_paid:
        r["ICR (EBIT/проценты)"] = round(ebit / abs(int_paid), 2)
    if ebit and ebit > 0:
        r["Чистый долг/EBIT"] = round((debt - cash) / ebit, 2)
    if rev:
        r["Рентаб. продаж %"] = round((ebit or 0) / rev * 100, 1)
        r["Чистая маржа %"] = round((np_ or 0) / rev * 100, 1)
    if eq is not None and assets:
        r["Капитал/Активы %"] = round(eq / assets * 100, 1)
    if cur_a and cur_l:
        r["Текущая ликвидность"] = round(cur_a / cur_l, 2)
    if debt and int_paid:
        r["Ср. ставка по долгу % (оценка)"] = round(abs(int_paid) / debt * 100, 1)
    if debt:
        r["Доля краткоср. долга %"] = round((v(b, 1510) or 0) / debt * 100, 1)
    if np_ is not None and eq:
        r["ROE % (ЧП/капитал на конец)"] = round(np_ / eq * 100, 1)
    m["ratios"] = r
    m["audit"] = bool(corr.get("auditReport"))
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", help="ИНН/ОГРН/название")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--save", metavar="TICKER", help="сохранить в папку акции (financials/rsbu.json)")
    a = ap.parse_args()
    oid, inn, name = find_org(a.query)
    reports = get(f"/nbo/organizations/{oid}/bfo/")
    out = []
    for rep in sorted(reports, key=lambda x: x["period"], reverse=True):
        tc = rep.get("typeCorrections") or []
        if not tc:
            continue
        corr = tc[0]["correction"]
        out.append(metrics(rep["period"], corr))
    for i, m in enumerate(out[:-1]):  # рост г/г
        prev = out[i + 1]
        for k, label in (("выручка_2110", "Рост выручки г/г %"), ("чистая_прибыль_2400", "Рост ЧП г/г %")):
            if m[k] is not None and prev[k] and prev[k] > 0:
                m["ratios"][label] = round((m[k] / prev[k] - 1) * 100, 1)
    res = {"org": name, "inn": inn, "gir_bo_id": oid, "url": f"{BASE}/organizations-card/{oid}", "years": out}
    if a.save:
        import datetime as _dt
        from _iss import ticker_dir, append_journal
        import os
        res["fetched"] = _dt.date.today().isoformat()
        p = os.path.join(ticker_dir(a.save, "financials"), "rsbu.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        append_journal(a.save, f"РСБУ из ГИР БО обновлена: годы {', '.join(str(m['period']) for m in out[:5])}", res["url"])
        print(f"Сохранено: {p}", file=sys.stderr)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    print(f"=== {name} | ИНН {inn} | {res['url']} ===  (РСБУ; суммы в млн ₽)")
    for m in out:
        fmt = lambda x: f"{x/1e3:,.0f} млн" if isinstance(x, (int, float)) else "—"  # noqa: E731
        print(f"\n[{m['period']}] аудит: {'да' if m['audit'] else 'нет/не найден'}")
        print(f"  Выручка {fmt(m['выручка_2110'])} | Приб. от продаж {fmt(m['прибыль_от_продаж_2200'])} | "
              f"ЧП {fmt(m['чистая_прибыль_2400'])} | Проценты к уплате {fmt(m['проценты_к_уплате_2330'])}")
        print(f"  Долг {fmt(m['долг_1410+1510'])} | Деньги {fmt(m['деньги_1250'])} | ЧД {fmt(m['чистый_долг'])} | "
              f"Капитал {fmt(m['капитал_1300'])} | Активы {fmt(m['активы_1600'])}")
        print(f"  CF: опер {fmt(m['операционный_CF_4100'])} | инв {fmt(m['инвест_CF_4200'])} | фин {fmt(m['фин_CF_4300'])}")
        print("  " + " | ".join(f"{k}: {x}" for k, x in m["ratios"].items()))


if __name__ == "__main__":
    main()
