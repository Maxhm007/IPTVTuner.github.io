#!/usr/bin/env python3
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (IPTVTuner-SelfHeal/2.0)"
TIMEOUT = 6
SOURCE_TIMEOUT = 12
MAX_REPLACEMENT_CANDIDATES = 3
FAILURES_BEFORE_INACTIVE = 3
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


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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
        return f"id:{tvg}"
    return f"name:{normalize(entry.get('group'))}:{canonical_name(entry.get('name') or entry.get('display'))}"


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
    trial = []
    if pending_url and pending_url != entry["url"]:
        trial.append({
            "url": pending_url,
            "display": entry["display"],
            "source": "pending-recovery",
            "source_rank": 0,
        })

    matches = [
        c for c in candidates
        if c["url"] != entry["url"] and same_channel(entry, c)
    ]
    matches.sort(key=lambda c: (c.get("source_rank", 99), not c["url"].startswith("https://")))

    seen = {x["url"] for x in trial}
    for c in matches:
        if c["url"] not in seen:
            trial.append(c)
            seen.add(c["url"])

    for cand in trial[:MAX_REPLACEMENT_CANDIDATES]:
        url = cand["url"]
        owner_key = used_urls.get(url)
        if owner_key and owner_key != catalog_key(entry):
            print(f"  rejecting duplicate URL already used by {owner_key}: {url}")
            continue
        print(f"  trying exact match from {cand.get('source', 'source')}: {url}")
        if is_working_hls(url):
            return url, cand.get("source", "unknown")
    return None, None


def replace_entry_block(text, entry, new_info_line, new_url_line):
    old_block = entry["raw_info"] + "\n" + entry["raw_url"]
    new_block = new_info_line + "\n" + new_url_line
    return text.replace(old_block, new_block, 1)


def load_state(path):
    if not path.exists():
        return {"version": 2, "cursor": 0, "channels": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state is not an object")
        data.setdefault("version", 2)
        data.setdefault("cursor", 0)
        data.setdefault("channels", {})
        return data
    except Exception as e:
        print(f"warning: invalid state file; starting fresh: {e}")
        return {"version": 2, "cursor": 0, "channels": {}}


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
    if total == 0:
        return [], 0
    cursor %= total
    count = min(BATCH_SIZE, total)
    indexes = [(cursor + i) % total for i in range(count)]
    next_cursor = (cursor + count) % total
    return indexes, next_cursor


def parallel_health(entries, indexes):
    results = {}
    active_indexes = [i for i in indexes if not entries[i]["inactive"]]
    with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as pool:
        futures = {pool.submit(is_working_hls, entries[i]["url"]): i for i in active_indexes}
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
        "# IPTV V008 Health",
        "",
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
        "",
        "## Inactive channels",
        "",
    ]
    any_inactive = False
    for e in entries:
        if not e["inactive"]:
            continue
        any_inactive = True
        key = catalog_key(e)
        rec = state["channels"].get(key, {})
        lines.append(
            f"- {e['display']} — failures={rec.get('failures', 0)}, "
            f"recovery={rec.get('recovery_successes', 0)}/{RECOVERY_SUCCESSES_REQUIRED}"
        )
    if not any_inactive:
        lines.append("- None")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def heal(path):
    p = Path(path)
    state_path = p.with_name("IPTV-V008-health.json")
    report_path = p.with_name("IPTV-V008-health.md")

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

    replacements = []
    inactivated = []
    reactivated = []

    for idx in indexes:
        entry = entries[idx]
        prefix = f"[{idx + 1}/{total}]"
        key, rec = channel_state(state, entry)
        rec["last_checked"] = now_iso()

        if entry["inactive"]:
            print(f"{prefix} INACTIVE {entry['display']}: looking for recovery")
            if candidates is None:
                candidates = load_candidates()

            new_url, source = find_replacement(
                entry,
                candidates,
                used_urls,
                pending_url=rec.get("pending_url") or None,
            )
            if new_url:
                if rec.get("pending_url") == new_url:
                    rec["recovery_successes"] = int(rec.get("recovery_successes", 0)) + 1
                else:
                    rec["pending_url"] = new_url
                    rec["recovery_successes"] = 1
                rec["replacement_source"] = source
                print(f"  recovery success {rec['recovery_successes']}/{RECOVERY_SUCCESSES_REQUIRED}: {new_url}")

                if rec["recovery_successes"] >= RECOVERY_SUCCESSES_REQUIRED:
                    original = replace_entry_block(original, entry, entry["info"], new_url)
                    used_urls.pop(entry["url"], None)
                    used_urls[new_url] = key
                    rec["status"] = "active"
                    rec["failures"] = 0
                    rec["recovery_successes"] = 0
                    rec["pending_url"] = ""
                    rec["last_working_url"] = new_url
                    rec["last_reactivated"] = now_iso()
                    reactivated.append((entry["display"], new_url))
                    print(f"  REACTIVATED -> {new_url}")
            else:
                rec["recovery_successes"] = 0
                rec["pending_url"] = ""
                print("  still inactive; no exact verified replacement")
            continue

        ok = health.get(idx, False)
        print(f"{prefix} checking {entry['display']}: {entry['url']}")
        if ok:
            rec["status"] = "active"
            rec["failures"] = 0
            rec["recovery_successes"] = 0
            rec["pending_url"] = ""
            rec["last_working_url"] = entry["url"]
            rec["last_success"] = now_iso()
            print("  OK")
            continue

        rec["failures"] = int(rec.get("failures", 0)) + 1
        rec["last_failure"] = now_iso()
        print(f"  FAILED ({rec['failures']}/{FAILURES_BEFORE_INACTIVE}); searching exact replacement")

        if candidates is None:
            candidates = load_candidates()
        new_url, source = find_replacement(entry, candidates, used_urls)

        if new_url:
            original = replace_entry_block(original, entry, entry["info"], new_url)
            used_urls.pop(entry["url"], None)
            used_urls[new_url] = key
            rec["status"] = "active"
            rec["failures"] = 0
            rec["last_working_url"] = new_url
            rec["replacement_source"] = source
            rec["last_replaced"] = now_iso()
            replacements.append((entry["display"], entry["url"], new_url))
            print(f"  REPLACED -> {new_url}")
        elif rec["failures"] >= FAILURES_BEFORE_INACTIVE:
            inactive_info = INACTIVE_INFO + entry["info"]
            inactive_url = INACTIVE_URL + entry["url"]
            original = replace_entry_block(original, entry, inactive_info, inactive_url)
            rec["status"] = "inactive"
            rec["last_inactivated"] = now_iso()
            inactivated.append((entry["display"], entry["url"]))
            print("  INACTIVATED after consecutive failures")
        else:
            print("  kept active; waiting for failure threshold")

    state["cursor"] = next_cursor
    state["last_run"] = now_iso()
    state["last_run_checked"] = len(indexes)
    state["total_channels"] = total

    if original != p.read_text(encoding="utf-8"):
        p.write_text(original, encoding="utf-8")

    final_entries = parse_playlist(p.read_text(encoding="utf-8"))
    write_report(
        report_path,
        final_entries,
        state,
        len(indexes),
        imported,
        replacements,
        inactivated,
        reactivated,
    )
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        "run summary: "
        f"checked={len(indexes)}, next_cursor={next_cursor + 1 if total else 0}, "
        f"replaced={len(replacements)}, inactivated={len(inactivated)}, "
        f"reactivated={len(reactivated)}"
    )


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "IPTV-V008.m3u"
    heal(target)
