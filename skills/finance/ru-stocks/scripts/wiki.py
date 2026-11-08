"""База знаний агента в формате Obsidian (LLM-вики). Один и тот же файл во всех агентах семейства:
ru-stocks-agent, ru-portfolio-agent, ru-bonds-agent, finex-agent — хранилища можно открыть в одном Obsidian.

  python wiki.py init                         # сделать хранилище Obsidian-вики: .obsidian, шаблоны, Home, index, log
  python wiki.py sync [ID ...]                # пересобрать хаб-заметки бумаг, заметки отчётов/документов, index
  python wiki.py log "что узнали" [--entity SBER] [--kind факт] [--source URL]   # журнал знаний (log.md)
  python wiki.py note topic "Ключевая ставка ЦБ" [--entity SBER ...] [--source URL]  # новая заметка из шаблона
  python wiki.py find "дивполитик" [--type thesis] [--entity SBER] [--since 2026-01-01]  # поиск по вики
  python wiki.py lint [--save]                # проверка: битые ссылки, нет frontmatter/источников, сироты,
                                              # устаревшие заметки, незаконспектированные отчёты, противоречия
  python wiki.py digest [--week 2026-W45]     # недельный дайджест: что узнали, события, новые отчёты и новости
  python wiki.py path                         # корень хранилища и корень Obsidian-хранилища

Три слоя (идея LLM-вики): сырые источники (PDF, тексты, *.jsonl — только пополняются) → вики (markdown-заметки
с [[ссылками]] и YAML-frontmatter, их пишет агент) → схема (references/wiki.md — правила ведения).
INVEST_VAULT=<папка> — общий Obsidian-хранилище для всех агентов: stocks/ (акции и портфель), bonds/, finex/.
Только stdlib, Python 3.9+.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

WIKI_VERSION = 1


# ---------- Какой это агент ----------
def detect_agent():
    env = os.environ.get("WIKI_AGENT")
    if env:
        return env
    if os.path.exists(os.path.join(HERE, "_common.py")) and os.path.exists(os.path.join(HERE, "payout.py")):
        return "finex"
    if os.path.exists(os.path.join(HERE, "moex_bond.py")):
        return "bonds"
    if os.path.exists(os.path.join(HERE, "portfolio.py")):
        return "portfolio"
    return "stocks"


AGENT = detect_agent()
AGENT_NAME = {"stocks": "ru-stocks", "portfolio": "ru-portfolio", "bonds": "ru-bonds", "finex": "finex"}[AGENT]
# Акции и портфель делят одни папки бумаг (совместимый формат), поэтому «вид хранилища» у них общий.
KIND = {"stocks": "stocks", "portfolio": "stocks", "bonds": "bonds", "finex": "finex"}[AGENT]
KIND_LABEL = {"stocks": "акции", "bonds": "облигации", "finex": "фонды FinEx"}[KIND]
ENTITY_TYPE = {"stocks": "security", "bonds": "bond", "finex": "fund"}[KIND]

AUTO_START = "<!-- wiki:auto:start — этот блок пересобирает wiki.py sync; свои заметки пишите ниже блока -->"
AUTO_END = "<!-- wiki:auto:end -->"
TODO_MARK = "_Агент:"  # заглушка, которую агент обязан заменить содержанием

# Типы, которые описывают «текущее состояние» (перезаписываются) — к ним применяются проверки источников,
# устаревания и противоречий. История (journal, analysis, log, digest, forum, news) только пополняется.
CURRENT_TYPES = {"security", "bond", "fund", "issuer", "profile", "thesis", "financials", "topic"}
REQUIRED = {
    "security": ["id"], "bond": ["id"], "fund": ["id"], "issuer": ["name"],
    "report": ["entity", "date"], "source": ["date"], "analysis": ["date"], "decision": ["date"],
    "digest": ["week"], "news": ["date"],
}
STALE_DAYS = 90


# ---------- Корни ----------
def desk():
    """Корень хранилища агента (STOCKS_DESK / FAMILY_OFFICE / BONDS_DESK / FINEX_DESK или INVEST_VAULT/<вид>)."""
    if AGENT == "finex":
        from _common import desk_root
    else:
        from _iss import desk_root
    return os.path.abspath(desk_root())


def vault():
    """Корень Obsidian-хранилища: INVEST_VAULT, если хранилище агента внутри него, иначе само хранилище."""
    d = desk()
    v = os.environ.get("INVEST_VAULT")
    if v:
        v = os.path.abspath(os.path.expanduser(v))
        if (d + os.sep).startswith(v.rstrip(os.sep) + os.sep) or d == v:
            return v
    return d


def shared(*parts):
    """Общие для всех агентов папки (topics, _templates) — в корне Obsidian-хранилища."""
    return os.path.join(vault(), *parts)


def rel(p, base=None):
    return os.path.relpath(p, base or desk()).replace(os.sep, "/")


def today():
    return dt.date.today().isoformat()


def read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return ""


def write(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, p)


def write_if_changed(p, text):
    if read(p) != text:
        write(p, text)
        return True
    return False


def load_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_jsonl(p):
    out = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


# ---------- Frontmatter (подмножество YAML, совместимое со свойствами Obsidian) ----------
FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)


def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        inner = v[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\") if v[0] == '"' else inner.replace("''", "'")
    if v in ("true", "false"):
        return v == "true"
    if v in ("null", "~", ""):
        return None
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v


def _split_list(s):
    out, cur, q, esc = [], "", None, False
    for ch in s:
        if q:
            cur += ch
            if esc:
                esc = False
            elif ch == "\\" and q == '"':
                esc = True
            elif ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            cur += ch
        elif ch == ",":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [_scalar(x) for x in out]


def parse_fm(text):
    """-> (dict, body). Понимает key: value, key: [a, b] и многострочные списки «- a»."""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    fm, key = {}, None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        li = re.match(r"^\s+-\s*(.*)$", line)
        if li and key is not None:
            if not isinstance(fm.get(key), list):
                fm[key] = []
            fm[key].append(_scalar(li.group(1)))
            continue
        kv = re.match(r"^([^\s:][^:]*):\s*(.*)$", line)
        if not kv:
            continue
        key, v = kv.group(1).strip(), kv.group(2).strip()
        if v.startswith("[") and v.endswith("]"):
            fm[key] = _split_list(v[1:-1])
        else:
            fm[key] = _scalar(v)
    return fm, text[m.end():]


def _q(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or re.search(r"[:#\[\]{},&*!|>'\"%@`]", s) or s[0] in "-?" or s.strip() != s \
            or s in ("true", "false", "null") or re.fullmatch(r"-?\d+(\.\d+)?", s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


ORDER = ["type", "id", "ticker", "isin", "name", "issuer", "inn", "entity", "up", "date", "week", "period",
         "status", "verdict", "score", "target", "last_analysis", "source", "aliases", "tags", "agent", "private",
         "created", "updated"]


def dump_fm(fm):
    keys = [k for k in ORDER if k in fm] + [k for k in fm if k not in ORDER]
    lines = ["---"]
    for k in keys:
        v = fm[k]
        if isinstance(v, list):
            lines.append(f"{k}: [" + ", ".join(_q(x) for x in v if x not in (None, "")) + "]")
        else:
            lines.append(f"{k}: {_q(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def with_fm(fm, body):
    return dump_fm(fm) + body.lstrip("\n")


def set_auto(body, block, title=None, tail=""):
    """Заменить авто-блок в теле заметки (или вставить после заголовка). Всё вне блока сохраняется."""
    block = f"{AUTO_START}\n{block.rstrip()}\n{AUTO_END}"
    if AUTO_START.split(" —")[0] in body and AUTO_END in body:
        return re.sub(re.escape(AUTO_START.split(" —")[0]) + r".*?" + re.escape(AUTO_END),
                      lambda _: block, body, count=1, flags=re.S)
    head = f"# {title}\n\n" if title and not body.lstrip().startswith("# ") else ""
    if body.lstrip().startswith("# "):
        first, _, rest = body.lstrip().partition("\n")
        return f"{first}\n\n{block}\n{rest.rstrip()}\n" if rest.strip() else f"{first}\n\n{block}\n\n{tail}"
    return f"{head}{block}\n\n{tail}" + body.lstrip()


def save_note(p, fm, body):
    """Записать заметку, если изменилась; поле updated меняется только при реальном изменении."""
    old_fm, _ = parse_fm(read(p))
    fm = dict(fm)
    fm["updated"] = old_fm.get("updated") or today()
    if with_fm(fm, body) == read(p):
        return False
    fm["updated"] = today()
    write(p, with_fm(fm, body))
    return True


def strip_auto(body):
    """Тело без авто-блока — то, что написали агент и пользователь."""
    return re.sub(re.escape(AUTO_START.split(" —")[0]) + r".*?" + re.escape(AUTO_END), "", body, flags=re.S)


def link(path_noext, label=None, table=False):
    sep = "\\|" if table else "|"
    return f"[[{path_noext}{sep}{label}]]" if label else f"[[{path_noext}]]"


def dlink(path_noext, label=None, table=False):
    """Ссылка на служебную заметку хранилища агента (index, log, digests…): в общем хранилище INVEST_VAULT
    имена совпадают у разных агентов, поэтому добавляем папку агента относительно корня Obsidian."""
    pre = rel(desk(), vault())
    full = path_noext if pre == "." else f"{pre}/{path_noext}"
    return link(full, label or (path_noext if pre != "." else None), table)


def tags(*extra):
    return [f"desk/{KIND}"] + [t for t in extra if t]


# ---------- Данные хранилища ----------
def watchlist():
    return load_json(os.path.join(desk(), "watchlist.json"), {"tickers": {}}).get("tickers", {})


def entity_dirs():
    """ID бумаг, у которых есть папка (stocks/portfolio/bonds): список наблюдения + папки с профилем."""
    root = desk()
    ids = set(watchlist())
    if os.path.isdir(root):
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isdir(p) and not name.startswith(("_", ".")) and \
                    (os.path.exists(os.path.join(p, "README.md")) or os.path.exists(os.path.join(p, "journal.md"))):
                ids.add(name)
    return sorted(i for i in ids if os.path.isdir(os.path.join(root, i)))


def journal_lines(p, n=None):
    lines = [ln.rstrip() for ln in read(p).splitlines() if ln.startswith("- ")]
    return lines[-n:] if n else lines


def md_files(d):
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith(".md"))


REPORT_RE = re.compile(r"^(\d{4})-([a-z0-9]+)-([a-z0-9_-]+)$", re.I)
PERIOD = {"q1": "1 кв.", "q2": "2 кв.", "q3": "3 кв.", "q4": "4 кв.", "h1": "1 п/г", "h2": "2 п/г",
          "9m": "9 мес.", "6m": "6 мес.", "3m": "3 мес.", "fy": "год"}


def report_index(eid):
    """reports/index.md: «- дата | имя | N стр. | URL» -> {имя: {date, pages, url}}."""
    out = {}
    for ln in read(os.path.join(desk(), eid, "reports", "index.md")).splitlines():
        parts = [x.strip() for x in ln.lstrip("- ").split("|")]
        if len(parts) >= 4 and re.match(r"\d{4}-\d{2}-\d{2}", parts[0]):
            out[parts[1]] = {"date": parts[0], "pages": parts[2], "url": parts[3]}
    return out


# ---------- Нормализация: frontmatter у файлов, которые пишут скрипты и агент ----------
def infer_type(relpath):
    """Тип заметки по пути внутри хранилища агента (для файлов без frontmatter)."""
    p = relpath.split("/")
    name = p[-1]
    if p[0] == "_private":
        return "private", True
    if p[0] == "_portfolio":
        return {"briefs": "brief", "reviews": "review"}.get(p[1] if len(p) > 2 else "", "journal"), True
    if KIND == "finex":
        if name == "journal.md" and len(p) == 1:
            return "journal", False
        if p[0] == "forums":
            return "forum", False
        if p[0] == "reports":
            return "payout", True
        if p[0] == "inbox":
            return "inbox", True
        return None, False
    if len(p) == 2:
        return {"README.md": "profile", "thesis.md": "thesis", "journal.md": "journal"}.get(name), False
    if len(p) >= 3:
        sub = p[1]
        if sub == "analyses":
            return "analysis", False
        if sub == "forums":
            return "forum", False
        if sub == "news":
            return "news-log", False
        if sub == "financials":
            return "financials", False
        if sub == "reports" and name == "index.md":
            return "report-index", False
        if sub == "decisions":
            return "decision", False
    return None, False


def normalize(paths):
    """Добавить frontmatter заметкам без него. Тело не трогаем."""
    n = 0
    for p in paths:
        text = read(p)
        if not text or FM_RE.match(text):
            continue
        r = rel(p)
        typ, private = infer_type(r)
        if not typ:
            continue
        fm = {"type": typ}
        parts = r.split("/")
        if KIND != "finex" and parts[0] in ENTITIES_CACHE:
            fm["entity"] = parts[0]
            fm["up"] = link(f"{parts[0]}/{parts[0]}", parts[0])
        m = re.match(r"(\d{4}-\d{2}-\d{2})", os.path.basename(p))
        if m:
            fm["date"] = m.group(1)
        fm["tags"] = tags(f"type/{typ}")
        if private:
            fm["private"] = True
        write(p, with_fm(fm, text))
        n += 1
    return n


ENTITIES_CACHE = set()


# ---------- Хаб-заметки бумаг (stocks / portfolio / bonds) ----------
def hub_stock(eid):
    root = os.path.join(desk(), eid)
    x = watchlist().get(eid) or {}
    la = x.get("last_analysis") or {}
    p = os.path.join(root, f"{eid}.md")
    fm0, body = parse_fm(read(p))
    name = (x.get("name") if KIND == "bonds" else x.get("emitent") or x.get("name")) or fm0.get("name") or eid
    fm = dict(fm0)
    fm.update({
        "type": ENTITY_TYPE, "id": eid, "ticker": eid,
        "isin": x.get("isin") or fm0.get("isin"), "name": name,
        "issuer": x.get("emitent") or fm0.get("issuer"), "inn": x.get("inn") or fm0.get("inn"),
        "status": "наблюдение" if eid in watchlist() else "архив",
        "verdict": la.get("verdict"), "score": la.get("score"), "target": la.get("target"),
        "last_analysis": la.get("date"), "analysis_price": la.get("price"),
        "aliases": sorted({a for a in [x.get("name"), x.get("emitent")] + list(fm0.get("aliases") or []) if a and a != eid}),
        "tags": sorted(set(tags(f"type/{ENTITY_TYPE}")) | {t for t in (fm0.get("tags") or []) if not str(t).startswith(("desk/", "type/"))}),
        "created": fm0.get("created") or x.get("added") or today(),
    })
    if KIND == "bonds":
        for k in ("maturity", "coupon_type", "listlevel"):
            if x.get(k):
                fm[k] = x.get(k)
    for k in [k for k, v in fm.items() if v in (None, "", [])]:
        fm.pop(k)

    L = []
    if la:
        L.append(f"> **Вывод последнего разбора** ({la.get('date')}): **{la.get('verdict') or '—'}**"
                 + (f" · скоринг {la['score']}" if la.get("score") is not None else "")
                 + ((f" · справедливая YTM {la['target']}%" if KIND == "bonds" else f" · справедливая стоимость {la['target']}")
                    if la.get("target") else "")
                 + (f" · цена на дату разбора {la['price']}" + ("%" if KIND == "bonds" else "") if la.get("price") else "")
                 + (f" · YTM {la['ytm']}%" if la.get("ytm") else ""))
    else:
        L.append("> Разбора ещё не было.")
    docs = [("README", "Профиль"), ("thesis", "Тезис"), ("journal", "Хронология"),
            ("financials/ifrs", "МСФО"), ("news/verified", "Проверенные новости")]
    have = [link(f"{eid}/{f}", t) for f, t in docs if os.path.exists(os.path.join(root, f + ".md"))]
    if KIND == "bonds" and x.get("inn"):
        have.append(link(f"issuers/{issuer_slug(x.get('emitent') or x.get('inn'))}", "Эмитент"))
    if have:
        L.append("\n**Документы:** " + " · ".join(have))
    ana = [f for f in md_files(os.path.join(root, "analyses"))]
    if ana:
        L.append("\n## Разборы")
        L += [f"- {link(f'{eid}/analyses/{f[:-3]}', f[:-3])}" for f in reversed(ana[-10:])]
    reps = report_names(eid)
    if reps:
        L.append("\n## Отчёты и документы")
        for b in reversed(reps[-12:]):
            fmr, bodyr = parse_fm(read(os.path.join(root, "reports", b + ".md")))
            done = "законспектирован" if TODO_MARK not in bodyr else "**нужен конспект**"
            L.append(f"- {link(f'{eid}/reports/{b}', b)} — {done}")
    dec = md_files(os.path.join(root, "decisions"))
    if dec:
        L.append("\n## Решения")
        L += [f"- {link(f'{eid}/decisions/{f[:-3]}', f[:-3])}" for f in reversed(dec[-10:])]
    fr = md_files(os.path.join(root, "forums"))
    if fr:
        L.append("\n## Срезы форумов")
        L += [f"- {link(f'{eid}/forums/{f[:-3]}', f[:-3])}" for f in reversed(fr[-5:])]
    news = [n for n in read_jsonl(os.path.join(root, "news", "news.jsonl")) if n.get("important")]
    if news:
        L.append("\n## Существенные новости (⚑, последние)")
        for n in sorted(news, key=lambda r: r.get("published") or r.get("first_seen") or "")[-10:][::-1]:
            d = (n.get("published") or n.get("first_seen") or "")[:10]
            t = (n.get("title") or "").replace("[", "(").replace("]", ")")
            L.append(f"- {d} [{t}]({n.get('link')})" + (f" — {n['source']}" if n.get("source") else ""))
    jl = journal_lines(os.path.join(root, "journal.md"), 10)
    if jl:
        L.append("\n## Последние события")
        L += jl[::-1]
    lg = [ln for ln in journal_lines(os.path.join(desk(), "log.md")) if f"[[{eid}/{eid}" in ln][-8:]
    if lg:
        L.append(f"\n## Что агент узнал (из {dlink('log')})")
        L += lg[::-1]
    tp = topics_for(eid)
    if tp:
        L.append("\n## Темы")
        L += [f"- {t}" for t in tp]
    title = f"{eid} — {name}" if name != eid else eid
    body = set_auto(body or "", "\n".join(L), title=title, tail="## Заметки\n\n")
    return save_note(p, fm, body)


def report_names(eid):
    d = os.path.join(desk(), eid, "reports")
    if not os.path.isdir(d):
        return []
    names = {os.path.splitext(f)[0] for f in os.listdir(d)
             if f.lower().endswith((".pdf", ".txt", ".html", ".xlsx", ".docx")) or (f.endswith(".md") and f != "index.md")}
    return sorted(names)


def report_notes(eid):
    """Заметка-источник на каждый архивный отчёт: frontmatter + ссылки на PDF/текст + место для конспекта."""
    d = os.path.join(desk(), eid, "reports")
    idx = report_index(eid)
    n = 0
    for b in report_names(eid):
        p = os.path.join(d, b + ".md")
        fm0, body = parse_fm(read(p))
        meta = idx.get(b, {})
        m = REPORT_RE.match(b)
        fm = dict(fm0)
        fm.update({"type": "report", "entity": eid, "up": link(f"{eid}/{eid}", eid),
                   "date": fm0.get("date") or meta.get("date") or today()})
        if m:
            fm.setdefault("period", f"{m.group(1)} {PERIOD.get(m.group(2).lower(), m.group(2))}")
            fm.setdefault("doc_kind", m.group(3).lower())
        if meta.get("url"):
            fm["source"] = meta["url"]
        fm["tags"] = sorted(set(tags("type/report")) | {t for t in (fm0.get("tags") or []) if not str(t).startswith(("desk/", "type/"))})
        files = []
        for ext in (".pdf", ".html", ".xlsx", ".docx"):
            if os.path.exists(os.path.join(d, b + ext)):
                files.append(link(f"{eid}/reports/{b}{ext}", ext[1:].upper()))
        if os.path.exists(os.path.join(d, b + ".txt")):
            files.append(f"[текст]({b}.txt)")
        block = [f"**Бумага:** {link(f'{eid}/{eid}', eid)} · **Файлы:** " + (" · ".join(files) or "—"),
                 f"**Источник:** {meta.get('url') or fm.get('source') or '—'}"
                 + (f" · {meta['pages']}" if meta.get("pages") else "") + f" · в архиве с {fm['date']}"]
        if not body.strip():
            body = (f"# Отчёт: {b} ({eid})\n\n" + AUTO_START + "\n" + AUTO_END + "\n\n## Конспект\n\n"
                    f"{TODO_MARK} ключевые цифры с номерами страниц, что изменилось против прошлого периода, "
                    "что это значит для тезиса. Каждую цифру — со ссылкой «стр. N»._\n\n"
                    f"## Что проверить дальше\n\n")
        body = set_auto(body, "\n".join(block))
        n += save_note(p, fm, body)
    return n


def issuer_slug(name):
    s = re.sub(r"[\"«»'“”„]", "", str(name or ""))
    s = re.sub(r"[\\/:*?<>|#^\[\]]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:80] or "эмитент"


def issuer_notes():
    """Облигации: заметка на эмитента со списком его выпусков (папка issuers/)."""
    by = {}
    for eid, x in watchlist().items():
        if x.get("inn") or x.get("emitent"):
            by.setdefault((x.get("emitent") or x.get("inn"), x.get("inn")), []).append(eid)
    n = 0
    for (name, inn), ids in by.items():
        p = os.path.join(desk(), "issuers", issuer_slug(name) + ".md")
        fm0, body = parse_fm(read(p))
        fm = dict(fm0)
        fm.update({"type": "issuer", "name": name, "inn": inn,
                   "tags": sorted(set(tags("type/issuer")) | {t for t in (fm0.get("tags") or []) if not str(t).startswith(("desk/", "type/"))}),
                   "created": fm0.get("created") or today()})
        L = [f"**ИНН:** {inn or '—'} · раскрытие: https://www.e-disclosure.ru (поиск по ИНН) · "
             f"ГИР БО: https://bo.nalog.gov.ru", "\n## Выпуски под наблюдением"]
        L += [f"- {link(f'{i}/{i}', i)}" + (f" — {watchlist()[i].get('name')}" if watchlist()[i].get("name") else "") for i in sorted(ids)]
        if not body.strip():
            body = (f"# {name}\n\n{AUTO_START}\n{AUTO_END}\n\n## Кредитный профиль\n\n{TODO_MARK} бизнес, долг, "
                    "ICR, ликвидность, рейтинги (агентство, дата, прогноз), суды — каждая цифра с источником._\n\n## Заметки\n\n")
        body = set_auto(body, "\n".join(L))
        n += save_note(p, fm, body)
    return n


def topics_for(eid):
    """Темы, в которых упомянута бумага (frontmatter entities или ссылка на хаб)."""
    out = []
    d = shared("topics")
    for f in md_files(d):
        fm, body = parse_fm(read(os.path.join(d, f)))
        ents = [str(e) for e in (fm.get("entities") or [])]
        if eid in ents or f"[[{eid}/{eid}" in body or f"[[{eid}]]" in body:
            out.append(link(f"topics/{f[:-3]}", f[:-3]))
    return out


# ---------- FinEx: фонды и документы ликвидаторов ----------
def finex_funds():
    try:
        from _common import funds as _funds
        ref = _funds()
    except Exception:
        ref = {}
    d = desk()
    navs = read_jsonl(os.path.join(d, "nav.jsonl"))
    quotes = read_jsonl(os.path.join(d, "prices.jsonl"))
    held = set(load_json(os.path.join(d, "portfolio.json"), {"positions": {}}).get("positions", {}))
    ids = held | {r.get("ticker") for r in navs} | {r.get("ticker") for r in quotes}
    return sorted(i for i in ids if i), ref, navs, quotes


def finex_hubs():
    ids, ref, navs, quotes = finex_funds()
    n = 0
    for t in ids:
        info = ref.get(t, {})
        p = os.path.join(desk(), "funds", f"{t}.md")
        fm0, body = parse_fm(read(p))
        nv = sorted([r for r in navs if r.get("ticker") == t], key=lambda r: (r.get("date") or "", r.get("added") or ""))
        qs = sorted([r for r in quotes if r.get("ticker") == t], key=lambda r: (r.get("date") or "", r.get("added") or ""))
        fm = dict(fm0)
        fm.update({"type": "fund", "id": t, "ticker": t, "name": info.get("name") or fm0.get("name"),
                   "isin": info.get("isin") or fm0.get("isin"), "ccy": info.get("ccy"), "icav": info.get("icav"),
                   "tags": sorted(set(tags("type/fund")) | {x for x in (fm0.get("tags") or []) if not str(x).startswith(("desk/", "type/"))}),
                   "created": fm0.get("created") or today()})
        if nv:
            fm.update({"nav": nv[-1].get("nav"), "nav_ccy": nv[-1].get("ccy"), "nav_date": nv[-1].get("date")})
        if qs:
            fm.update({"quote": qs[-1].get("price"), "quote_ccy": qs[-1].get("ccy"), "quote_kind": qs[-1].get("kind"),
                       "quote_date": qs[-1].get("date")})
        for k in [k for k, v in fm.items() if v in (None, "", [])]:
            fm.pop(k)
        L = [f"**Фонд:** {info.get('name') or t} · ISIN {info.get('isin') or 'не сверен'} · валюта класса "
             f"{info.get('ccy') or 'не сверена'} · {info.get('icav') or ''}",
             "\nСм. также: " + link("topics/Ликвидация FinEx", "Ликвидация FinEx") + " · " + dlink("index", "индекс")]
        if nv:
            L.append("\n## СЧА на пай (введённые)\n\n| Дата | СЧА | Валюта | Источник |\n|---|---|---|---|")
            L += [f"| {r.get('date')} | {r.get('nav')} | {r.get('ccy')} | {str(r.get('source') or '').replace('|', '/')} |" for r in nv[-8:][::-1]]
        if qs:
            L.append("\n## Котировки и предложения выкупа\n\n| Дата | Цена | Валюта | Вид | Источник |\n|---|---|---|---|---|")
            L += [f"| {r.get('date')} | {r.get('price')} | {r.get('ccy')} | {r.get('kind')} | {str(r.get('source') or '').replace('|', '/')} |" for r in qs[-8:][::-1]]
        docs = [r for r in read_jsonl(os.path.join(desk(), "docs", "index.jsonl"))
                if t in (r.get("label") or "") + " " + (r.get("summary") or "")]
        if docs:
            L.append("\n## Документы, где упомянут фонд")
            L += [f"- {link('docs/' + doc_base(r), r.get('label') or doc_base(r))}" for r in docs[-10:][::-1]]
        if not body.strip():
            body = (f"# {t} — {info.get('name') or t}\n\n{AUTO_START}\n{AUTO_END}\n\n## Заметки\n\n")
        body = set_auto(body, "\n".join(L))
        n += save_note(p, fm, body)
    return n


def doc_base(r):
    return os.path.splitext(os.path.basename(r.get("file") or r.get("url") or "doc"))[0]


def finex_docs():
    """Заметка-источник на каждый документ из архива docs/ (ликвидаторы, суд, регуляторы, НРД)."""
    n = 0
    for r in read_jsonl(os.path.join(desk(), "docs", "index.jsonl")):
        b = doc_base(r)
        p = os.path.join(desk(), "docs", b + ".md")
        fm0, body = parse_fm(read(p))
        fm = dict(fm0)
        fm.update({"type": "source", "doc_kind": "document", "date": (r.get("found") or today())[:10],
                   "source": r.get("url"), "status": r.get("status") or "не прочитан",
                   "tags": sorted(set(tags("type/source")) | {x for x in (fm0.get("tags") or []) if not str(x).startswith(("desk/", "type/"))})})
        if r.get("label"):
            fm["title"] = r["label"]
        fn = os.path.basename(r.get("file") or "")
        files = [link(f"docs/{fn}", "файл")] if fn else []
        if r.get("text"):
            files.append(f"[текст]({os.path.basename(r['text'])})")
        L = [f"**Документ:** {r.get('label') or b} · найден {r.get('found')} · статус: **{fm['status']}**",
             f"**Источник:** {r.get('url')} · " + " · ".join(files),
             "\n**Краткое содержание (docs.py --read --summary):** " + (r.get("summary") or "_ещё не прочитан_")]
        if not body.strip():
            body = (f"# {r.get('label') or b}\n\n{AUTO_START}\n{AUTO_END}\n\n## Конспект\n\n{TODO_MARK} кто издал, "
                    "что решено, что меняется для держателей через НРД, сроки, суммы, что требуется от пайщика "
                    "(со ссылками на страницы)._\n\n## Фонды\n\n")
        body = set_auto(body, "\n".join(L))
        n += save_note(p, fm, body)
    return n


# ---------- Индекс, Home, log ----------
def log_tail(n=15):
    return journal_lines(os.path.join(desk(), "log.md"), n)


def build_index():
    d = desk()
    L = [f"Обновлено {today()} · вид хранилища: **{KIND_LABEL}** · журнал знаний: {dlink('log')}"
         + (f" · стартовая страница: {link('Home')}" if os.path.exists(shared("Home.md")) else "")]
    if KIND == "finex":
        ids, ref, navs, quotes = finex_funds()
        L.append("\n## Фонды\n\n| Фонд | Название | СЧА | Дата СЧА | Посл. котировка |\n|---|---|---|---|---|")
        for t in ids:
            fm, _ = parse_fm(read(os.path.join(d, "funds", f"{t}.md")))
            L.append(f"| {link('funds/' + t, t, table=True)} | {fm.get('name') or ''} | {fm.get('nav') or '—'} {fm.get('nav_ccy') or ''} "
                     f"| {fm.get('nav_date') or '—'} | {fm.get('quote') or '—'} {fm.get('quote_ccy') or ''} |")
        docs = read_jsonl(os.path.join(d, "docs", "index.jsonl"))
        if docs:
            unread = [r for r in docs if (r.get("status") or "") != "прочитан"]
            L.append(f"\n## Документы ({len(docs)}, не прочитано: {len(unread)})")
            for r in (unread + [r for r in docs if r not in unread][::-1])[:25]:
                L.append(f"- {'🆕 ' if r in unread else ''}{link('docs/' + doc_base(r), r.get('label') or doc_base(r))} — {(r.get('found') or '')[:10]}")
        if os.path.exists(os.path.join(d, "journal.md")):
            L.append(f"\nХронология дела: {dlink('journal')}")
    else:
        wl = watchlist()
        ids = entity_dirs()
        L.append(f"\n## Бумаги ({len(ids)})\n\n| Бумага | Название | Статус | Вердикт | Скоринг | Разбор |\n|---|---|---|---|---|---|")
        for i in ids:
            x = wl.get(i) or {}
            la = x.get("last_analysis") or {}
            L.append(f"| {link(f'{i}/{i}', i, table=True)} | {x.get('emitent') or x.get('name') or ''} | "
                     f"{'наблюдение' if i in wl else 'архив'} | {la.get('verdict') or '—'} | "
                     f"{la.get('score') if la.get('score') is not None else '—'} | {la.get('date') or 'не было'} |")
        if KIND == "bonds" and os.path.isdir(os.path.join(d, "issuers")):
            L.append("\n## Эмитенты")
            L += [f"- {link('issuers/' + f[:-3], f[:-3])}" for f in md_files(os.path.join(d, "issuers"))]
        pf = os.path.join(d, "_portfolio")
        if os.path.isdir(pf):
            L.append("\n## Семейный офис (личное: в .gitignore хранилища)")
            for sub, lab in (("briefs", "брифинг"), ("reviews", "обзор")):
                fs = md_files(os.path.join(pf, sub))
                if fs:
                    L.append(f"- последний {lab}: {link(f'_portfolio/{sub}/{fs[-1][:-3]}', fs[-1][:-3])}")
            if os.path.exists(os.path.join(pf, "journal.md")):
                L.append(f"- {link('_portfolio/journal', 'хронология портфеля')}")
    tp = md_files(shared("topics"))
    if tp:
        L.append("\n## Темы")
        L += [f"- {link('topics/' + f[:-3], f[:-3])}" for f in tp]
    dg = [f for f in md_files(os.path.join(d, "digests"))]
    if dg:
        L.append("\n## Дайджесты")
        L += [f"- {dlink('digests/' + f[:-3], f[:-3])}" for f in dg[::-1][:8]]
    lint_p = os.path.join(d, "wiki-lint.md")
    if os.path.exists(lint_p):
        fm, _ = parse_fm(read(lint_p))
        L.append(f"\n## Проверка вики\n\nПоследняя: {fm.get('date')} — ошибок {fm.get('errors', 0)}, "
                 f"предупреждений {fm.get('warnings', 0)}, задач {fm.get('todo', 0)} → {dlink('wiki-lint')}")
    lt = log_tail(15)
    if lt:
        L.append("\n## Что агент узнал недавно (log)")
        L += lt[::-1]
    p = os.path.join(d, "index.md")
    fm0, body = parse_fm(read(p))
    fm = {"type": "index", "desk": KIND, "tags": tags("type/index")}
    save_note(p, fm, set_auto(body or f"# Индекс базы знаний: {KIND_LABEL}\n\n", "\n".join(L)))


HOME = """# Инвестиционная база знаний

