"""Special handling for BootOrder, which is a list rather than a choice.

Every other enumeration attribute holds exactly one of its possible values.
BootOrder holds an *ordered subset* of them, colon-joined:

    NVMe0:USBHDD:USBCD:USBFDD:PXEBOOT:LENOVOCLOUD:ON-PREMISE

Rendering that in a combo box would silently collapse it to a single device and
make the machine unbootable, so it gets a dedicated reorder editor instead.
"""

from __future__ import annotations

SEPARATOR = ":"

BOOT_ORDER = "BootOrder"

# Devices that can actually boot an installed OS on this class of machine.
# Used to warn before writing an order that has no local disk in it.
BOOTABLE_LOCAL = frozenset(
    {
        "NVMe0",
        "NVMe1",
        "HDD0",
        "HDD1",
        "HDD2",
        "HDD3",
        "HDD4",
        "OtherHDD",
    }
)

# Placeholder meaning "nothing", never useful in a user-authored order.
NO_DEVICE = "NODEV"


def parse(value: str) -> list[str]:
    """Split a stored BootOrder into its ordered device list."""
    return [part for part in value.split(SEPARATOR) if part]


def serialise(devices: list[str]) -> str:
    """Join a device list back into the firmware's wire format."""
    return SEPARATOR.join(devices)


def partition(value: str, possible: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Split the vocabulary into (enabled in order, available to add).

    Anything the firmware offers but the current order omits is 'available'.
    NODEV is dropped from both lists: it is a firmware placeholder, not a device
    a person would deliberately add.
    """
    enabled = [d for d in parse(value) if d != NO_DEVICE]
    seen = set(enabled)
    available = [d for d in possible if d not in seen and d != NO_DEVICE]
    return enabled, available


def move(devices: list[str], index: int, delta: int) -> list[str]:
    """Return a copy with the entry at *index* shifted by *delta* positions."""
    target = index + delta
    if not (0 <= index < len(devices)) or not (0 <= target < len(devices)):
        return list(devices)
    result = list(devices)
    result[index], result[target] = result[target], result[index]
    return result


def is_unbootable(devices: list[str]) -> bool:
    """True if this order contains no local disk to boot an installed OS from.

    Deliberately conservative: it only flags the clear-cut case of no internal
    storage at all, since a network-boot-only order is legitimate for some
    people and should warn, not be forbidden.
    """
    return not any(d in BOOTABLE_LOCAL for d in devices)
