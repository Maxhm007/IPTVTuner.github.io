#!/usr/bin/env python3
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

USER_AGENT = "Mozilla/5.0 (IPTVTuner-SelfHeal/4.0)"
TIMEOUT = 7
SOURCE_TIMEOUT = 12
CHECK_WORKERS = 8
RECOVERY_SUCCESSES_REQUIRED = 2
MAX_REPLACEMENT_CANDIDATES = 3

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

RESTRICTED = (
    "hbo", "cinemax", "showtime", "disney channel", "disney jr", "disney xd",
    "espn", "sony six", "sony ten", "star sports", "supersport", "wwe network",
    "star movies", "sony pix", "fox movies", "fox family movies",
)


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(s):
    s = (s or "").lower()
    s = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", s)
    s = re.sub(r"\b(hd|fhd|sd|uhd|4k|1080p|720p|576p|480p|360p|tv|channel|live)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def canonical_name(s):
    n = normalize(s)
    return ALIASES.get(n, n)


def base_tvg_id(value):
    raw = (value or "").strip().lower().split("@", 1)[0]
    compact = re.sub(r"[^a-z0-9]+", "", raw)
    if not raw or compact in {"none", "null", "na", "notvgid"} or compact.startswith("notvgid"):
        return ""
    return raw


def credential_style(url):
    return bool(re.search(r"/live/[^/]+/[^/]+/[^/?]+", url or "", re.I))


def is_restricted(entry):
    name = canonical_name(entry.get("name") or entry.get("display"))
    return any(x in name for x in RESTRICTED)


def request_bytes(url, max_bytes=512 * 1024, range_request=False, timeout=TIMEOUT):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.apple.mpegurl, application/x-mpegURL, video/*, audio/*, */*",
    }
    if range_request:
        headers["Range"] = f"bytes=0-{max_bytes - 1}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read(max_bytes), getattr(response, "url", url)


def fetch_text(url, timeout=TIMEOUT):
    raw, final_url = request_bytes(url, timeout=timeout)
    return raw.decode("utf-8", errors="replace"), final_url


def first_uri_after(text, tag):
    lines = [line.strip() for line in text.splitlines()]
    for i, line in enumerate(lines):
        if line.startswith(tag):
            for nxt in lines[i + 1:]:
                if not nxt:
                    continue
                if not nxt.startswith("#"):
                    return nxt
                break
    return None


def probe_manifest_once(url):
    try:
        text, final_url = fetch_text(url)
        if "#EXTM3U" not in text.lstrip()[:2048]:
            return "dead", "not-hls", None, None
        return "reachable", "manifest-ok", text, final_url
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 410}:
            return "dead", f"http-{exc.code}", None, None
        return "uncertain", f"http-{exc.code}", None, None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return "uncertain", type(exc).__name__, None, None
    except Exception as exc:
        return "uncertain", type(exc).__name__, None, None


def validate_segment(text, base_url):
    try:
        variant = first_uri_after(text, "#EXT-X-STREAM-INF")
        if variant:
            text, base_url = fetch_text(urljoin(base_url, variant))
            if "#EXTM3U" not in text.lstrip()[:2048]:
                return False
        segment = first_uri_after(text, "#EXTINF")
        if not segment and "#EXT-X-TARGETDURATION" in text:
            segment = next((x.strip() for x in text.splitlines() if x.strip() and not x.strip().startswith("#")), None)
        if not segment:
            return False
        raw, _ = request_bytes(urljoin(base_url, segment), max_bytes=2048, range_request=True)
        return bool(raw)
    except Exception:
        return False


def probe_stream(url, attempts=2):
    dead_votes = 0
    last_reason = "unknown"
    best = "uncertain"
    for attempt in range(attempts):
        status, reason, text, base = probe_manifest_once(url)
        last_reason = reason
        if status == "dead":
            dead_votes += 1
        elif status == "reachable":
            best = "reachable"
            if validate_segment(text, base):
                return "healthy", "manifest+segment"
        if attempt + 1 < attempts:
            time.sleep(0.3)
    if dead_votes == attempts:
        return "dead", last_reason
    return best, last_reason


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
        display = info.split(",", 1)[1].strip() if "," in info else attrs.get("tvg-name", "")
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
            "name": attrs.get("tvg-name") or display,
            "display": display,
            "tvg_id": attrs.get("tvg-id", ""),
            "group": attrs.get("group-title", ""),
            "inactive": inactive,
            "raw_info": raw_info,
            "raw_url": lines[j],
        })
        i = j + 1
    return out


