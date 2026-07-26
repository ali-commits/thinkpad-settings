"""Application entry point."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "com.rabeei.ThinkPadSettings"

# Kept in one place so the .desktop file, the About dialog and the window title
# cannot drift apart.
APP_NAME = "ThinkPad BIOS Settings"


class ThinkPadSettingsApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._window: MainWindow | None = None

        self._add_action("about", self._on_about)
        self._add_action("refresh", self._on_refresh, ["<Control>r"])
        self._add_action("search", self._on_search, ["<Control>f"])
        self._add_action("quit", self._on_quit, ["<Control>q", "<Control>w"])

    def _add_action(
        self,
        name: str,
        handler: Callable[[Gio.SimpleAction, Any], None],
        accels: list[str] | None = None,
    ) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", handler)
        self.add_action(action)
        if accels:
            self.set_accels_for_action(f"app.{name}", accels)

    def do_activate(self) -> None:
        # Single-instance: a second launch raises the existing window rather
        # than opening a duplicate editor onto the same firmware.
        if self._window is None:
            self._window = MainWindow(application=self)
        self._window.present()

    # -- actions ---------------------------------------------------------

    def _on_refresh(self, *_args: object) -> None:
        if self._window is not None:
            self._window.reload()

    def _on_search(self, *_args: object) -> None:
        if self._window is not None:
            self._window.focus_search()

    def _on_quit(self, *_args: object) -> None:
        if self._window is not None:
            self._window.close()
        else:
            self.quit()

    def _on_about(self, *_args: object) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=__version__,
            developer_name="Built for this machine",
            license_type=Gtk.License.MIT_X11,
            comments=(
                "Reads and writes Lenovo firmware settings through fwupd, using "
                "the think_lmi kernel driver.\n\n"
                "Changes are staged locally and written in a single batch, so "
                "you are asked to authenticate once no matter how many settings "
                "you edit. Most settings take effect only after a restart."
            ),
        )
        about.add_credit_section(
            "Built on", ["fwupd", "think_lmi", "GTK4 / libadwaita"]
        )
        about.present(self._window)


def main() -> int:
    return ThinkPadSettingsApp().run(sys.argv)
