"""LLM-bíró: amit csak ítélettel lehet eldönteni (pontozas.md), és a végső pont.

A bíró ugyanazt a nyersanyagot és ugyanazokat a képeket kapja, mint a
jelentésíró: a futtatóéval azonos sandboxból, eszköz nélkül, és JSON-ban ítél.
A promptja: eval/biro_prompt.md.

    python eval/judge.py eval/runs/2026-11-13_1400            # egy futás minden jelentése
    python eval/judge.py eval/runs/2026-11-13_1400 --cases p04 --repeat 3   # a bíró zaja
    python eval/judge.py jelentes.md --case p07               # egy jelentés

Nem Claude bíró (OpenAI-kompatibilis végpont: OpenRouter, Qwen, GLM, Gemini):

    secr 'python eval/judge.py eval/runs/2026-11-14_0814 --model api:qwen/qwen3-vl-235b-a22b-instruct'

Az `api:` előtag után a végpont modellazonosítója áll. A végpont és a kulcs
környezeti változóból jön: JUDGE_API_BASE (alapból az OpenRouter) és
JUDGE_API_KEY (vagy OPENROUTER_API_KEY). A kulcs a Bitwardenben van, ezért
`secr`-rel kell futtatni. A bíró ugyanazt a promptot, nyersanyagot és képeket
kapja, mint a Claude-bíró; a pontozás és az idézet-ellenőrzés közös.

Kimenet: <futás>/biro/<modell>-<prompt-lenyomat>/<eset>/run<k>.biro<r>.json (a
bíró nyers ítélete, az érvénytelen idézetek, a végső pontok), és ugyanott
osszesito.md + osszesito.json. A mappa neve a bíró modelljéből és a prompt
lenyomatából áll: ha a prompt vagy a modell változik, az újrabírálás új
mappába kerül, a régi ítéletek megmaradnak, és a kettő összevethető. Az
összesítő mindig a mappa összes ítéletéből készül, nem csak az utolsó hívásból.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
import yaml

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
sys.path.insert(0, str(EVAL))
import score  # noqa: E402

# A Python-rendszerben (ahol a src/ is megvan) a bíró ugyanazt a sandboxot és
# digestet kapja, mint a jelentésíró. Máshol (pl. az n8n-es repóban) beépített
# kiírással dolgozik: ugyanazok a mezők, ugyanabban a formában.
try:
    import run as runner  # noqa: E402
    if not (ROOT / "src" / "digest.py").is_file():
        runner = None
except ImportError:
    runner = None

BIRO_PROMPT = EVAL / "biro_prompt.md"
TIMEOUT_SEC = 1200
KIZARO_KODOK = {"K1a", "K1b", "K1c", "K2", "K4", "K5"}
# Ha a bíró ugyanarra a mondatra több kódot ad, az egy hiba, és a
# számlálásban ez a sorrend dönti el, melyik kód alá kerül (pontozas.md, 1. rész).
KOD_SORREND = ("K4", "K5", "K1c", "K1b", "K1a", "K2")
PONTOK = "ABCDE"
DONTESEK = ("lefedve", "szintek_jo", "idotav_jo", "meggyozodes_jo")
E_DONTESEK = ("nincs_felfujas", "szakszavak_erthetok")

# A bíró eszköz nélkül fut: a képeket az üzenetben kapja (stream-json bemenet),
# mert a Read eszközt egy próbán meg sem hívta, a kép nélkül pedig a K1-et
# (kitalált szám) nem lehet megítélni.
JUDGE_FLAGS = [
    "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
    "--tools", "",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--setting-sources", "",
    "--no-session-persistence",
]
API_PREFIX = "api:"
DEFAULT_JUDGE = "api:nvidia/nemotron-3.5-super-vl:free"
API_BASE_DEFAULT = "https://openrouter.ai/api/v1"
API_RETRIES = 6


def user_env(name: str) -> str:
    """Windows felhasználói környezeti változó a registryből (újraindítás nélkül is
    látszik, ha a futó folyamat előtte indult). Más rendszeren üres."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0]).strip()
    except (ImportError, OSError):
        return ""


