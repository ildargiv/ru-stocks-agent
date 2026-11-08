#!/usr/bin/env bash
# Установка агента «Инвесткомитет» (акции РФ).
#   ./install.sh                 -> Hermes Agent: отдельный профиль "stocks" (команда `stocks chat`)
#   ./install.sh hermes-default  -> Hermes Agent: только навык в основной профиль (~/.hermes)
#   ./install.sh openclaw        -> OpenClaw: навык ~/.openclaw/skills/ru-stocks
#   ./install.sh claude          -> Claude Code: навык ~/.claude/skills/ru-stocks + субагенты stocks и stocks-devil
#   ./install.sh codex           -> Codex CLI: навык ~/.codex/skills/ru-stocks
#   ./install.sh all             -> всё, что установлено
# Hermes: каталог берётся из HERMES_HOME, на Windows — %LOCALAPPDATA%/hermes, иначе ~/.hermes.
# Данные по акциям хранятся в ~/stocks-desk (переменная STOCKS_DESK) — общие для всех сред.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_SRC="$HERE/skills/finance/ru-stocks"
TARGET="${1:-hermes}"

# Python: python3, python или py (Windows/MSYS)
PY="$(command -v python3 || command -v python || command -v py || true)"
# Каталог Hermes: HERMES_HOME, иначе %LOCALAPPDATA%/hermes на Windows, иначе ~/.hermes
if [[ -n "${HERMES_HOME:-}" ]]; then HH="$HERMES_HOME"
elif [[ -n "${LOCALAPPDATA:-}" && -d "$(cygpath -u "$LOCALAPPDATA" 2>/dev/null || echo "$LOCALAPPDATA")/hermes" ]]; then
  HH="$(cygpath -u "$LOCALAPPDATA" 2>/dev/null || echo "$LOCALAPPDATA")/hermes"
else HH="$HOME/.hermes"; fi

copy_skill() {  # $1 = каталог назначения навыка
  mkdir -p "$(dirname "$1")"
  rm -rf "$1"
  cp -r "$SKILL_SRC" "$1"
  chmod +x "$1/scripts/"*.py
  echo "Навык -> $1"
}

install_hermes() {
  local home="$HH/profiles/stocks"
  if [[ ! -d "$home" ]]; then
    echo "Создаю профиль Hermes 'stocks' (копия текущих ключей/модели)..."
    hermes profile create stocks --clone
  fi
  cp "$HERE/SOUL.md" "$home/SOUL.md"
  echo "SOUL.md -> $home/SOUL.md"
  copy_skill "$home/skills/finance/ru-stocks"
  echo "Hermes: запустите 'stocks chat'"
}

install_hermes_default() {
  copy_skill "$HH/skills/finance/ru-stocks"
  echo "Hermes: новая сессия или /reset"
}

install_openclaw() {
  copy_skill "$HOME/.openclaw/skills/ru-stocks"
  echo "OpenClaw: перезапустите gateway/сессию. Чтобы агент вёл себя как инвесткомитет,"
  echo "  добавьте содержимое $HERE/SOUL.md в SOUL.md вашего workspace (~/.openclaw/workspace/SOUL.md)"
  echo "  или создайте отдельного агента с workspace = эта папка (там уже есть AGENTS.md и SOUL.md)."
}

install_claude() {
  copy_skill "$HOME/.claude/skills/ru-stocks"
  mkdir -p "$HOME/.claude/agents"
  cp "$HERE/agents/claude-code/stocks.md" "$HERE/agents/claude-code/stocks-devil.md" "$HOME/.claude/agents/"
  echo "Субагенты -> $HOME/.claude/agents/stocks.md, stocks-devil.md"
  echo "Claude Code: перезапустите сессию; скажите «используй агента stocks» или просто «следи за SBER»"
}

install_codex() {
  copy_skill "$HOME/.codex/skills/ru-stocks"
  echo "Codex: перезапустите сессию. Альтернатива: работайте из этой папки — Codex прочитает AGENTS.md"
}

case "$TARGET" in
  hermes) install_hermes ;;
  hermes-default) install_hermes_default ;;
  openclaw) install_openclaw ;;
  claude) install_claude ;;
  codex) install_codex ;;
  all)
    command -v hermes >/dev/null && install_hermes || echo "Hermes не найден — пропускаю"
    [[ -d "$HOME/.openclaw" ]] && install_openclaw || echo "OpenClaw не найден — пропускаю"
    [[ -d "$HOME/.claude" ]] && install_claude || echo "Claude Code не найден — пропускаю"
    [[ -d "$HOME/.codex" ]] && install_codex || echo "Codex не найден — пропускаю"
    ;;
  *) echo "Неизвестная цель: $TARGET (hermes | hermes-default | openclaw | claude | codex | all)"; exit 1 ;;
esac

mkdir -p "${STOCKS_DESK:-$HOME/stocks-desk}"
echo "Хранилище акций: ${STOCKS_DESK:-$HOME/stocks-desk}"
if [[ -z "$PY" ]]; then echo "Python не найден в PATH (нужен python3, python или py)"; exit 1; fi
"$PY" -m pip install --user --quiet pypdf 2>/dev/null || echo "pypdf не установлен — для чтения PDF: pip install pypdf"
"$PY" "$SKILL_SRC/scripts/wiki.py" init >/dev/null 2>&1 && echo "База знаний Obsidian готова: откройте хранилище в Obsidian (python wiki.py path)"
echo "Проверка доступа к MOEX..."
"$PY" "$SKILL_SRC/scripts/moex_share.py" SBER | head -3 || echo "MOEX ISS недоступен — проверьте сеть"
