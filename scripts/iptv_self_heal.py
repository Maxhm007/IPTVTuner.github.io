#!/usr/bin/env python3
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

USER_AGENT = "Mozilla/5.0 (IPTVTuner-SelfHeal/3.0)"
TIMEOUT = 6
SOURCE_TIMEOUT = 12
MAX_REPLACEMENT_CANDIDATES = 3
FAILURES_BEFORE_ACTION = 3
RECOVERY_SUCCESSES_REQUIRED = 2
BATCH_SIZE = 40
CHECK_WORKERS = 8

SOURCES = [
    ("iptv-org/bd", "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/bd.m3u", 10),
    ("iptv-org/in", "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/in.m3u", 20),
    ("iptv-org/us", "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/us.m3u", 20),
    ("iptv-org/uk", "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/uk.m3u", 20),
    ("iptv-org/qa", "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/qa.m3u", 20),
    ("iptv-org/jp", "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/jp.m3u", 20),
    ("iptv-org/at", "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/at.m3u", 20),
]

ATTR_RE = re.compile(r'(tvg-id|tvg-name|group-title)="([^"]*)"')
INACTIVE_INFO = "#SELFHEAL-INACTIVE "
INACTIVE_URL = "#SELFHEAL-URL "

ALIASES = {
    "anando": "ananda",
    "anando tv": "ananda",
    "shomoy": "somoy",
    "shomoy tv": "somoy",
    "tsports": "t sports",
    "t sports": "t sports",
    "ekhon tv hd": "ekhon",
    "ekushey tv hd": "ekushey",
    "boishakhi tv hd": "boishakhi",
}

# These channels may require a paid/subscription service. The self-healer will
# not discover or insert replacement streams for them automatically.
RESTRICTED_NAME_PARTS = (
    "hbo", "cinemax", "showtime", "disney channel", "disney jr", "disney xd",
    "espn", "sony six", "sony ten", "star sports", "supersport", "wwe network",
    "star movies", "sony pix", "fox movies", "fox family movies",
)


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch_bytes(url, timeout=TIMEOUT, max_bytes=512 * 1024, range_request=False):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.apple.mpegurl, application/x-mpegURL, video/*, audio/*, */*",
    }
    if range_request:
        headers["Range"] = f"bytes=0-{max_bytes - 1}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(max_bytes)
        return raw, getattr(r, "url", url)


def fetch_text(url, timeout=TIMEOUT):
    raw, final_url = fetch_bytes(url, timeout=timeout)
    return raw.decode("utf-8", errors="replace"), final_url


def first_variant_uri(text):
    lines = [x.strip() for x in text.splitlines()]
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            for nxt in lines[i + 1:]:
                if not nxt:
                    continue
                if not nxt.startswith("#"):
                    return nxt
                break
    return None


def first_media_uri(text):
    lines = [x.strip() for x in text.splitlines()]
    for i, line in enumerate(lines):
        if line.startswith("#EXTINF"):
            for nxt in lines[i + 1:]:
                if not nxt:
                    continue
                if not nxt.startswith("#"):
                    return nxt
                break
    # Some live playlists omit EXTINF in unusual cases. Only accept a URI if
    # this is clearly a media playlist.
    if "#EXT-X-TARGETDURATION" in text:
        for line in lines:
            if line and not line.startswith("#"):
                return line
    return None


def validate_hls_once(url):
    """Validate manifest structure and fetch one actual media segment."""
    text, final_url = fetch_text(url)
    if "#EXTM3U" not in text.lstrip()[:2048]:
        return False

    media_text = text
    media_base = final_url
    variant = first_variant_uri(text)
    if variant:
        variant_url = urljoin(final_url, variant)
        media_text, media_base = fetch_text(variant_url)
        if "#EXTM3U" not in media_text.lstrip()[:2048]:
            return False

    segment = first_media_uri(media_text)
    if not segment:
        return False

    segment_url = urljoin(media_base, segment)
    payload, _ = fetch_bytes(segment_url, timeout=TIMEOUT, max_bytes=2048, range_request=True)
    return len(payload) > 0


def is_working_hls(url, attempts=2):
    for attempt in range(attempts):
        try:
            if validate_hls_once(url):
                return True
        except Exception:
            pass
        if attempt + 1 < attempts:
            time.sleep(0.25)
    return False