Хранилище Obsidian, которое ведут ИИ-агенты семейства: ru-stocks-agent (акции), ru-portfolio-agent
(семейный офис), ru-bonds-agent (облигации), finex-agent (ликвидация FinEx). Агент пишет заметки сам;
вы читаете, правите и дополняете их в Obsidian. Правила формата — `references/wiki.md` в любом из агентов.

{auto}

## Как пользоваться
- **Индекс** каждого хранилища — список бумаг с вердиктами, темы, дайджесты, последние выводы.
- **Хаб бумаги** (`SBER/SBER.md`, `funds/FXUS.md`) — всё по одной бумаге: тезис, разборы, отчёты, новости.
- **log.md** — журнал знаний: что агент узнал, исправил, на каком источнике.
- **topics/** — сквозные темы (ставка ЦБ, отрасли, регулирование), общие для всех агентов.
- **digests/** — недельные дайджесты. **_templates/** — шаблоны заметок (плагин «Шаблоны»).
- Плагин Dataview (необязательно) превращает свойства заметок в живые таблицы:

```dataview
TABLE verdict AS "Вердикт", score AS "Скоринг", last_analysis AS "Разбор"
FROM #type/security OR #type/bond
SORT score DESC
```

```dataview
TABLE period, date FROM #type/report WHERE contains(file.content, "{todo}") SORT date DESC
```

Личные данные (портфель, количества, входящие сообщения) лежат в файлах из `.gitignore` и в вики не попадают.
"""


def build_home():
    p = shared("Home.md")
    v = vault()
    rows = []
    for dirpath, dirs, files in os.walk(v):
        dirs[:] = [x for x in dirs if not x.startswith((".", "_")) and dirpath.count(os.sep) - v.count(os.sep) < 2]
        if "index.md" in files:
            fm, _ = parse_fm(read(os.path.join(dirpath, "index.md")))
            if fm.get("type") == "index":
                r = rel(os.path.join(dirpath, "index"), v)
                lab = {"stocks": "Акции и портфель", "bonds": "Облигации", "finex": "Фонды FinEx"}.get(fm.get("desk"), r)
                rows.append(f"- {link(r, lab)} · журнал знаний: {link(r[:-5] + 'log' if r != 'index' else 'log', 'log')}")
    auto = "## Хранилища агентов\n\n" + ("\n".join(sorted(rows)) or "- пока пусто")
    text = read(p)
    if not text:
        body = HOME.replace("{auto}", f"{AUTO_START}\n{auto}\n{AUTO_END}").replace("{todo}", TODO_MARK)
        write(p, with_fm({"type": "home", "tags": ["type/home"]}, body))
    else:
        fm, body = parse_fm(text)
        write_if_changed(p, with_fm(fm or {"type": "home"}, set_auto(body, auto)))


def log_entry(text, entity=None, kind="факт", source=""):
    """Журнал знаний: одна строка на один усвоенный факт/вывод/поправку. Только добавление."""
    p = os.path.join(desk(), "log.md")
    if not os.path.exists(p):
        write(p, with_fm({"type": "log", "desk": KIND, "tags": tags("type/log")},
                         f"# Журнал знаний: {KIND_LABEL}\n\nЧто агент узнал, вывел или исправил — одна строка на факт, "
                         "с источником. Только добавление.\n\n"))
    if entity:
        eid = entity.upper()
        ent = link(f"funds/{eid}", eid) if KIND == "finex" else link(f"{eid}/{eid}", eid)
    else:
        ent = "общее"
    line = f"- {today()} · {ent} · {kind} · {text.strip()}" + (f" — {source}" if source else "") + f" · {AGENT_NAME}\n"
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(line)
    return line.strip()


# ---------- Шаблоны и инициализация ----------
TEMPLATES = {
    "topic": ("type: topic\nentities: []\nsource: \ntags: [type/topic]\ncreated: {{date}}\nupdated: {{date}}",
              "# {{title}}\n\n## Суть\n\n_Агент: 3–5 предложений; каждая цифра — со ссылкой на источник и датой._\n\n"
              "## Как влияет на бумаги\n\n- (ссылка на хаб бумаги) — \n\n## Факты\n\n- показатель:: значение (источник, дата)\n\n"
              "## Открытые вопросы\n\n## Источники\n\n"),
    "source": ("type: source\nentity: \nup: \ndate: {{date}}\nsource: \ntags: [type/source]",
               "# {{title}}\n\n**Где:** URL · **Дата публикации:** · **Кто автор:**\n\n## Конспект\n\n"
               "_Агент: главное своими словами, цифры — со страницей/абзацем._\n\n## Что это меняет\n\n"
               "## Надёжность\n\n✅ первоисточник / ⚠️ частично / 💬 слух / ❌ опровергнуто — чем подтверждено\n"),
    "report": ("type: report\nentity: \nup: \ndate: {{date}}\nperiod: \nsource: \ntags: [type/report]",
               "# Отчёт: {{title}}\n\n## Конспект\n\n_Агент: ключевые цифры с номерами страниц, что изменилось "
               "против прошлого периода, что это значит для тезиса._\n\n## Что проверить дальше\n\n"),
    "analysis": ("type: analysis\nentity: \nup: \ndate: {{date}}\nverdict: \nscore: \ntarget: \ntags: [type/analysis]",
                 "# Разбор: {{title}}\n\n## TL;DR\n\n## Аргументы за\n\n## Аргументы против (адвокат дьявола)\n\n"
                 "## Оценка\n\n## Триггеры пересмотра\n\n## Источники\n\n"),
    "news": ("type: news\nentity: \nup: \ndate: {{date}}\nsource: \nstatus: \ntags: [type/news]",
             "# {{title}}\n\n**Статус проверки:** ✅/⚠️/💬/❌ · **Первоисточник:** \n\n## Что произошло\n\n## Влияние на тезис\n\n"),
    "decision": ("type: decision\nentity: \nup: \ndate: {{date}}\nstatus: \ntags: [type/decision]",
                 "# Решение: {{title}}\n\n## Контекст\n\n## Варианты\n\n## Что решено и почему\n\n"
                 "## Когда пересмотреть\n\n_Решение принимает пользователь; агент фиксирует аргументы._\n"),
    "digest": ("type: digest\nweek: \nperiod: \ntags: [type/digest]",
               "# Дайджест: {{title}}\n\n## Выводы недели\n\n## Что проверить на следующей неделе\n\n"),
    "issuer": ("type: issuer\nname: \ninn: \ntags: [type/issuer]",
               "# {{title}}\n\n## Кредитный профиль\n\n## Рейтинги\n\n## Суды и существенные факты\n\n## Заметки\n\n"),
}

OBSIDIAN = {
    "app.json": {"alwaysUpdateLinks": True, "newLinkFormat": "shortest", "useMarkdownLinks": False,
                 "attachmentFolderPath": "./", "showUnsupportedFiles": True,
                 "userIgnoreFilters": ["__pycache__/"]},
    "templates.json": {"folder": "_templates", "dateFormat": "YYYY-MM-DD"},
    "graph.json": {"colorGroups": [
        {"query": "tag:#type/security OR tag:#type/bond OR tag:#type/fund", "color": {"a": 1, "rgb": 14701138}},
        {"query": "tag:#type/topic OR tag:#type/issuer", "color": {"a": 1, "rgb": 5431378}},
        {"query": "tag:#type/analysis OR tag:#type/thesis", "color": {"a": 1, "rgb": 11621088}},
        {"query": "tag:#type/report OR tag:#type/source", "color": {"a": 1, "rgb": 8421504}}]},
}

GITIGNORE = """# Личные данные — не публиковать, даже если хранилище в git/облаке
portfolio.json
_portfolio/
_private/
inbox/
reports/payout-*
*.ods
*.xlsx
*.xls
# служебное Obsidian
.obsidian/workspace*
.obsidian/cache
.trash/
"""

SEED_TOPICS = {
    "Ключевая ставка ЦБ": "Ключевая ставка Банка России: текущий уровень, траектория, консенсус, влияние на ставку "
                          "дисконтирования, доходности ОФЗ, флоатеры и дивидендные акции.",
    "Ликвидация FinEx": "Ликвидация FinEx Funds ICAV и FinEx Physically Backed Funds ICAV: факты дела, хронология, "
                        "механизм выплат держателям через НРД. Подтверждённые факты — со ссылкой на документ.",
}


def ensure_vault(verbose=False):
    v, d = vault(), desk()
    made = []
    od = os.path.join(v, ".obsidian")
    if not os.path.isdir(od):
        os.makedirs(od)
        for fn, data in OBSIDIAN.items():
            write(os.path.join(od, fn), json.dumps(data, ensure_ascii=False, indent=2))
        made.append(".obsidian/")
    td = shared("_templates")
    for name, (fm, body) in TEMPLATES.items():
        p = os.path.join(td, name + ".md")
        if not os.path.exists(p):
            write(p, f"---\n{fm}\n---\n{body}")
            made.append(f"_templates/{name}.md")
    gi = os.path.join(v, ".gitignore")
    cur = read(gi)
    missing = [ln for ln in GITIGNORE.splitlines() if ln and not ln.startswith("#") and ln not in cur.splitlines()]
    if missing or not cur:
        write(gi, (cur.rstrip() + "\n\n" if cur.strip() else "") + GITIGNORE)
        made.append(".gitignore")
    seeds = ["Ликвидация FinEx"] if KIND == "finex" else ["Ключевая ставка ЦБ"]
    for t in seeds:
        p = shared("topics", t + ".md")
        if not os.path.exists(p):
            fm = {"type": "topic", "entities": [], "tags": ["type/topic"], "created": today(), "updated": today()}
            write(p, with_fm(fm, f"# {t}\n\n## Суть\n\n{SEED_TOPICS[t]}\n\n{TODO_MARK} дополнить свежими фактами "
                                 "с источником и датой._\n\n## Факты\n\n## Как влияет на бумаги\n\n## Источники\n\n"))
            made.append(f"topics/{t}.md")
    if not os.path.exists(os.path.join(d, "log.md")):
        log_entry("Создана база знаний (Obsidian-вики)", kind="служебное")
        made.append("log.md")
    if verbose:
        print("Создано: " + (", ".join(made) if made else "ничего — всё уже на месте"))
    return made


def sync(ids=None, quiet=False):
    """Пересобрать вики по данным хранилища. Идемпотентно; ручные заметки вне авто-блоков не трогает."""
    ensure_vault()
    d = desk()
    ch = 0
    if KIND == "finex":
        normalize([os.path.join(dp, f) for dp, _, fs in os.walk(d) for f in fs if f.endswith(".md")
                   and not rel(os.path.join(dp, f)).startswith(("docs/", "funds/", "digests/"))
                   and rel(os.path.join(dp, f)) not in ("index.md", "log.md", "wiki-lint.md")])
        ch += finex_hubs() + finex_docs()
    else:
        ENTITIES_CACHE.clear()
        ENTITIES_CACHE.update(entity_dirs())
        targets = [i for i in (ids or ENTITIES_CACHE) if i in ENTITIES_CACHE]
        paths = []
        for i in targets:
            for dp, _, fs in os.walk(os.path.join(d, i)):
                paths += [os.path.join(dp, f) for f in fs if f.endswith(".md")]
        pf = os.path.join(d, "_portfolio")
        if os.path.isdir(pf):
            paths += [os.path.join(dp, f) for dp, _, fs in os.walk(pf) for f in fs if f.endswith(".md")]
        normalize(paths)
        for i in targets:
            ch += report_notes(i)
            ch += bool(hub_stock(i))
        if KIND == "bonds":
            ch += issuer_notes()
    if not os.path.exists(shared("Home.md")):
        build_home()  # сначала стартовая страница, чтобы индекс сразу на неё сослался
    build_index()
    build_home()
    if not quiet:
        print(f"Вики синхронизирована: {d} (Obsidian-хранилище: {vault()}); обновлено заметок: {ch}")


def safe_sync(ids=None):
    """Вызов из других скриптов: сбой вики никогда не ломает основную работу."""
    try:
        sync(ids, quiet=True)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ вики не обновлена: {e} (python wiki.py sync)", file=sys.stderr)


def safe_log(text, entity=None, kind="факт", source=""):
    try:
        log_entry(text, entity, kind, source)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ log.md не обновлён: {e}", file=sys.stderr)


# ---------- Новая заметка ----------
def slugify(s):
    s = re.sub(r"[\\/:*?\"<>|#^\[\]]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:100]


def cmd_note(a):
    ensure_vault()
    typ = a.type
    ent = (a.entity or [None])[0]
    if ent:
        ent = ent.upper()
    title = a.title
    date = a.date or today()
    tpl = read(shared("_templates", typ + ".md"))
    tfm, tbody = parse_fm(tpl) if tpl else ({}, "# {{title}}\n\n")
    body = tbody.replace("{{title}}", title).replace("{{date}}", date)
    fm = {k: v for k, v in tfm.items() if v not in (None, "", [])}
    fm.update({"type": typ, "date": date if typ not in ("topic", "issuer") else None, "agent": AGENT_NAME,
               "created": today(), "updated": today()})
    if a.source:
        fm["source"] = a.source
    if a.entity:
        if typ == "topic":
            fm["entities"] = [e.upper() for e in a.entity]
        else:
            fm["entity"] = ent
            fm["up"] = link(f"funds/{ent}", ent) if KIND == "finex" else link(f"{ent}/{ent}", ent)
    fm["tags"] = sorted(set(tags(f"type/{typ}")) | set(a.tag or []))
    fm = {k: v for k, v in fm.items() if v not in (None, "", [])}
    name = slugify(title)
    if typ == "topic":
        p = shared("topics", name + ".md")
    elif typ == "issuer":
        p = os.path.join(desk(), "issuers", name + ".md")
    elif typ == "digest":
        p = os.path.join(desk(), "digests", name + ".md")
    else:
        sub = {"source": "sources", "news": "news", "analysis": "analyses", "decision": "decisions",
               "report": "reports"}.get(typ, typ + "s")
        base = os.path.join(desk(), ent) if ent and KIND != "finex" else desk()
        p = os.path.join(base, sub, f"{date}-{name}.md" if typ != "report" else f"{name}.md")
    if os.path.exists(p):
        print(f"Уже есть: {p}")
        return
    write(p, with_fm(fm, body))
    print(f"Создано: {p}")
    if a.entity and KIND != "finex":
        safe_sync([e.upper() for e in a.entity])


# ---------- Поиск ----------
def all_notes(root=None):
    root = root or vault()
    out = []
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if x not in (".obsidian", ".git", ".trash", "__pycache__")]
        out += [os.path.join(dp, f) for f in fs if f.endswith(".md")]
    return sorted(out)


def note_entity(p, fm):
    if fm.get("entity"):
        return str(fm["entity"])
    if fm.get("id"):
        return str(fm["id"])
    return None


def cmd_find(a):
    pat = re.compile(a.query, re.I) if a.query else None
    hits = 0
    for p in all_notes():
        if "/_templates/" in p.replace(os.sep, "/"):
            continue
        text = read(p)
        fm, body = parse_fm(text)
        if a.type and fm.get("type") != a.type:
            continue
        if a.entity:
            e = a.entity.upper()
            ents = [str(x) for x in (fm.get("entities") or [])]
            if note_entity(p, fm) != e and e not in ents:
                continue
        if a.since and str(fm.get("date") or fm.get("updated") or "") < a.since:
            continue
        r = rel(p, vault())
        if not pat:
            print(f"{r}  [{fm.get('type', '?')}] {fm.get('date') or fm.get('updated') or ''}")
            hits += 1
        else:
            for n, line in enumerate(text.splitlines(), 1):
                if pat.search(line):
                    print(f"{r}:{n}: {line.strip()[:220]}")
                    hits += 1
                    if hits >= a.limit:
                        break
        if hits >= a.limit:
            print(f"… показаны первые {a.limit}")
            break
    if not hits:
        print("Ничего не найдено. Вики пуста по этому вопросу — ищи в первоисточниках и запиши найденное.")


# ---------- Проверка (lint) ----------
LINK_RE = re.compile(r"(!?)\[\[([^\]\|#\^\\]+)(?:[#\^][^\]\|]*)?(?:\\?\|[^\]]*)?\]\]")
FIELD_RE = re.compile(r"^\s*(?:[-*]\s+)?([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё_ -]{1,40})::\s*(.+?)\s*$")
NUM_RE = re.compile(r"\d[\d\s ]*(?:[.,]\d+)?\s?(?:₽|руб|млрд|млн|трлн|тыс|%|\$|USD|EUR|CNY|б\.п\.)", re.I)
CITE_RE = re.compile(r"\[\[|https?://|источник|стр\.\s*\d|\(.*\d{4}.*\)|cbr\.ru|moex|e-disclosure|скрипт|\.py", re.I)
MANAGED = {"type", "id", "ticker", "isin", "name", "issuer", "entity", "up", "date", "tags", "aliases", "created",
           "updated", "status", "agent", "private", "desk", "entities", "source", "title", "week", "period", "doc_kind"}


def norm_val(v):
    return re.sub(r"\s+", "", str(v)).replace(",", ".").lower().rstrip(".")


def cmd_lint(a):
    v = vault()
    notes = [p for p in all_notes(v) if "/_templates/" not in p.replace(os.sep, "/")]
    files = []
    for dp, dirs, fs in os.walk(v):
        dirs[:] = [x for x in dirs if x not in (".obsidian", ".git", ".trash", "__pycache__")]
        files += [rel(os.path.join(dp, f), v).lower() for f in fs]
    noext = {rel(p, v)[:-3].lower() for p in notes}
    errors, warns, todo = [], [], []
    inbound = {rel(p, v)[:-3].lower(): 0 for p in notes}
    facts = {}  # entity -> key -> {value: [notes]}
    wl = watchlist() if KIND != "finex" else {}

    def resolve(t):
        t = t.strip().replace("\\", "/").lower()
        if "." in os.path.basename(t) and not t.endswith(".md"):
            return [f for f in files if f == t or f.endswith("/" + t)]
        t = t[:-3] if t.endswith(".md") else t
        return [n for n in noext if n == t or n.endswith("/" + t)]

    for p in notes:
        r = rel(p, v)
        text = read(p)
        fm, body = parse_fm(text)
        typ = fm.get("type")
        if not fm:
            warns.append(f"{r}: нет frontmatter (type, entity, date, source)")
        elif not typ:
            warns.append(f"{r}: в frontmatter нет type")
        else:
            miss = [k for k in REQUIRED.get(typ, []) if fm.get(k) in (None, "", [])]
            if miss:
                warns.append(f"{r}: [{typ}] не заполнено: {', '.join(miss)}")
        # ссылки (в т.ч. из frontmatter: up, entities)
        for m in LINK_RE.finditer(text):
            target = m.group(2).strip()
            hit = resolve(target)
            if not hit:
                errors.append(f"{r}: битая ссылка [[{target}]]")
            for h in hit:
                if h in inbound and h != r[:-3].lower():
                    inbound[h] += 1
        # заглушки
        if (TODO_MARK in body or "_заполнить_" in body) and typ != "home":
            what = "нужен конспект" if typ in ("report", "source") else "есть незаполненные разделы"
            todo.append(f"{r}: {what}")
        if typ in CURRENT_TYPES:
            upd = str(fm.get("updated") or "")[:10]
            try:
                age = (dt.date.today() - dt.date.fromisoformat(upd)).days if upd else \
                    (dt.date.today() - dt.date.fromtimestamp(os.path.getmtime(p))).days
            except ValueError:
                age = 0
            if age > STALE_DAYS and typ in ("thesis", "topic", "profile"):
                warns.append(f"{r}: не обновлялась {age} дн. — проверить актуальность")
            manual = strip_auto(body)
            has_src_col = bool(re.search(r"^\|.*Источник.*\|", manual, re.M))
            uncited = []
            for line in manual.splitlines():
                s_ = line.strip()
                if not s_ or s_.startswith(("#", ">", "<!--", "|---")) or (s_.startswith("|") and has_src_col):
                    continue
                if NUM_RE.search(s_) and not CITE_RE.search(s_):
                    uncited.append(s_[:120])
            if uncited:
                warns.append(f"{r}: цифры без источника ({len(uncited)}): «{uncited[0]}»" + (" …" if len(uncited) > 1 else ""))
            ent = note_entity(p, fm)
            if typ in ("profile", "thesis", "financials") and r.count("/") >= 1:
                ent = r.split("/")[-2]
            if ent:
                for k, val in fm.items():
                    if k not in MANAGED and not isinstance(val, (list, dict)) and val not in (None, ""):
                        facts.setdefault(ent, {}).setdefault(k, {}).setdefault(norm_val(val), []).append(r)
                for line in strip_auto(body).splitlines():
                    fmatch = FIELD_RE.match(line)
                    if fmatch:
                        k = fmatch.group(1).strip().lower()
                        val = re.split(r"\s+\(", fmatch.group(2))[0]
                        facts.setdefault(ent, {}).setdefault(k, {}).setdefault(norm_val(val), []).append(r)
        # тезис против итога последнего комитета
        if typ == "thesis" and KIND != "finex":
            eid = r.split("/")[-2]
            la = (wl.get(eid) or {}).get("last_analysis") or {}
            for k in ("verdict", "target", "score"):
                if fm.get(k) not in (None, "") and la.get(k) not in (None, "") and norm_val(fm[k]) != norm_val(la[k]):
                    warns.append(f"{r}: противоречие — {k} в тезисе «{fm[k]}», в итоге комитета (desk.py analysed) «{la[k]}»")
    for ent, keys in facts.items():
        for k, vals in keys.items():
            if len(vals) > 1:
                desc = "; ".join(f"«{val}» в {', '.join(sorted(set(ps)))}" for val, ps in vals.items())
                warns.append(f"противоречие [{ent}] {k}: {desc}")
    skip_orphan = {"index", "log", "home", "journal", "wiki-lint", "readme"}
    for n, cnt in inbound.items():
        base = n.rsplit("/", 1)[-1]
        if cnt == 0 and base not in skip_orphan and not n.startswith(("_portfolio/", "_templates/")) \
                and "/_portfolio/" not in n and "/inbox/" not in n and not n.startswith("inbox/"):
            fm, _ = parse_fm(read(os.path.join(v, n + ".md")))
            if fm.get("type") in ("topic", "source", "news", "decision", "issuer", "report", "analysis"):
                warns.append(f"{n}.md: сирота — на заметку никто не ссылается (свяжи с бумагой или темой)")
    # данные агента
    if KIND == "finex":
        for r_ in read_jsonl(os.path.join(desk(), "docs", "index.jsonl")):
            if (r_.get("status") or "") != "прочитан":
                todo.append(f"{rel(os.path.join(desk(), 'docs', doc_base(r_)), v)}.md: документ не прочитан (docs.py --read ... --summary)")
        ids, ref, navs, _ = finex_funds()
        for n_ in navs:
            want = (ref.get(n_.get("ticker")) or {}).get("ccy")
            if want and n_.get("ccy") and n_["ccy"] != want:
                warns.append(f"противоречие [{n_.get('ticker')}] валюта СЧА {n_['ccy']} ≠ валюта класса паёв {want} "
                             f"(nav.jsonl, {n_.get('date')}, {n_.get('source')})")
            if not n_.get("source"):
                warns.append(f"[{n_.get('ticker')}] СЧА {n_.get('nav')} на {n_.get('date')} без источника")
    else:
        for eid, x in wl.items():
            la = x.get("last_analysis") or {}
            if not la:
                todo.append(f"{eid}: под наблюдением, но разбора ещё не было")
            elif la.get("date"):
                age = (dt.date.today() - dt.date.fromisoformat(la["date"])).days
                if age > STALE_DAYS:
                    warns.append(f"{eid}: последний разбор {age} дн. назад — пора пересмотреть")
            if not os.path.exists(os.path.join(desk(), eid, f"{eid}.md")):
                todo.append(f"{eid}: нет хаб-заметки (python wiki.py sync)")

    print(f"Проверка вики: {v}\nошибок {len(errors)}, предупреждений {len(warns)}, задач {len(todo)}")
    for title, items in (("Ошибки", errors), ("Предупреждения", warns), ("Задачи", todo)):
        if items:
            print(f"\n## {title}")
            print("\n".join(f"- {x}" for x in items[:a.limit]))
            if len(items) > a.limit:
                print(f"- … ещё {len(items) - a.limit}")
    if a.save:
        fm = {"type": "lint", "date": today(), "errors": len(errors), "warnings": len(warns), "todo": len(todo),
              "tags": tags("type/lint")}
        out = [f"# Проверка вики {today()}\n",
               "Что исправить: битые ссылки, заметки без frontmatter, цифры без источника, противоречия "
               "(разберись по первоисточнику и оставь верное значение, исправление — в log.md как «поправка»).\n"]
        for title, items in (("Ошибки", errors), ("Предупреждения", warns), ("Задачи", todo)):
            out.append(f"\n## {title} ({len(items)})\n")
            out += [f"- {x}" for x in items] or ["- нет"]
        write(os.path.join(desk(), "wiki-lint.md"), with_fm(fm, "\n".join(out) + "\n"))
        build_index()
        print(f"\nОтчёт: {os.path.join(desk(), 'wiki-lint.md')}")


# ---------- Недельный дайджест ----------
def week_range(w):
    if w:
        y, n = re.fullmatch(r"(\d{4})-W(\d{1,2})", w).groups()
        start = dt.date.fromisocalendar(int(y), int(n), 1)
    else:
        t = dt.date.today()
        start = t - dt.timedelta(days=t.weekday())
    end = start + dt.timedelta(days=6)
    y, n, _ = start.isocalendar()
    return f"{y}-W{n:02d}", start, end


def in_range(s, start, end):
    try:
        x = dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return False
    return start <= x <= end


def cmd_digest(a):
    ensure_vault()
    wk, start, end = week_range(a.week)
    d = desk()
    L = [f"Период: {start} — {end} · хранилище: {KIND_LABEL} · {dlink('index', 'индекс')}"]
    lg = [ln for ln in journal_lines(os.path.join(d, "log.md")) if in_range(ln[2:12], start, end)]
    L.append(f"\n## Что агент узнал ({len(lg)})")
    L += lg or ["- ничего не записано в log.md"]
    ev, ana, reps, news, frm = [], [], [], [], []
    if KIND == "finex":
        ev = [ln for ln in journal_lines(os.path.join(d, "journal.md")) if in_range(ln[2:12], start, end)]
        reps = [f"- {link('docs/' + doc_base(r), r.get('label') or doc_base(r))} — {r.get('status')}"
                for r in read_jsonl(os.path.join(d, "docs", "index.jsonl")) if in_range(r.get("found"), start, end)]
        news = [f"- {(n.get('published') or n.get('first_seen') or '')[:10]} [{(n.get('title') or '').replace('[', '(').replace(']', ')')}]({n.get('link')})"
                for n in read_jsonl(os.path.join(d, "news.jsonl")) if n.get("important") and in_range(n.get("first_seen"), start, end)]
        frm = [f"- {link('forums/' + f[:-3], f[:-3])}" for f in md_files(os.path.join(d, "forums")) if in_range(f[:10], start, end)]
    else:
        for i in entity_dirs():
            root = os.path.join(d, i)
            ev += [f"{ln[:2]}{link(f'{i}/{i}', i)} {ln[2:]}" for ln in journal_lines(os.path.join(root, "journal.md")) if in_range(ln[2:12], start, end)]
            ana += [f"- {link(f'{i}/analyses/{f[:-3]}', f'{i}: {f[:-3]}')}" for f in md_files(os.path.join(root, "analyses")) if in_range(f[:10], start, end)]
            idx = report_index(i)
            reps += [f"- {link(f'{i}/reports/{b}', f'{i}: {b}')}" for b, m in idx.items() if in_range(m.get("date"), start, end)]
            news += [f"- {link(f'{i}/{i}', i)} {(n.get('published') or n.get('first_seen') or '')[:10]} "
                     f"[{(n.get('title') or '').replace('[', '(').replace(']', ')')}]({n.get('link')})"
                     for n in read_jsonl(os.path.join(root, "news", "news.jsonl")) if n.get("important") and in_range(n.get("first_seen"), start, end)]
            frm += [f"- {link(f'{i}/forums/{f[:-3]}', f'{i}: {f[:-3]}')}" for f in md_files(os.path.join(root, "forums")) if in_range(f[:10], start, end)]
    L.append(f"\n## События ({len(ev)})")
    L += ev or ["- нет"]
    if KIND != "finex":
        L.append(f"\n## Разборы комитета ({len(ana)})")
        L += ana or ["- не было"]
    L.append(f"\n## Новые отчёты и документы ({len(reps)})")
    L += reps or ["- нет"]
    L.append(f"\n## Существенные новости ⚑ ({len(news)})")
    L += news[:30] or ["- нет"]
    if frm:
        L.append(f"\n## Срезы форумов ({len(frm)})")
        L += frm
    lint_p = os.path.join(d, "wiki-lint.md")
    if os.path.exists(lint_p):
        fm, _ = parse_fm(read(lint_p))
        L.append(f"\n## Состояние вики\n\nПроверка {fm.get('date')}: ошибок {fm.get('errors')}, предупреждений "
                 f"{fm.get('warnings')}, задач {fm.get('todo')} → {dlink('wiki-lint')}")
    p = os.path.join(d, "digests", f"{wk}-{KIND}.md")
    fm0, body = parse_fm(read(p))
    fm = dict(fm0)
    fm.update({"type": "digest", "week": wk, "period": f"{start} — {end}", "desk": KIND,
               "tags": tags("type/digest"), "updated": today()})
    fm.setdefault("created", today())
    if not body.strip():
        body = (f"# Дайджест {wk}: {KIND_LABEL}\n\n{AUTO_START}\n{AUTO_END}\n\n## Выводы недели\n\n{TODO_MARK} 3–7 пунктов: "
                "что изменилось в тезисах, что подтвердилось/опроверглось, где нужно действие пользователя._\n\n"
                "## Что проверить на следующей неделе\n\n")
    body = set_auto(body, "\n".join(L))
    write(p, with_fm(fm, body))
    build_index()
    print(f"Дайджест: {p}\nДальше: прочитай его, допиши «Выводы недели» и отправь пользователю коротко.")


# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("init")
    p = sp.add_parser("sync")
    p.add_argument("ids", nargs="*")
    p = sp.add_parser("log")
    p.add_argument("text")
    p.add_argument("--entity")
    p.add_argument("--kind", default="факт", help="факт | вывод | поправка | источник | тезис | решение | вопрос")
    p.add_argument("--source", default="")
    p = sp.add_parser("note")
    p.add_argument("type", choices=sorted(TEMPLATES))
    p.add_argument("title")
    p.add_argument("--entity", action="append")
    p.add_argument("--source")
    p.add_argument("--date")
    p.add_argument("--tag", action="append")
    p = sp.add_parser("find")
    p.add_argument("query", nargs="?")
    p.add_argument("--type")
    p.add_argument("--entity")
    p.add_argument("--since")
    p.add_argument("--limit", type=int, default=60)
    p = sp.add_parser("lint")
    p.add_argument("--save", action="store_true")
    p.add_argument("--limit", type=int, default=40)
    p = sp.add_parser("digest")
    p.add_argument("--week", help="YYYY-Www, по умолчанию текущая неделя")
    sp.add_parser("path")
    a = ap.parse_args()
    if a.cmd == "init":
        ensure_vault(verbose=True)
        sync()
        print(f"Откройте в Obsidian папку {vault()} («Открыть папку как хранилище»). Правила — references/wiki.md.")
    elif a.cmd == "sync":
        sync([i.upper() for i in a.ids] or None)
    elif a.cmd == "log":
        print(log_entry(a.text, a.entity, a.kind, a.source))
        if a.entity:
            safe_sync([a.entity.upper()])
    elif a.cmd == "note":
        cmd_note(a)
    elif a.cmd == "find":
        cmd_find(a)
    elif a.cmd == "lint":
        cmd_lint(a)
    elif a.cmd == "digest":
        cmd_digest(a)
    elif a.cmd == "path":
        print(f"хранилище агента: {desk()}\nObsidian-хранилище: {vault()}")


if __name__ == "__main__":
    main()
