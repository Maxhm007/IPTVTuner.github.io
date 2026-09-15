#!/usr/bin/env python3
import re
import sys
import time
import urllib.request
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (IPTVTuner-SelfHeal/1.4)"
TIMEOUT = 6
MAX_REPLACEMENT_CANDIDATES = 3

SOURCES = [
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/bd.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/in.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/us.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/uk.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/qa.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/jp.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/at.m3u",
]

ATTR_RE = re.compile(r'(tvg-id|tvg-name|group-title)="([^"]*)"')
INACTIVE_INFO = "#SELFHEAL-INACTIVE "
INACTIVE_URL = "#SELFHEAL-URL "


def fetch_text(url, timeout=TIMEOUT):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.apple.mpegurl, application/x-mpegURL, text/plain, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(512 * 1024)
        return raw.decode("utf-8", errors="replace"), getattr(r, "url", url)


def is_working_hls(url):
    try:
        text, _ = fetch_text(url)
        head = text.lstrip()[:2000]
        if "#EXTM3U" not in head:
            return False
        return any(tag in text for tag in ("#EXT-X-STREAM-INF", "#EXTINF", "#EXT-X-TARGETDURATION"))
    except Exception:
        return False


def normalize(s):
    s = (s or "").lower()
    s = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", s)
    s = re.sub(r"\b(hd|fhd|sd|uhd|4k|1080p|720p|576p|480p|360p)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def base_tvg_id(value):
    value = (value or "").strip().lower().split("@", 1)[0]
    if not value or "no tvg id" in value or value in {"none", "null", "n/a"}:
        return ""
    return value


def parse_playlist(text):
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        raw_info = lines[i]
        stripped = raw_info.strip()
        inactive = False

        if stripped.startswith(INACTIVE_INFO + "#EXTINF:"):
            inactive = True
            info = stripped[len(INACTIVE_INFO):]
        elif stripped.startswith("#EXTINF:"):
            info = stripped
        else:
            i += 1
            continue

        attrs = dict(ATTR_RE.findall(info))
        name = info.split(",", 1)[1].strip() if "," in info else attrs.get("tvg-name", "")
        j = i + 1

        if inactive:
            while j < len(lines) and not lines[j].strip().startswith(INACTIVE_URL):
                if lines[j].strip().startswith("#EXTINF:") or lines[j].strip().startswith(INACTIVE_INFO + "#EXTINF:"):
                    break
                j += 1
            if j < len(lines) and lines[j].strip().startswith(INACTIVE_URL):
                url = lines[j].strip()[len(INACTIVE_URL):].strip()
            else:
                i += 1
                continue
        else:
            while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith("#")):
                j += 1
            if j < len(lines) and lines[j].strip().startswith(("http://", "https://")):
                url = lines[j].strip()
            else:
                i += 1
                continue

        out.append({
            "info": info,
            "url": url,
            "name": attrs.get("tvg-name") or name,
            "display": name,
            "tvg_id": attrs.get("tvg-id", ""),
            "group": attrs.get("group-title", ""),
            "inactive": inactive,
            "raw_info": raw_info,
            "raw_url": lines[j],
        })
        i = j + 1
    return out


def catalog_key(entry):
    tvg = base_tvg_id(entry.get("tvg_id"))
    if tvg:
        return ("id", tvg)
    return (
        "name",
        normalize(entry.get("group")),
        normalize(entry.get("name") or entry.get("display")),
    )


def sync_legacy_catalog(v8_path):
    legacy_path = v8_path.with_name("IPTV-V007.m3u")
    if not legacy_path.exists():
        print("warning: IPTV-V007.m3u not found; legacy catalog sync skipped")
        return 0

    current_text = v8_path.read_text(encoding="utf-8")
    legacy_text = legacy_path.read_text(encoding="utf-8")
    current_entries = parse_playlist(current_text)
    legacy_entries = parse_playlist(legacy_text)

    existing = {catalog_key(e) for e in current_entries}
    additions = []
    for entry in legacy_entries:
        key = catalog_key(entry)
        if key in existing:
            continue
        additions.append(entry)
        existing.add(key)

    if not additions:
        print("legacy catalog already fully represented in V008")
        return 0

    block = ["", "########## V007 LEGACY CATALOG - SELF HEAL ##########", ""]
    for entry in additions:
        block.extend([entry["info"], entry["url"], ""])

    v8_path.write_text(current_text.rstrip() + "\n" + "\n".join(block), encoding="utf-8")
    print(f"imported {len(additions)} missing V007 channel(s) into V008")
    return len(additions)


