"""Futtató az n8n-es workflow-hoz: az esetek jelentése az "Eval: webhook"-on át.

Az n8n-workflow eval módja (lásd a ⚙️ Settings node-ot) nem gyűjt és nem küld
Telegramra: a kérésben kapott elemekből megírja a jelentést, lefuttatja a
kimeneti ellenőrzést, és mindkettőt visszaadja. Ez a futtató a befagyasztott
eseteket az n8n elemformájára alakítja (ugyanaz, mint az "All new content"
kimenete: a képek és a képkockák base64-ben, ahogy a helyi szolgáltatás adja),
elküldi, és a jelentést a Python-rendszer futásaival azonos helyre teszi, így a
gépi pontozó és a bíró ugyanúgy dolgozik rajta (`--system n8n`).

    python eval/run_n8n.py --url http://localhost:5678/webhook/content-digest-eval --runs 4
    python eval/run_n8n.py --url ... --cases p04,p07 --frames 0        # képkockák nélkül
    python eval/run_n8n.py --url ... --claude-url http://127.0.0.1:8790  # előfizetéssel

A webhook tokenje: EVAL_N8N_TOKEN (környezeti változó, vagy Windows felhasználói
változó). Az n8n-ben ugyanez az "Eval: webhook" Header Auth hitelesítése
(`X-Eval-Token`).

Kimenet: eval/runs/<időbélyeg>_n8n/<eset>/run<k>.md (sima szöveg, a bíró
miatt .md) + run<k>.json, és osszesito.md.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
CASES = EVAL / "cases"
RUNS = EVAL / "runs"
sys.path.insert(0, str(EVAL))
import score  # noqa: E402

MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
SOURCES = {"youtube": "youtube", "x": "x", "email": "email"}  # az n8n-nek nincs web-forrása
TIMEOUT_SEC = 900
RETRIES = 3


def user_env(name: str) -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0]).strip()
    except (ImportError, OSError):
        return ""


def n8n_cases(wanted: list[str] | None) -> list[Path]:
    manifest = yaml.safe_load((EVAL / "manifest.yaml").read_text(encoding="utf-8"))
    out = []
    for case in manifest["cases"]:
        if case.get("tartalek") or "n8n" not in (case.get("rendszer") or []):
            continue
        case_dir = CASES / case["id"]
        ref = yaml.safe_load((case_dir / "reference.yaml").read_text(encoding="utf-8"))
        if ref.get("allapot") != "jovahagyva":
            continue
        if wanted and not any(case["id"] == w or case["id"].startswith(w + "_") for w in wanted):
            continue
        out.append(case_dir)
    return out


def data_url(path: Path) -> str | None:
    mt = MEDIA.get(path.suffix.lower())
    if not mt or not path.is_file():
        return None
    return f"data:{mt};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def spread(lst: list, k: int) -> list:
    """k elem a lista teljes hosszán elosztva (ahogy a Build prompt is választ)."""
    if k <= 0:
        return []
    if k >= len(lst):
        return lst
    return [lst[round(i * (len(lst) - 1) / max(1, k - 1))] for i in range(k)]


def to_n8n(data: dict, frames_per_video: int, base: Path = ROOT) -> dict | None:
    """Egy befagyasztott elem az n8n "All new content" formájában (None: az n8n nem ismeri)."""
    src = SOURCES.get(data.get("source"))
    if not src:
        return None
    meta = data.get("meta") or {}
    url = data.get("url") or ""
    handle = re.search(r"x\.com/([^/]+)/status", url)
    item = {
        "uid": data.get("uid"),
        "source": src,
        "profile": "investor",
        "author": data.get("target_name", "") + (f" (@{handle.group(1)})" if src == "x" and handle else ""),
        "title": data.get("title") or "",
        "url": url,
        "published": data.get("published") or "",
        "text": data.get("text") or "",
    }
    if src == "youtube":
        has = bool(meta.get("has_transcript"))
        ts = meta.get("transcript_source")
        item.update({"hasTranscript": has,
                     "transcriptSource": "whisper" if ts == "whisper" else ("youtube" if has else "none"),
                     "transcriptLanguage": meta.get("transcript_language") or ""})
        if not has and meta.get("description"):
            item["text"] = meta["description"]
    if src == "x":
        item["images"] = [u for u in (data_url(base / p) for p in meta.get("images") or []) if u]
    frames = []
    for f in spread(meta.get("frames") or [], frames_per_video):
        url_ = data_url(base / f.get("path", ""))
        if url_:
            mt, b64 = url_[5:].split(";base64,", 1)
            frames.append({"at": f.get("at", ""), "context": f.get("context", ""), "mediaType": mt, "data": b64})
    if frames:
        item["frames"] = frames
    return item


def post(url: str, token: str, payload: dict) -> list[dict]:
    last = ""
    for attempt in range(RETRIES):
        try:
            resp = requests.post(url, json=payload, headers={"X-Eval-Token": token}, timeout=TIMEOUT_SEC)
        except requests.RequestException as exc:
            last = str(exc)[:200]
        else:
            if resp.status_code in (401, 403):
                raise RuntimeError(f"a webhook {resp.status_code}-t adott: rossz EVAL_N8N_TOKEN?")
            try:
                data = resp.json()
            except ValueError:
                data = None
            # Üres vagy hibás válasz: a workflow hibára futott (pl. az n8n
            # JS-futtatója induláskor még nem állt készen). Újrapróbáljuk.
            if isinstance(data, list) and data and all("text" in d for d in data):
                return data
            last = f"HTTP {resp.status_code}: {resp.text[:200]}"
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"a webhook {RETRIES} próbálkozás után sem adott jelentést: {last}")


def run_one(case_dir: Path, k: int, out_dir: Path, args, token: str) -> dict:
    case = case_dir.name
    record: dict = {"eset": case, "futas": k, "rendszer": "n8n", "kepkocka": args.frames}
    started = time.monotonic()
    try:
        raw = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((case_dir / "input").glob("*.json"))]
        items = [x for x in (to_n8n(d, args.frames) for d in raw) if x]
        record["kihagyva"] = [d.get("uid") for d in raw if not SOURCES.get(d.get("source"))]
        settings = {k_: v for k_, v in (("model", args.model), ("claudeUrl", args.claude_url)) if v is not None}
        reports = post(args.url, token, {"items": items, "settings": settings})
        text = "\n\n".join(r["text"] for r in reports)
        record.update({"kimeneti_ellenorzes": [f for r in reports for f in r.get("findings") or []],
                       "visszatartva": any(r.get("held") for r in reports)})
        case_out = out_dir / case
        case_out.mkdir(parents=True, exist_ok=True)
        saved = case_out / f"run{k}.md"
        saved.write_text(text + "\n", encoding="utf-8")
        record["jelentes"] = str(saved.relative_to(ROOT))
        source = "\n".join(json.dumps(d, ensure_ascii=False) for d in raw)
        res = score.score(saved, case, "n8n", source=source)
        record["pontozas"] = {
            "atment": res.atment, "csak_uj_szabaly": res.csak_uj_szabaly,
            "kizaro_hibak": [c.kod for c in res.kizaro_hibak], "d_pont": res.d_pont,
            "forma_hibak": [c.kod for c in res.forma if c.ok is False], "szintek": res.szintek,
        }
    except Exception as exc:  # egy eset hibája ne állítsa meg a többit
        record["hiba"] = str(exc)[:400]
    finally:
        record["masodperc"] = round(time.monotonic() - started)
        (out_dir / case).mkdir(parents=True, exist_ok=True)
        (out_dir / case / f"run{k}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
    return record


def summary(records: list[dict], out_dir: Path, meta: dict) -> str:
    lines = [f"# n8n eval-futás {meta['idopont']}", "",
             f"Webhook: {meta['url']}. Képkocka videónként: {meta['kepkocka']}. "
             f"Modell: {meta['modell'] or 'a workflow beállítása'}. Esetek: {len(meta['esetek'])}, "
             f"esetenként {meta['futasok']} futás.", "",
             "Kódos pontozás (`--system n8n`); a bíró: `python eval/judge.py <ez a mappa> --system n8n`.", ""]
    for case in meta["esetek"]:
        lines.append(f"## {case}")
        for r in sorted((x for x in records if x["eset"] == case), key=lambda x: x["futas"]):
            p = r.get("pontozas")
            if not p:
                lines.append(f"- run{r['futas']}: HIBA: {r.get('hiba')}")
                continue
            status = "átment" if p["atment"] else ("megbukott (csak új szabály)" if p["csak_uj_szabaly"] else "megbukott")
            kiz = f" kizáró: {', '.join(p['kizaro_hibak'])}" if p["kizaro_hibak"] else ""
            forma = f" forma: {', '.join(p['forma_hibak'])}" if p["forma_hibak"] else ""
            held = f" **visszatartva: {'; '.join(r['kimeneti_ellenorzes'])}**" if r.get("visszatartva") else ""
            d = f"{p['d_pont']:.1f}" if p["d_pont"] is not None else "–"
            szint = sum(s["talalt"] for s in p["szintek"]), sum(s["szintek"] for s in p["szintek"])
            lines.append(f"- run{r['futas']}: {status}; D {d}/15; szintek {szint[0]}/{szint[1]};{kiz}{forma}{held}; "
                         f"{r.get('masodperc')} mp")
        lines.append("")
    text = "\n".join(lines) + "\n"
    (out_dir / "osszesito.md").write_text(text, encoding="utf-8")
    return text


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True, help="az n8n Eval: webhook production URL-je")
    ap.add_argument("--cases", help="vesszővel elválasztott esetek (pl. p01,p07); alapból mind, ami n8n-en fut")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--frames", type=int, default=10, help="képkocka videónként (a workflow framesPerVideo-ja); 0 = nincs")
    ap.add_argument("--model", default=None, help="a workflow model beállításának felülírása")
    ap.add_argument("--claude-url", default=None, help="a workflow claudeUrl-jének felülírása (előfizetés)")
    ap.add_argument("--parallel", type=int, default=1)
    args = ap.parse_args()

    token = os.environ.get("EVAL_N8N_TOKEN") or user_env("EVAL_N8N_TOKEN")
    if not token:
        ap.error("nincs EVAL_N8N_TOKEN (az n8n Eval: webhook hitelesítésének értéke)")
    cases = n8n_cases(args.cases.split(",") if args.cases else None)
    if not cases:
        print("nincs n8n-en futtatható, jóváhagyott eset")
        return 1
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M") + "_n8n"
    out_dir = RUNS / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"idopont": stamp, "rendszer": "n8n", "url": args.url, "kepkocka": args.frames, "modell": args.model,
            "claude_url": args.claude_url, "futasok": args.runs, "esetek": [c.name for c in cases]}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(cases)} eset × {args.runs} futás → {out_dir.relative_to(ROOT)}", flush=True)

    jobs = [(c, k) for c in cases for k in range(1, args.runs + 1)]
    records = []
    with cf.ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        for r in pool.map(lambda j: run_one(j[0], j[1], out_dir, args, token), jobs):
            records.append(r)
            p = r.get("pontozas") or {}
            status = r.get("hiba") or ("átment" if p.get("atment") else f"megbukott {p.get('kizaro_hibak')}")
            print(f"  {r['eset']} run{r['futas']}: {status}; {r.get('masodperc')} mp", flush=True)
    print()
    print(summary(records, out_dir, meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
