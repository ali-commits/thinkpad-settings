#!/usr/bin/env bash
# User-local install. Touches nothing outside $HOME and needs no root.
# Run ./uninstall.sh to undo.
set -euo pipefail

APP_ID="com.rabeei.ThinkPadSettings"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BIN_DIR="$HOME/.local/bin"
LIB_DIR="$HOME/.local/share/thinkpad-settings"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"

echo "Installing ThinkPad BIOS Settings"

# -- dependency check ---------------------------------------------------
missing=()
python3 -c "
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk, Adw, Gio, GLib
" 2>/dev/null || missing+=("python3-gobject gtk4 libadwaita")

command -v fwupdmgr >/dev/null 2>&1 || missing+=("fwupd")

if [ ${#missing[@]} -gt 0 ]; then
    echo "Missing dependencies. Install them with:" >&2
    echo "    sudo dnf install python3-gobject gtk4 libadwaita fwupd" >&2
    exit 1
fi

# -- warn, don't block, if the firmware interface is absent -------------
if [ ! -d /sys/class/firmware-attributes ]; then
    echo "  ! /sys/class/firmware-attributes does not exist."
    echo "    The app will install but will have nothing to show. This needs a"
    echo "    Lenovo ThinkPad with the think_lmi driver (or another vendor's"
    echo "    firmware-attributes driver) loaded."
fi

# -- files --------------------------------------------------------------
install -d "$BIN_DIR" "$LIB_DIR" "$DESKTOP_DIR" "$ICON_DIR"

rm -rf "${LIB_DIR:?}/thinkpad_settings"
cp -r "$SRC/thinkpad_settings" "$LIB_DIR/thinkpad_settings"
find "$LIB_DIR/thinkpad_settings" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

cat > "$BIN_DIR/thinkpad-settings" <<EOF
#!/usr/bin/env bash
exec python3 -c 'import sys; sys.path.insert(0, "$LIB_DIR"); from thinkpad_settings.app import main; sys.exit(main())' "\$@"
EOF
chmod +x "$BIN_DIR/thinkpad-settings"

install -m644 "$SRC/data/$APP_ID.svg" "$ICON_DIR/$APP_ID.svg"
install -m644 "$SRC/data/$APP_ID.desktop" "$DESKTOP_DIR/$APP_ID.desktop"

# -- caches -------------------------------------------------------------
# GTK4 walks uncached icon directories fine, and building icon-theme.cache here
# would only create a staleness trap: once it exists GTK serves it, so every
# later icon change would need a regeneration. Deliberately not run.
# update-desktop-database only builds mimeinfo.cache, which matters for
# MimeType= associations this app does not declare, but it is harmless and
# helps desktops that do not watch the directory.
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo
echo "Installed:"
echo "  $BIN_DIR/thinkpad-settings"
echo "  $DESKTOP_DIR/$APP_ID.desktop"

case ":$PATH:" in
    *":$BIN_DIR:"*) echo; echo "Run 'thinkpad-settings', or launch it from Activities." ;;
    *) echo
       echo "  ! $BIN_DIR is not on your PATH."
       echo "    Launch from Activities, or run: $BIN_DIR/thinkpad-settings" ;;
esac