def load_candidates():
    candidates = []
    for src in SOURCES:
        try:
            text, _ = fetch_text(src, timeout=12)
            parsed = parse_playlist(text)
            candidates.extend(parsed)
            print(f"loaded {src}: {len(parsed)} entries")
        except Exception as e:
            print(f"warning: source failed {src}: {e}")
    return candidates


def same_channel(entry, cand):
    e_id = base_tvg_id(entry.get("tvg_id"))
    c_id = base_tvg_id(cand.get("tvg_id"))

    if e_id and c_id:
        return e_id == c_id

    en = normalize(entry.get("name") or entry.get("display"))
    cn = normalize(cand.get("name") or cand.get("display"))
    if not en or not cn or en != cn:
        return False

    eg = normalize(entry.get("group"))
    cg = normalize(cand.get("group"))
    if eg and cg and eg != cg:
        return False

    return True


def find_replacement(entry, candidates):
    matches = [
        c for c in candidates
        if c["url"] != entry["url"] and same_channel(entry, c)
    ]
    matches.sort(key=lambda c: (not c["url"].startswith("https://")))

    for cand in matches[:MAX_REPLACEMENT_CANDIDATES]:
        print(f"  trying exact channel match: {cand['display']} -> {cand['url']}")
        if is_working_hls(cand["url"]):
            return cand["url"]
    return None


def replace_entry_block(text, entry, new_info_line, new_url_line):
    old_block = entry["raw_info"] + "\n" + entry["raw_url"]
    new_block = new_info_line + "\n" + new_url_line
    return text.replace(old_block, new_block, 1)


def heal(path):
    p = Path(path)
    imported = sync_legacy_catalog(p)
    original = p.read_text(encoding="utf-8")
    entries = parse_playlist(original)
    total = len(entries)
    candidates = None
    replacements = []
    inactivated = []
    reactivated = []

    for idx, entry in enumerate(entries, start=1):
        prefix = f"[{idx}/{total}]"

        if entry["inactive"]:
            print(f"{prefix} checking INACTIVE {entry['display']}: searching replacement")
            if candidates is None:
                candidates = load_candidates()
            new_url = find_replacement(entry, candidates)
            if new_url:
                original = replace_entry_block(original, entry, entry["info"], new_url)
                reactivated.append((entry["display"], new_url))
                print(f"  REACTIVATED -> {new_url}")
            else:
                print("  still inactive; no exact verified replacement")
            continue

        print(f"{prefix} checking {entry['display']}: {entry['url']}")
        if is_working_hls(entry["url"]):
            print("  OK")
            continue

        print("  FAILED; searching exact replacement")
        if candidates is None:
            candidates = load_candidates()

        new_url = find_replacement(entry, candidates)
        if new_url:
            original = replace_entry_block(original, entry, entry["info"], new_url)
            replacements.append((entry["display"], entry["url"], new_url))
            print(f"  REPLACED -> {new_url}")
        else:
            inactive_info = INACTIVE_INFO + entry["info"]
            inactive_url = INACTIVE_URL + entry["url"]
            original = replace_entry_block(original, entry, inactive_info, inactive_url)
            inactivated.append((entry["display"], entry["url"]))
            print("  INACTIVATED; will retry on future runs")

    if imported or replacements or inactivated or reactivated:
        p.write_text(original, encoding="utf-8")
        print(
            "playlist changed: "
            f"imported={imported}, replaced={len(replacements)}, "
            f"inactivated={len(inactivated)}, reactivated={len(reactivated)}"
        )
        for name, old, new in replacements:
            print(f"- replaced {name}: {old} -> {new}")
        for name, old in inactivated:
            print(f"- inactive {name}: {old}")
        for name, new in reactivated:
            print(f"- reactivated {name}: {new}")
    else:
        print("no playlist changes required")

    print(f"completed full rotation: {total}/{total}. Next scheduled run starts again at [1/{total}].")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "IPTV-V008.m3u"
    heal(target)
