# Инвесткомитет — агент-аналитик акций РФ

[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) ![Agent Skills](https://img.shields.io/badge/Agent%20Skills-SKILL.md-0E7A5C)

Навык по стандарту Agent Skills · Hermes Agent, OpenClaw, Claude Code, Codex CLI · брат-близнец [ru-bonds-agent](https://github.com/ildargiv/ru-bonds-agent)

Аналитик акций Мосбиржи, который работает как ваш собственный инвестиционный отдел:

- **Список наблюдения.** Говорите «следи за Сбером» — агент ставит акцию на наблюдение и сразу делает первичный глубокий разбор. Акций может быть сколько угодно.
- **Папка на каждую акцию.** Отчёты МСФО/РСБУ (PDF и текст), новости, история цен и дивидендов, проверенные слухи, тезис и протоколы всех разборов копятся в `~/stocks-desk/<ТИКЕР>/`. Каждый следующий разбор опирается на предыдущий: что изменилось и подтвердились ли ожидания.
- **Инвесткомитет вместо одного мнения.** Отраслевой аналитик, долгосрочный инвестор, **адвокат дьявола**, риск-менеджер, дивидендный аналитик и эксперт по корпоративному управлению, макроэкономист пишут мнения независимо; адвокат дьявола атакует; эксперты отвечают; голосование; решение председателя со скорингом, диапазоном стоимости и триггерами пересмотра.
- **Живые данные.** MOEX ISS, ГИР БО ФНС, отчётность компании, e-disclosure, cbr.ru, новости; собственный пересчёт FCF, долга, payout; мультипликаторы, DCF в трёх сценариях, обратный DCF, дивидендная модель.

## Состав
```
SOUL.md                              личность агента (Hermes, OpenClaw)
AGENTS.md                            инструкция для OpenClaw, Codex, Cursor и др.
agents/claude-code/stocks.md         субагент-председатель для Claude Code
agents/claude-code/stocks-devil.md   субагент «адвокат дьявола» для Claude Code
install.sh / install.ps1             установка
skills/finance/ru-stocks/
  SKILL.md                           процедуры: следить, глубокий разбор, обновление, сводка по списку, сравнение
  references/
    expert-panel.md                  инвесткомитет: роли, вопросы, порядок заседания, голосование
    analysis-framework.md            методология: бизнес, финансы, оценка, дивиденды, корпоративное управление РФ, скоринг
    desk-structure.md                что лежит в папке акции и как её вести
    data-sources.md                  где брать данные
    reading-reports.md               как читать МСФО/РСБУ глазами акционера
    news-forums.md                   новости и форумы: сбор и проверка
    report-template.md               протокол комитета и сводки
  scripts/
    desk.py                          список наблюдения, папки, статус изменений, хронология
    moex_share.py                    карточка акции, доходность против IMOEX, дивиденды, архив цен
    news.py                          сбор новостей в папку акции без дублей
    gir_bo.py                        РСБУ из ГИР БО ФНС
    report_extract.py                чтение и архивирование PDF-отчётов
    valuation.py                     мультипликаторы, DCF, обратный DCF, модель Гордона
```

## Папка акции
```
~/stocks-desk/
  watchlist.json
  SBER/
    README.md        профиль компании          thesis.md     текущий тезис и триггеры
    journal.md       хронология событий        analyses/     протоколы комитета по датам
    market/          цены, дивиденды, снимки   financials/   МСФО-таблица, РСБУ
    reports/         PDF и текст отчётов       news/         новости и проверенные события
    forums/          срезы форумов с проверкой
```
Одна папка — для всех сред: начните в Claude Code, продолжите в Hermes. Корень меняется переменной `STOCKS_DESK`.

## Установка
```bash
git clone https://github.com/ildargiv/ru-stocks-agent.git && cd ru-stocks-agent && chmod +x install.sh
```
| Среда | Команда | Что получаете |
|---|---|---|
| Hermes Agent (Linux/macOS/WSL2) | `./install.sh` или `./install.sh hermes-default` | профиль `stocks` с характером из SOUL.md (`stocks chat`), либо навык в основном профиле |
| OpenClaw | `./install.sh openclaw` (Windows: `install.ps1 openclaw`) | навык `ru-stocks`; характер — добавить SOUL.md в workspace или открыть эту папку как workspace |
| Claude Code | `./install.sh claude` (Windows: `powershell -ExecutionPolicy Bypass -File install.ps1 claude`) | навык + субагенты `stocks` и `stocks-devil`; эксперты комитета работают параллельными субагентами |
| Codex CLI | `./install.sh codex` | навык; также читает `AGENTS.md` |
| claude.ai (сайт, приложение) | заархивировать `skills/finance/ru-stocks` в zip и загрузить в Settings → Skills | методология и комитет; скрипты и папки — только где есть доступ к файлам и сети |

Нужен Python 3.9+ (только стандартная библиотека) и `pypdf` для чтения PDF.

## Примеры запросов
- «Следи за Сбером и Северсталью»
- «Разбери Лукойл комитетом: стоит ли докупать после отсечки?»
- «Вышел отчёт МТС за 9 месяцев — что изменилось для акционеров?»
- «Что нового по моим акциям?»
- «Сравни Х5, Магнит и Ленту: кто лучше по соотношению качество/цена?»
- «Каждый будний день в 19:00 проверяй мой список и пиши, только если что-то важное» (cron Hermes / OpenClaw, задачи по расписанию Claude)

## Проверка скриптов вручную
```bash
S=skills/finance/ru-stocks/scripts
python3 $S/desk.py add SBER --note "дивиденды"
python3 $S/moex_share.py SBER --save
python3 $S/news.py SBER --days 30 --all
python3 $S/desk.py status
python3 $S/valuation.py --ticker SBER --net-income 1580 --equity 7300 --dps 34.84 --fcf 800 --growth 5 --rf 15 --erp 6
```

## Лицензия
MIT, см. [LICENSE](LICENSE).

**Важно.** Агент даёт аналитику и образовательную информацию. Это не индивидуальная инвестиционная рекомендация и не финансовый совет. Решения об инвестициях и ответственность за них — на пользователе. Агент может ошибаться: проверяйте ключевые цифры по первоисточникам.
