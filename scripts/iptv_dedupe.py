#!/usr/bin/env python3
import re
import sys
from pathlib import Path

INACTIVE_PREFIX = "#SELFHEAL-INACTIVE "
INACTIVE_URL = "#SELFHEAL-URL "

ALIASES = {
    "anando tv": "ananda tv",
    "anando": "ananda tv",
    "redbull tv": "red bull tv",
    "redbull": "red bull tv",
    "nhk world japan hd": "nhk world japan",
    "cgtn": "cgtn news",
}


def canonical_name(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", s)
    s = re.sub(r"\b(fhd|uhd|4k|1080p|720p|576p|480p|360p|sd)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return ALIASES.get(s, s)


def display_name(info: str) -> str:
    base = info[len(INACTIVE_PREFIX):] if info.startswith(INACTIVE_PREFIX) else info
    return base.split(",", 1)[1].strip() if "," in base else base


def dedupe(path: str) -> None:
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines()
    out = []
    seen = set()
    removed = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        is_entry = stripped.startswith("#EXTINF:") or stripped.startswith(INACTIVE_PREFIX + "#EXTINF:")
        if not is_entry:
            out.append(line)
            i += 1
            continue

        name = display_name(stripped)
        key = canonical_name(name)

        j = i + 1
        while j < len(lines):
            s = lines[j].strip()
            if stripped.startswith(INACTIVE_PREFIX + "#EXTINF:"):
                if s.startswith(INACTIVE_URL):
                    j += 1
                    break
            else:
                if s.startswith(("http://", "https://")):
                    j += 1
                    break
            if s.startswith("#EXTINF:") or s.startswith(INACTIVE_PREFIX + "#EXTINF:"):
                break
            j += 1

        block = lines[i:j]
        if key and key in seen:
            removed.append(name)
        else:
            if key:
                seen.add(key)
            out.extend(block)
        i = j

    # Collapse excessive blank lines left by removed blocks.
    cleaned = []
    blank = False
    for line in out:
        if not line.strip():
            if blank:
                continue
            blank = True
        else:
            blank = False
        cleaned.append(line)

    text = "\n".join(cleaned).rstrip() + "\n"
    p.write_text(text, encoding="utf-8")
    print(f"dedupe complete: removed={len(removed)}")
    for name in removed:
        print(f"  removed duplicate: {name}")


if __name__ == "__main__":
    dedupe(sys.argv[1] if len(sys.argv) > 1 else "IPTV-V008.m3u")
