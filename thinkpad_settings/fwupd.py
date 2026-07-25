"""Async D-Bus client for fwupd's BIOS settings API.

The app runs as an unprivileged desktop user. Reads and writes both go through
fwupd on the system bus, which is gated by polkit:

    org.freedesktop.fwupd.get-bios-settings   allow_active=auth_admin_keep
    org.freedesktop.fwupd.set-bios-settings   allow_active=auth_admin

Because ``SetBiosSettings`` takes a dict, every staged change is written in a
single call, so N edits still cost exactly one authentication prompt.

Writing through fwupd rather than poking
/sys/class/firmware-attributes/thinklmi/attributes/*/current_value directly
also means fwupd validates the value against the firmware's allowed list and
handles the ``save_settings`` commit for us.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Callable

from gi.repository import Gio, GLib

BUS_NAME = "org.freedesktop.fwupd"
OBJECT_PATH = "/"
INTERFACE = "org.freedesktop.fwupd"

# Reads and writes can both block on a human typing into a polkit dialog, so the
# GDBus default of 25s is far too short. These are ceilings, not expected waits.
GET_TIMEOUT_MS = 120_000
SET_TIMEOUT_MS = 300_000

# Not a real setting: a read-only flag meaning changes are staged for next boot.
PENDING_REBOOT = "pending_reboot"

# fwupd omits this key entirely rather than erroring when the caller is not
# authorised to read values.
VALUE_KEY = "BiosSettingCurrentValue"

# FwupdFeatureFlags.ALLOW_AUTHENTICATION.
#
# This is not optional decoration. fwupd checks it *before* deciding whether to
# consult polkit at all: a client that has not declared it gets an immediate,
# successful reply with every value stripped out and no authentication prompt
# ever shown. Declaring it is what makes fwupd run the polkit check and hand
# back real values. Feature flags are per-connection, so it must be sent on the
# same bus connection as the calls that follow.
FEATURE_ALLOW_AUTHENTICATION = 1 << 8


class Failure(enum.Enum):
    """Why a call failed, in terms the UI can act on."""

    CANCELLED = "cancelled"  # we cancelled it ourselves — stay quiet
    DENIED = "denied"  # polkit did not authorise: dismissed, wrong password or refused
    TIMEOUT = "timeout"  # no reply in time; the write may still have landed
    UNAVAILABLE = "unavailable"  # fwupd not running / not on the bus
    UNSUPPORTED = "unsupported"  # no firmware-attributes device on this machine
    OTHER = "other"


# fwupd maps a dismissed polkit dialog, a mistyped password and an outright
# refusal onto a single AuthFailed error — it never reads polkit's "dismissed"
# bit — so these cannot be told apart and must share one neutral message.
_AUTH_ERRORS = {
    "org.freedesktop.fwupd.AuthFailed",
    "org.freedesktop.fwupd.AuthExpired",
    "org.freedesktop.fwupd.PermissionDenied",
}


class FwupdError(Exception):
    def __init__(self, kind: Failure, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class BiosSetting:
    """One firmware attribute as reported by fwupd.

    ``current_value`` is None when fwupd declined to disclose it. That happens
    on an unauthorised read: the call still SUCCEEDS and still lists every
    attribute, it just omits the value key. Defaulting that to "" would make
    the UI display every setting as disabled, which is a lie, so it stays None
    and callers must handle it.
    """

    name: str
    description: str
    current_value: str | None
    possible_values: tuple[str, ...] = field(default=())
    read_only: bool = False
    setting_id: str = ""
    path: str = ""

    @property
    def disclosed(self) -> bool:
        return self.current_value is not None

    @property
    def is_enum(self) -> bool:
        """True when the firmware constrains this to a fixed set of values.

        Derived from the presence of a possible-values list rather than from
        fwupd's BiosSettingType, which reports 0 (unknown) for thinklmi's
        free-text attributes instead of a string kind.
        """
        return bool(self.possible_values)

    @property
    def editable(self) -> bool:
        return not self.read_only and self.name != PENDING_REBOOT and self.disclosed


def _plain(error: GLib.Error) -> str:
    """The human half of a GDBus error message.

    Gio.dbus_error_strip_remote_error() reports success but leaves the message
    PyGObject exposes untouched, so the "GDBus.Error:<name>: " prefix has to be
    removed by hand or it ends up in front of the user.
    """
    message = error.message or "Unknown error"
    if message.startswith("GDBus.Error:"):
        _, _, tail = message.partition(": ")
        message = tail or message
    return message


def _classify(error: GLib.Error) -> FwupdError:
    """Map a GLib/GDBus error onto something the UI can present sensibly."""
    if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
        return FwupdError(Failure.CANCELLED, "Cancelled")

    if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT):
        # Deliberately neutral: this is raised for reads too, where advice
        # about a change having been applied would be nonsense. The write path
        # adds that caveat itself.
        return FwupdError(
            Failure.TIMEOUT, "The firmware service did not respond in time"
        )

    message = _plain(error)
    remote = ""
    if Gio.dbus_error_is_remote_error(error):
        remote = Gio.dbus_error_get_remote_error(error) or ""

    if remote in (
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.NameHasNoOwner",
    ):
        return FwupdError(
            Failure.UNAVAILABLE,
            "The fwupd service is not running. Start it with: "
            "sudo systemctl start fwupd",
        )

    if remote in _AUTH_ERRORS:
        return FwupdError(Failure.DENIED, "Not authorized — nothing was changed")

    if remote in ("org.freedesktop.fwupd.NotSupported", "org.freedesktop.fwupd.NotFound"):
        return FwupdError(
            Failure.UNSUPPORTED,
            "This machine does not expose editable BIOS settings to Linux. "
            "It needs a Lenovo ThinkPad with the think_lmi kernel driver loaded.",
        )

    lowered = message.lower()
    if "not authorized" in lowered or "not permitted" in lowered:
        return FwupdError(Failure.DENIED, "Not authorized — nothing was changed")

    return FwupdError(Failure.OTHER, message)


def _parse(raw: list[dict]) -> list[BiosSetting]:
    settings = []
    for item in raw:
        name = item.get("Name")
        if not name:
            continue
        possible = item.get("BiosSettingPossibleValues") or ()
        raw_value = item.get(VALUE_KEY)
        settings.append(
            BiosSetting(
                name=name,
                description=item.get("Description") or name,
                current_value=None if raw_value is None else str(raw_value),
                possible_values=tuple(possible),
                read_only=bool(item.get("BiosSettingReadOnly", False)),
                setting_id=item.get("BiosSettingId") or "",
                path=item.get("Filename") or "",
            )
        )
    settings.sort(key=lambda s: s.name)
    return settings


class FwupdClient:
    """Talks to fwupd on the system bus without ever blocking the main loop.

    Every public method takes a callback invoked on the GLib main context with
    either the result or a FwupdError.
    """

    def __init__(self) -> None:
        self._bus: Gio.DBusConnection | None = None
        self._announced = False
        self._cancellable = Gio.Cancellable()

    def cancel(self) -> None:
        """Abandon any call in flight, e.g. when the window is closing."""
        self._cancellable.cancel()
        self._cancellable = Gio.Cancellable()

    # -- connection ------------------------------------------------------

    def _with_bus(
        self,
        on_ready: Callable[[Gio.DBusConnection], None],
        on_error: Callable[[FwupdError], None],
    ) -> None:
        if self._bus is not None and self._announced:
            on_ready(self._bus)
            return

        def announce(bus: Gio.DBusConnection) -> None:
            """Tell fwupd we can handle an authentication prompt.

            Skipping this does not fail loudly — it just makes every read come
            back with the values quietly removed.
            """

            def flagged(_source, result):
                try:
                    bus.call_finish(result)
                except GLib.Error:
                    # Older daemons may not know the call. Carry on: the
                    # redaction check in get_settings catches the consequence.
                    pass
                self._announced = True
                on_ready(bus)

            bus.call(
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                "SetFeatureFlags",
                GLib.Variant("(t)", (FEATURE_ALLOW_AUTHENTICATION,)),
                GLib.VariantType("()"),
                Gio.DBusCallFlags.NONE,
                10_000,
                self._cancellable,
                flagged,
            )

        if self._bus is not None:
            announce(self._bus)
            return

        def finished(_source, result):
            try:
                self._bus = Gio.bus_get_finish(result)
            except GLib.Error as exc:
                on_error(_classify(exc))
                return
            announce(self._bus)

        Gio.bus_get(Gio.BusType.SYSTEM, self._cancellable, finished)

    # -- reads -----------------------------------------------------------

    def get_settings(
        self,
        on_success: Callable[[list[BiosSetting]], None],
        on_error: Callable[[FwupdError], None],
    ) -> None:
        """Fetch every firmware attribute. Prompts for auth once per session."""

        def call(bus: Gio.DBusConnection) -> None:
            def finished(_source, result):
                try:
                    reply = bus.call_finish(result)
                except GLib.Error as exc:
                    on_error(_classify(exc))
                    return

                settings = _parse(reply.unpack()[0])
                # An unauthorised read is not reported as an error: the reply
                # succeeds and lists every attribute with its value withheld.
                # Detect that here so the UI asks for authentication instead of
                # rendering a window full of fabricated values.
                real = [s for s in settings if s.name != PENDING_REBOOT]
                if real and not any(s.disclosed for s in real):
                    on_error(
                        FwupdError(
                            Failure.DENIED,
                            "fwupd withheld every setting value, so "
                            "authentication was declined or did not complete",
                        )
                    )
                    return
                on_success(settings)

            bus.call(
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                "GetBiosSettings",
                None,
                GLib.VariantType("(aa{sv})"),
                Gio.DBusCallFlags.NONE,
                GET_TIMEOUT_MS,
                self._cancellable,
                finished,
            )

        self._with_bus(call, on_error)

    # -- writes ----------------------------------------------------------

    def set_settings(
        self,
        changes: dict[str, str],
        on_success: Callable[[], None],
        on_error: Callable[[FwupdError], None],
    ) -> None:
        """Write every staged change in one call, so auth is prompted once."""
        if not changes:
            on_success()
            return

        def call(bus: Gio.DBusConnection) -> None:
            def finished(_source, result):
                try:
                    bus.call_finish(result)
                except GLib.Error as exc:
                    on_error(_classify(exc))
                    return
                on_success()

            bus.call(
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                "SetBiosSettings",
                # D-Bus arguments are always a tuple, hence the trailing comma:
                # the signature is (a{ss}), not a{ss}.
                GLib.Variant("(a{ss})", (changes,)),
                GLib.VariantType("()"),
                Gio.DBusCallFlags.NONE,
                SET_TIMEOUT_MS,
                self._cancellable,
                finished,
            )

        self._with_bus(call, on_error)
