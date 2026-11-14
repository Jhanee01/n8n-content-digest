"""Gépi pontozó: amit kód is el tud dönteni (pontozas.md).

Egy jelentés egy esethez mérve:

    python eval/score.py reports/x.md --case p01_chifoi_yt_whisper_60k
    python eval/score.py jelentes.txt --case p07_camel_x_last_chance --system n8n
    python eval/score.py reports/x.md --case p01 --json        # gépi kimenet

Az éles jelentések formai ellenőrzése, referencia nélkül (monitoring):

    python eval/score.py --live                 # a reports/ befektetési jelentései
    python eval/score.py --live --since 2026-10-01

Amit NEM ez dönt el, mert ítélet kell hozzá (LLM-bíró): K1 (kitalált szám,
irány, pozíció), K2 (tanács), K4 `tilos` listája, K5, és az A, C, E pont. A B
pontból csak a számok egyeztetése van itt; hogy a szám a jó szerephez tartozik-e,
azt a bíró dönti el.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
CASES = EVAL / "cases"

# A SKILL kötelező jelöléseinek eleje. A teljes mondat utómenet után
# változhat ("Gépi átiratból — …" helyett "Gépi átiratból, …"), az eleje nem.
JELOLESEK = (
    "Felirat nélkül",
    "Gépi átiratból",
    "A riport szövege nem jött át",
    "Grafikonok nélkül",
)

# D6: a szó szerinti meta-nyelv lista. Ami ezen túl van, az E pont (bíró).
META_NYELV = (
    "az elérhető adatok alapján",
    "a rendelkezésre álló adatok alapján",
    "a képkockákból kiderül",
    "a képkockák alapján",
    "az átirat elemzése szerint",
    "az átiratból kiderül",
    "az átirat alapján",
)

MAX_KEP = 5
MAX_VIDEO_SZO = 200
MAX_MONDAT_BEKEZDES = 4
SZINT_TURES = 0.01  # B pont: ±1%

URL = re.compile(r"https?://[^\s)>\]]+")
KEP = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
TABLAZAT = re.compile(r"^\s*\|.*\|\s*$\n^\s*\|[\s:|-]+\|\s*$", re.M)
# A SKILL sablonja: "- **12:31** — …" (grafikonlista) és "- **dátum** — …" (X).
SABLON_ELVALASZTO = re.compile(r"^(\s*[-•]\s+\*\*[^*]+\*\*)\s+[—–-]\s+")


# --------------------------------------------------------------------------
# Eredmény
# --------------------------------------------------------------------------


@dataclass
class Check:
    kod: str
    nev: str
    ok: bool | None  # None: nem értelmezhető ennél a jelentésnél vagy rendszernél
    reszlet: list[str] = field(default_factory=list)
    szabaly: str | None = None  # "D2"/"D3": új szabályból eredő (külön oszlop)


@dataclass
class Eredmeny:
    jelentes: str
    eset: str | None
    rendszer: str
    kizaro: list[Check] = field(default_factory=list)
    forma: list[Check] = field(default_factory=list)
    szintek: list[dict] = field(default_factory=list)

    @property
    def kizaro_hibak(self) -> list[Check]:
        return [c for c in self.kizaro if c.ok is False]

    @property
    def atment(self) -> bool:
        return not self.kizaro_hibak

    @property
    def csak_uj_szabaly(self) -> bool:
        """Megbukott, de csak a D2/D3 új szabályai miatt (pontozas.md, 1. rész)."""
        hibak = self.kizaro_hibak
        return bool(hibak) and all(c.szabaly for c in hibak)

    @property
    def d_pont(self) -> float | None:
        ertekelt = [c for c in self.forma if c.ok is not None]
        if not ertekelt:
            return None
        return 15 * sum(c.ok for c in ertekelt) / len(ertekelt)


# --------------------------------------------------------------------------
# A jelentés szerkezete
# --------------------------------------------------------------------------


def heading_level(line: str) -> int:
    m = re.match(r"^(#{1,6})\s", line)
    return len(m.group(1)) if m else 0


def item_block(lines: list[str], url: str, system: str) -> tuple[int, int, str] | None:
    """Az elem szakasza a jelentésben (kezdő sor, záró sor, típus), a linkje alapján.

    python: videónál, riportnál, hírlevélnél a `###` szakasz; az X-szekcióban a
    listaelem. n8n: az emojival vagy •-vel kezdődő blokk.
    """
    if not url:
        return None
    hits = [i for i, line in enumerate(lines) if url in line]
    if not hits:
        return None
    at = hits[0]

    if system == "n8n":
        starts = ("🎥", "📄", "📧", "•", "📈", "𝕏", "🧭")
        start = at
        while start > 0 and not lines[start].lstrip().startswith(starts):
            start -= 1
        end = at + 1
        while end < len(lines) and not lines[end].lstrip().startswith(starts):
            end += 1
        kind = "lista" if lines[start].lstrip().startswith("•") else "szakasz"
        return start, end, kind

    # Melyik `##` szekcióban vagyunk?
    sec = at
    while sec > 0 and heading_level(lines[sec]) not in (1, 2):
        sec -= 1
    if "𝕏" in lines[sec]:
        start = at
        while start > sec and not lines[start].startswith("- "):
            start -= 1
        end = at + 1
        while end < len(lines) and not (lines[end].startswith("- ") or heading_level(lines[end])):
            end += 1
        return start, end, "lista"

    start = at
    while start > 0 and heading_level(lines[start]) not in (1, 2, 3):
        start -= 1
    end = at + 1
    while end < len(lines) and heading_level(lines[end]) not in (1, 2, 3) and lines[end].strip() != "---":
        end += 1
    return start, end, "szakasz"


def first_content_line(lines: list[str], start: int, end: int, kind: str) -> str:
    """A szakasz első tartalmi sora: a fejléc és a meta-sor (csatorna · dátum) után."""
    if kind == "lista":
        # "- **dátum** — szöveg": a dátumcímke utáni rész számít.
        return SABLON_ELVALASZTO.sub("", lines[start]).strip()
    for line in lines[start + 1:end]:
        s = line.strip()
        if not s:
            continue
        if s.startswith("*") and s.endswith("*") and " · " in s:  # *csatorna · dátum · …*
            continue
        if " · " in s and len(s) < 160 and not s.startswith("**"):  # n8n meta-sor
            continue
        return s
    return ""


def video_blocks(lines: list[str], system: str) -> list[tuple[int, int]]:
    """A videó-szakaszok (python: a `## 🎥` alatti `###`-ek)."""
    if system != "python":
        return []
    blocks, in_video, start = [], False, None
    for i, line in enumerate(lines + ["# vége"]):
        lvl = heading_level(line)
        if lvl in (1, 2, 3) or line.strip() == "---":
            if start is not None:
                blocks.append((start, i))
                start = None
            if lvl in (1, 2):
                in_video = "🎥" in line
            if lvl == 3 and in_video:
                start = i
    return blocks


# --------------------------------------------------------------------------
# Számok (B pont, tilos_szamok)
# --------------------------------------------------------------------------

_SZAM = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[  ]\d{3})+|\d{1,3}(?:\.\d{3})+(?![.,]\d)|\d+(?:[.,]\d+)?)"
    r"\s?(ezer|e\b|k\b|K\b|millió|millio|M\b|milliárd|mrd)?"
)


def numbers(text: str) -> list[float]:
    """Minden szám a szövegben, egységes alakra hozva (58 ezer = 58k = 58 000)."""
    out = []
    for raw, unit in _SZAM.findall(text):
        r = raw.replace(" ", "").replace(" ", "")
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", r):  # 58.000 = ezres tagolás
            r = r.replace(".", "")
        r = r.replace(",", ".")
        try:
            value = float(r)
        except ValueError:
            continue
        mult = {"ezer": 1e3, "e": 1e3, "k": 1e3, "K": 1e3, "millió": 1e6, "millio": 1e6,
                "M": 1e6, "milliárd": 1e9, "mrd": 1e9}.get(unit or "", 1)
        out.append(value * mult)
    return out


def level_values(level) -> tuple[list[float], tuple[float, float] | None]:
    if isinstance(level, (int, float)):
        return [float(level)], None
    if isinstance(level, list):
        return [float(x) for x in level], None
    a, b = str(level).split("-")
    return [], (float(a), float(b))


def level_found(level, found: list[float]) -> bool:
    values, rng = level_values(level)
    if rng:
        return any(rng[0] <= x <= rng[1] for x in found)
    return any(abs(x - v) <= abs(v) * SZINT_TURES for v in values for x in found)


# --------------------------------------------------------------------------
# Formai ellenőrzések (D pont)
# --------------------------------------------------------------------------


def check_dash(lines: list[str]) -> Check:
    """D1: nincs gondolatjel a folyó szövegben, a SKILL sablonjának helyein kívül."""
    bad = []
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if not s or heading_level(line) or s.startswith("!["):
            continue
        if s.startswith("|"):  # táblázatsor: a "–" ott üres cella, a táblázat a D2
            continue
        if any(s.lstrip("*_ ").startswith(j) for j in JELOLESEK):
            continue
        # A listaelem félkövér címkéje a sablon része ("- **5:33 — BTCUSD:**"),
        # a címke utáni elválasztóval együtt.
        s = SABLON_ELVALASZTO.sub("", s)
        # Több félkövér részből vagy zárójeles kiegészítésből álló címke:
        # "- **21:44** és **21:52** — …", "- **0:16** (a poszt videójából) — …"
        s = re.sub(r"^\s*[-•]\s+[^—–]{0,80}?\*\*[^—–]{0,40}?\s[—–-]\s", "", s)
        s = re.sub(r"^\s*[-•]\s+\*\*[^*]+\*\*:?", "", s)
        # Számtartomány szóközökkel: "2026. szept. 12. - 2027. okt. 1."
        s = re.sub(r"(\d\.?)\s[–-]\s(?=\d)", r"\1-", s)
        s = re.sub(r"eredeti cím:.*$", "", s)  # idézett eredeti cím
        s = re.sub(r"„[^”\"]*[”\"]|\"[^\"]*\"", "", s)  # idézet (a záró jel lehet egyenes is)
        if "—" in s or re.search(r"\s–\s", s) or re.search(r"\S\s-\s\S", s):
            bad.append(f"{n}. sor: {line.strip()[:110]}")
    return Check("D1", "nincs gondolatjel a folyó szövegben", not bad, bad[:10])


def check_table(text: str) -> Check:
    found = TABLAZAT.findall(text)
    return Check("D2", "nincs Markdown-táblázat", not found, [f[:80] for f in found[:3]])


def check_images(lines: list[str], base: Path, source: str | None = None) -> Check:
    images, bad = [], []
    for n, line in enumerate(lines, 1):
        for path in KEP.findall(line):
            images.append(path)
            if not (line.strip().startswith("![") and line.strip().endswith(")")):
                bad.append(f"{n}. sor: nem önálló sorban: {line.strip()[:80]}")
            if not (base / path).exists():
                bad.append(f"{n}. sor: nincs ilyen fájl: {path}")
            # A SKILL szerint az útvonalat a digestből kell másolni, nem kitalálni.
            elif source is not None and path not in source.replace("\\\\", "/").replace("\\", "/"):
                bad.append(f"{n}. sor: az útvonal nem a bemenetből van: {path}")
    if len(images) > MAX_KEP:
        bad.insert(0, f"{len(images)} kép, legfeljebb {MAX_KEP}")
    return Check("D3", f"legfeljebb {MAX_KEP} kép, önálló sorban, a fájl létezik", not bad, bad[:10])


def check_summary(text: str, lines: list[str], system: str) -> Check:
    if system == "n8n":
        ok = "🧭 Összkép" in text
        return Check("D4", "van Összkép szakasz", ok, [] if ok else ["nincs \"🧭 Összkép\" sor"])
    # A pontozas.md csak annyit kér, hogy legyen; több személynél a közös,
    # a jelentés végén álló Összkép is megfelel.
    ok = any(line.strip() == "## Összkép" for line in lines)
    return Check("D4", "van Összkép szakasz", ok, [] if ok else ["nincs ## Összkép"])


def check_video_length(lines: list[str], system: str) -> Check:
    """D5: a videó-összefoglaló (grafikonlista, képek, link nélkül) legfeljebb 200 szó."""
    blocks = video_blocks(lines, system)
    if not blocks:
        return Check("D5", f"videónként legfeljebb {MAX_VIDEO_SZO} szó", None)
    bad = []
    for start, end in blocks:
        words, in_list = 0, False
        for line in lines[start + 1:end]:
            s = line.strip()
            if s.startswith("**Amit a grafikonokon mutat"):
                in_list = True
                continue
            if in_list and (s.startswith("- ") or not s):
                continue
            in_list = False
            if not s or s.startswith("![") or s.startswith("🔗") or (s.startswith("*") and " · " in s):
                continue
            if any(s.startswith(j) for j in JELOLESEK):
                continue
            words += len(re.findall(r"\S+", re.sub(r"\*\*[^*]+:\*\*", "", s)))
        if words > MAX_VIDEO_SZO:
            bad.append(f"{lines[start].strip()[:70]}: {words} szó")
    return Check("D5", f"videónként legfeljebb {MAX_VIDEO_SZO} szó", not bad, bad)


def check_meta(text: str) -> Check:
    low = text.lower()
    found = [m for m in META_NYELV if m in low]
    return Check("D6", "nincs meta-nyelv (szó szerinti lista)", not found, found)


def check_markdown(lines: list[str], system: str) -> Check:
    if system != "n8n":
        return Check("D7", "nincs Markdown-jelölés (csak n8n)", None)
    bad = []
    for n, line in enumerate(lines, 1):
        if "**" in line or heading_level(line) or KEP.search(line) or "`" in line or re.match(r"^\s*\|", line):
            bad.append(f"{n}. sor: {line.strip()[:80]}")
    return Check("D7", "nincs Markdown-jelölés (csak n8n)", not bad, bad[:10])


def check_links(text: str, source: str | None) -> Check:
    if source is None:
        return Check("D8", "minden link szerepel a bemenetben", None)
    bad = [u for u in dict.fromkeys(URL.findall(text)) if u.rstrip(".,;") not in source]
    return Check("D8", "minden link szerepel a bemenetben", not bad, bad[:10])


def check_short_line(lines: list[str], blocks: list[tuple[int, int, str]], ref: dict | None,
                     system: str) -> Check:
    """D9: ahol a szakasznak van állítása, van „Röviden” sor (csak python)."""
    if system != "python" or not ref or not ref.get("allitasok"):
        return Check("D9", "ahol van állítás, van „Röviden” sor", None)
    heads = [b for b in blocks if b[2] == "szakasz"]
    if not heads:
        return Check("D9", "ahol van állítás, van „Röviden” sor", None)
    bad = [lines[s].strip()[:70] for s, e, _ in heads
           if not any(line.strip().startswith("**Röviden:**") for line in lines[s:e])]
    return Check("D9", "ahol van állítás, van „Röviden” sor", not bad, bad)


def sentences(paragraph: str) -> int:
    p = re.sub(r"\*\*[^*]+:\*\*", "", paragraph)
    p = re.sub(r"\b(kb|pl|ill|stb|dr|vö)\.", r"\1", p)
    p = re.sub(r"(\d)\.(\s)", r"\1\2", p)  # "2026. november", "1. hullám"
    return len([s for s in re.split(r"[.!?]+(?:\s+|$)", p) if s.strip()])


def check_paragraphs(lines: list[str]) -> Check:
    """D10: a „Lényeg” bekezdései legfeljebb 3-4 mondatosak."""
    bad, in_lenyeg, para = [], False, []

    def flush():
        if para:
            text = " ".join(para)
            if sentences(text) > MAX_MONDAT_BEKEZDES:
                bad.append(f"{sentences(text)} mondat: {text[:90]}")
            para.clear()

    for line in lines + [""]:
        s = line.strip()
        if s.startswith("**Lényeg:**"):
            flush()
            in_lenyeg = True
        elif s.startswith("**") and s.split(":**")[0] != s or heading_level(line) or s.startswith(("- ", "![", "🔗")):
            flush()
            in_lenyeg = False
            continue
        if not in_lenyeg:
            continue
        if not s:
            flush()
        else:
            para.append(s)
    return Check("D10", f"a „Lényeg” bekezdései legfeljebb {MAX_MONDAT_BEKEZDES} mondatosak",
                 not bad, bad[:10])


def form_checks(text: str, system: str, base: Path, ref: dict | None = None,
                source: str | None = None, blocks: list | None = None) -> list[Check]:
    lines = text.splitlines()
    checks = [
        check_dash(lines),
        check_table(text),
        check_images(lines, base, source) if system == "python" else Check("D3", "képek (csak python)", None),
        check_summary(text, lines, system),
        check_video_length(lines, system),
        check_meta(text),
        check_markdown(lines, system),
        check_links(text, source),
        check_short_line(lines, blocks or [], ref, system),
        check_paragraphs(lines) if system == "python" else Check("D10", "bekezdések (csak python)", None),
    ]
    return checks


# --------------------------------------------------------------------------
# Egy eset
# --------------------------------------------------------------------------


def find_case(name: str) -> Path:
    exact = CASES / name
    if exact.is_dir():
        return exact
    hits = sorted(p for p in CASES.iterdir() if p.is_dir() and p.name.startswith(name + "_"))
    if len(hits) != 1:
        raise SystemExit(f"nincs ilyen eset, vagy nem egyértelmű: {name}")
    return hits[0]


def load_case(case_dir: Path) -> tuple[dict, list[dict], str]:
    ref = yaml.safe_load((case_dir / "reference.yaml").read_text(encoding="utf-8"))
    items = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((case_dir / "input").glob("*.json"))]
    # A bemenet szövege a linkek ellenőrzéséhez: minden, amit a modell kap.
    source = "\n".join(json.dumps(it, ensure_ascii=False) for it in items)
    return ref, items, source


def score(report: Path, case: str | None, system: str, base: Path | None = None,
          source: str | None = None) -> Eredmeny:
    """`base`: ahol a képeket keressük (eval-futásnál a sandbox); `source`: a
    bemenet szövege a linkek és képútvonalak ellenőrzéséhez (alapból az eset
    befagyasztott elemei)."""
    text = report.read_text(encoding="utf-8")
    lines = text.splitlines()
    res = Eredmeny(jelentes=str(report), eset=None, rendszer=system)

    if case is None:
        res.forma = form_checks(text, system, ROOT)
        return res

    case_dir = find_case(case)
    res.eset = case_dir.name
    ref, items, case_source = load_case(case_dir)
    source = source if source is not None else case_source

    blocks = [b for b in (item_block(lines, it.get("url") or "", system) for it in items) if b]

    # K3 és K3e: a kötelező jelölés az elem szakaszában, és ha kell, legelöl.
    jelolesek = ref.get("kotelezo_jeloles") or []
    if system == "n8n":
        # pontozas.md: a „Grafikonok nélkül” jelölést az n8n promptja nem írja
        # elő, ezért ott nem kérjük számon.
        jelolesek = [j for j in jelolesek if not j.startswith("Grafikonok nélkül")]
    k3_bad, k3e_bad = [], []
    for jel in jelolesek:
        scopes = blocks or [(0, len(lines), "egész")]
        for start, end, kind in scopes:
            block = "\n".join(lines[start:end])
            if jel not in block:
                k3_bad.append(f"„{jel}” nincs a szakaszban: {lines[start].strip()[:60]}")
            elif ref.get("jeloles_elol") and kind != "egész":
                first = first_content_line(lines, start, end, kind)
                if not first.lstrip("*_ ").startswith(jel):
                    k3e_bad.append(f"„{jel}” nem legelöl; az első sor: {first[:80]}")
    res.kizaro.append(Check("K3", "kötelező jelölés az elem szakaszában",
                            not k3_bad if jelolesek else None, k3_bad))
    if ref.get("jeloles_elol"):
        res.kizaro.append(Check("K3e", "a jelölés legelöl áll", not k3e_bad and not k3_bad,
                                k3e_bad, szabaly="D3"))

    # K4, tilos_szamok: szó szerint, a szóközök egységesítése után.
    flat = text.replace(" ", " ")
    bad = [str(n) for n in ref.get("tilos_szamok") or [] if str(n) in flat]
    res.kizaro.append(Check("K4sz", "nincs tiltott szám", not bad if ref.get("tilos_szamok") else None, bad))

    res.forma = form_checks(text, system, base or ROOT, ref, source, blocks)

    # B pont, a számok egyeztetése (a bíró még megnézi a szerepüket).
    found = numbers(text)
    for claim in ref.get("allitasok") or []:
        levels = claim.get("szintek") or []
        hits = [lv for lv in levels if level_found(lv, found)]
        res.szintek.append({
            "allitas": claim.get("id"),
            "szintek": len(levels),
            "talalt": len(hits),
            "hianyzik": [lv for lv in levels if lv not in hits],
        })
    return res


# --------------------------------------------------------------------------
# Kimenet
# --------------------------------------------------------------------------


def jel(ok: bool | None) -> str:
    return {True: "✓", False: "✗", None: "–"}[ok]


def print_result(res: Eredmeny) -> None:
    print(f"{res.jelentes}  ({res.eset or 'referencia nélkül'}, {res.rendszer})")
    if res.kizaro:
        status = "ÁTMENT" if res.atment else ("MEGBUKOTT, csak D2/D3 miatt" if res.csak_uj_szabaly else "MEGBUKOTT")
        print(f"  Kizáró hibák (kód): {status}   [a K1, K2, K4 tilos, K5 a bíróé]")
        for c in res.kizaro:
            print(f"    {jel(c.ok)} {c.kod} {c.nev}{f' ({c.szabaly})' if c.szabaly else ''}")
            for r in c.reszlet:
                print(f"        {r}")
    d = res.d_pont
    print(f"  Forma (D): {d:.1f}/15" if d is not None else "  Forma (D): –")
    for c in res.forma:
        print(f"    {jel(c.ok)} {c.kod} {c.nev}")
        for r in c.reszlet:
            print(f"        {r}")
    if res.szintek:
        print("  Szintek (B, számegyezés; a szerepüket a bíró nézi):")
        for s in res.szintek:
            if s["szintek"]:
                miss = f"  hiányzik: {s['hianyzik']}" if s["hianyzik"] else ""
                print(f"    {s['allitas']}: {s['talalt']}/{s['szintek']}{miss}")


def live(since: str | None) -> int:
    """Az éles befektetési jelentések formai ellenőrzése (monitoring, 1. réteg)."""
    files = sorted(Path(p) for p in glob.glob(str(ROOT / "reports" / "????-??-??_????.md")))
    if since:
        files = [f for f in files if f.name[:10] >= since]
    if not files:
        print("nincs jelentés")
        return 0
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    by_month: dict[str, dict[str, int]] = {}
    worst: list[tuple[int, str]] = []
    for f in files:
        res = score(f, None, "python")
        failed = [c for c in res.forma if c.ok is False]
        worst.append((len(failed), f.name))
        month = f.name[:7]
        by_month.setdefault(month, {"jelentes": 0})
        by_month[month]["jelentes"] += 1
        for c in res.forma:
            names[c.kod] = c.nev
            if c.ok is False:
                counts[c.kod] = counts.get(c.kod, 0) + 1
                by_month[month][c.kod] = by_month[month].get(c.kod, 0) + 1
    print(f"{len(files)} éles befektetési jelentés ({files[0].name[:10]} – {files[-1].name[:10]})\n")
    print("Hány jelentésben bukott el az adott ellenőrzés:")
    for kod in sorted(names, key=lambda k: int(k[1:])):
        print(f"  {kod:4} {counts.get(kod, 0):4}  {names[kod]}")
    print("\nHavonta:")
    for month, row in sorted(by_month.items()):
        rest = ", ".join(f"{k} {v}" for k, v in sorted(row.items()) if k != "jelentes")
        print(f"  {month}: {row['jelentes']} jelentés; {rest or 'hiba nélkül'}")
    print("\nA legtöbb hibával:")
    for n, name in sorted(worst, reverse=True)[:5]:
        print(f"  {name}: {n} formai hiba")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("report", nargs="?", help="a jelentés fájlja")
    ap.add_argument("--case", help="az eset azonosítója vagy előtagja (pl. p01)")
    ap.add_argument("--system", choices=("python", "n8n"), default="python")
    ap.add_argument("--json", action="store_true", help="gépi kimenet")
    ap.add_argument("--live", action="store_true", help="a reports/ éles jelentései, referencia nélkül")
    ap.add_argument("--since", help="--live mellett: ettől a naptól (ÉÉÉÉ-HH-NN)")
    args = ap.parse_args()

    if args.live:
        return live(args.since)
    if not args.report:
        ap.error("adj meg egy jelentést, vagy használd a --live kapcsolót")
    res = score(Path(args.report), args.case, args.system)
    if args.json:
        out = asdict(res) | {"atment": res.atment, "csak_uj_szabaly": res.csak_uj_szabaly, "d_pont": res.d_pont}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print_result(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
