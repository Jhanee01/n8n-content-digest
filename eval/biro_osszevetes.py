"""Két bíró egyezése ugyanazokon a jelentéseken.

    python eval/biro_osszevetes.py eval/runs/2026-11-14_0814
    python eval/biro_osszevetes.py eval/runs/2026-11-14_0814 --ref opus-637b2dd25cad

A futás `biro/` mappájában minden bírót a referenciához (alapból az első
`opus-` kezdetű) mér. Jelentésenként az első bírálat számít. Kimenet:
<futás>/biro/osszevetes.md.

Amit mér, és miért:
- **átment/megbukott egyezés:** a végeredmény ugyanaz-e;
- **kizáró kódonként:** hányszor találta a referencia, hányszor a jelölt, és
  hányszor mindkettő (egyezés = mindkettő / bármelyik);
- **állításonként a „lefedve”** és a **meggyőződés** döntés egyezése;
- **A-E pontok** átlagos eltérése.
A referencia nem az igazság: ahol eltérnek, ott a kalibrálásnál ember dönt.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PONTOK = "ABCDE"


def load(jdir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted(jdir.rglob("*.biro*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        if "eredmeny" not in r:
            continue
        key = f"{r['eset']}/{Path(r['jelentes']).stem}"
        if key not in out or r["ismetles"] < out[key]["ismetles"]:
            out[key] = r
    return out


def compare(ref: dict[str, dict], cand: dict[str, dict]) -> dict:
    keys = sorted(set(ref) & set(cand))
    same_pass = sum(ref[k]["eredmeny"]["atment"] == cand[k]["eredmeny"]["atment"] for k in keys)
    codes: dict[str, list[int]] = {}
    diffs = []
    for k in keys:
        a = set(ref[k]["eredmeny"]["kizaro_kodok"])
        b = set(cand[k]["eredmeny"]["kizaro_kodok"])
        for c in a | b:
            row = codes.setdefault(c, [0, 0, 0])
            row[0] += c in a
            row[1] += c in b
            row[2] += c in a and c in b
        if a != b:
            diffs.append((k, sorted(a), sorted(b)))
    claims = {"lefedve": [0, 0], "meggyozodes_jo": [0, 0]}
    for k in keys:
        ra = {x.get("id"): x for x in ref[k]["itelet"].get("allitasok") or []}
        ca = {x.get("id"): x for x in cand[k]["itelet"].get("allitasok") or []}
        for cid in ra:
            for field in claims:
                if field == "meggyozodes_jo" and not (ra[cid].get("lefedve") and ca.get(cid, {}).get("lefedve")):
                    continue
                claims[field][1] += 1
                claims[field][0] += ra[cid].get(field) == ca.get(cid, {}).get(field)
    pts = {}
    for p in PONTOK:
        d = [abs(ref[k]["eredmeny"]["pont"][p] - cand[k]["eredmeny"]["pont"][p]) for k in keys
             if ref[k]["eredmeny"]["pont"][p] is not None and cand[k]["eredmeny"]["pont"][p] is not None]
        pts[p] = sum(d) / len(d) if d else None
    cost = sum(r.get("koltseg_usd") or 0 for r in cand.values())
    return {"n": len(keys), "same_pass": same_pass, "codes": codes, "diffs": diffs,
            "claims": claims, "pts": pts, "cost": cost,
            "ids": sorted({r.get("modell_azonosito") or "?" for r in cand.values()})}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", help="egy eval/runs/<időbélyeg> mappa (benne biro/)")
    ap.add_argument("--ref", help="a referenciabíró mappaneve a biro/ alatt")
    args = ap.parse_args()

    base = Path(args.run) / "biro"
    dirs = sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []
    if len(dirs) < 2:
        raise SystemExit("legalább két bíró ítéletei kellenek a biro/ alatt")
    ref_dir = base / args.ref if args.ref else next((d for d in dirs if d.name.startswith("opus-")), dirs[0])
    ref = load(ref_dir)
    lines = [f"# Bírók összevetése: {Path(args.run).name}", "",
             f"Referencia: `{ref_dir.name}` ({len(ref)} jelentés). Jelentésenként az első bírálat.", ""]
    for d in dirs:
        if d == ref_dir:
            continue
        c = compare(ref, load(d))
        n = c["n"] or 1
        lines += [f"## {d.name}", "",
                  f"Modell: {', '.join(c['ids'])}. Közös jelentés: {c['n']}. Költség: ${c['cost']:.2f}.", "",
                  f"- átment/megbukott egyezik: **{c['same_pass']}/{c['n']}** ({100 * c['same_pass'] / n:.0f}%)",
                  f"- „lefedve” egyezik: {c['claims']['lefedve'][0]}/{c['claims']['lefedve'][1]}",
                  f"- meggyőződés egyezik (ahol mindkettő lefedettnek látta): "
                  f"{c['claims']['meggyozodes_jo'][0]}/{c['claims']['meggyozodes_jo'][1]}",
                  "- pontok átlagos eltérése: " + ", ".join(
                      f"{p} {c['pts'][p]:.1f}" for p in PONTOK if c["pts"][p] is not None), "",
                  "| Kód | Referencia | Jelölt | Mindkettő | Egyezés |", "|---|---|---|---|---|"]
        for code, (a, b, both) in sorted(c["codes"].items()):
            union = a + b - both
            lines.append(f"| {code} | {a} | {b} | {both} | {100 * both / union:.0f}% |")
        if c["diffs"]:
            lines += ["", "Eltérő kizáró kódok (referencia | jelölt):"]
            lines += [f"- {k}: {', '.join(a) or 'nincs'} | {', '.join(b) or 'nincs'}" for k, a, b in c["diffs"]]
        lines.append("")
    text = "\n".join(lines) + "\n"
    (base / "osszevetes.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
