"""Human-readable descriptions for raw firmware attribute names.

The firmware reports names like ``VTdFeature`` and values like ``ACandBattery``
with no descriptions worth showing — thinklmi's ``Description`` field is just
the name repeated. This module supplies the labels, explanations, risk levels
and per-value wording that make the raw attributes intelligible.

The catalog in ``metadata.json`` is keyed on attribute name and was written
against a ThinkPad T14 Gen 3. Any attribute not in it still renders correctly
via ``_derive`` — a different ThinkPad model exposing extra attributes gets
readable labels, just no hand-written description.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).with_name("metadata.json")

UNCATEGORISED = "Other"

# Sidebar order. Deliberately not alphabetical: the things people actually come
# here to change sit at the top, the obscure ones sink.
CATEGORY_ORDER = [
    "CPU & Virtualization",
    "Power & Thermal",
    "Boot & Startup",
    "Security",
    "Passwords",
    "Devices & Ports",
    "Network",
    "Input & Display",
    "Wake & Alarms",
    "Firmware & Management",
    UNCATEGORISED,
]

# Preferred icon per category, each with fallbacks, because icon availability
# varies between Adwaita versions. Resolved at runtime against the live theme.
CATEGORY_ICONS = {
    "CPU & Virtualization": ["computer-chip-symbolic", "applications-engineering-symbolic", "system-run-symbolic"],
    "Power & Thermal": ["power-profile-balanced-symbolic", "battery-symbolic", "preferences-system-power-symbolic"],
    "Boot & Startup": ["system-reboot-symbolic", "media-playback-start-symbolic", "system-run-symbolic"],
    "Security": ["security-high-symbolic", "channel-secure-symbolic", "changes-prevent-symbolic"],
    "Passwords": ["dialog-password-symbolic", "changes-prevent-symbolic"],
    "Devices & Ports": ["drive-harddisk-usb-symbolic", "media-removable-symbolic", "drive-harddisk-symbolic"],
    "Network": ["network-wireless-symbolic", "network-workgroup-symbolic"],
    "Input & Display": ["input-keyboard-symbolic", "video-display-symbolic"],
    "Wake & Alarms": ["alarm-symbolic", "preferences-system-time-symbolic", "document-open-recent-symbolic"],
    "Firmware & Management": ["application-x-firmware-symbolic", "system-software-update-symbolic", "emblem-system-symbolic"],
    UNCATEGORISED: ["view-more-symbolic", "emblem-system-symbolic"],
}

RISK_SAFE = "safe"
RISK_CAUTION = "caution"
RISK_DANGER = "danger"

_RISK_RANK = {RISK_SAFE: 0, RISK_CAUTION: 1, RISK_DANGER: 2}


@dataclass(frozen=True)
class SettingMeta:
    name: str
    label: str
    description: str
    category: str = UNCATEGORISED
    risk: str = RISK_CAUTION
    risk_note: str = ""
    value_labels: dict[str, str] = field(default_factory=dict)

    def label_for(self, value: str) -> str:
        """Friendly wording for a raw firmware value, falling back to itself."""
        return self.value_labels.get(value) or _humanise(value)

    @property
    def is_risky(self) -> bool:
        return self.risk in (RISK_CAUTION, RISK_DANGER)


# Acronyms that must survive CamelCase expansion intact. Matched
# case-sensitively and longest-first, so ACPI is consumed before AC and NVMe
# before its constituent letters.
_ACRONYMS = sorted(
    [
        "ATAPI", "HTTPS", "NVMe", "IPv4", "IPv6", "PCIe", "TFTP", "ACPI",
        "BIOS", "UEFI", "VTd", "USB", "HDD", "FDD", "PXE", "TPM", "TXT",
        "AMT", "DMA", "LAN", "WAN", "MAC", "SID", "CPU", "LCD", "SSD",
        "CD", "AC",
    ],
    key=len,
    reverse=True,
)

_SPLIT = re.compile(
    r"""
      (?<=[a-z0-9])(?=[A-Z])      # fooBar    -> foo Bar
    | (?<=[A-Z])(?=[A-Z][a-z])    # HTTPSBoot -> HTTPS Boot
    | (?<=[A-Za-z])(?=[0-9])      # Slot0     -> Slot 0
    """,
    re.VERBOSE,
)


def _humanise(raw: str) -> str:
    """Turn a CamelCase firmware token into something readable.

    Only a fallback, for values and attributes the catalog does not cover, so
    it favours never mangling an acronym over being clever.
    """
    if not raw:
        return raw

    # Park acronyms behind placeholders so the splitter cannot cut through
    # them, padding with spaces so neighbours stay separate tokens.
    #
    # The (?<![A-Z]) guard is what stops the match from landing mid-run: the
    # "AC" in SATAControllerMode is preceded by an uppercase letter and is not
    # a real word start, whereas the one in ACandBattery and PlaceholderACValue
    # is. The cost is occasionally leaving a run like USBHDD unsplit, which is
    # merely less pretty — far better than emitting "Sat AC ontroller Mode".
    text = raw.replace("_", " ")
    parked: dict[str, str] = {}
    for index, acronym in enumerate(_ACRONYMS):
        pattern = re.compile(r"(?<![A-Z])" + re.escape(acronym))
        key = f"\x00{index}\x00"
        text, count = pattern.subn(f" {key} ", text)
        if count:
            parked[key] = acronym

    words = [w for w in _SPLIT.sub(" ", text).split() if w]

    out = []
    for word in words:
        if word in parked:
            out.append(parked[word])
        elif word.isupper() and len(word) > 1:
            # An all-caps leftover such as BOOT reads better title-cased.
            out.append(word.capitalize())
        else:
            out.append(word)

    text = " ".join(out).strip()
    if not text:
        return raw
    # Capitalise only if the first token is not a protected acronym.
    return text if text[0].isupper() else text[:1].upper() + text[1:]


def _derive(name: str) -> SettingMeta:
    """Best-effort metadata for an attribute missing from the catalog."""
    return SettingMeta(
        name=name,
        label=_humanise(name),
        description=(
            "This setting is not in the built-in catalog, so no description is "
            "available. Its name and permitted values come straight from your "
            "firmware."
        ),
        category=UNCATEGORISED,
        # Unknown means unknown: warn rather than imply it is safe to flip.
        risk=RISK_CAUTION,
        risk_note="Not catalogued — its effect on this machine is unverified.",
    )


@lru_cache(maxsize=1)
def _catalog() -> dict[str, SettingMeta]:
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A missing or corrupt catalog degrades the app to derived labels
        # rather than preventing it from starting.
        return {}

    catalog: dict[str, SettingMeta] = {}
    for entry in raw:
        name = entry.get("name")
        if not name:
            continue
        category = entry.get("category") or UNCATEGORISED
        if category not in CATEGORY_ORDER:
            category = UNCATEGORISED
        risk = entry.get("risk") or RISK_CAUTION
        if risk not in _RISK_RANK:
            risk = RISK_CAUTION
        catalog[name] = SettingMeta(
            name=name,
            label=entry.get("label") or _humanise(name),
            description=entry.get("description") or "",
            category=category,
            risk=risk,
            risk_note=entry.get("risk_note") or "",
            value_labels={
                v["value"]: v["label"]
                for v in entry.get("value_labels") or []
                if v.get("value")
            },
        )
    return catalog


def get(name: str) -> SettingMeta:
    return _catalog().get(name) or _derive(name)


def category_index(category: str) -> int:
    try:
        return CATEGORY_ORDER.index(category)
    except ValueError:
        return len(CATEGORY_ORDER)


def risk_rank(risk: str) -> int:
    return _RISK_RANK.get(risk, 1)
