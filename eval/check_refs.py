"""A reference.yaml fájlok formai ellenőrzése: érvényes-e, és megvannak-e a mezők.

    python eval/check_refs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

EVAL = Path(__file__).resolve().parent
REQUIRED = ("id", "allapot", "allitasok", "kotelezo_jeloles", "tilos_szamok", "tilos")
CLAIM = ("eszkoz", "irany", "szintek", "idotav", "meggyozodes", "pozicio", "tezis_vagy_trade", "kritikus")
IRANY = {"long", "short", "kivar", "keszpenz", "semleges", "vegyes"}
SZABALY = {"D2", "D3", "D6"}
RANGE = re.compile(r"^\d+(\.\d+)?-\d+(\.\d+)?$")


def number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def level_ok(level) -> bool:
    """Egy szint: szám, több elfogadott szám listája, vagy "a-b" tartomány (pontozas.md, B)."""
    if number(level):
        return True
    if isinstance(level, list):
        return bool(level) and all(number(x) for x in level)
    return isinstance(level, str) and bool(RANGE.match(level))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    bad = 0
    for ref in sorted((EVAL / "cases").glob("*/reference.yaml")):
        case = ref.parent.name
        try:
            data = yaml.safe_load(ref.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            print(f"✗ {case}: hibás YAML: {exc}")
            bad += 1
            continue
        errors = [f"hiányzó mező: {k}" for k in REQUIRED if k not in data]
        if data.get("id") != case:
            errors.append(f"az id ({data.get('id')}) nem egyezik a mappanévvel")
        if data.get("allapot") not in ("piszkozat", "jovahagyva"):
            errors.append("allapot: piszkozat vagy jovahagyva")
        for claim in data.get("allitasok") or []:
            missing = [k for k in CLAIM if k not in claim]
            if missing:
                errors.append(f"{claim.get('id')}: hiányzik {', '.join(missing)}")
            if claim.get("irany") not in IRANY:
                errors.append(f"{claim.get('id')}: ismeretlen irany: {claim.get('irany')}")
            for level in claim.get("szintek") or []:
                if not level_ok(level):
                    errors.append(f"{claim.get('id')}: hibás szint: {level!r}")
        for item in data.get("tilos") or []:
            if isinstance(item, dict):
                if not item.get("szoveg"):
                    errors.append(f"tilos: hiányzó szoveg: {item}")
                if item.get("szabaly") not in SZABALY:
                    errors.append(f"tilos: ismeretlen szabaly: {item.get('szabaly')}")
            elif not isinstance(item, str):
                errors.append(f"tilos: szöveg vagy {{szoveg, szabaly}} kell: {item!r}")
        if "jeloles_elol" in data and not isinstance(data["jeloles_elol"], bool):
            errors.append("jeloles_elol: true vagy false")
        if data.get("jeloles_elol") and not data.get("kotelezo_jeloles"):
            errors.append("jeloles_elol: true, de nincs kotelezo_jeloles")
        if errors:
            bad += 1
            print(f"✗ {case}")
            for e in errors:
                print(f"    {e}")
        else:
            print(f"✓ {case}: {data['allapot']}, {len(data.get('allitasok') or [])} állítás")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