def catalog_key(entry):
    tid = base_tvg_id(entry.get("tvg_id"))
    if tid:
        return f"id:{tid}"
    return f"name:{normalize(entry.get('group'))}:{canonical_name(entry.get('name') or entry.get('display'))}"


def same_channel(a, b):
    a_id = base_tvg_id(a.get("tvg_id"))
    b_id = base_tvg_id(b.get("tvg_id"))
    if a_id and b_id:
        return a_id == b_id
    a_name = canonical_name(a.get("name") or a.get("display"))
    b_name = canonical_name(b.get("name") or b.get("display"))
    if not a_name or a_name != b_name:
        return False
    a_group = normalize(a.get("group"))
    b_group = normalize(b.get("group"))
    return not (a_group and b_group and a_group != b_group)


def load_candidates():
    candidates = []
    for source_name, src, rank in SOURCES:
        try:
            text, _ = fetch_text(src, SOURCE_TIMEOUT)
            parsed = parse_playlist(text)
            for item in parsed:
                item["source"] = source_name
                item["source_rank"] = rank
            candidates.extend(parsed)
            print(f"loaded {source_name}: {len(parsed)}")
        except Exception as exc:
            print(f"warning source {source_name}: {exc}")
    return candidates


def find_replacement(entry, candidates, used_urls, pending=None):
    if is_restricted(entry):
        print("  replacement disabled for restricted/subscription channel")
        return None, None
    trial = []
    if pending and pending != entry["url"]:
        trial.append({"url": pending, "source": "pending-recovery", "source_rank": 0})
    matches = [
        c for c in candidates
        if c["url"] != entry["url"]
        and same_channel(entry, c)
        and not credential_style(c["url"])
    ]
    matches.sort(key=lambda c: (c.get("source_rank", 99), not c["url"].startswith("https://")))
    seen = {x["url"] for x in trial}
    for candidate in matches:
        if candidate["url"] not in seen:
            trial.append(candidate)
            seen.add(candidate["url"])

    for candidate in trial[:MAX_REPLACEMENT_CANDIDATES]:
        url = candidate["url"]
        owner = used_urls.get(url)
        if owner and owner != catalog_key(entry):
            continue
        status, _ = probe_stream(url, attempts=1)
        print(f"  candidate {candidate.get('source', 'source')}: {status} {url}")
        if status == "healthy":
            return url, candidate.get("source", "unknown")
    return None, None


def replace_block(text, entry, info_line, url_line):
    old = entry["raw_info"] + "\n" + entry["raw_url"]
    new = info_line + "\n" + url_line
    return text.replace(old, new, 1)


def load_state(path):
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    state.setdefault("channels", {})
    state["version"] = 4
    return state


def state_for(state, entry):
    key = catalog_key(entry)
    rec = state["channels"].setdefault(key, {})
    rec.setdefault("name", entry["display"])
    rec.setdefault("group", entry.get("group", ""))
    rec.setdefault("status", "inactive" if entry["inactive"] else "active")
    rec.setdefault("recovery_successes", 0)
    rec.setdefault("pending_url", "")
    return key, rec


