#!/usr/bin/env python3
"""Deep-merge missing keys from en.json into other translations.i18n locales.

Usage:
  python i18n_fill_missing.py              # merge missing (keeps EN text as placeholder)
  python i18n_fill_missing.py --check      # assert 0 missing; exit 1 if any
  python i18n_fill_missing.py --dump zh    # print missing key=value for a locale
  python i18n_fill_missing.py --apply PATH # apply flat JSON { "a.b": "text" } overlays
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "hiddifypanel" / "translations.i18n"
LOCALES = ["zh", "fa", "fr", "ru", "pt", "my"]


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


def get_path(root: dict, dotted: str):
    cur = root
    for p in dotted.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def deep_merge_missing(dst: dict, src: dict) -> int:
    """Copy keys from src into dst when missing. Returns count of leaf keys added."""
    added = 0
    for k, v in src.items():
        if k not in dst:
            dst[k] = json.loads(json.dumps(v))  # deep copy
            added += len(flatten(v)) if isinstance(v, dict) else 1
        elif isinstance(v, dict) and isinstance(dst.get(k), dict):
            added += deep_merge_missing(dst[k], v)
    return added


def load(lang: str) -> dict:
    return json.loads((ROOT / f"{lang}.json").read_text(encoding="utf-8"))


def save(lang: str, data: dict) -> None:
    (ROOT / f"{lang}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def missing_vs_en(lang: str, en_flat: dict, data: dict) -> dict:
    flat = flatten(data)
    return {k: en_flat[k] for k in sorted(en_flat) if k not in flat}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dump", metavar="LANG")
    ap.add_argument("--apply", metavar="PATH", help="flat JSON overlay path")
    ap.add_argument("--locale", help="with --apply, target locale")
    ap.add_argument("--merge", action="store_true", help="merge missing structure from en")
    args = ap.parse_args()

    en = load("en")
    en_flat = flatten(en)

    if args.dump:
        miss = missing_vs_en(args.dump, en_flat, load(args.dump))
        print(json.dumps(miss, ensure_ascii=False, indent=2))
        print(f"# {args.dump} missing {len(miss)}", file=sys.stderr)
        return 0

    if args.apply:
        if not args.locale:
            print("--locale required with --apply", file=sys.stderr)
            return 2
        overlay = json.loads(Path(args.apply).read_text(encoding="utf-8"))
        data = load(args.locale)
        for k, v in overlay.items():
            set_path(data, k, v)
        save(args.locale, data)
        print(f"applied {len(overlay)} keys to {args.locale}")
        return 0

    if args.merge:
        for lang in LOCALES:
            data = load(lang)
            n = deep_merge_missing(data, en)
            save(lang, data)
            miss = missing_vs_en(lang, en_flat, data)
            print(f"{lang}: added≈{n}, remaining_missing={len(miss)}")
        return 0

    if args.check:
        bad = 0
        for lang in LOCALES:
            miss = missing_vs_en(lang, en_flat, load(lang))
            # Also treat identical-to-en for newly required high-vis keys? No — only structure.
            print(f"{lang}: missing={len(miss)}")
            if miss:
                bad = 1
                for k in list(miss)[:8]:
                    print(f"  - {k}")
        return bad

    print("Specify --merge, --check, --dump LANG, or --apply PATH --locale LANG", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