def ask_api(model: str, content: list[dict]) -> tuple[str, dict]:
    """Egy OpenAI-kompatibilis /chat/completions hívás, képekkel. (szöveg, meta)."""
    base = os.environ.get("JUDGE_API_BASE", API_BASE_DEFAULT).rstrip("/")
    key = (os.environ.get("JUDGE_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
           or user_env("JUDGE_API_KEY") or user_env("OPENROUTER_API_KEY") or "")
    if not key:
        raise RuntimeError("nincs JUDGE_API_KEY / OPENROUTER_API_KEY "
                           "(Bitwarden + secr, vagy Windows felhasználói környezeti változó)")
    parts = []
    for block in content:
        if block["type"] == "text":
            parts.append({"type": "text", "text": block["text"]})
        else:
            src = block["source"]
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
    # A gondolkodó modellek (pl. Nemotron) a gondolkodást is a kimeneti keretből
    # fizetik: 8000 tokennél a válasz üresen maradt. Ezért bő keret, és a
    # gondolkodás közepesre korlátozva (az OpenRouter egységes kapcsolója; a nem
    # gondolkodó modellek figyelmen kívül hagyják).
    body = {"model": model, "messages": [{"role": "user", "content": parts}],
            "temperature": 0, "max_tokens": 32000, "reasoning": {"effort": "medium"},
            # Szigorú JSON-mód: a gyengébb modellek enélkül gyakran escape-eletlen
            # idézőjelet hagynak az idézetben. Ha a végpont nem ismeri, nélküle megy.
            "response_format": {"type": "json_object"}}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               # OpenRouter: a projekt neve a statisztikájukban (személyes adat nélkül).
               "X-Title": "eval-biro"}
    last = ""
    for attempt in range(API_RETRIES):
        resp = requests.post(f"{base}/chat/completions", json=body, headers=headers, timeout=TIMEOUT_SEC)
        if resp.status_code in (429, 500, 502, 503, 504):
            # Az ingyenes szolgáltatók gyakran túlterheltek: hosszabb várakozás.
            last = f"{resp.status_code}: {resp.text[:200]}"
            time.sleep(min(180, 15 * 2 ** attempt))
            continue
        if resp.status_code == 400 and "response_format" in body and "response_format" in resp.text:
            body.pop("response_format")
            continue
        if not resp.ok:
            raise RuntimeError(f"a végpont {resp.status_code}-t adott: {resp.text[:300]}")
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        text = ((choice.get("message") or {}).get("content")) or ""
        if isinstance(text, list):  # néhány végpont darabokban adja vissza
            text = "".join(x.get("text", "") for x in text if isinstance(x, dict))
        usage = data.get("usage") or {}
        if not text.strip():
            reasoning = len(str((choice.get("message") or {}).get("reasoning") or ""))
            raise RuntimeError(f"üres válasz (finish_reason: {choice.get('finish_reason')}, "
                               f"kimeneti token: {usage.get('completion_tokens')}, gondolkodás: {reasoning} karakter)")
        return text, {"modell_azonosito": data.get("model") or model,
                      "szolgaltato": data.get("provider"),
                      "lepesek": 1, "koltseg_usd": usage.get("cost"),
                      "kimeneti_token": usage.get("completion_tokens"),
                      "gondolkodott": bool((choice.get("message") or {}).get("reasoning"))}
    raise RuntimeError(f"a végpont {API_RETRIES} próbálkozás után sem válaszolt: {last}")


MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".webp": "image/webp", ".gif": "image/gif"}


def file_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def prompt_fingerprint() -> str:
    return file_fingerprint(BIRO_PROMPT)


def judge_id(model: str) -> str:
    """A bírálat mappaneve: a bíró modellje és a prompt lenyomata."""
    return re.sub(r"[^A-Za-z0-9._]+", "-", model).strip("-") + "-" + prompt_fingerprint()


