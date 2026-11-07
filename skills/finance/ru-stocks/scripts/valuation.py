"""Оценка акции: мультипликаторы, DCF по FCFE/FCFF в трёх сценариях, дивидендная модель, обратный DCF.
Все суммы компании — в МЛРД ₽ (выручка, прибыль, FCF, долг). Цена и дивиденд — в ₽ на акцию.

Мультипликаторы (цена и число акций можно подтянуть с биржи через --ticker):
  python valuation.py --ticker SBER --net-income 1580 --equity 7300 --dps 34.84

DCF по свободному денежному потоку акционерам (FCFE), 3 сценария, ставка = ОФЗ + премия × бета:
  python valuation.py --ticker CHMF --fcf 60 --growth 4 --rf 15 --erp 6 --beta 1.1 --tg 4 --years 5

DCF по FCFF (поток фирмы) — тогда передайте чистый долг: --fcff --net-debt 120

Дивидендная модель Гордона (цена = DPS×(1+g)/(ke−g)) и обратный DCF (какой рост «зашит» в цену) — считаются,
когда хватает входных данных. Сценарии: медведь (рост −50% от базы и ставка +2 п.п.), база, бык (рост +50%, ставка −1 п.п.);
переопределяются --bear-growth/--bull-growth.

Модель — инструмент проверки допущений, а не источник истины: каждое допущение агент обязан обосновать
(отчётность, прогноз менеджмента, консенсус, макро) и показать чувствительность.
"""
import argparse
import json


def coe(rf, erp, beta):
    return rf + erp * beta


def dcf(fcf0, growth, ke, tg, years):
    """Стоимость (млрд ₽) потока, растущего growth% years лет, затем tg% вечно, дисконт ke%."""
    g, r, t = growth / 100, ke / 100, tg / 100
    if r <= t:
        return None
    pv, cf = 0.0, fcf0
    for y in range(1, years + 1):
        cf *= 1 + g
        pv += cf / (1 + r) ** y
    tv = cf * (1 + t) / (r - t)
    return pv + tv / (1 + r) ** years


