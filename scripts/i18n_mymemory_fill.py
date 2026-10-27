#!/usr/bin/env python3
"""Fill missing i18n keys via MyMemory free API (concurrent, no pip deps)."""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "hiddifypanel" / "translations.i18n"
SCRIPTS = Path(__file__).resolve().parent
LOCALES = {
    "fr": "fr",
    "ru": "ru",
    "pt": "pt",
    "my": "ms",
}
WORKERS = 8
PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}|\$[a-zA-Z_][a-zA-Z0-9_]*|«[^»]*»")


def flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.update(flatten(v, p))
            else:
                out[p] = v
    return out


def set_path(root: dict, dotted: str, value):
    parts = dotted.split(".")
    cur = root
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def protect(text: str) -> tuple[str, list[str]]:
    held: list[str] = []

    def repl(m):
        held.append(m.group(0))
        return f"__PH{len(held) - 1}__"

    return PLACEHOLDER_RE.sub(repl, text), held


def restore(text: str, held: list[str]) -> str:
    def repl(m):
        i = int(m.group(1))
        return held[i] if 0 <= i < len(held) else m.group(0)

    return re.sub(r"__PH(\d+)__", repl, text)


def mymemory(text: str, target: str) -> str:
    q = urllib.parse.urlencode({"q": text[:450], "langpair": f"en|{target}"})
    url = f"https://api.mymemory.translated.net/get?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "HiddifyI18nFill/1.1"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    t = (data.get("responseData") or {}).get("translatedText") or text
    if "MYMEMORY WARNING" in t.upper() or t.lower().startswith("query length"):
        return text
    return t


def translate_text(text: str, target: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return text
    protected, held = protect(text)
    parts = protected.split(" | ")
    out = []
    for part in parts:
        for attempt in range(3):
            try:
                out.append(mymemory(part, target))
                break
            except Exception:
                if attempt == 2:
                    out.append(part)
                else:
                    time.sleep(0.4 * (attempt + 1))
    return restore(" | ".join(out), held)


def fill_locale(lang: str, code: str, en_flat: dict) -> None:
    data = json.loads((ROOT / f"{lang}.json").read_text(encoding="utf-8"))
    flat = flatten(data)
    missing = {k: en_flat[k] for k in en_flat if k not in flat}
    # Resume from existing overlay if present
    overlay_path = SCRIPTS / f"_overlay_{lang}.json"
    overlay = {}
    if overlay_path.exists():
        try:
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
        except Exception:
            overlay = {}
    todo = {k: v for k, v in missing.items() if k not in overlay}
    print(f"== {lang}: missing={len(missing)} todo={len(todo)} have={len(overlay)} ==", flush=True)
    if not todo and missing:
        # apply existing overlay
        for k, v in overlay.items():
            if k in missing:
                set_path(data, k, v)
        (ROOT / f"{lang}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  {lang}: applied cached overlay", flush=True)
        return
    if not todo:
        print(f"  {lang}: nothing to do", flush=True)
        return

    items = list(todo.items())
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {
            ex.submit(translate_text, v if isinstance(v, str) else str(v), code): k
            for k, v in items
            if isinstance(v, str)
        }
        for non_str_k, non_str_v in ((k, v) for k, v in items if not isinstance(v, str)):
            overlay[non_str_k] = non_str_v
        for fut in as_completed(futs):
            k = futs[fut]
            try:
                overlay[k] = fut.result()
            except Exception:
                overlay[k] = todo[k]
            done += 1
            if done % 40 == 0 or done == len(futs):
                print(f"  {lang}: {done}/{len(futs)}", flush=True)
                overlay_path.write_text(
                    json.dumps(overlay, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )

    for k, v in overlay.items():
        set_path(data, k, v)
    (ROOT / f"{lang}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    overlay_path.write_text(json.dumps(overlay, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  {lang}: done overlay={len(overlay)}", flush=True)


def main():
    langs = sys.argv[1:] or list(LOCALES)
    en = json.loads((ROOT / "en.json").read_text(encoding="utf-8"))
    en_flat = flatten(en)
    for lang in langs:
        if lang not in LOCALES:
            print(f"skip unknown {lang}", flush=True)
            continue
        fill_locale(lang, LOCALES[lang], en_flat)


if __name__ == "__main__":
    main()