def normalize(s):
    s = (s or "").lower()
    s = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", s)
    s = re.sub(r"\b(hd|fhd|sd|uhd|4k|1080p|720p|576p|480p|360p)\b", " ", s)
    s = re.sub(r"\b(tv|channel|live)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def canonical_name(s):
    n = normalize(s)
    return ALIASES.get(n, n)


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
                nxt = lines[j].strip()
                if nxt.startswith("#EXTINF:") or nxt.startswith(INACTIVE_INFO + "#EXTINF:"):
                    break
                j += 1
            if j >= len(lines) or not lines[j].strip().startswith(INACTIVE_URL):
                i += 1
                continue
            url = lines[j].strip()[len(INACTIVE_URL):].strip()
        else:
            while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith("#")):
                j += 1
            if j >= len(lines) or not lines[j].strip().startswith(("http://", "https://")):
                i += 1
                continue
            url = lines[j].strip()

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
        return f"id:{tvg}"
    return f"name:{normalize(entry.get('group'))}:{canonical_name(entry.get('name') or entry.get('display'))}"


def is_restricted(entry):
    name = canonical_name(entry.get("name") or entry.get("display"))
    return any(part in name for part in RESTRICTED_NAME_PARTS)


def looks_credential_style(url):
    # Common private IPTV pattern: /live/<username>/<password>/<channel>
    return bool(re.search(r"/live/[^/]+/[^/]+/[^/?]+", url or "", re.I))


def sync_legacy_catalog(v8_path):
    legacy_path = v8_path.with_name("IPTV-V007.m3u")
    if not legacy_path.exists():
        print("warning: IPTV-V007.m3u not found; legacy catalog sync skipped")
        return 0

    current_text = v8_path.read_text(encoding="utf-8")
    current_entries = parse_playlist(current_text)
    legacy_entries = parse_playlist(legacy_path.read_text(encoding="utf-8"))
    existing = {catalog_key(e) for e in current_entries}
    additions = []
    skipped = 0

    for entry in legacy_entries:
        key = catalog_key(entry)
        if key in existing:
            continue
        if is_restricted(entry) or looks_credential_style(entry.get("url", "")):
            skipped += 1
            continue
        additions.append(entry)
        existing.add(key)

    if additions:
        block = ["", "########## V007 LEGACY CATALOG - SELF HEAL ##########", ""]
        for entry in additions:
            block.extend([entry["info"], entry["url"], ""])
        v8_path.write_text(current_text.rstrip() + "\n" + "\n".join(block), encoding="utf-8")
        print(f"imported {len(additions)} eligible V007 channel(s) into V008")
    else:
        print("no eligible missing V007 channels to import")
    if skipped:
        print(f"skipped {skipped} restricted/credential-style legacy channel(s)")
    return len(additions)


def load_candidates():
    candidates = []
    for source_name, src, source_rank in SOURCES:
        try:
            text, _ = fetch_text(src, timeout=SOURCE_TIMEOUT)
            parsed = parse_playlist(text)
            for item in parsed:
                item["source"] = source_name
                item["source_rank"] = source_rank
            candidates.extend(parsed)
            print(f"loaded {source_name}: {len(parsed)} entries")
        except Exception as e:
            print(f"warning: source failed {source_name}: {e}")
    return candidates


def same_channel(entry, cand):
    e_id = base_tvg_id(entry.get("tvg_id"))
    c_id = base_tvg_id(cand.get("tvg_id"))
    if e_id and c_id:
        return e_id == c_id
    en = canonical_name(entry.get("name") or entry.get("display"))
    cn = canonical_name(cand.get("name") or cand.get("display"))
    if not en or not cn or en != cn:
        return False
    eg = normalize(entry.get("group"))
    cg = normalize(cand.get("group"))
    if eg and cg and eg != cg:
        return False
    return True