def norm(text: str) -> str:
    """Az idézet-ellenőrzéshez: szóközök, idézőjelek és jelölések egységesítése."""
    t = text.replace(" ", " ").replace(" ", " ").replace("**", "").replace("*", "")
    t = re.sub(r"[„”“\"'’‘]", "", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def quote_ok(quote: str, report: str, min_words: int = 3) -> bool:
    q = norm(quote)
    return len(q.split()) >= min_words and q in norm(report)


def claude_binary() -> str:
    if runner is not None:
        return runner.claude_binary()
    found = shutil.which("claude")
    if not found:
        raise RuntimeError("a claude parancs nem található (Claude-bíróhoz kell; api: bíróhoz nem)")
    if found.lower().endswith((".cmd", ".bat")):
        exe = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return str(exe)
    return found


def render_item(data: dict, index: int, total: int) -> str:
    """Egy elem a jelentésíró nyersanyagának formájában (a src/digest.py mintájára)."""
    meta = data.get("meta") or {}
    source = data.get("source", "?")
    when = str(data.get("published", ""))[:16].replace("T", " ")
    lines = [f"### {index}/{total} · {data.get('target_id', '?')} · {data.get('target_name', '?')} · {source} · {when}",
             f"cím: {data.get('title', '')}"]
    if data.get("url"):
        lines.append(f"url: {data['url']}")
    if source == "youtube":
        if meta.get("channel_name"):
            lines.append(f"csatorna: {meta['channel_name']}")
        if meta.get("has_transcript"):
            lines.append(f"felirat: {meta.get('transcript_source', '?')} ({meta.get('transcript_language', '?')})")
        else:
            lines.append("felirat: NINCS — csak a cím és a leírás alapján írhatsz róla")
        if meta.get("description"):
            lines.append("leírás: " + " ".join(str(meta["description"]).split())[:400])
    elif source == "x":
        stat = [f"{meta[k]} {label}" for k, label in
                (("likes", "like"), ("retweets", "retweet"), ("replies", "válasz"), ("views", "megtekintés"))
                if meta.get(k)]
        if stat:
            lines.append("statisztika: " + ", ".join(stat))
        if meta.get("is_reply"):
            lines.append("ez egy válaszposzt")
    elif source == "email" and meta.get("from"):
        lines.append(f"feladó: {' '.join(str(meta['from']).split())}")
    elif source == "web":
        if meta.get("site"):
            lines.append(f"oldal: {meta['site']}")
        if meta.get("excerpt"):
            lines.append(f"felvezető: {' '.join(str(meta['excerpt']).split())}")
        if meta.get("extracted_by") == "index":
            lines.append("kinyerés: SIKERTELEN — a cikk szövege nincs meg; csak a címet "
                         "ismertesd, a felvezető tartalmát se, és a cikkről ne állíts semmit")
        elif meta.get("extracted_by"):
            lines.append(f"kinyerés: {meta['extracted_by']}")
    for image in meta.get("images") or []:
        lines.append(f"kép: {image}")
    for frame in meta.get("frames") or []:
        line = f"képkocka [{frame.get('at', '')}]: {frame.get('path', '')}"
        context = " ".join(str(frame.get("context") or "").split())
        if context:
            line += f"  — ekkor hangzik el: {context}"
        lines.append(line)
    lines += ["szöveg:", (data.get("text") or "").strip() or "(nincs szöveg)"]
    # A src.digest kimenete szöveges módban jön át: a \r is sortörés lesz.
    return "\n".join(lines).replace("\r\n", "\n").replace("\r", "\n")


def builtin_sandbox(case_dir: Path, work: Path) -> tuple[Path, str, list[dict]]:
    """Sandbox a src/ nélkül: az elemek és a képeik az eredeti relatív útvonalukon."""
    items = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((case_dir / "input").glob("*.json"))]
    items.sort(key=lambda d: str(d.get("published", "")))
    inbox = work / "data" / "inbox"
    inbox.mkdir(parents=True)
    for n, data in enumerate(items):
        (inbox / f"{n:03d}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        meta = data.get("meta") or {}
        for rel in list(meta.get("images") or []) + [f.get("path", "") for f in meta.get("frames") or []]:
            if rel and (ROOT / rel).is_file():
                (work / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / rel, work / rel)
    digest = f"{len(items)} elem\n\n" + "\n\n".join(render_item(d, i, len(items)) for i, d in enumerate(items, 1))
    return work, digest, items


def build_judge_sandbox(case_dir: Path) -> tuple[Path, str, list[dict]]:
    """Ugyanaz a sandbox, mint a futtatóé; visszaadja a digestet is."""
    work = Path(tempfile.mkdtemp(prefix=f"biro_{case_dir.name[:3]}_"))
    if runner is None:
        return builtin_sandbox(case_dir, work)
    sandbox, items = runner.build_sandbox(case_dir, work)
    ids = ",".join(dict.fromkeys(it.get("target_id") for it in items))
    proc = subprocess.run([sys.executable, "-m", "src.digest", "--targets", ids], cwd=sandbox,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return sandbox, proc.stdout, items


def ask_judge(case_dir: Path, report_text: str, model: str) -> tuple[dict, dict, Path]:
    sandbox, digest, items = build_judge_sandbox(case_dir)
    ref_text = (case_dir / "reference.yaml").read_text(encoding="utf-8")
    prompt = (BIRO_PROMPT.read_text(encoding="utf-8")
              + "\n\n<<NYERSANYAG>>\n" + digest
              + "\n\n<<REFERENCIA>>\n" + ref_text
              + "\n\n<<JELENTES>>\n" + report_text)

    # A nyersanyag képei: ugyanazok, amiket a jelentésíró a digestből kapott.
    content: list[dict] = [{"type": "text", "text": prompt}]
    images = []
    for it in items:
        meta_ = it.get("meta") or {}
        images += list(meta_.get("images") or []) + [f.get("path", "") for f in meta_.get("frames") or []]
    for rel in dict.fromkeys(p for p in images if p):
        path = sandbox / rel
        media_type = MEDIA_TYPES.get(path.suffix.lower())
        if not path.is_file() or not media_type:
            continue
        content.append({"type": "text", "text": f"Kép: {rel}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                    "data": base64.b64encode(path.read_bytes()).decode("ascii")}})
    if model.startswith(API_PREFIX):
        started = time.monotonic()
        text, meta = ask_api(model[len(API_PREFIX):], content)
        meta.update({"masodperc": round(time.monotonic() - started), "kepek": len(content) // 2})
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < 0:
            raise RuntimeError(f"a bíró nem adott JSON-ítéletet: {text[:300]}")
        try:
            return json.loads(text[start:end + 1]), meta, sandbox
        except json.JSONDecodeError as exc:
            at = start + exc.pos
            raise RuntimeError(f"hibás JSON ({exc.msg}) itt: …{text[max(0, at - 80):at + 40]!r}…") from None

    line = json.dumps({"type": "user", "message": {"role": "user", "content": content}})

    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    cmd = [claude_binary(), "-p", "--model", model, *JUDGE_FLAGS]
    started = time.monotonic()
    proc = subprocess.run(cmd, input=line + "\n", capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=sandbox, env=env, timeout=TIMEOUT_SEC)
    meta: dict = {"masodperc": round(time.monotonic() - started), "kepek": len(content) // 2}
    outer = None
    thought = False
    for raw in proc.stdout.splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            # Az alias ("opus") mögötti tényleges modell: ez változhat két mérés között.
            meta["modell_azonosito"] = event.get("model")
        elif event.get("type") == "assistant":
            blocks = (event.get("message") or {}).get("content") or []
            thought = thought or any(b.get("type") == "thinking" for b in blocks if isinstance(b, dict))
        elif event.get("type") == "result":
            outer = event
    if outer is None:
        raise RuntimeError(f"a bíró nem adott eredményt (exit {proc.returncode}): {proc.stderr[-300:]}")
    meta.update({"lepesek": outer.get("num_turns"), "koltseg_usd": outer.get("total_cost_usd"),
                 "gondolkodott": thought, "kimeneti_token": (outer.get("usage") or {}).get("output_tokens")})
    text = outer.get("result") or ""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise RuntimeError(f"a bíró nem adott JSON-ítéletet: {text[:300]}")
    return json.loads(text[start:end + 1]), meta, sandbox


# --------------------------------------------------------------------------
# A végső pont (pontozas.md, 1-3. rész)
# --------------------------------------------------------------------------


def label(k: dict) -> str:
    """A kód a számláláshoz: a szabályváltozásból eredő K4 külön (pl. K4(D2))."""
    return f"{k['kod']}({k['szabaly']})" if k.get("szabaly") and k["kod"] == "K4" else k["kod"]


def group_by_quote(entries: list[dict], report_text: str) -> list[list[dict]]:
    """Az egymást fedő idézetű tételek egy hibának számítanak.

    A bíró ugyanarra a mondatra gyakran több kódot is ad (K4 és K5, K4 és
    K1b): ez egy hiba, nem kettő. Az idézet helye a jelentésben dönt.
    """
    rep = norm(report_text)
    spans = []
    for k in entries:
        q = norm(k.get("idezet") or "")
        at = rep.find(q)
        spans.append((at, at + len(q), k))
    spans.sort(key=lambda s: (s[0], s[1]))
    groups: list[list[dict]] = []
    end = -1
    for a, b, k in spans:
        if groups and a < end:
            groups[-1].append(k)
            end = max(end, b)
        else:
            groups.append([k])
            end = b
    return groups


def as_failure(group: list[dict]) -> dict:
    """Egy hiba a csoportból. Ha van benne szabályváltozásból eredő tétel (D2, D6),
    a hiba az új szabályhoz tartozik: a többi kód ugyanarra a mondatra ráadás."""
    tagged = [k for k in group if k.get("szabaly")]
    main = min(tagged or group, key=lambda k: KOD_SORREND.index(k["kod"]))
    return {
        "kod": label(main),
        "szabaly": main.get("szabaly"),
        "kodok": sorted({label(k) for k in group}),
        "idezet": max((k.get("idezet") or "" for k in group), key=len),
        "tetelek": group,
    }


def e_verdict(e: dict, report_text: str) -> dict:
    """Az E pont két döntése a bíró felsorolásából: ami nincs idézve, nem számít."""
    if "felfujas" not in e and "nyers_szakszavak" not in e:
        # Régi alak (2026-11-13 előtti ítéletek): két igen/nem mező.
        return {"nincs_felfujas": bool(e.get("nincs_felfujas")),
                "szakszavak_erthetok": bool(e.get("szakszavak_erthetok")),
                "felfujas": [], "nyers_szakszavak": [], "ervenytelen": 0}
    rep = norm(report_text)
    felf_all = [x for x in e.get("felfujas") or [] if isinstance(x, dict)]
    szak_all = [x for x in e.get("nyers_szakszavak") or [] if isinstance(x, dict)]
    felf = [x for x in felf_all if quote_ok(x.get("idezet") or "", report_text, min_words=1)]
    szak = [x for x in szak_all if norm(x.get("szo") or "") and norm(x.get("szo") or "") in rep]
    return {"nincs_felfujas": not felf, "szakszavak_erthetok": not szak,
            "felfujas": felf, "nyers_szakszavak": szak,
            "ervenytelen": len(felf_all) - len(felf) + len(szak_all) - len(szak)}


def final_score(ref: dict, code: score.Eredmeny, verdict: dict, report_text: str) -> dict:
    tilos = ref.get("tilos") or []

    valid, invalid = [], []
    for k in verdict.get("kizaro") or []:
        if k.get("kod") not in KIZARO_KODOK or not quote_ok(k.get("idezet") or "", report_text):
            invalid.append(k)
            continue
        if k["kod"] == "K4" and isinstance(k.get("tilos_index"), int) and 0 <= k["tilos_index"] < len(tilos):
            item = tilos[k["tilos_index"]]
            if isinstance(item, dict) and item.get("szabaly"):
                k["szabaly"] = item["szabaly"]
        valid.append(k)

    judge_fail = [as_failure(g) for g in group_by_quote(valid, report_text)]
    code_fail = [{"kod": c.kod, "szabaly": c.szabaly, "kodok": [c.kod], "reszlet": c.reszlet}
                 for c in code.kizaro_hibak]
    all_fail = judge_fail + code_fail
    atment = not all_fail
    csak_uj = bool(all_fail) and all(f.get("szabaly") for f in all_fail)

    claims = ref.get("allitasok") or []
    by_id = {a.get("id"): a for a in verdict.get("allitasok") or []}
    code_levels = {s["allitas"]: s for s in code.szintek}

    a_num = a_den = 0.0
    b_parts: list[float] = []
    c_hits = c_den = 0
    for claim in claims:
        v = by_id.get(claim["id"], {})
        w = 2 if claim.get("kritikus") else 1
        a_den += w
        if v.get("lefedve"):
            a_num += w
            if v.get("meggyozodes_jo") is not None:
                c_den += 1
                c_hits += bool(v.get("meggyozodes_jo"))
        n_levels = len(claim.get("szintek") or [])
        has_time = bool(claim.get("idotav"))
        if n_levels or has_time:
            # A szint akkor számít, ha a kód is megtalálta, és a bíró szerint jó szerepben van.
            found = min(int(v.get("szintek_jo") or 0), code_levels.get(claim["id"], {}).get("talalt", 0))
            ok = found + (1 if has_time and v.get("idotav_jo") else 0)
            b_parts.append(ok / (n_levels + (1 if has_time else 0)))

    e = e_verdict(verdict.get("e") or {}, report_text)
    pont = {
        "A": round(40 * a_num / a_den, 1) if a_den else None,
        "B": round(25 * sum(b_parts) / len(b_parts), 1) if b_parts else None,
        "C": round(10 * c_hits / c_den, 1) if c_den else None,
        "D": round(code.d_pont, 1) if code.d_pont is not None else None,
        "E": round(5 * e["nincs_felfujas"] + 5 * e["szakszavak_erthetok"], 1),
    }
    negativ = not claims
    if negativ:
        pont["A"] = pont["B"] = pont["C"] = None
    return {
        "atment": atment,
        "csak_uj_szabaly": csak_uj,
        "uj_szabalyok": sorted({f["szabaly"] for f in all_fail}) if csak_uj else [],
        "negativ_eset": negativ,
        "kizaro_hibak": all_fail,
        "kizaro_kodok": sorted({f["kod"] for f in all_fail}),
        "ervenytelen_itelet": invalid,
        "e": e,
        "pont": pont,
        "tartalom": round(sum(pont[k] or 0 for k in "ABC"), 1) if not negativ else None,
        "forma": round((pont["D"] or 0) + pont["E"], 1),
    }


# --------------------------------------------------------------------------
# Futtatás
# --------------------------------------------------------------------------


def judge_one(report: Path, case: str, model: str, r: int, keep: bool, out_dir: Path,
              system: str = "python") -> dict:
    case_dir = score.find_case(case)
    ref_path = case_dir / "reference.yaml"
    ref = yaml.safe_load(ref_path.read_text(encoding="utf-8"))
    text = report.read_text(encoding="utf-8")
    record: dict = {"jelentes": str(report), "eset": case_dir.name, "ismetles": r, "modell": model,
                    "rendszer": system,
                    "prompt_lenyomat": prompt_fingerprint(),
                    "referencia_lenyomat": file_fingerprint(ref_path)}
    sandbox = None
    try:
        verdict, meta, sandbox = ask_judge(case_dir, text, model)
        record.update(meta)
        inbox_text = "\n".join(p.read_text(encoding="utf-8")
                               for p in sorted((sandbox / "data" / "inbox").glob("*.json")))
        code = score.score(report, case_dir.name, system, base=sandbox, source=inbox_text)
        record["itelet"] = verdict
        record["eredmeny"] = final_score(ref, code, verdict, text)
    except Exception as exc:  # egy eset hibája ne állítsa meg a többit
        record["hiba"] = str(exc)[:500]
    finally:
        # Csak a saját, "biro_" előtagú ideiglenes mappát töröljük.
        if sandbox and not keep and sandbox.name.startswith("biro_"):
            shutil.rmtree(sandbox, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{report.stem}.biro{r}.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def status_of(e: dict) -> str:
    if e["atment"]:
        return "átment"
    if e["csak_uj_szabaly"]:
        return f"megbukott (csak új szabály: {', '.join(e.get('uj_szabalyok') or [])})"
    return "megbukott"


# --------------------------------------------------------------------------
# Összesítő (pontozas.md, 3-4. rész)
# --------------------------------------------------------------------------


def mean(values) -> float | None:
    xs = [x for x in values if x is not None]
    return sum(xs) / len(xs) if xs else None


def fmt(x: float | None) -> str:
    return "–" if x is None else f"{x:.1f}"


def aggregate(records: list[dict]) -> dict:
    """A számok az összesítőhöz. A fő táblák jelentésenként az ELSŐ érvényes
    bírálatot használják; az ismétlések csak a bíró zajának mérésére valók."""
    ok = sorted((r for r in records if "eredmeny" in r),
                key=lambda r: (r["eset"], r["jelentes"], r["ismetles"]))
    by_report: dict[str, list[dict]] = {}
    for r in ok:
        by_report.setdefault(r["jelentes"], []).append(r)
    first = [rs[0] for rs in by_report.values()]

    cases = {}
    for case in sorted({r["eset"] for r in first}):
        rs = [r for r in first if r["eset"] == case]
        es = [r["eredmeny"] for r in rs]
        cases[case] = {
            "jelentes": len(rs),
            "atment": sum(e["atment"] for e in es),
            "csak_uj_szabaly": sum(e["csak_uj_szabaly"] for e in es),
            "valodi_bukas": sum(not e["atment"] and not e["csak_uj_szabaly"] for e in es),
            "negativ_eset": es[0]["negativ_eset"],
            "pont": {k: mean(e["pont"][k] for e in es) for k in PONTOK},
            "pont_min": {k: min((e["pont"][k] for e in es if e["pont"][k] is not None), default=None) for k in PONTOK},
            "pont_max": {k: max((e["pont"][k] for e in es if e["pont"][k] is not None), default=None) for k in PONTOK},
            "referencia_lenyomatok": sorted({r.get("referencia_lenyomat") or "?" for r in rs}),
        }

    codes: dict[str, dict[str, int]] = {}
    for r in first:
        for kod in r["eredmeny"].get("kizaro_kodok") or []:
            codes.setdefault(kod, {})
            codes[kod][r["eset"]] = codes[kod].get(r["eset"], 0) + 1

    # A bíró zaja: ugyanaz a jelentés többször bírálva.
    repeated = {rep: rs for rep, rs in by_report.items() if len(rs) > 1}
    noise: dict = {"jelentes": len(repeated), "biralat": sum(len(rs) for rs in repeated.values())}
    if repeated:
        flips = {k: [0, 0] for k in DONTESEK + E_DONTESEK}
        spread = {k: [] for k in PONTOK}
        same_pass = same_status = same_codes = 0
        differing = []
        for rep, rs in repeated.items():
            es = [r["eredmeny"] for r in rs]
            same_pass += len({e["atment"] for e in es}) == 1
            same_status += len({status_of(e) for e in es}) == 1
            code_sets = [tuple(e.get("kizaro_kodok") or []) for e in es]
            if len(set(code_sets)) == 1:
                same_codes += 1
            else:
                differing.append({"jelentes": rep, "kodok": [list(c) for c in code_sets]})
            ids = [a.get("id") for a in rs[0]["itelet"].get("allitasok") or []]
            for claim_id in ids:
                rows = [{a.get("id"): a for a in r["itelet"].get("allitasok") or []}.get(claim_id, {}) for r in rs]
                for k in DONTESEK:
                    flips[k][1] += 1
                    flips[k][0] += len({json.dumps(row.get(k)) for row in rows}) > 1
            for k in E_DONTESEK:
                flips[k][1] += 1
                flips[k][0] += len({e["e"][k] for e in es}) > 1
            for k in PONTOK:
                vals = [e["pont"][k] for e in es if e["pont"][k] is not None]
                if len(vals) > 1:
                    spread[k].append(max(vals) - min(vals))
        noise.update({
            "azonos_atment": same_pass, "azonos_statusz": same_status, "azonos_kodok": same_codes,
            "elteres": differing,
            "billenes": {k: {"elter": v[0], "osszes": v[1]} for k, v in flips.items()},
            "pont_terjedelem": {k: {"atlag": mean(v), "max": max(v, default=None)} for k, v in spread.items()},
        })

    pos = [c for c in cases.values() if not c["negativ_eset"]]
    total = {
        "jelentes": sum(c["jelentes"] for c in cases.values()),
        "atment": sum(c["atment"] for c in cases.values()),
        "csak_uj_szabaly": sum(c["csak_uj_szabaly"] for c in cases.values()),
        "valodi_bukas": sum(c["valodi_bukas"] for c in cases.values()),
        # Az A-C csak a pozitív esetek átlaga (pontozas.md, 3. rész); a D és az E mindené.
        "pont": {k: mean(c["pont"][k] for c in (pos if k in "ABC" else cases.values())) for k in PONTOK},
    }
    first_any = records[0] if records else {}
    return {
        "biro": first_any.get("modell"),
        "prompt_lenyomat": first_any.get("prompt_lenyomat"),
        "modell_azonositok": sorted({r.get("modell_azonosito") for r in ok if r.get("modell_azonosito")}),
        "biralat": len(records),
        "hibas_biralat": len(records) - len(ok),
        "ervenytelen_itelet": sum(len(r["eredmeny"]["ervenytelen_itelet"]) for r in ok),
        "gondolkodas_nelkul": sum(r.get("gondolkodott") is False for r in ok),
        "koltseg_usd": round(sum(r.get("koltseg_usd") or 0 for r in records), 2),
        "esetek": cases, "osszesen": total, "kizaro_kodok": codes, "biro_zaja": noise,
    }


def summarize(records: list[dict], out_dir: Path) -> str:
    agg = aggregate(records)
    ids = ", ".join(agg["modell_azonositok"]) or "?"
    lines = ["# A bíró összesítője", "",
             f"Bíró: {agg['biro']} ({ids}), prompt-lenyomat `{agg['prompt_lenyomat']}`. "
             f"Bírálatok: {agg['biralat']}, ebből hibás: {agg['hibas_biralat']}; "
             f"érvénytelen (idézet nélküli) kizáró ítélet: {agg['ervenytelen_itelet']}; "
             f"gondolkodás nélkül ítélt: {agg['gondolkodas_nelkul']}. "
             f"Költség API-áron: ${agg['koltseg_usd']:.2f}.", ""]
    if len(agg["modell_azonositok"]) > 1:
        lines += ["**Figyelem: a mappában több modell ítéletei keverednek.**", ""]
    changed = [c for c, v in agg["esetek"].items() if len(v["referencia_lenyomatok"]) > 1]
    if changed:
        lines += [f"**Figyelem: a referencia a bírálatok között változott: {', '.join(changed)}.**", ""]

    lines += ["## Eredmény", "",
              "Jelentésenként az első bírálat. A pontok a futások átlagai; negatív esetnél nincs A-C.", "",
              "| Eset | Jelentés | Átment | Bukott: csak új szabály | Bukott: valódi | A | B | C | D | E |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for case, c in agg["esetek"].items():
        lines.append(f"| {case} | {c['jelentes']} | {c['atment']} | {c['csak_uj_szabaly']} | {c['valodi_bukas']} | "
                     + " | ".join(fmt(c["pont"][k]) for k in PONTOK) + " |")
    t = agg["osszesen"]
    lines += [f"| **Összesen, az esetek átlaga** | {t['jelentes']} | {t['atment']} | {t['csak_uj_szabaly']} | "
              f"{t['valodi_bukas']} | " + " | ".join(fmt(t["pont"][k]) for k in PONTOK) + " |", ""]

    lines += ["## Kizáró hibák", ""]
    if agg["kizaro_kodok"]:
        lines += ["Hány jelentésben fordult elő (egy mondat egy hiba, akkor is, ha a bíró több kódot adott rá).", "",
                  "| Kód | Jelentés | Esetenként |", "|---|---|---|"]
        for kod, per_case in sorted(agg["kizaro_kodok"].items()):
            detail = ", ".join(f"{c[:3]} {n}/{agg['esetek'][c]['jelentes']}" for c, n in sorted(per_case.items()))
            lines.append(f"| {kod} | {sum(per_case.values())} | {detail} |")
        lines.append("")
    else:
        lines += ["Nem volt kizáró hiba.", ""]

    multi = {c: v for c, v in agg["esetek"].items() if v["jelentes"] > 1}
    if multi:
        lines += ["## A jelentésíró zaja", "",
                  "A pontok legkisebb és legnagyobb értéke az eset futásai között.", "",
                  "| Eset | Átment | A | B | C | D | E |", "|---|---|---|---|---|---|---|"]
        for case, c in multi.items():
            cells = [f"{fmt(c['pont_min'][k])} – {fmt(c['pont_max'][k])}" if c["pont_min"][k] is not None else "–"
                     for k in PONTOK]
            lines.append(f"| {case} | {c['atment']}/{c['jelentes']} | " + " | ".join(cells) + " |")
        lines.append("")

    n = agg["biro_zaja"]
    lines += ["## A bíró zaja", ""]
    if n["jelentes"]:
        lines += [f"{n['jelentes']} jelentés kapott több bírálatot (összesen {n['biralat']}).", "",
                  f"- átment/megbukott minden ismétlésben azonos: {n['azonos_atment']}/{n['jelentes']}",
                  f"- a státusz (átment, csak új szabály, valódi bukás) azonos: {n['azonos_statusz']}/{n['jelentes']}",
                  f"- a kizáró kódok halmaza azonos: {n['azonos_kodok']}/{n['jelentes']}",
                  "- döntésenként hányszor tért el az ismétlések között: "
                  + ", ".join(f"{k} {v['elter']}/{v['osszes']}" for k, v in n["billenes"].items()),
                  "- a pontok terjedelme egy jelentésen belül (átlag, legnagyobb): "
                  + ", ".join(f"{k} {fmt(v['atlag'])} / {fmt(v['max'])}" for k, v in n["pont_terjedelem"].items()), ""]
        for d in n["elteres"]:
            lines.append(f"- eltérő kódok: {Path(d['jelentes']).parent.name}/{Path(d['jelentes']).name}: "
                         + " | ".join(", ".join(k) or "nincs" for k in d["kodok"]))
        if n["elteres"]:
            lines.append("")
    else:
        lines += ["Nincs ismételt bírálat. Méréséhez: `--repeat 3` egy részhalmazon.", ""]

    lines += ["## Részletek", ""]
    for case in sorted({r["eset"] for r in records}):
        lines.append(f"### {case}")
        for r in sorted((x for x in records if x["eset"] == case), key=lambda x: (x["jelentes"], x["ismetles"])):
            name = Path(r["jelentes"]).stem + (f" (bíró #{r['ismetles']})" if r["ismetles"] > 1 else "")
            if "hiba" in r:
                lines.append(f"- {name}: HIBA: {r['hiba']}")
                continue
            e = r["eredmeny"]
            p = e["pont"]
            pts = " ".join(f"{k} {p[k]}" for k in PONTOK if p[k] is not None)
            inv = f" [érvénytelen ítélet: {len(e['ervenytelen_itelet'])}]" if e["ervenytelen_itelet"] else ""
            lines.append(f"- {name}: {status_of(e)}; {pts}{inv}")
            for h in e["kizaro_hibak"]:
                extra = f" [a bíró kódjai: {'+'.join(h['kodok'])}]" if len(h["kodok"]) > 1 else ""
                what = f"„{h['idezet'][:110]}”" if h.get("idezet") else "; ".join(h.get("reszlet") or [])[:110]
                lines.append(f"  - {h['kod']}{extra}: {what}")
            for x in e["e"]["felfujas"]:
                lines.append(f"  - E, felfújás: „{(x.get('idezet') or '')[:90]}”")
            if e["e"]["nyers_szakszavak"]:
                lines.append("  - E, nyers szakszó: " + ", ".join(x.get("szo") or "" for x in e["e"]["nyers_szakszavak"]))
        lines.append("")
    text = "\n".join(lines) + "\n"
    (out_dir / "osszesito.md").write_text(text, encoding="utf-8")
    (out_dir / "osszesito.json").write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    return text


def load_records(base: Path) -> list[dict]:
    """A bírálat-mappa összes ítélete, nem csak az utolsó hívásé."""
    out = []
    for path in sorted(base.rglob("*.biro*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", help="egy eval/runs/<időbélyeg> mappa vagy egy jelentés")
    ap.add_argument("--case", help="egy jelentésnél: az eset")
    ap.add_argument("--cases", help="mappánál: csak ezek az esetek (pl. p04,p07)")
    # 2026-11-14, a felhasználó döntése: a bíró nem Claude-modell. A Nemotron a
    # bírótesztet 12/12-re teljesítette (eval/runs/biro_proba). Az Opus
    # referenciának megmaradt: --model opus.
    ap.add_argument("--model", default=DEFAULT_JUDGE,
                    help="a bíró modellje; api:<azonosító> = OpenAI-kompatibilis végpont, opus = Claude")
    ap.add_argument("--repeat", type=int, default=1, help="ugyanazt hányszor bírálja (a bíró zaja)")
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--hianyzo", action="store_true",
                    help="csak ami még nincs meg hibátlanul (pótlás egy megszakadt futás után)")
    ap.add_argument("--system", choices=("python", "n8n"), default=None,
                    help="melyik rendszer jelentései (alapból a futás meta.json-jából, különben python)")
    args = ap.parse_args()

    target = Path(args.target)
    bid = judge_id(args.model)
    system = args.system
    if system is None:
        meta_file = (target if target.is_dir() else target.parent.parent) / "meta.json"
        try:
            system = json.loads(meta_file.read_text(encoding="utf-8")).get("rendszer", "python")
        except (OSError, ValueError):
            system = "python"
    if target.is_file():
        if not args.case:
            ap.error("egy jelentésnél add meg a --case-t")
        base = target.parent / "biro" / bid
        jobs = [(target, args.case, base)]
    else:
        base = target / "biro" / bid
        wanted = args.cases.split(",") if args.cases else None
        jobs = []
        for case_dir in sorted(p for p in target.iterdir() if p.is_dir() and p.name != "biro"):
            if wanted and not any(case_dir.name.startswith(w) for w in wanted):
                continue
            jobs += [(rep, case_dir.name, base / case_dir.name) for rep in sorted(case_dir.glob("run*.md"))]
    jobs = [(rep, case, out, r) for rep, case, out in jobs for r in range(1, args.repeat + 1)]
    if args.hianyzo:
        def done(rep: Path, out: Path, r: int) -> bool:
            f = out / f"{rep.stem}.biro{r}.json"
            try:
                return "eredmeny" in json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
        before = len(jobs)
        jobs = [j for j in jobs if not done(j[0], j[2], j[3])]
        print(f"--hianyzo: {before - len(jobs)} kész bírálat kimarad", flush=True)
    print(f"{len(jobs)} bírálat, bíró: {args.model}, rendszer: {system} → {base}", flush=True)

    with cf.ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = [pool.submit(judge_one, rep, case, args.model, r, args.keep, out, system)
                   for rep, case, out, r in jobs]
        for fut in cf.as_completed(futures):
            rec = fut.result()
            e = rec.get("eredmeny") or {}
            status = rec.get("hiba") or (status_of(e) + (f": {e['kizaro_kodok']}" if e.get("kizaro_kodok") else ""))
            print(f"  {Path(rec['jelentes']).parent.name}/{Path(rec['jelentes']).name} #{rec['ismetles']}: "
                  f"{status}; {rec.get('masodperc')} mp, ${(rec.get('koltseg_usd') or 0):.2f}", flush=True)
    print()
    print(summarize(load_records(base), base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
