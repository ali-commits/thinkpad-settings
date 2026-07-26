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

# Early versions of install.sh built an icon-theme.cache here. Left behind it
# keeps pointing GTK at the icon we just deleted, which produces a "Failed to
# load icon" warning on every launch — including for the packaged build.
ICON_ROOT="$HOME/.local/share/icons/hicolor"
if [ -f "$ICON_ROOT/icon-theme.cache" ] && \
   ! find "$ICON_ROOT" -name '*.svg' -o -name '*.png' | grep -q .; then
    rm -f "$ICON_ROOT/icon-theme.cache"
    find "$ICON_ROOT" -type d -empty -delete 2>/dev/null || true
fi

echo "Removed. Your BIOS settings were not changed."
