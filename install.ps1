# Установка агента «Инвесткомитет» (акции РФ) в Claude Code / Codex CLI / OpenClaw на Windows (без WSL).
#   powershell -ExecutionPolicy Bypass -File install.ps1 claude
#   powershell -ExecutionPolicy Bypass -File install.ps1 codex
#   powershell -ExecutionPolicy Bypass -File install.ps1 openclaw
#   powershell -ExecutionPolicy Bypass -File install.ps1 hermes   # Hermes для Windows: %LOCALAPPDATA%\hermes
param([ValidateSet("claude", "codex", "openclaw", "hermes")][string]$Target = "claude")
$ErrorActionPreference = "Stop"
$Src = Join-Path $PSScriptRoot "skills\finance\ru-stocks"

function Copy-Skill($Dest) {
    New-Item -ItemType Directory -Force (Split-Path $Dest) | Out-Null
    if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
    Copy-Item -Recurse $Src $Dest
    Write-Host "Навык -> $Dest"
}

switch ($Target) {
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
    "hermes" {
        $hh = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }
        $profile = Join-Path $hh "profiles\stocks"
        if (Test-Path $profile) {
            Copy-Item (Join-Path $PSScriptRoot "SOUL.md") (Join-Path $profile "SOUL.md") -Force
            Copy-Skill (Join-Path $profile "skills\finance\ru-stocks")
            Write-Host "Профиль Hermes 'stocks' обновлён: $profile"
        } else {
            Copy-Skill (Join-Path $hh "skills\finance\ru-stocks")
            Write-Host "Профиль 'stocks' не найден — навык установлен в основной профиль: $hh"
        }
    }
    "openclaw" {
        Copy-Skill (Join-Path $HOME ".openclaw\skills\ru-stocks")
        Write-Host "Перезапустите OpenClaw. Характер агента: добавьте SOUL.md из этой папки в SOUL.md вашего workspace."
    }
}
$desk = if ($env:STOCKS_DESK) { $env:STOCKS_DESK } else { Join-Path $HOME "stocks-desk" }
New-Item -ItemType Directory -Force $desk | Out-Null
Write-Host "Хранилище акций: $desk"
try { python -m pip install --user --quiet pypdf } catch { Write-Host "Для чтения PDF: pip install pypdf" }
try { python (Join-Path $Src "scripts\wiki.py") init | Out-Null; Write-Host "База знаний Obsidian: откройте $desk в Obsidian как хранилище" } catch { Write-Host "База знаний: python wiki.py init" }
