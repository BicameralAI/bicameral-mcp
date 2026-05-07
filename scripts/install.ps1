# Bicameral MCP one-line installer (Windows PowerShell).
#
# Usage:
#   irm https://raw.githubusercontent.com/BicameralAI/bicameral-mcp/main/scripts/install.ps1 | iex
#
# What it does:
#   1. Installs uv if not already on PATH (uv is a single-binary, no-Python-prereq Python toolchain).
#   2. Runs `uv tool install bicameral-mcp`.
#   3. Runs `bicameral-mcp setup` to register the MCP server with Claude Code,
#      install the post-commit + session-end hooks, and wire up the slash commands.
#
# After install, upgrade with:
#   bicameral-mcp update          (uses the same uv tool path)

$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "==> Installing uv (single-binary Python toolchain)..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # uv's PowerShell installer adds itself to PATH for the current session.
}

Write-Host "==> Installing bicameral-mcp via uv..."
uv tool install bicameral-mcp

Write-Host "==> Running setup wizard..."
bicameral-mcp setup @args