def find_replacement(entry, candidates, used_urls, pending_url=None):
    if is_restricted(entry):
        print("  replacement search disabled for restricted/subscription channel")
        return None, None

    trial = []
    if pending_url and pending_url != entry["url"]:
        trial.append({"url": pending_url, "source": "pending-recovery", "source_rank": 0})

    matches = [c for c in candidates if c["url"] != entry["url"] and same_channel(entry, c)]
    matches.sort(key=lambda c: (c.get("source_rank", 99), not c["url"].startswith("https://")))
    seen = {x["url"] for x in trial}
    for c in matches:
        if c["url"] not in seen and not looks_credential_style(c["url"]):
            trial.append(c)
            seen.add(c["url"])

    for cand in trial[:MAX_REPLACEMENT_CANDIDATES]:
        url = cand["url"]
        owner_key = used_urls.get(url)
        if owner_key and owner_key != catalog_key(entry):
            print(f"  rejecting duplicate URL already used by {owner_key}: {url}")
            continue
        print(f"  validating exact match from {cand.get('source', 'source')}: {url}")
        if is_working_hls(url, attempts=1):
            return url, cand.get("source", "unknown")
    return None, None


def replace_entry_block(text, entry, new_info_line, new_url_line):
    old_block = entry["raw_info"] + "\n" + entry["raw_url"]
    return text.replace(old_block, new_info_line + "\n" + new_url_line, 1)


