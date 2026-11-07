# Установка агента «Инвесткомитет / семейный офис» (акции РФ) на Windows (без WSL).
#   powershell -ExecutionPolicy Bypass -File install.ps1 hermes-default   # Hermes: навык в основной профиль
#   powershell -ExecutionPolicy Bypass -File install.ps1 hermes           # Hermes: отдельный профиль "stocks" с SOUL.md
#   powershell -ExecutionPolicy Bypass -File install.ps1 claude
#   powershell -ExecutionPolicy Bypass -File install.ps1 codex
#   powershell -ExecutionPolicy Bypass -File install.ps1 openclaw
# Каталог Hermes: $env:HERMES_HOME, иначе %LOCALAPPDATA%\hermes, иначе ~\.hermes.
param([ValidateSet("hermes", "hermes-default", "claude", "codex", "openclaw")][string]$Target = "claude")
$ErrorActionPreference = "Stop"
$Src = Join-Path $PSScriptRoot "skills\finance\ru-stocks"

function Copy-Skill($Dest) {
    New-Item -ItemType Directory -Force (Split-Path $Dest) | Out-Null
    if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
    Copy-Item -Recurse $Src $Dest
    Write-Host "Навык -> $Dest"
}

function Get-HermesHome {
    if ($env:HERMES_HOME) { return $env:HERMES_HOME }
    $local = Join-Path $env:LOCALAPPDATA "hermes"
    if (Test-Path $local) { return $local }
    return (Join-Path $HOME ".hermes")
}

function Find-Python {
    foreach ($c in @("py", "python", "python3")) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { return $c }
    }
    return $null
}

switch ($Target) {
    "hermes-default" {
        $h = Get-HermesHome
        Copy-Skill (Join-Path $h "skills\finance\ru-stocks")
        Write-Host "Hermes: начните новую сессию или выполните /reset"
    }
    "hermes" {
        $h = Get-HermesHome
        $prof = Join-Path $h "profiles\stocks"
        if (-not (Test-Path $prof)) {
            Write-Host "Создаю профиль Hermes 'stocks' (копия текущих ключей/модели)..."
            hermes profile create stocks --clone
        }
        Copy-Item (Join-Path $PSScriptRoot "SOUL.md") (Join-Path $prof "SOUL.md") -Force
        Copy-Skill (Join-Path $prof "skills\finance\ru-stocks")
        Write-Host "Hermes: запустите 'stocks chat'"
    }
    "claude" {
        Copy-Skill (Join-Path $HOME ".claude\skills\ru-stocks")
        $agents = Join-Path $HOME ".claude\agents"
        New-Item -ItemType Directory -Force $agents | Out-Null
        Copy-Item (Join-Path $PSScriptRoot "agents\claude-code\stocks.md") $agents -Force
        Copy-Item (Join-Path $PSScriptRoot "agents\claude-code\stocks-devil.md") $agents -Force
        Write-Host "Субагенты -> $agents (stocks, stocks-devil). Перезапустите сессию Claude Code."
    }
    "codex" {
        Copy-Skill (Join-Path $HOME ".codex\skills\ru-stocks")
        Write-Host "Перезапустите сессию Codex."
    }
    "openclaw" {
        Copy-Skill (Join-Path $HOME ".openclaw\skills\ru-stocks")
        Write-Host "Перезапустите OpenClaw. Характер агента: добавьте SOUL.md из этой папки в SOUL.md вашего workspace."
    }
}
$desk = if ($env:STOCKS_DESK) { $env:STOCKS_DESK } else { Join-Path $HOME "stocks-desk" }
New-Item -ItemType Directory -Force $desk | Out-Null
Write-Host "Хранилище акций и портфеля: $desk"
$py = Find-Python
if ($py) {
    try { & $py -m pip install --user --quiet pypdf } catch { Write-Host "Для чтения PDF: $py -m pip install pypdf" }
    Write-Host "Проверка доступа к MOEX..."
    try { & $py (Join-Path $Src "scripts\moex_share.py") SBER | Select-Object -First 3 } catch { Write-Host "MOEX ISS недоступен — проверьте сеть" }
} else {
    Write-Host "Python не найден (py / python / python3). Установите Python 3.9+ с python.org."
}
Write-Host "Портфель: скажите агенту «мой портфель: SBER, LKOH, MGNT» — дальше он ведёт его сам (SKILL.md, процедуры E–G)."
