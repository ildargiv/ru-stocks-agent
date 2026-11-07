"""Извлечение ключевых цифр из PDF-отчётности (МСФО / РСБУ / презентация / пресс-релиз / годовой отчёт).
Скачивает PDF по ссылке (или берёт локальный файл), достаёт текст и находит строки с ключевыми
показателями — чтобы агент быстро сверил цифры и затем прочитал нужные страницы целиком.

  python report_extract.py https://example.ru/ifrs_2026_h1.pdf
  python report_extract.py report.pdf --dump report.txt     # весь текст в файл для чтения
  python report_extract.py report.pdf --grep "дивиденд|байбэк|казначейск"
  python report_extract.py <URL> --save SBER --name 2026-9m-ifrs
      # архив в папку акции: reports/2026-9m-ifrs.pdf + .txt и строка в reports/index.md

Нужен один из: `pip install pypdf` (предпочтительно) или утилита pdftotext (poppler-utils).
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

KEYS = {
    "Выручка": r"выручк|revenue",
    "EBITDA": r"ebitda|oibda",
    "Операционная прибыль": r"операционн\w+ прибыл|прибыль от продаж|operating (profit|income)",
    "Финансовые расходы / проценты": r"финансов\w+ расход|процент\w* к уплате|процентн\w+ расход|finance (costs|expense)|interest expense",
    "Чистая прибыль": r"чист\w+ (прибыл|убыт)|net (profit|income|loss)|прибыль за (период|год)|убыток за (период|год)",
    "Кредиты и займы": r"кредит\w* и займ|заёмн\w+ средств|заемн\w+ средств|borrowings|loans and borrowings",
    "Облигации": r"облигаци",
    "Аренда": r"обязательств\w+ по аренд|lease liabilit",
    "Денежные средства": r"денежн\w+ средств\w* и (их )?эквивалент|cash and cash equivalents",
    "Чистый долг": r"чист\w+ долг|net debt",
    "Капитал": r"итого капитал|собственн\w+ капитал|total equity",
    "Операционный денежный поток": r"денежн\w+ (поток|средства)\w* от операционн|net cash (from|generated).{0,20}operating",
    "Капзатраты": r"приобретени\w+ основн\w+ средств|капитальн\w+ (затрат|вложени)|capex|purchase of property",
    "Ковенанты": r"ковенант|covenant",
    "Непрерывность деятельности": r"непрерывност\w+ деятельност|going concern|существенн\w+ неопределенност",
    "Аудитор / мнение": r"мнение с оговорк|отказ от выражения мнения|модифицированн\w+ мнени|аудиторск\w+ заключени",
    "Связанные стороны": r"связанн\w+ сторон|related part",
    "Поручительства/гарантии": r"поручительств|выданн\w+ гаранти|guarantees issued",
    "События после отчётной даты": r"событи\w+ после отчетн|событи\w+ после отчётн|subsequent events",
    "Дивиденды": r"дивиденд|dividend",
    "Прибыль на акцию": r"прибыль на акцию|прибыль на одну акци|earnings per share|EPS",
    "Количество акций / казначейские": r"казначейск|выкуплен\w+ собственн|средневзвешенн\w+ количеств|treasury shares",
    "Свободный денежный поток": r"свободн\w+ денежн\w+ поток|free cash flow|FCF",
    "Прогноз / гайденс": r"прогноз|ожидаем|guidance|outlook",
}


def fetch(src):
    if os.path.exists(src):
        return src
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        f.write(r.read())
    return path


def pdf_text(path):
    try:
        from pypdf import PdfReader  # type: ignore
        return [(i + 1, p.extract_text() or "") for i, p in enumerate(PdfReader(path).pages)]
    except ImportError:
        pass
    if shutil.which("pdftotext"):
        txt = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True,
                             encoding="utf-8", errors="ignore").stdout
        return [(i + 1, t) for i, t in enumerate(txt.split("\f"))]
    raise SystemExit("Установите pypdf (pip install pypdf) или poppler-utils (pdftotext).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="URL или путь к PDF")
    ap.add_argument("--grep", help="Свой regex вместо стандартного набора")
    ap.add_argument("--dump", help="Сохранить полный текст в файл")
    ap.add_argument("--max", type=int, default=6, help="Совпадений на показатель")
    ap.add_argument("--save", metavar="TICKER", help="Сохранить PDF и текст в папку акции (reports/)")
    ap.add_argument("--name", help="Имя файла в архиве, напр. 2026-h1-ifrs (по умолчанию из URL)")
    a = ap.parse_args()
    path = fetch(a.src)
    pages = pdf_text(path)
    if a.save:
        import datetime as _dt
        from _iss import ticker_dir, append_journal
        rdir = ticker_dir(a.save, "reports")
        base = re.sub(r"[^\w.-]+", "_", a.name or os.path.splitext(os.path.basename(a.src.split("?")[0]))[0])
        shutil.copyfile(path, os.path.join(rdir, base + ".pdf"))
        a.dump = a.dump or os.path.join(rdir, base + ".txt")
        with open(os.path.join(rdir, "index.md"), "a", encoding="utf-8") as f:
            f.write(f"- {_dt.date.today().isoformat()} | {base} | {len(pages)} стр. | {a.src}\n")
        append_journal(a.save, f"Отчёт добавлен в архив: {base}", a.src)
        print(f"Архив: {os.path.join(rdir, base)}.pdf/.txt")
    print(f"Страниц: {len(pages)}; символов: {sum(len(t) for _, t in pages)}")
    if sum(len(t) for _, t in pages) < 30 * len(pages):
        print("Похоже на скан без текстового слоя — нужен OCR (ocrmypdf) или читайте как изображения.")
    if a.dump:
        with open(a.dump, "w", encoding="utf-8") as f:
            for n, t in pages:
                f.write(f"\n\n===== стр. {n} =====\n{t}")
        print(f"Текст сохранён: {a.dump}")
    keys = {"Поиск": a.grep} if a.grep else KEYS
    for label, rx in keys.items():
        pat = re.compile(rx, re.I)
        hits = []
        for n, t in pages:
            for line in t.splitlines():
                if pat.search(line) and (a.grep or re.search(r"\d", line)):
                    hits.append(f"  стр.{n}: {' '.join(line.split())[:200]}")
        if hits:
            print(f"\n## {label} ({len(hits)})")
            print("\n".join(hits[: a.max]))


if __name__ == "__main__":
    main()