def load_state(path):
    if not path.exists():
        return {"version": 3, "cursor": 0, "channels": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state is not an object")
        data.setdefault("version", 3)
        data.setdefault("cursor", 0)
        data.setdefault("channels", {})
        return data
    except Exception as e:
        print(f"warning: invalid state file; starting fresh: {e}")
        return {"version": 3, "cursor": 0, "channels": {}}


def channel_state(state, entry):
    key = catalog_key(entry)
    rec = state["channels"].setdefault(key, {})
    rec.setdefault("name", entry["display"])
    rec.setdefault("group", entry.get("group", ""))
    rec.setdefault("failures", 0)
    rec.setdefault("recovery_successes", 0)
    rec.setdefault("pending_url", "")
    rec.setdefault("last_working_url", "")
    rec.setdefault("status", "inactive" if entry["inactive"] else "active")
    return key, rec


def select_rotation(entries, cursor):
    total = len(entries)
    if not total:
        return [], 0
    cursor %= total
    count = min(BATCH_SIZE, total)
    indexes = [(cursor + i) % total for i in range(count)]
    return indexes, (cursor + count) % total


def parallel_health(entries, indexes):
    results = {}
    active_indexes = [i for i in indexes if not entries[i]["inactive"]]
    with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as pool:
        futures = {pool.submit(is_working_hls, entries[i]["url"], 2): i for i in active_indexes}
        for future in as_completed(futures):
            i = futures[future]
            try:
                results[i] = bool(future.result())
            except Exception:
                results[i] = False
    return results


def write_report(report_path, entries, state, processed, imported, replacements, inactivated, reactivated):
    active = sum(1 for e in entries if not e["inactive"])
    inactive = len(entries) - active
    lines = [
        "# IPTV V008 Health", "",
        f"- Updated: {now_iso()}",
        f"- Total channels: {len(entries)}",
        f"- Active: {active}",
        f"- Inactive: {inactive}",
        f"- Checked this run: {processed}",
        f"- Imported from V007: {imported}",
        f"- Replaced: {len(replacements)}",
        f"- Inactivated: {len(inactivated)}",
        f"- Reactivated: {len(reactivated)}",
        f"- Next cursor: {state.get('cursor', 0) + 1 if entries else 0}",
        "", "## Inactive channels", "",
    ]
    found = False
    for e in entries:
        if e["inactive"]:
            found = True
            rec = state["channels"].get(catalog_key(e), {})
            lines.append(f"- {e['display']} — failures={rec.get('failures', 0)}, recovery={rec.get('recovery_successes', 0)}/{RECOVERY_SUCCESSES_REQUIRED}")
    if not found:
        lines.append("- None")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def heal(playlist_path, state_dir=None):
    p = Path(playlist_path)
    state_root = Path(state_dir) if state_dir else p.parent
    state_root.mkdir(parents=True, exist_ok=True)
    state_path = state_root / "IPTV-V008-health.json"
    report_path = state_root / "IPTV-V008-health.md"

    imported = sync_legacy_catalog(p)
    original = p.read_text(encoding="utf-8")
    entries = parse_playlist(original)
    state = load_state(state_path)
    total = len(entries)
    indexes, next_cursor = select_rotation(entries, int(state.get("cursor", 0)))
    health = parallel_health(entries, indexes)
    candidates = None

    used_urls = {}
    for e in entries:
        if e["url"]:
            used_urls.setdefault(e["url"], catalog_key(e))

    replacements, inactivated, reactivated = [], [], []

    for idx in indexes:
        entry = entries[idx]
        prefix = f"[{idx + 1}/{total}]"
        key, rec = channel_state(state, entry)
        rec["last_checked"] = now_iso()

        if entry["inactive"]:
            print(f"{prefix} INACTIVE {entry['display']}: looking for recovery")
            if candidates is None:
                candidates = load_candidates()
            new_url, source = find_replacement(entry, candidates, used_urls, rec.get("pending_url") or None)
            if new_url:
                if rec.get("pending_url") == new_url:
                    rec["recovery_successes"] = int(rec.get("recovery_successes", 0)) + 1
                else:
                    rec["pending_url"] = new_url
                    rec["recovery_successes"] = 1
                rec["replacement_source"] = source
                print(f"  recovery validation {rec['recovery_successes']}/{RECOVERY_SUCCESSES_REQUIRED}")
                if rec["recovery_successes"] >= RECOVERY_SUCCESSES_REQUIRED:
                    original = replace_entry_block(original, entry, entry["info"], new_url)
                    used_urls.pop(entry["url"], None)
                    used_urls[new_url] = key
                    rec.update({"status": "active", "failures": 0, "recovery_successes": 0, "pending_url": "", "last_working_url": new_url})
                    reactivated.append((entry["display"], new_url))
                    print(f"  REACTIVATED -> {new_url}")
            else:
                rec["recovery_successes"] = 0
                rec["pending_url"] = ""
                print("  still inactive; no verified exact replacement")
            continue

        print(f"{prefix} checking {entry['display']}: {entry['url']}")
        if health.get(idx, False):
            rec.update({"status": "active", "failures": 0, "recovery_successes": 0, "pending_url": "", "last_working_url": entry["url"]})
            print("  OK (manifest + media segment verified)")
            continue

        rec["failures"] = int(rec.get("failures", 0)) + 1
        failures = rec["failures"]
        print(f"  FAILED ({failures}/{FAILURES_BEFORE_ACTION})")

        if failures < FAILURES_BEFORE_ACTION:
            print("  no replacement search yet; waiting for consecutive-failure threshold")
            continue

        if candidates is None:
            candidates = load_candidates()
        new_url, source = find_replacement(entry, candidates, used_urls)
        if new_url:
            original = replace_entry_block(original, entry, entry["info"], new_url)
            used_urls.pop(entry["url"], None)
            used_urls[new_url] = key
            rec.update({"status": "active", "failures": 0, "last_working_url": new_url, "replacement_source": source, "pending_url": "", "recovery_successes": 0})
            replacements.append((entry["display"], entry["url"], new_url))
            print(f"  REPLACED after {FAILURES_BEFORE_ACTION} consecutive failures -> {new_url}")
        else:
            original = replace_entry_block(original, entry, INACTIVE_INFO + entry["info"], INACTIVE_URL + entry["url"])
            rec.update({"status": "inactive", "pending_url": "", "recovery_successes": 0})
            inactivated.append((entry["display"], entry["url"]))
            print("  INACTIVATED; no verified exact replacement")

    state["version"] = 3
    state["cursor"] = next_cursor
    state["last_run"] = now_iso()
    state["last_run_checked"] = len(indexes)
    state["total_channels"] = total
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if imported or replacements or inactivated or reactivated:
        p.write_text(original, encoding="utf-8")

    latest_entries = parse_playlist(original)
    write_report(report_path, latest_entries, state, len(indexes), imported, replacements, inactivated, reactivated)
    print(f"run summary: checked={len(indexes)}, next_cursor={next_cursor + 1 if total else 0}, replaced={len(replacements)}, inactivated={len(inactivated)}, reactivated={len(reactivated)}")


if __name__ == "__main__":
    playlist = sys.argv[1] if len(sys.argv) > 1 else "IPTV-V008.m3u"
    state_dir = sys.argv[2] if len(sys.argv) > 2 else None
    heal(playlist, state_dir)
