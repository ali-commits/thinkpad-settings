#!/usr/bin/env bash
# Removes everything install.sh created. Firmware settings themselves are
# untouched — this only removes the editor.
set -euo pipefail

APP_ID="com.rabeei.ThinkPadSettings"

rm -f  "$HOME/.local/bin/thinkpad-settings"
rm -rf "$HOME/.local/share/thinkpad-settings"
rm -f  "$HOME/.local/share/applications/$APP_ID.desktop"
rm -f  "$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "Removed. Your BIOS settings were not changed."