def implied_growth(target_value, fcf0, ke, tg, years):
    """Бинарный поиск роста, при котором DCF = рыночной стоимости."""
    lo, hi = -50.0, 100.0
    if dcf(fcf0, lo, ke, tg, years) is None:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        v = dcf(fcf0, mid, ke, tg, years)
        if v is None:
            return None
        lo, hi = (mid, hi) if v < target_value else (lo, mid)
    return (lo + hi) / 2


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticker", help="подтянуть цену и число акций с MOEX")
    ap.add_argument("--price", type=float, help="цена акции, ₽")
    ap.add_argument("--shares", type=float, help="число акций, млрд шт. (для всех классов акций — суммой с учётом цены преф.)")
    ap.add_argument("--treasury", type=float, default=0, help="казначейские/квазиказначейские акции, млрд шт. — вычитаются")
    ap.add_argument("--net-income", type=float, help="чистая прибыль акционерам, млрд ₽ (LTM или прогноз)")
    ap.add_argument("--ebitda", type=float, help="EBITDA, млрд ₽")
    ap.add_argument("--revenue", type=float, help="выручка, млрд ₽")
    ap.add_argument("--net-debt", type=float, default=0, help="чистый долг, млрд ₽ (отрицательный — чистая денежная позиция)")
    ap.add_argument("--pref-div", type=float, default=0,
                    help="дивиденды по привилегированным акциям, млрд ₽ — вычитаются из ЧП для EPS и payout обычки")
    ap.add_argument("--equity", type=float, help="капитал акционеров, млрд ₽")
    ap.add_argument("--dps", type=float, help="дивиденд на акцию за 12 мес. или прогноз, ₽")
    ap.add_argument("--fcf", type=float, help="базовый свободный денежный поток, млрд ₽ (FCFE по умолчанию)")
    ap.add_argument("--fcff", action="store_true", help="--fcf это поток фирмы: из стоимости вычитается чистый долг")
    ap.add_argument("--growth", type=float, default=0, help="рост FCF в прогнозный период, %% в год (номинал, ₽)")
    ap.add_argument("--bear-growth", type=float)
    ap.add_argument("--bull-growth", type=float)
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--tg", type=float, default=4, help="рост в постпрогнозный период, %% (≈ долгосрочная инфляция)")
    ap.add_argument("--ke", type=float, help="ставка дисконтирования, %% (иначе rf + erp × beta)")
    ap.add_argument("--rf", type=float, help="доходность длинных ОФЗ, %% — брать свежую (КБД 10 лет)")
    ap.add_argument("--erp", type=float, default=6, help="премия за риск акций, %%")
    ap.add_argument("--beta", type=float, default=1.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    src = []
    if a.ticker:
        from _iss import resolve
        from moex_share import quote
        info = resolve(a.ticker)
        q = quote(info["secid"], info["board"])
        a.price = a.price or q["price"]
        if not a.shares and q["issuesize"]:
            a.shares = q["issuesize"] / 1e9
        src.append(f"MOEX: цена {q['price']} ₽, акций в выпуске {q['issuesize']:,.0f} (только этот класс акций!)".replace(",", " "))
    if not (a.price and a.shares):
        raise SystemExit("Нужны --price и --shares (или --ticker).")
    shares = a.shares - a.treasury
    cap = a.price * shares  # млрд ₽
    ev = cap + a.net_debt
    res = {"inputs": vars(a), "source": src, "cap_bn": cap, "ev_bn": ev, "multiples": {}, "dcf": {}, "ddm": {}}
    m = res["multiples"]
    if a.net_income:
        m["P/E"] = cap / a.net_income
        m["EPS, ₽"] = (a.net_income - a.pref_div) / shares
    if a.ebitda:
        m["EV/EBITDA"] = ev / a.ebitda
        m["ЧД/EBITDA"] = a.net_debt / a.ebitda
    if a.revenue:
        m["EV/Выручка"] = ev / a.revenue
    if a.equity:
        m["P/B"] = cap / a.equity
        if a.net_income:
            m["ROE, %"] = a.net_income / a.equity * 100
    if a.dps:
        m["Дивдоходность, %"] = a.dps / a.price * 100
        if a.net_income:
            m["Payout, % ЧП"] = a.dps * shares / (a.net_income - a.pref_div) * 100
    if a.fcf:
        m["FCF yield, %"] = a.fcf / (ev if a.fcff else cap) * 100

    ke = a.ke if a.ke is not None else (coe(a.rf, a.erp, a.beta) if a.rf is not None else None)
    res["ke"] = ke
    if ke is not None and a.fcf:
        scen = {
            "медведь": (a.bear_growth if a.bear_growth is not None else a.growth * 0.5, ke + 2),
            "база": (a.growth, ke),
            "бык": (a.bull_growth if a.bull_growth is not None else a.growth * 1.5, ke - 1),
        }
        for name, (g, k) in scen.items():
            v = dcf(a.fcf, g, k, a.tg, a.years)
            if v is None:
                res["dcf"][name] = {"growth": g, "ke": k, "error": "ставка ≤ терминального роста"}
                continue
            eq = v - (a.net_debt if a.fcff else 0)
            ps = eq / shares
            res["dcf"][name] = {"growth": g, "ke": k, "equity_bn": eq, "per_share": ps, "upside_pct": (ps / a.price - 1) * 100}
        target = cap + (a.net_debt if a.fcff else 0)
        ig = implied_growth(target, a.fcf, ke, a.tg, a.years)
        res["implied_growth"] = ig
        sens = {}
        for dk in (-2, -1, 0, 1, 2):
            row = {}
            for dg in (-2, 0, 2):
                v = dcf(a.fcf, a.growth + dg, ke + dk, a.tg, a.years)
                row[f"g {a.growth + dg:.0f}%"] = None if v is None else (v - (a.net_debt if a.fcff else 0)) / shares
            sens[f"ke {ke + dk:.1f}%"] = row
        res["sensitivity"] = sens
    if ke is not None and a.dps:
        g = a.tg
        if ke > g:
            fair = a.dps * (1 + g / 100) / ((ke - g) / 100)
            res["ddm"] = {"g": g, "ke": ke, "fair": fair, "upside_pct": (fair / a.price - 1) * 100,
                          "implied_g": ke - a.dps / a.price * 100}

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
        return
    f = lambda x, n=1: "—" if x is None else f"{x:,.{n}f}".replace(",", " ")  # noqa: E731
    for s in src:
        print(s)
    print(f"Цена {f(a.price, 2)} ₽ | акций {f(shares, 3)} млрд | капитализация {f(cap)} млрд ₽ | EV {f(ev)} млрд ₽")
    if m:
        print("Мультипликаторы: " + " | ".join(f"{k} {f(v, 2)}" for k, v in m.items()))
    if ke is not None:
        print(f"Стоимость капитала ke = {f(ke, 2)}%" + (f" (ОФЗ {a.rf}% + {a.erp}% × β {a.beta})" if a.ke is None else ""))
    for name, d in res["dcf"].items():
        if "error" in d:
            print(f"  DCF {name}: {d['error']}")
        else:
            print(f"  DCF {name:<8} рост {f(d['growth'])}%, ke {f(d['ke'])}% → {f(d['per_share'], 2)} ₽/акц. ({f(d['upside_pct'])}% к цене)")
    if res.get("implied_growth") is not None:
        print(f"  Обратный DCF: цена предполагает рост FCF ≈ {f(res['implied_growth'])}% в год {a.years} лет при ke {f(ke)}%")
    if res.get("sensitivity"):
        cols = list(next(iter(res["sensitivity"].values())).keys())
        print("  Чувствительность (₽/акц.): " + " ".join(f"{c:>10}" for c in cols))
        for k, row in res["sensitivity"].items():
            print(f"    {k:<12}            " + " ".join(f"{f(v, 1):>10}" for v in row.values()))
    if res["ddm"]:
        d = res["ddm"]
        print(f"  Модель Гордона: DPS {a.dps} ₽, g {d['g']}%, ke {f(ke)}% → {f(d['fair'], 2)} ₽ ({f(d['upside_pct'])}%); "
              f"рынок закладывает g ≈ {f(d['implied_g'])}%")


if __name__ == "__main__":
    main()
