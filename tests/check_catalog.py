"""Validate metadata.json against the captured firmware dump.

Catches the ways a hand-edited catalog silently goes wrong: an attribute whose
value labels no longer match what the firmware permits, a category that is not
in CATEGORY_ORDER, a risk level with no explanation, or a description that
asserts the author's own machine state as fact.

Needs no hardware and no display.

    python3 tests/check_catalog.py
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from thinkpad_settings import metadata

CATALOG = HERE.parent / "thinkpad_settings" / "metadata.json"
FIXTURE = HERE / "fixture-t14gen3.json"

# Phrases that pin a description to one machine's current state. The reader's
# machine differs, and the sentence can end up contradicting the value shown in
# the very same row.
STATEFUL = re.compile(
    r"as it is now|currently (?:disabled|enabled|the|runs|does|has|set)"
    r"|no BIOS passwords are set on this machine|none is on this machine",
    re.IGNORECASE,
)

problems: list[str] = []


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    firmware = json.loads(FIXTURE.read_text(encoding="utf-8"))["BiosSettings"]

    permitted = {
        item["Name"]: tuple(item.get("BiosSettingPossibleValues") or ())
        for item in firmware
    }

    names = [entry["name"] for entry in catalog]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        problems.append(f"duplicate entries: {sorted(duplicates)}")

    missing = set(permitted) - set(names)
    if missing:
        problems.append(
            f"firmware attributes absent from the catalog: {sorted(missing)}"
        )

    unknown = set(names) - set(permitted)
    if unknown:
        problems.append(
            f"catalog entries not present in the firmware dump: {sorted(unknown)}"
        )

    for entry in catalog:
        name = entry["name"]

        if entry.get("category") not in metadata.CATEGORY_ORDER:
            problems.append(
                f"{name}: category {entry.get('category')!r} is not in CATEGORY_ORDER"
            )

        risk = entry.get("risk")
        if risk not in (
            metadata.RISK_SAFE,
            metadata.RISK_CAUTION,
            metadata.RISK_DANGER,
        ):
            problems.append(f"{name}: invalid risk {risk!r}")
        elif risk != metadata.RISK_SAFE and not entry.get("risk_note", "").strip():
            problems.append(f"{name}: risk {risk!r} with no risk_note")

        for field in ("label", "description"):
            if not entry.get(field, "").strip():
                problems.append(f"{name}: empty {field}")

        for field in ("description", "risk_note"):
            match = STATEFUL.search(entry.get(field, ""))
            if match:
                problems.append(
                    f"{name}: {field} asserts machine state — {match.group(0)!r}"
                )

        if name in permitted:
            labelled = [v["value"] for v in entry.get("value_labels") or []]
            expected = list(permitted[name])
            if labelled != expected:
                problems.append(
                    f"{name}: value_labels {labelled} do not match "
                    f"firmware possible values {expected}"
                )
            for pair in entry.get("value_labels") or []:
                if not pair.get("label", "").strip():
                    problems.append(
                        f"{name}: empty label for value {pair.get('value')!r}"
                    )

    print(f"catalog entries : {len(catalog)}")
    print(f"firmware attrs  : {len(permitted)}")

    if problems:
        print(f"\nFAILED — {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nCatalog OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
