"""Offline UI smoke test.

Runs against a captured ThinkPad T14 Gen 3 attribute dump, so it needs no
Lenovo hardware, no fwupd and no authentication. Requires a display.

    python3 tests/smoke.py

Builds the real MainWindow against a stubbed fwupd client so nothing touches
firmware and no window is presented on the user's desktop.
"""

import json
import sys
import tempfile
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from thinkpad_settings import bootorder, metadata
from thinkpad_settings.fwupd import BiosSetting, FwupdError, _parse

DUMP = Path(__file__).resolve().parent / "fixture-t14gen3.json"
RAW: list[dict[str, object]] = json.loads(DUMP.read_text(encoding="utf-8"))[
    "BiosSettings"
]
SETTINGS = _parse(RAW)

failures: list[str] = []
applied_payload: dict[str, str] = {}


def check(label: str, cond: object, detail: str = "") -> None:
    print(
        f"{'  ok  ' if cond else ' FAIL '} {label}{(' - ' + detail) if detail else ''}"
    )
    if not cond:
        failures.append(label)


class StubClient:
    def __init__(self) -> None:
        self.set_calls = 0
        self.last_raw: list[dict[str, object]] = list(RAW)

    def get_settings(
        self,
        on_success: Callable[[list[BiosSetting]], None],
        on_error: Callable[[object], None],
    ) -> None:
        on_success(list(SETTINGS))

    def set_settings(
        self,
        changes: dict[str, str],
        on_success: Callable[[], None],
        on_error: Callable[[object], None],
    ) -> None:
        self.set_calls += 1
        applied_payload.clear()
        applied_payload.update(changes)
        on_success()

    def cancel(self) -> None:
        pass


