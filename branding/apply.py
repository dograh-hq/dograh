#!/usr/bin/env python3
"""Apply the Donna white-label overlay on top of a pristine upstream checkout.

Runs at build time (CI / Docker / `make brand`), NOT committed, so the source
tree stays identical to dograh-hq/dograh and upstream merges never conflict on
branding. Everything is driven by branding/brand.json.

  python3 branding/apply.py            apply in place
  python3 branding/apply.py --dry-run  report what would change
  python3 branding/apply.py --check    list leftover upstream references
  python3 branding/apply.py --update-lock   accept current upstream versions of overridden files
"""
import argparse, glob, hashlib, json, os, re, shutil, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BR = os.path.join(ROOT, "branding")
cfg = json.load(open(os.path.join(BR, "brand.json")))
LOCK = os.path.join(BR, "overrides.lock.json")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]


def files(globs, exclude):
    out = set()
    for g in globs:
        out |= {p for p in glob.glob(os.path.join(ROOT, g), recursive=True) if os.path.isfile(p)}
    ex = set()
    for g in exclude:
        ex |= set(glob.glob(os.path.join(ROOT, g), recursive=True))
    return sorted(out - ex)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--update-lock", action="store_true")
    a = ap.parse_args()
    rel = lambda p: os.path.relpath(p, ROOT)
    warnings = []

    if a.check:
        pats = [re.compile(p, re.I) for p in cfg["check_patterns"]]
        n = 0
        for p in files(["ui/src/**/*", "ui/public/**/*", "ui/*.ts", "ui/Dockerfile", "api/**/*.py"],
                       cfg["text_exclude"]):
            try:
                for i, line in enumerate(open(p, encoding="utf-8"), 1):
                    if any(r.search(line) for r in pats):
                        print(f"{rel(p)}:{i}: {line.strip()[:120]}"); n += 1
            except UnicodeDecodeError:
                pass
        print(f"\n{n} leftover reference(s)"); return

    lock = json.load(open(LOCK)) if os.path.exists(LOCK) else {}

    # 1. overrides (whole-file replacements). Warn if upstream changed the target.
    ov_root = os.path.join(BR, "overrides")
    for src in glob.glob(ov_root + "/**/*", recursive=True):
        if not os.path.isfile(src):
            continue
        dst = os.path.join(ROOT, os.path.relpath(src, ov_root)); key = rel(dst)
        cur = sha(dst) if os.path.exists(dst) else None
        if cur and key in lock and lock[key] != cur and not a.update_lock:
            warnings.append(f"UPSTREAM CHANGED {key}: review branding/overrides/{key}, then --update-lock")
        if a.update_lock and cur:
            lock[key] = cur
        elif cur and key not in lock:
            lock[key] = cur
        print(f"override  {key}")
        if not a.dry_run and not a.update_lock:
            os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copyfile(src, dst)

    # 2. assets
    for name, dest in cfg["assets"].items():
        print(f"asset     {dest}")
        if not a.dry_run and not a.update_lock:
            shutil.copyfile(os.path.join(BR, "assets", name), os.path.join(ROOT, dest))

    # 3. text / url / regex rules
    text_rules = [(re.compile(p), r) for p, r in cfg["text_replacements"]]
    urls = sorted(cfg["url_replacements"].items(), key=lambda kv: -len(kv[0]))
    fb = cfg.get("url_fallback", "#")
    url_re = [(re.compile(re.escape(u) + r"[^\s\"'`)<>\\]*"), n) for u, n in urls]
    changed = 0
    if not a.update_lock:
        for p in files(cfg["text_globs"], cfg["text_exclude"]):
            try:
                s = o = open(p, encoding="utf-8").read()
            except UnicodeDecodeError:
                continue
            for rx, new in url_re:
                s = rx.sub(new or fb, s)
            for rx, r in text_rules:
                s = rx.sub(r, s)
            for rule in cfg["regex_rules"]:
                if rel(p) == rule["file"]:
                    s2 = re.sub(rule["pattern"], rule["replacement"], s, flags=re.S)
                    if s2 == s:
                        warnings.append(f"regex rule matched nothing in {rule['file']} (upstream changed?)")
                    s = s2
            if s != o:
                changed += 1
                if not a.dry_run:
                    open(p, "w", encoding="utf-8").write(s)
        print(f"text      {changed} file(s) rewritten")

    if not a.dry_run:
        json.dump(lock, open(LOCK, "w"), indent=2, sort_keys=True)
    for w in warnings:
        print("WARNING:", w, file=sys.stderr)
    sys.exit(1 if any(w.startswith("UPSTREAM") for w in warnings) else 0)


main()