def write_report(path, entries, state, stats):
    active = sum(1 for e in entries if not e["inactive"])
    uncertain = sum(1 for r in state["channels"].values() if r.get("probe_status") in {"uncertain", "reachable"} and r.get("status") == "active")
    lines = [
        "# IPTV V008 Health",
        "",
        f"- Updated: {now_iso()}",
        f"- Total channels: {len(entries)}",
        f"- Active: {active}",
        f"- Inactive (confirmed dead): {len(entries) - active}",
        f"- Active but uncertain from GitHub runner: {uncertain}",
        f"- Checked this run: {stats['checked']}",
        f"- Inactivated: {stats['inactivated']}",
        f"- Reactivated: {stats['reactivated']}",
        f"- Replaced: {stats['replaced']}",
        "",
        "## Inactive channels",
        "",
    ]
    dead = [e for e in entries if e["inactive"]]
    if not dead:
        lines.append("- None")
    else:
        for e in dead:
            rec = state["channels"].get(catalog_key(e), {})
            lines.append(f"- {e['display']} — reason={rec.get('probe_reason', 'confirmed-dead')}, recovery={rec.get('recovery_successes', 0)}/{RECOVERY_SUCCESSES_REQUIRED}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def heal(playlist, state_dir=None):
    playlist_path = Path(playlist)
    root = Path(state_dir) if state_dir else playlist_path.parent
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "IPTV-V008-health.json"
    report_path = root / "IPTV-V008-health.md"

    original = playlist_path.read_text(encoding="utf-8")
    entries = parse_playlist(original)
    state = load_state(state_path)
    valid_keys = {catalog_key(e) for e in entries}
    state["channels"] = {k: v for k, v in state["channels"].items() if k in valid_keys}

    used_urls = {}
    for e in entries:
        used_urls.setdefault(e["url"], catalog_key(e))

    active_indexes = [i for i, e in enumerate(entries) if not e["inactive"]]
    health = {}
    with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as pool:
        futures = {pool.submit(probe_stream, entries[i]["url"], 2): i for i in active_indexes}
        for future in as_completed(futures):
            i = futures[future]
            try:
                health[i] = future.result()
            except Exception as exc:
                health[i] = ("uncertain", type(exc).__name__)

    candidates = None
    stats = {"checked": len(entries), "inactivated": 0, "reactivated": 0, "replaced": 0}

    for idx, entry in enumerate(entries):
        key, rec = state_for(state, entry)
        rec["last_checked"] = now_iso()

        if entry["inactive"]:
            original_status, original_reason = probe_stream(entry["url"], attempts=2)
            rec["probe_status"] = original_status
            rec["probe_reason"] = original_reason
            recovered_url = None
            recovered_source = None
            if original_status == "healthy":
                recovered_url = entry["url"]
                recovered_source = "original-url"
            else:
                if candidates is None:
                    candidates = load_candidates()
                recovered_url, recovered_source = find_replacement(entry, candidates, used_urls, rec.get("pending_url") or None)

            if recovered_url:
                if rec.get("pending_url") == recovered_url:
                    rec["recovery_successes"] = int(rec.get("recovery_successes", 0)) + 1
                else:
                    rec["pending_url"] = recovered_url
                    rec["recovery_successes"] = 1
                rec["replacement_source"] = recovered_source
                if rec["recovery_successes"] >= RECOVERY_SUCCESSES_REQUIRED:
                    original = replace_block(original, entry, entry["info"], recovered_url)
                    rec.update({
                        "status": "active",
                        "probe_status": "healthy",
                        "probe_reason": "recovered",
                        "recovery_successes": 0,
                        "pending_url": "",
                        "last_working_url": recovered_url,
                        "last_reactivated": now_iso(),
                    })
                    used_urls[recovered_url] = key
                    stats["reactivated"] += 1
                    if recovered_url != entry["url"]:
                        stats["replaced"] += 1
                    print(f"[{idx + 1}/{len(entries)}] REACTIVATED {entry['display']} -> {recovered_url}")
            else:
                rec["recovery_successes"] = 0
                rec["pending_url"] = ""
                print(f"[{idx + 1}/{len(entries)}] inactive {entry['display']}: no verified recovery")
            continue

        status, reason = health.get(idx, ("uncertain", "missing-result"))
        rec["probe_status"] = status
        rec["probe_reason"] = reason
        if status == "healthy":
            rec.update({"status": "active", "last_working_url": entry["url"], "last_success": now_iso()})
            print(f"[{idx + 1}/{len(entries)}] OK {entry['display']}")
            continue

        if status in {"uncertain", "reachable"}:
            rec["status"] = "active"
            print(f"[{idx + 1}/{len(entries)}] UNCERTAIN {entry['display']}: {reason}; kept active")
            continue

        original = replace_block(original, entry, INACTIVE_INFO + entry["info"], INACTIVE_URL + entry["url"])
        rec.update({
            "status": "inactive",
            "last_failure": now_iso(),
            "recovery_successes": 0,
            "pending_url": "",
        })
        stats["inactivated"] += 1
        print(f"[{idx + 1}/{len(entries)}] INACTIVATED {entry['display']}: {reason}")

    latest = parse_playlist(original)
    state.update({
        "version": 4,
        "last_run": now_iso(),
        "last_run_checked": len(entries),
        "total_channels": len(entries),
    })
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(report_path, latest, state, stats)

    if original != playlist_path.read_text(encoding="utf-8"):
        playlist_path.write_text(original, encoding="utf-8")

    print(
        "run summary: "
        f"checked={stats['checked']}, inactivated={stats['inactivated']}, "
        f"reactivated={stats['reactivated']}, replaced={stats['replaced']}"
    )


if __name__ == "__main__":
    heal(
        sys.argv[1] if len(sys.argv) > 1 else "IPTV-V008.m3u",
        sys.argv[2] if len(sys.argv) > 2 else None,
    )