def run(app: Adw.Application) -> None:
    from thinkpad_settings import window as ts_window

    # Substituting the client is the entire point of this test.
    ts_window.FwupdClient = StubClient  # type: ignore[assignment, attr-defined]
    win = ts_window.MainWindow(application=app)

    check("window constructed", win is not None)
    check(
        "settings loaded",
        len(win._settings) == 103,
        f"{len(win._settings)} (104 minus pending_reboot)",
    )
    check(
        "pending_reboot parsed",
        win._pending_reboot is True,
        f"pending={win._pending_reboot}",
    )
    check("banner revealed for pending reboot", win._banner.get_revealed())

    cats = win._categories()
    check("categories present", len(cats) == 10, str(cats))

    total_rows = 0
    for cat in cats:
        win._category = cat
        try:
            win._rebuild_pane()
            page = win._scroll.get_child()
            n = sum(1 for s in win._settings if metadata.get(s.name).category == cat)
            total_rows += n
            print(f"        {cat:26s} {n:3d} rows  -> {type(page).__name__}")
        except Exception:
            traceback.print_exc()
            failures.append(f"render {cat}")
    check("all settings reachable via categories", total_rows == 103, str(total_rows))

    for term, expect_some in [
        ("virtual", True),
        ("thunderbolt", True),
        ("wake", True),
        ("zzzznotathing", False),
    ]:
        win._search = term
        win._rebuild_pane()
        hits = [s for s in win._settings if win._matches(s)]
        check(f"search {term!r}", bool(hits) == expect_some, f"{len(hits)} hits")
    win._search = ""

    # Export must be byte-faithful to what fwupd reported, so the file drops
    # straight in as a fixture for another model.
    # Test the slug logic, not this machine's DMI: CI is not a ThinkPad, and a
    # test that only passes on the author's laptop is worse than no test.
    for raw_model, expected_slug in [
        ("ThinkPad T14 Gen 3", "thinkpad-t14-gen-3"),
        ("  ThinkPad  T495  ", "thinkpad-t495"),
        ("20N4CTO1WW", "20n4cto1ww"),
        ("", "firmware"),
        ("///", "firmware"),
    ]:
        got_slug = ts_window.slugify_model(raw_model)
        check(f"slugify {raw_model!r}", got_slug == expected_slug, got_slug)
    machine = win._machine_name()
    check(
        "machine name is filename-safe and non-empty",
        bool(machine) and machine == ts_window.slugify_model(machine),
        machine,
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "export.json"
        win._write_export(Gio.File.new_for_path(str(out)))
        check("export wrote a file", out.exists())
        exported = json.loads(out.read_text(encoding="utf-8"))
        check(
            "export matches fwupd's reply exactly",
            exported["BiosSettings"] == RAW,
            f"{len(exported.get('BiosSettings', []))} entries",
        )
        check(
            "exported file re-parses as a fixture",
            len(_parse(exported["BiosSettings"])) == len(SETTINGS),
        )

    # Fuzzy search: the answer must come first, and the weak subsequence tail
    # must be cut off. "wol" technically subsequence-matches most settings.
    for query, expected_first, max_results in [
        ("kbbeep", "KeyboardBeep", 3),
        ("wol", "WakeOnLAN", 4),
        ("thund", "ThunderboltAccess", 4),
        ("sleep", "SleepState", 3),
        ("tpm", "SecurityChip", 4),
        ("virt", "VirtualizationTechnology", 5),
    ]:
        win._search = query
        ranked = win._ranked()
        top_name = ranked[0].name if ranked else None
        check(
            f"search {query!r} ranks {expected_first} first",
            top_name == expected_first,
            str(top_name),
        )
        check(
            f"search {query!r} stays focused (<= {max_results})",
            len(ranked) <= max_results,
            f"{len(ranked)} results",
        )
    win._search = "zzzznotathing"
    check("nonsense query returns nothing", win._ranked() == [])
    win._search = ""

    # The fallback humaniser must never cut through a word. Parking acronyms by
    # plain substring replacement turned SATAControllerMode into
    # "Sat AC ontroller Mode" — a real attribute name on other ThinkPad models.
    from thinkpad_settings.metadata import _humanise

    for raw, expected in [
        ("SATAControllerMode", "Sata Controller Mode"),
        ("ACandBattery", "AC and Battery"),
        ("PlaceholderACValue", "Placeholder AC Value"),
        ("VTdFeature", "VTd Feature"),
        ("IPv4NetworkStack", "IPv4 Network Stack"),
        ("NVMe0", "NVMe 0"),
        ("USBHDD", "USB HDD"),
        ("ATAPICD0", "ATAPI CD 0"),
        ("MACAddressPassThrough", "MAC Address Pass Through"),
        ("KernelDMAProtection", "Kernel DMA Protection"),
    ]:
        check(f"humanise {raw}", _humanise(raw) == expected, _humanise(raw))

    # The catalog only covers the machine it was captured on. On any other
    # model an uncatalogued password/lock/permanent setting must still be
    # treated as dangerous, or it would silently skip confirmation.
    for raw, want_danger in [
        ("SupervisorPassword", True),
        ("LockBIOSSetting", True),
        ("TpmClearRequest", True),
        ("SecureWipeDrive", True),
        ("AbsoluteModule", True),
        ("BootOrderLockAlt", True),
        ("BlockSleep", False),
        ("ClockingMode", False),
        ("BlockSid", False),
        ("SGXControl", False),
        ("SATAControllerMode", False),
        ("DiscreteGraphicsMode", False),
    ]:
        is_danger = metadata.get(raw).risk == metadata.RISK_DANGER
        check(
            f"uncatalogued {raw} danger={want_danger}",
            is_danger == want_danger,
            str(is_danger),
        )

    # Sidebar selection: exercises the row.category attribute and the
    # collapsed-mode navigation, neither of which the render loop touches.
    win._search = ""
    # GtkListBox only emits row-selected on an actual change, and the render
    # loop above set _category directly, so clear the selection first.
    win._sidebar_list.unselect_all()
    row = win._sidebar_list.get_first_child()
    seen = []
    while row is not None:
        win._sidebar_list.select_row(cast("Gtk.ListBoxRow", row))
        seen.append(win._category)
        row = row.get_next_sibling()
    check("selecting every sidebar row works", seen == cats, f"{seen}")
    check("show_content set after selection", win._split.get_show_content())

    # Selecting a category while searching must clear the search.
    win._search_entry.set_text("virtual")
    win._search = "virtual"
    win._sidebar_list.select_row(
        cast("Gtk.ListBoxRow", win._sidebar_list.get_first_child())
    )
    check("selecting a category clears search", win._search == "", repr(win._search))
    check(
        "search entry cleared too",
        win._search_entry.get_text() == "",
        repr(win._search_entry.get_text()),
    )

    # The ComboRow trap: set_model() and set_selected() both emit
    # notify::selected, indistinguishable from a user click. Building a page
    # must therefore stage nothing.
    win._staged.clear()
    for cat in cats:
        win._category = cat
        win._rebuild_pane()
    check(
        "building pages stages nothing (ComboRow notify guard)",
        win._staged == {},
        f"leaked: {sorted(win._staged)}",
    )

    kinds: dict[str, int] = {}
    win._building = True
    try:
        for s in win._settings:
            try:
                row = win._row_for(s)
                kinds[type(row).__name__] = kinds.get(type(row).__name__, 0) + 1
            except Exception:
                traceback.print_exc()
                failures.append(f"row_for {s.name}")
    finally:
        win._building = False
    print(f"        row types: {kinds}")
    check("every setting produced a row", sum(kinds.values()) == 103)
    check(
        "row census stages nothing", win._staged == {}, f"leaked: {sorted(win._staged)}"
    )

    # Every contributed machine must render completely: no attribute may fall
    # through to "Other", which is what a missing catalog entry looks like to
    # the person running it.
    for fixture in sorted(DUMP.parent.glob("fixture-*.json")):
        raw_fx = json.loads(fixture.read_text(encoding="utf-8"))["BiosSettings"]
        parsed = _parse(raw_fx)
        uncatalogued = [
            x.name
            for x in parsed
            if metadata.get(x.name).category == metadata.UNCATEGORISED
        ]
        check(
            f"{fixture.name}: every attribute is catalogued",
            not uncatalogued,
            f"{len(uncatalogued)} in Other: {uncatalogued[:5]}",
        )
        unlabelled = [
            f"{x.name}={v}"
            for x in parsed
            for v in x.possible_values
            if v not in metadata.get(x.name).value_labels
        ]
        check(
            f"{fixture.name}: every permitted value has a label",
            not unlabelled,
            f"{len(unlabelled)} missing: {unlabelled[:5]}",
        )

    # Error classification, including the two traps the research surfaced.
    from gi.repository import Gio as _Gio

    from thinkpad_settings.fwupd import _classify

    auth = _Gio.dbus_error_new_for_dbus_error(
        "org.freedesktop.fwupd.AuthFailed", "Failed to obtain auth"
    )
    c = _classify(auth)
    check("AuthFailed -> DENIED", c.kind.value == "denied", c.kind.value)
    check(
        "AuthFailed message has no GDBus prefix",
        "GDBus.Error" not in c.message,
        c.message,
    )

    timeout: GLib.Error = GLib.Error.new_literal(
        _Gio.io_error_quark(), "Timeout was reached", int(_Gio.IOErrorEnum.TIMED_OUT)
    )
    c = _classify(timeout)
    check("timeout -> TIMEOUT", c.kind.value == "timeout", c.kind.value)

    unknown = _Gio.dbus_error_new_for_dbus_error(
        "org.freedesktop.fwupd.Internal", "something broke"
    )
    c = _classify(unknown)
    check("unknown remote -> OTHER", c.kind.value == "other", c.kind.value)
    check("unknown message stripped", c.message == "something broke", c.message)

    local: GLib.Error = GLib.Error.new_literal(
        _Gio.io_error_quark(), "plain failure", 0
    )
    c = _classify(local)
    check(
        "non-remote error survives classification", c.kind.value == "other", c.message
    )

    # NotFound/NotSupported are per-request refusals from fwupd, not statements
    # about the machine. Claiming "this machine has no editable BIOS settings"
    # here was flatly wrong on a ThinkPad that plainly does.
    for remote, text, kind in [
        ("org.freedesktop.fwupd.NotFound", "attribute not found", "rejected"),
        ("org.freedesktop.fwupd.NotSupported", "SecureBoot is read only", "rejected"),
        (
            "org.freedesktop.fwupd.NotSupported",
            "Bogus doesn't map to any possible values for BootMode",
            "rejected",
        ),
        (
            "org.freedesktop.fwupd.NothingToDo",
            "no BIOS settings needed to be changed",
            "nothing",
        ),
    ]:
        c = _classify(_Gio.dbus_error_new_for_dbus_error(remote, text))
        check(f"{remote.split('.')[-1]} -> {kind}", c.kind.value == kind, c.kind.value)
        check(
            f"{remote.split('.')[-1]} keeps fwupd's wording",
            c.message == text,
            c.message,
        )

    # An unauthorised read succeeds but omits every value. Defaulting those to
    # "" would render 103 switches in the off position as if that were real.
    import copy as _copy

    from thinkpad_settings.fwupd import VALUE_KEY
    from thinkpad_settings.fwupd import _parse as _p

    raw = json.loads(DUMP.read_text(encoding="utf-8"))["BiosSettings"]
    check("authorised read discloses everything", all(x.disclosed for x in _p(raw)))

    withheld = _copy.deepcopy(raw)
    for item in withheld:
        if item["Name"] != "pending_reboot":
            item.pop(VALUE_KEY, None)
    parsed = _p(withheld)
    real_only = [x for x in parsed if x.name != "pending_reboot"]
    check(
        "withheld values are None, never ''",
        all(x.current_value is None for x in real_only),
    )
    check("withheld settings are not editable", not any(x.editable for x in parsed))
    check(
        "blanket withholding is detectable as denial",
        bool(real_only) and not any(x.disclosed for x in real_only),
    )

    partial = _copy.deepcopy(raw)
    target = next(i for i in partial if i["Name"] == "TouchPad")
    target.pop(VALUE_KEY, None)
    parsed_partial = _p(partial)
    real_partial = [x for x in parsed_partial if x.name != "pending_reboot"]
    check(
        "one withheld value is not a blanket denial",
        any(x.disclosed for x in real_partial),
    )
    touchpad = next(x for x in parsed_partial if x.name == "TouchPad")
    check(
        "the single withheld setting is read-only",
        not touchpad.editable and not touchpad.disclosed,
    )

    # fwupd will not consult polkit at all unless the client first declares
    # ALLOW_AUTHENTICATION. Without it every read succeeds with the values
    # stripped and no prompt is ever shown, so this ordering is load-bearing.
    from thinkpad_settings.fwupd import (
        FEATURE_ALLOW_AUTHENTICATION,
        FwupdClient,
    )

    check(
        "ALLOW_AUTHENTICATION is 256",
        FEATURE_ALLOW_AUTHENTICATION == 256,
        str(FEATURE_ALLOW_AUTHENTICATION),
    )

    class FakeReply:
        def __init__(self, items: list[dict[str, object]]) -> None:
            self._items = items

        def unpack(self) -> tuple[list[dict[str, object]]]:
            return (self._items,)

    class FakeBus:
        def __init__(self, items: list[dict[str, object]]) -> None:
            self.methods: list[str] = []
            self.flag_value: int | None = None
            self._items = items

        def call(  # noqa: PLR0913, PLR0917  (mirrors Gio.DBusConnection.call)
            self,
            name: str,
            path: str,
            iface: str,
            method: str,
            params: GLib.Variant | None,
            rtype: GLib.VariantType | None,
            flags: object,
            timeout: int,
            cancellable: object,
            cb: Callable[[object, str], None],
        ) -> None:
            self.methods.append(method)
            if method == "SetFeatureFlags" and params is not None:
                self.flag_value = params.unpack()[0]
            cb(self, method)

        def call_finish(self, result: str) -> object:
            if result == "SetFeatureFlags":
                return None
            return FakeReply(self._items)

    client = FwupdClient()
    # Keep the fake directly: reading it back through the typed _bus slot would
    # only see Gio.DBusConnection, which has none of these attributes.
    fake_bus = FakeBus(raw)
    client._bus = cast("_Gio.DBusConnection", fake_bus)
    got: list[object] = []
    client.get_settings(got.append, got.append)
    check(
        "SetFeatureFlags precedes GetBiosSettings",
        fake_bus.methods == ["SetFeatureFlags", "GetBiosSettings"],
        str(fake_bus.methods),
    )
    check(
        "declared flag is ALLOW_AUTHENTICATION",
        fake_bus.flag_value == FEATURE_ALLOW_AUTHENTICATION,
        str(fake_bus.flag_value),
    )
    first = got[0] if got else None
    check(
        "read succeeded through the fake bus",
        isinstance(first, list) and len(first) == 104,
        type(first).__name__,
    )

    # A redacted reply must surface as denial, not as 103 fabricated values.
    client2 = FwupdClient()
    client2._bus = cast("_Gio.DBusConnection", FakeBus(withheld))
    got2: list[tuple[str, object]] = []
    client2.get_settings(
        lambda s: got2.append(("ok", s)), lambda e: got2.append(("err", e))
    )
    outcome, payload = got2[0] if got2 else ("none", None)
    check(
        "redacted reply raises denial, not success",
        outcome == "err"
        and isinstance(payload, FwupdError)
        and payload.kind.value == "denied",
        outcome,
    )

    # An emptied boot order serialises to "", which is falsy — the exact case
    # that most needs the "may not boot" warning.
    check("emptied boot order serialises to ''", bootorder.serialise([]) == "")
    check(
        "emptied boot order is flagged unbootable",
        bootorder.is_unbootable(bootorder.parse("")),
    )

    vt = win._by_name["VirtualizationTechnology"]
    check("VT currently Enable", vt.current_value == "Enable")
    win._stage(vt, "Disable")
    check("stage recorded", win._staged.get("VirtualizationTechnology") == "Disable")
    check("apply bar revealed", win._apply_revealer.get_reveal_child())
    check(
        "apply bar text",
        win._staged_label.get_text() == "1 setting changed",
        win._staged_label.get_text(),
    )

    win._stage(vt, "Enable")
    check("no-op change unstaged", "VirtualizationTechnology" not in win._staged)
    check("apply bar hidden again", not win._apply_revealer.get_reveal_child())

    bo = win._by_name["BootOrder"]
    enabled, _available = bootorder.partition(
        bo.current_value or "", bo.possible_values
    )

    # fwupd validates a written value against possible_values, and BootOrder's
    # value is a colon-joined list while its possible values are single device
    # codes — so any reorder is rejected AND aborts the whole batch. The row is
    # therefore read-only, and the pre-flight check must catch it regardless.
    win._staged.clear()
    win._staged["BootOrder"] = bootorder.serialise(bootorder.move(enabled, 0, 1))
    check(
        "boot order reorder is caught before sending",
        win._rejection() is not None,
        str(win._rejection()),
    )
    win._staged.clear()

    win._staged["SecurityChip"] = "Disable"
    risky = sorted(
        (n for n in win._staged if metadata.get(n).is_risky),
        key=lambda n: -metadata.risk_rank(metadata.get(n).risk),
    )
    check(
        "danger sorts before caution",
        metadata.get(risky[0]).risk == "danger",
        f"first={risky[0]}",
    )

    check("unbootable order detected", bootorder.is_unbootable(["PXEBOOT", "USBHDD"]))
    check("current order is bootable", not bootorder.is_unbootable(enabled))

    # Confirmation gating: a modal in front of 62 of 104 settings stops being
    # read, so only danger changes (or a concrete warning) may block.
    applied_directly = []
    real_apply = win._apply
    win._apply = lambda: applied_directly.append(True)  # type: ignore[method-assign]

    win._staged.clear()
    win._staged["SleepState"] = "Windows"  # caution
    win._confirm_apply()
    check(
        "caution-only change applies without a dialog",
        applied_directly == [True],
        f"{applied_directly}",
    )

    applied_directly.clear()
    win._staged.clear()
    win._staged["SecurityChip"] = "Disable"  # danger
    win._confirm_apply()
    check(
        "danger change is blocked pending confirmation",
        applied_directly == [],
        f"{applied_directly}",
    )

    applied_directly.clear()
    win._staged.clear()
    win._staged["BootOrder"] = ""  # emptied: falsy but fatal
    win._confirm_apply()
    check(
        "emptied boot order never reaches apply",
        applied_directly == [],
        f"{applied_directly}",
    )

    applied_directly.clear()
    win._staged.clear()
    win._staged["SecureBoot"] = "Enable"  # firmware-reported read-only
    check(
        "read-only setting is caught before sending",
        win._rejection() is not None,
        str(win._rejection()),
    )
    win._confirm_apply()
    check(
        "read-only setting never reaches apply",
        applied_directly == [],
        f"{applied_directly}",
    )

    applied_directly.clear()
    win._staged.clear()
    win._staged["BootMode"] = "NotAValue"  # outside possible_values
    check(
        "out-of-range value is caught before sending",
        win._rejection() is not None,
        str(win._rejection()),
    )

    win._apply = real_apply  # type: ignore[method-assign]

    # BootOrder must not be rendered through the single-value labeller.
    desc = win._describe("BootOrder", "NVMe0:PXEBOOT")
    check("boot order renders as a device sequence", "→" in desc, desc)
    check(
        "boot order description names real devices",
        "NVMe" in desc and "PXE" in desc,
        desc,
    )
    check(
        "emptied boot order describes itself plainly",
        win._describe("BootOrder", "") == "no boot devices at all",
        win._describe("BootOrder", ""),
    )
    check(
        "ordinary value still uses the value labeller",
        win._describe("SleepState", "Linux").startswith("Linux"),
        win._describe("SleepState", "Linux"),
    )

    win._staged.clear()
    win._staged["SleepState"] = "Windows"
    win._staged["SecurityChip"] = "Disable"

    win._apply()
    check("set_settings called", cast("StubClient", win._client).set_calls == 1)
    check(
        "payload matched staged",
        set(applied_payload) == {"SleepState", "SecurityChip"},
        str(sorted(applied_payload)),
    )
    check("staged cleared after apply", not win._staged)

    win._staged["TouchPad"] = "Disable"
    win._revert_all()
    check("revert clears staging", not win._staged)

    app.quit()


def main() -> int:
    app = Adw.Application(application_id="com.rabeei.ThinkPadSettings.Smoke")

    def activate(a: Adw.Application) -> None:
        try:
            run(a)
        except Exception:
            traceback.print_exc()
            failures.append("unhandled exception")
            a.quit()

    app.connect("activate", activate)
    app.run([])

    print()
    if failures:
        print(f"FAILED ({len(failures)}): {failures}")
        return 1
    print("ALL SMOKE TESTS PASSED")
    return 0


sys.exit(main())
