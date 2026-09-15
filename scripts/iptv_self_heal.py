#!/usr/bin/env python3
import re
import sys
import time
import urllib.request
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (IPTVTuner-SelfHeal/1.1)"
TIMEOUT = 12

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
    return (value or "").strip().lower().split("@", 1)[0]


def parse_playlist(text):
    lines = [x.strip() for x in text.splitlines()]
    out = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF:"):
            info = lines[i]
            attrs = dict(ATTR_RE.findall(info))
            name = info.split(",", 1)[1].strip() if "," in info else attrs.get("tvg-name", "")
            j = i + 1
            while j < len(lines) and (not lines[j] or lines[j].startswith("#")):
                j += 1
            if j < len(lines) and lines[j].startswith(("http://", "https://")):
                out.append({
                    "info": info,
                    "url": lines[j],
                    "name": attrs.get("tvg-name") or name,
                    "display": name,
                    "tvg_id": attrs.get("tvg-id", ""),
                    "group": attrs.get("group-title", ""),
                })
                i = j
        i += 1
    return out


def load_candidates():
    candidates = []
    for src in SOURCES:
        try:
            text, _ = fetch_text(src, timeout=20)
            parsed = parse_playlist(text)
            candidates.extend(parsed)
            print(f"loaded {src}: {len(parsed)} entries")
        except Exception as e:
            print(f"warning: source failed {src}: {e}")
    return candidates


def same_channel(entry, cand):
    """Strict channel identity check. No fuzzy/partial matching."""
    e_id = base_tvg_id(entry.get("tvg_id"))
    c_id = base_tvg_id(cand.get("tvg_id"))

    # Best signal: exact tvg-id (ignoring @SD/@HD suffix).
    if e_id and c_id:
        return e_id == c_id

    # Fallback only when an ID is missing: exact normalized name.
    en = normalize(entry.get("name") or entry.get("display"))
    cn = normalize(cand.get("name") or cand.get("display"))
    if not en or not cn or en != cn:
        return False

    # If both carry group metadata, require it to match too.
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

    # Prefer HTTPS, then test in source order.
    matches.sort(key=lambda c: (not c["url"].startswith("https://")))

    for cand in matches[:20]:
        print(f"  trying exact channel match: {cand['display']} -> {cand['url']}")
        if is_working_hls(cand["url"]):
            return cand["url"]
    return None


def heal(path):
    p = Path(path)
    original = p.read_text(encoding="utf-8")
    entries = parse_playlist(original)
    candidates = None
    replacements = []

    for entry in entries:
        print(f"checking {entry['display']}: {entry['url']}")
        if is_working_hls(entry["url"]):
            print("  OK")
            continue

        print("  FAILED; searching exact replacement")
        if candidates is None:
            candidates = load_candidates()

        new_url = find_replacement(entry, candidates)
        if new_url:
            original = original.replace(entry["url"], new_url, 1)
            replacements.append((entry["display"], entry["url"], new_url))
            print(f"  REPLACED -> {new_url}")
        else:
            print("  NO EXACT VERIFIED REPLACEMENT; keeping existing URL")
        time.sleep(0.2)

    if replacements:
        p.write_text(original, encoding="utf-8")
        print(f"updated {len(replacements)} channel(s)")
        for name, old, new in replacements:
            print(f"- {name}: {old} -> {new}")
    else:
        print("no playlist changes required")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "IPTV-V008.m3u"
    heal(target)
