# Установка агента «Инвесткомитет» (акции РФ) в Claude Code / Codex CLI / OpenClaw на Windows (без WSL).
#   powershell -ExecutionPolicy Bypass -File install.ps1 claude
#   powershell -ExecutionPolicy Bypass -File install.ps1 codex
#   powershell -ExecutionPolicy Bypass -File install.ps1 openclaw
# Для Hermes Agent используйте install.sh в WSL.
param([ValidateSet("claude", "codex", "openclaw")][string]$Target = "claude")
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
    "openclaw" {
        Copy-Skill (Join-Path $HOME ".openclaw\skills\ru-stocks")
        Write-Host "Перезапустите OpenClaw. Характер агента: добавьте SOUL.md из этой папки в SOUL.md вашего workspace."
    }
}
$desk = if ($env:STOCKS_DESK) { $env:STOCKS_DESK } else { Join-Path $HOME "stocks-desk" }
New-Item -ItemType Directory -Force $desk | Out-Null
Write-Host "Хранилище акций: $desk"
try { python -m pip install --user --quiet pypdf } catch { Write-Host "Для чтения PDF: pip install pypdf" }
