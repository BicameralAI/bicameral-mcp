#!/bin/sh
# Bicameral MCP one-line installer (POSIX — macOS, Linux).
#
# Usage:
#   curl -LsSf https://raw.githubusercontent.com/BicameralAI/bicameral-mcp/main/scripts/install.sh | sh
#
# What it does:
#   1. Installs uv if not already on PATH (uv is a single-binary, no-Python-prereq Python toolchain).
#   2. Runs `uv tool install bicameral-mcp`.
#   3. Runs `bicameral-mcp setup` to register the MCP server with Claude Code,
#      install the post-commit + session-end hooks, and wire up the slash commands.
#
# After install, upgrade with:
#   bicameral-mcp update          (uses the same uv tool path)
#
# Re-running this script is safe — uv tool install will skip the install step
# if bicameral-mcp is already present at the same version. To force-reinstall,
# run `uv tool install --force bicameral-mcp` directly.

set -e

UV_BIN_DIR="$HOME/.local/bin"

if ! command -v uv >/dev/null 2>&1; then
  echo "==> Installing uv (single-binary Python toolchain)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv installs into ~/.local/bin by default; make it visible to this shell.
  case ":$PATH:" in
    *":$UV_BIN_DIR:"*) ;;
    *) PATH="$UV_BIN_DIR:$PATH"; export PATH ;;
  esac
fi

echo "==> Installing bicameral-mcp via uv..."
uv tool install bicameral-mcp

echo "==> Running setup wizard..."
exec bicameral-mcp setup "$@"
