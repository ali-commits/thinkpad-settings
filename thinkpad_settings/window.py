"""Main window: category sidebar, settings pane, staged-change bar."""

from __future__ import annotations

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from . import bootorder, metadata
from .fwupd import PENDING_REBOOT, BiosSetting, Failure, FwupdClient, FwupdError

# Values that read naturally as an on/off switch rather than a two-item combo.
_BOOLEAN = {"Disable", "Enable"}
_ON = "Enable"
_OFF = "Disable"

_STYLE = """
.staged-badge {
    font-size: 0.8em;
    font-weight: bold;
    padding: 1px 7px;
    border-radius: 9999px;
    background: alpha(@accent_bg_color, 0.18);
    color: @accent_color;
}
.risk-icon { opacity: 0.85; }
"""


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.set_title("ThinkPad BIOS Settings")
        self.set_default_size(1000, 720)
        self.set_size_request(360, 480)

        self._client = FwupdClient()
        self._settings: list[BiosSetting] = []
        self._by_name: dict[str, BiosSetting] = {}
        self._staged: dict[str, str] = {}
        self._category = ""
        self._search = ""
        # True from the moment SetBiosSettings is dispatched until it returns,
        # so the close dialog cannot claim the firmware is untouched mid-write.
        self._writing = False
        # A successful write is authoritative about a pending reboot even if
        # fwupd's cached view has not caught up yet.
        self._applied_this_session = False
        # Each reorder click rebuilds the pane, which would otherwise snap the
        # boot-order expander shut after every single move.
        self._boot_expanded = False
        # Suppresses change handlers while widgets are being populated, so
        # rebuilding the pane does not look like the user editing every row.
        self._building = False
        self._pending_reboot = False

        self._load_style()
        self._build_ui()
        self.connect("close-request", self._on_close_request)
        self.reload()

    # -- chrome ----------------------------------------------------------

    def _load_style(self) -> None:
        provider = Gtk.CssProvider()
        # load_from_data is deprecated as of GTK 4.12; load_from_string is the
        # current API. The Gtk.StyleContext *class* is deprecated, but the
        # static add_provider_for_display is not and remains the supported way
        # to register a display-wide provider in GTK 4.22.
        provider.load_from_string(_STYLE)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_ui(self) -> None:
        self._toasts = Adw.ToastOverlay()

        # -- sidebar ----------------------------------------------------
        self._sidebar_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._sidebar_list.add_css_class("navigation-sidebar")
        self._sidebar_list.connect("row-selected", self._on_category_selected)
        # row-selected does not fire when the already-selected row is clicked,
        # which would make that click dead while search results are showing.
        self._sidebar_list.connect("row-activated", self._on_category_selected)

        sidebar_scroll = Gtk.ScrolledWindow(
            child=self._sidebar_list,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )

        sidebar_view = Adw.ToolbarView(content=sidebar_scroll)
        sidebar_view.add_top_bar(
            Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Settings"))
        )
        sidebar_page = Adw.NavigationPage(child=sidebar_view, title="Categories")

        # -- content ----------------------------------------------------
        self._search_entry = Gtk.SearchEntry(
            placeholder_text="Search all settings", hexpand=True
        )
        # Each keystroke rebuilds the pane, so coalesce bursts of typing.
        self._search_entry.set_search_delay(150)
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._search_bar = Gtk.SearchBar(child=self._search_entry)
        self._search_bar.set_key_capture_widget(self)
        self._search_bar.connect_entry(self._search_entry)

        menu = Gio.Menu()
        menu.append("Refresh", "app.refresh")
        menu.append("About", "app.about")

        self._search_button = Gtk.ToggleButton(
            icon_name="system-search-symbolic", tooltip_text="Search settings"
        )
        self._search_button.bind_property(
            "active",
            self._search_bar,
            "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )

        self._content_title = Adw.WindowTitle(title="ThinkPad BIOS Settings")
        header = Adw.HeaderBar(title_widget=self._content_title)
        header.pack_end(
            Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        )
        header.pack_end(self._search_button)

        self._banner = Adw.Banner(revealed=False)
        self._banner.set_use_markup(False)
        self._banner.connect("button-clicked", self._on_banner_clicked)

        self._pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True
        )
        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)

        self._spinner_page = Adw.StatusPage(title="Reading firmware settings…")
        self._error_page = Adw.StatusPage(icon_name="dialog-error-symbolic")
        self._stack.add_named(self._scroll, "settings")
        self._stack.add_named(self._spinner_page, "loading")
        self._stack.add_named(self._error_page, "error")
        self._stack.set_visible_child_name("loading")

        self._pane.append(self._stack)

        content_view = Adw.ToolbarView(content=self._pane)
        content_view.add_top_bar(header)
        content_view.add_top_bar(self._search_bar)
        content_view.add_top_bar(self._banner)
        content_view.add_bottom_bar(self._build_apply_bar())

        self._content_page = Adw.NavigationPage(child=content_view, title="Settings")

        self._split = Adw.NavigationSplitView(
            sidebar=sidebar_page, content=self._content_page
        )
        self._split.set_min_sidebar_width(200)
        self._split.set_max_sidebar_width(260)

        self._toasts.set_child(self._split)
        self.set_content(self._toasts)

        # Without this the sidebar keeps its width down to the 360px minimum
        # and squeezes the settings pane to nothing. Collapsed turns the split
        # into a navigation stack with a back button.
        breakpoint_ = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 620sp")
        )
        breakpoint_.add_setter(self._split, "collapsed", True)
        self.add_breakpoint(breakpoint_)

    def _build_apply_bar(self) -> Gtk.Widget:
        self._staged_label = Gtk.Label(xalign=0.0, hexpand=True)
        self._staged_label.add_css_class("dim-label")

        self._revert_button = Gtk.Button(label="Revert")
        self._revert_button.connect("clicked", lambda *_: self._revert_all())

        self._apply_button = Gtk.Button(label="Apply Changes")
        self._apply_button.add_css_class("suggested-action")
        self._apply_button.connect("clicked", lambda *_: self._confirm_apply())

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(self._staged_label)
        box.append(self._revert_button)
        box.append(self._apply_button)

        self._apply_revealer = Gtk.Revealer(
            child=box,
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            reveal_child=False,
        )
        return self._apply_revealer

    # -- loading ---------------------------------------------------------

    def reload(self) -> None:
        """Re-read everything from fwupd, discarding nothing that is staged."""
        self._stack.set_visible_child_name("loading")
        self._client.get_settings(self._on_settings_loaded, self._on_load_error)

    def _on_settings_loaded(self, settings: list[BiosSetting]) -> None:
        self._settings = [s for s in settings if s.name != PENDING_REBOOT]
        self._by_name = {s.name: s for s in self._settings}

        flag = next((s for s in settings if s.name == PENDING_REBOOT), None)
        firmware_says = bool(flag and (flag.current_value or "").strip() == "1")
        # Never let a stale cached read retract a reboot notice we know is due.
        self._pending_reboot = firmware_says or self._applied_this_session

        # Drop staged edits that the firmware now already agrees with, which
        # happens after a successful apply or an external change.
        self._staged = {
            name: value
            for name, value in self._staged.items()
            if name in self._by_name and self._by_name[name].current_value != value
        }

        if not self._settings:
            # A non-Lenovo machine, or a driver that exposed nothing. Say so
            # rather than presenting an empty window.
            self._error_page.set_icon_name("dialog-information-symbolic")
            self._error_page.set_title("No firmware settings available")
            self._error_page.set_description(
                "fwupd is running but reports no editable firmware settings "
                "for this machine. This app needs a Lenovo ThinkPad with the "
                "think_lmi kernel driver, or another vendor's "
                "firmware-attributes driver."
            )
            self._error_page.set_child(None)
            self._stack.set_visible_child_name("error")
            self._update_apply_bar()
            self._update_banner()
            return

        self._populate_sidebar()
        self._stack.set_visible_child_name("settings")
        self._rebuild_pane()
        self._update_apply_bar()
        self._update_banner()

    def _on_load_error(self, error: FwupdError) -> None:
        if error.kind in (Failure.CANCELLED, Failure.DENIED):
            # A dismissed dialog and a refused one are indistinguishable here,
            # and neither is really an error. Offer a retry instead of alarming.
            self._error_page.set_title("Authentication required")
            self._error_page.set_description(
                "Reading firmware settings needs administrator authentication."
            )
            self._error_page.set_icon_name("dialog-password-symbolic")
        else:
            self._error_page.set_title("Could not read firmware settings")
            # Daemon error text is arbitrary and StatusPage parses markup.
            self._error_page.set_description(GLib.markup_escape_text(error.message))
            self._error_page.set_icon_name("dialog-error-symbolic")

        retry = Gtk.Button(label="Try Again", halign=Gtk.Align.CENTER)
        retry.add_css_class("pill")
        retry.add_css_class("suggested-action")
        retry.connect("clicked", lambda *_: self.reload())
        self._error_page.set_child(retry)
        self._stack.set_visible_child_name("error")

    # -- sidebar ---------------------------------------------------------

    def _categories(self) -> list[str]:
        present = {metadata.get(s.name).category for s in self._settings}
        return [c for c in metadata.CATEGORY_ORDER if c in present]

    def _icon_for(self, category: str) -> str:
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        for name in metadata.CATEGORY_ICONS.get(category, []):
            if theme.has_icon(name):
                return name
        return "emblem-system-symbolic"

    def _populate_sidebar(self) -> None:
        while (child := self._sidebar_list.get_first_child()) is not None:
            self._sidebar_list.remove(child)

        categories = self._categories()
        if self._category not in categories:
            self._category = categories[0] if categories else ""

        selected_row = None
        for category in categories:
            count = sum(
                1 for s in self._settings if metadata.get(s.name).category == category
            )
            row = Adw.ActionRow(subtitle=f"{count} settings")
            row.set_use_markup(False)
            row.set_title(category)
            row.add_prefix(Gtk.Image.new_from_icon_name(self._icon_for(category)))
            row.category = category
            self._sidebar_list.append(row)
            if category == self._category:
                selected_row = row

        self._building = True
        if selected_row is not None:
            self._sidebar_list.select_row(selected_row)
        self._building = False

    def _on_category_selected(self, _list, row) -> None:
        if self._building or row is None:
            return
        self._category = row.category
        if self._search:
            self._search = ""
            self._search_entry.set_text("")
            self._search_button.set_active(False)
        self._rebuild_pane()
        # When collapsed, picking a category should navigate to it rather than
        # leaving the user staring at the unchanged sidebar.
        self._split.set_show_content(True)

    # -- search ----------------------------------------------------------

    def focus_search(self) -> None:
        self._search_button.set_active(True)
        self._search_entry.grab_focus()

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._search = entry.get_text().strip().lower()
        self._rebuild_pane()

    def _matches(self, setting: BiosSetting) -> bool:
        meta = metadata.get(setting.name)
        haystack = " ".join(
            (setting.name, meta.label, meta.description, meta.category)
        ).lower()
        return all(word in haystack for word in self._search.split())

    # -- settings pane ---------------------------------------------------

    def _rebuild_pane(self, keep_scroll: bool = False) -> None:
        # Reordering the boot list rebuilds the whole pane; without this the
        # view jumps back to the top after every arrow click.
        offset = self._scroll.get_vadjustment().get_value() if keep_scroll else 0.0

        self._building = True
        try:
            page = Adw.PreferencesPage()

            if self._search:
                shown = [s for s in self._settings if self._matches(s)]
                self._content_title.set_title("Search results")
                self._content_title.set_subtitle(
                    f"{len(shown)} of {len(self._settings)} settings"
                )
                if not shown:
                    # StatusPage has no use-markup toggle either, and the search
                    # term is arbitrary user input.
                    empty = Adw.StatusPage(
                        icon_name="system-search-symbolic",
                        title="No matches",
                        description=GLib.markup_escape_text(
                            f"Nothing matches “{self._search}”."
                        ),
                    )
                    self._scroll.set_child(empty)
                    return
                by_category: dict[str, list[BiosSetting]] = {}
                for setting in shown:
                    by_category.setdefault(
                        metadata.get(setting.name).category, []
                    ).append(setting)
                for category in metadata.CATEGORY_ORDER:
                    if category in by_category:
                        page.add(self._group(category, by_category[category]))
            else:
                shown = [
                    s
                    for s in self._settings
                    if metadata.get(s.name).category == self._category
                ]
                self._content_title.set_title(self._category or "Settings")
                self._content_title.set_subtitle(f"{len(shown)} settings")
                page.add(self._group(None, shown))

            self._scroll.set_child(page)
        finally:
            self._building = False

        if keep_scroll and offset:
            # The new page has not been allocated yet, so the adjustment's
            # upper bound is still stale; restore once layout has settled.
            def restore() -> bool:
                self._scroll.get_vadjustment().set_value(offset)
                return False

            GLib.idle_add(restore)

    def _group(self, title: str | None, settings: list[BiosSetting]) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        if title:
            # PreferencesGroup has no use-markup toggle, so escape instead.
            group.set_title(GLib.markup_escape_text(title))
        for setting in sorted(settings, key=lambda s: metadata.get(s.name).label):
            group.add(self._row_for(setting))
        return group

    def _row_for(self, setting: BiosSetting) -> Gtk.Widget:
        meta = metadata.get(setting.name)

        if setting.name == bootorder.BOOT_ORDER:
            return self._boot_order_row(setting, meta)
        if not setting.editable:
            return self._readonly_row(setting, meta)
        if not setting.is_enum:
            return self._text_row(setting, meta)
        if set(setting.possible_values) == _BOOLEAN:
            return self._switch_row(setting, meta)
        return self._combo_row(setting, meta)

    def _decorate(self, row: Adw.PreferencesRow, setting: BiosSetting, meta) -> None:
        """Attach the description, risk marker and staged badge to a row."""
        # Row titles and subtitles are parsed as Pango markup by default, and
        # both category names and descriptions contain bare ampersands. Turning
        # markup off covers title and subtitle in one go.
        row.set_use_markup(False)
        row.set_title(meta.label)
        subtitle = meta.description
        if meta.risk_note:
            subtitle = f"{subtitle}\n⚠ {meta.risk_note}" if subtitle else meta.risk_note
        row.set_subtitle(subtitle)
        row.set_subtitle_lines(0)

        if meta.risk == metadata.RISK_DANGER:
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.add_css_class("error")
            icon.add_css_class("risk-icon")
            icon.set_tooltip_text(meta.risk_note or "Changing this can lock you out.")
            row.add_prefix(icon)

        badge = Gtk.Label(label="changed", valign=Gtk.Align.CENTER)
        badge.add_css_class("staged-badge")
        badge.set_visible(setting.name in self._staged)
        row.add_suffix(badge)
        row.staged_badge = badge

    def _readonly_row(self, setting: BiosSetting, meta) -> Adw.ActionRow:
        row = Adw.ActionRow()
        self._decorate(row, setting, meta)

        if setting.disclosed:
            shown = meta.label_for(setting.current_value)
            reason = (
                "Your firmware reports this as read-only. Change it in BIOS "
                "setup by pressing F1 during startup."
            )
        else:
            # fwupd withheld the value. Say so rather than showing a default
            # that would read as a real setting.
            shown = "Unknown"
            reason = "fwupd did not disclose this value."

        value = Gtk.Label(label=shown)
        value.add_css_class("dim-label")
        row.add_suffix(value)

        lock = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
        lock.set_tooltip_text(reason)
        lock.add_css_class("dim-label")
        row.add_suffix(lock)
        return row

    def _switch_row(self, setting: BiosSetting, meta) -> Adw.SwitchRow:
        row = Adw.SwitchRow()
        self._decorate(row, setting, meta)
        row.set_active(self._value_of(setting) == _ON)

        def changed(widget, _param):
            if self._building:
                return
            self._stage(setting, _ON if widget.get_active() else _OFF, row)

        row.connect("notify::active", changed)
        return row

    def _combo_row(self, setting: BiosSetting, meta) -> Adw.ComboRow:
        values = list(setting.possible_values)
        row = Adw.ComboRow()
        self._decorate(row, setting, meta)
        row.set_model(Gtk.StringList.new([meta.label_for(v) for v in values]))

        current = self._value_of(setting)
        row.set_selected(values.index(current) if current in values else Gtk.INVALID_LIST_POSITION)

        def changed(widget, _param):
            if self._building:
                return
            index = widget.get_selected()
            if 0 <= index < len(values):
                self._stage(setting, values[index], row)

        row.connect("notify::selected", changed)
        return row

    def _text_row(self, setting: BiosSetting, meta) -> Adw.EntryRow:
        row = Adw.EntryRow()
        row.set_use_markup(False)
        row.set_title(meta.label)
        row.set_text(self._value_of(setting))

        badge = Gtk.Label(label="changed", valign=Gtk.Align.CENTER)
        badge.add_css_class("staged-badge")
        badge.set_visible(setting.name in self._staged)
        row.add_suffix(badge)
        row.staged_badge = badge

        # Adw.EntryRow has no subtitle, so the description that every other row
        # shows inline has to travel as a tooltip here.
        hint = meta.description
        if meta.risk_note:
            hint = f"{hint}\n⚠ {meta.risk_note}" if hint else meta.risk_note
        if hint:
            row.set_tooltip_text(hint)
        if meta.risk == metadata.RISK_DANGER:
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.add_css_class("error")
            icon.add_css_class("risk-icon")
            icon.set_tooltip_text(meta.risk_note or "Changing this can lock you out.")
            row.add_prefix(icon)

        def changed(widget):
            if self._building:
                return
            self._stage(setting, widget.get_text(), row)

        row.connect("changed", changed)
        return row

    def _boot_order_row(self, setting: BiosSetting, meta) -> Adw.ExpanderRow:
        """Read-only view of the boot order.

        This cannot be an editor. fwupd validates a written value against the
        attribute's possible-values list, and BootOrder's value is a
        colon-joined sequence while its possible values are single device
        codes, so every reordering is rejected with

            NVMe0:USBHDD:... doesn't map to any possible values for BootOrder

        Worse, one rejected entry aborts the whole SetBiosSettings batch, so
        offering the edit here would silently break every other change staged
        alongside it.
        """
        row = Adw.ExpanderRow()
        row.set_use_markup(False)
        row.set_title(meta.label)
        subtitle = meta.description
        subtitle = (
            f"{subtitle}\nThis one cannot be changed from here — fwupd rejects "
            "list values. Use BIOS setup (F1 at startup)."
            if subtitle
            else "Cannot be changed from here."
        )
        row.set_subtitle(subtitle)
        row.set_subtitle_lines(0)
        row.set_expanded(bool(self._boot_expanded))

        def remember_expanded(widget, _param):
            if not self._building:
                self._boot_expanded = widget.get_expanded()

        row.connect("notify::expanded", remember_expanded)

        lock = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
        lock.set_tooltip_text(
            "fwupd cannot write an ordered list. Change the boot order in BIOS "
            "setup by pressing F1 during startup."
        )
        lock.add_css_class("dim-label")
        row.add_suffix(lock)

        devices = bootorder.parse(self._value_of(setting))
        if not devices:
            entry = Adw.ActionRow()
            entry.set_use_markup(False)
            entry.set_title("No boot devices listed")
            entry.add_css_class("dim-label")
            row.add_row(entry)
            return row

        for index, device in enumerate(devices):
            entry = Adw.ActionRow()
            entry.set_use_markup(False)
            entry.set_title(meta.label_for(device))
            entry.set_subtitle(device)
            entry.add_prefix(
                Gtk.Label(label=str(index + 1), css_classes=["dim-label", "monospace"])
            )
            row.add_row(entry)

        return row

    # -- staging ---------------------------------------------------------

    def _value_of(self, setting: BiosSetting) -> str:
        """The value to display: staged if edited, otherwise from firmware."""
        if setting.name in self._staged:
            return self._staged[setting.name]
        # None means fwupd withheld it; only undisclosed settings hit this, and
        # they are never editable, so the empty string is display-only.
        return setting.current_value or ""

    def _stage(self, setting: BiosSetting, value: str, row) -> None:
        if value == setting.current_value:
            self._staged.pop(setting.name, None)
        else:
            self._staged[setting.name] = value

        badge = getattr(row, "staged_badge", None)
        if badge is not None:
            badge.set_visible(setting.name in self._staged)
        self._update_apply_bar()

    def _revert_all(self) -> None:
        self._staged.clear()
        self._rebuild_pane()
        self._update_apply_bar()
        self._toast("Changes reverted")

    def _update_apply_bar(self) -> None:
        count = len(self._staged)
        self._apply_revealer.set_reveal_child(count > 0)
        self._staged_label.set_text(
            "1 setting changed" if count == 1 else f"{count} settings changed"
        )

    def _update_banner(self) -> None:
        if self._pending_reboot:
            self._banner.set_title(
                "Firmware changes are saved and will take effect after a restart."
            )
            self._banner.set_button_label("Restart…")
            self._banner.set_revealed(True)
        else:
            self._banner.set_revealed(False)

    # -- applying --------------------------------------------------------

    def _describe(self, name: str, value: str) -> str:
        """Readable rendering of a staged value, for the confirmation dialog.

        BootOrder holds a colon-joined list, so passing it through the
        single-value labeller would print garbage.
        """
        meta = metadata.get(name)
        if name == bootorder.BOOT_ORDER:
            devices = bootorder.parse(value)
            if not devices:
                return "no boot devices at all"
            return " → ".join(meta.label_for(d) for d in devices)
        return meta.label_for(value)

    def _rejection(self) -> str | None:
        """Why fwupd would refuse this batch, checked before sending it.

        fwupd aborts the entire SetBiosSettings call on the first bad entry, so
        one unwritable value would silently take every other staged change down
        with it. Catching it here names the offender instead.
        """
        for name, value in sorted(self._staged.items()):
            setting = self._by_name.get(name)
            label = metadata.get(name).label
            if setting is None:
                return f"“{label}” is no longer offered by your firmware."
            if setting.read_only:
                return f"“{label}” is read-only and cannot be changed from here."
            if setting.is_enum and value not in setting.possible_values:
                return (
                    f"“{label}” cannot be set to “{value}” — your firmware only "
                    "accepts a single value from its own list."
                )
        return None

    def _confirm_apply(self) -> None:
        if not self._staged:
            return

        problem = self._rejection()
        if problem:
            self._toast(problem)
            return

        # Only genuinely lock-you-out changes get a modal. Gating on "caution"
        # too would put a dialog in front of 62 of the 104 settings, and a
        # confirmation that always appears stops being read. Caution notes are
        # already visible inline on every row.
        danger = sorted(
            name
            for name in self._staged
            if metadata.get(name).risk == metadata.RISK_DANGER
        )
        caution = sorted(
            name
            for name in self._staged
            if metadata.get(name).risk == metadata.RISK_CAUTION
        )

        warnings = []
        boot_value = self._staged.get(bootorder.BOOT_ORDER)
        # Must be an identity check: removing every device serialises to "",
        # which is falsy, and that is the case most in need of the warning.
        if boot_value is not None and bootorder.is_unbootable(
            bootorder.parse(boot_value)
        ):
            warnings.append(
                "Your new boot order contains no internal disk, so this machine "
                "may not be able to start the installed system."
            )

        if not danger and not warnings:
            self._apply()
            return

        lines = list(warnings)
        for name in danger:
            meta = metadata.get(name)
            note = f" — {meta.risk_note}" if meta.risk_note else ""
            lines.append(
                f"‼ {meta.label}: {self._describe(name, self._staged[name])}{note}"
            )
        if caution:
            labels = ", ".join(metadata.get(n).label for n in caution)
            lines.append(f"Also changing: {labels}.")

        dialog = Adw.AlertDialog(
            heading="Apply these firmware changes?",
            body="\n\n".join(lines),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Apply Anyway")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(source, result):
            if source.choose_finish(result) == "apply":
                self._apply()

        dialog.choose(self, None, answered)

    def _apply(self) -> None:
        changes = dict(self._staged)
        self._writing = True
        self._apply_button.set_sensitive(False)
        self._revert_button.set_sensitive(False)
        # The authentication dialog can sit open for a while. Locking the pane
        # keeps the staged set from drifting out from under the write.
        self._stack.set_sensitive(False)
        self._staged_label.set_text("Applying…")

        def unlock() -> None:
            self._writing = False
            self._apply_button.set_sensitive(True)
            self._revert_button.set_sensitive(True)
            self._stack.set_sensitive(True)

        def done() -> None:
            unlock()
            self._applied_this_session = True
            count = len(changes)
            # Clear only what was actually written, and only if it is still the
            # value we sent, so an edit that slipped in meanwhile survives.
            for name, value in changes.items():
                if self._staged.get(name) == value:
                    self._staged.pop(name, None)
            self._toast(
                "1 setting applied. Restart to activate it."
                if count == 1
                else f"{count} settings applied. Restart to activate them."
            )
            self._pending_reboot = True
            self._update_banner()
            # Reset the bar here too: if the follow-up reload fails, nothing
            # else would clear the "Applying…" label.
            self._update_apply_bar()
            self.reload()

        def failed(error: FwupdError) -> None:
            unlock()
            self._update_apply_bar()
            if error.kind is Failure.CANCELLED:
                self._toast("Nothing was changed")
            elif error.kind is Failure.TIMEOUT:
                # The write may still have landed, so re-read rather than
                # claiming either outcome. Staged edits are deliberately kept.
                self._toast(
                    f"{error.message}. It is unclear whether the change was "
                    "applied — re-checking."
                )
                self.reload()
            elif error.kind is Failure.DENIED:
                self._toast(error.message)
            elif error.kind is Failure.NOTHING_TO_DO:
                # Every value already matched; treat it as a benign no-op.
                self._staged.clear()
                self._toast("Those values were already set")
                self.reload()
            elif error.kind is Failure.REJECTED:
                # fwupd's own words — it names the setting and the reason, and
                # this is emphatically not a claim about the machine.
                self._toast(f"Firmware rejected the change: {error.message}")
            else:
                self._toast(f"Could not apply: {error.message}")

        self._client.set_settings(changes, done, failed)

    # -- misc ------------------------------------------------------------

    def _toast(self, message: str) -> None:
        # Toast titles are markup by default and these can carry raw error text.
        toast = Adw.Toast(title=message, timeout=4)
        toast.set_use_markup(False)
        self._toasts.add_toast(toast)

    def _on_banner_clicked(self, _banner) -> None:
        dialog = Adw.AlertDialog(
            heading="Restart now?",
            body="Close your work first. Firmware changes apply during startup.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restart", "Restart")
        dialog.set_response_appearance("restart", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(source, result):
            if source.choose_finish(result) == "restart":
                self._reboot()

        dialog.choose(self, None, answered)

    def _reboot(self) -> None:
        """Ask logind to reboot, the same path the GNOME menu uses."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            bus.call_sync(
                "org.freedesktop.login1",
                "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager",
                "Reboot",
                GLib.Variant("(b)", (True,)),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except GLib.Error as exc:
            self._toast(f"Could not restart: {exc.message}")

    def _on_close_request(self, *_args) -> bool:
        if self._writing:
            # The write is already with fwupd. Closing cannot recall it, so do
            # not offer to "discard" anything or claim nothing has changed.
            dialog = Adw.AlertDialog(
                heading="A firmware write is in progress",
                body=(
                    "Changes have already been sent to fwupd. Closing now will "
                    "not undo them, and you will not see whether they "
                    "succeeded. Wait a moment for it to finish."
                ),
            )
            dialog.add_response("stay", "Wait")
            dialog.add_response("close", "Close Anyway")
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("stay")
            dialog.set_close_response("stay")

            def answered_write(source, result):
                if source.choose_finish(result) == "close":
                    self.destroy()

            dialog.choose(self, None, answered_write)
            return True

        if not self._staged:
            self._client.cancel()
            return False

        count = len(self._staged)
        dialog = Adw.AlertDialog(
            heading="Discard unapplied changes?",
            body=(
                f"{'1 setting has' if count == 1 else f'{count} settings have'} "
                "been changed but not applied. Closing now discards those edits. "
                "Nothing has been written to your firmware."
            ),
        )
        dialog.add_response("stay", "Keep Editing")
        dialog.add_response("discard", "Discard")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("stay")
        dialog.set_close_response("stay")

        def answered(source, result):
            if source.choose_finish(result) == "discard":
                self._staged.clear()
                self._client.cancel()
                self.destroy()

        dialog.choose(self, None, answered)
        return True  # block this close; the dialog decides
