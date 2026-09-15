#!/usr/bin/env python3
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"
PREFIX = "[IPTV Inactive]"
MARKER_PREFIX = "<!-- iptv-inactive-key:"
ATTR_RE = re.compile(r'(tvg-id|tvg-name|group-title)="([^"]*)"')
INACTIVE_INFO = "#SELFHEAL-INACTIVE "
INACTIVE_URL = "#SELFHEAL-URL "
CREATE_DELAY = 0.35


def request(method, path, payload=None):
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "IPTVTuner-IssueSync/1.1",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def normalize(s):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())


def inactive_key(name, group, tvg_id):
    basis = (tvg_id or "").strip().lower()
    compact = re.sub(r"[^a-z0-9]+", "", basis)
    if not basis or compact.startswith("notvgid") or compact in {"none", "null", "na"}:
        basis = f"{normalize(group)}|{normalize(name)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def parse_inactive(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    found = {}
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s.startswith(INACTIVE_INFO + "#EXTINF:"):
            i += 1
            continue
        info = s[len(INACTIVE_INFO):]
        attrs = dict(ATTR_RE.findall(info))
        display = info.split(",", 1)[1].strip() if "," in info else attrs.get("tvg-name", "Unknown")
        name = attrs.get("tvg-name") or display
        group = attrs.get("group-title", "")
        tvg_id = attrs.get("tvg-id", "")
        url = ""
        j = i + 1
        while j < len(lines):
            q = lines[j].strip()
            if q.startswith(INACTIVE_URL):
                url = q[len(INACTIVE_URL):].strip()
                break
            if q.startswith("#EXTINF:") or q.startswith(INACTIVE_INFO + "#EXTINF:"):
                break
            j += 1
        key = inactive_key(name, group, tvg_id)
        found[key] = {
            "key": key,
            "name": display,
            "group": group,
            "tvg_id": tvg_id,
            "url": url,
        }
        i = max(i + 1, j + 1)
    return found


def list_open_tracking_issues(repo):
    tracked = {}
    page = 1
    while True:
        qs = urllib.parse.urlencode({"state": "open", "per_page": 100, "page": page})
        items = request("GET", f"/repos/{repo}/issues?{qs}") or []
        for issue in items:
            if "pull_request" in issue:
                continue
            title = issue.get("title", "")
            body = issue.get("body") or ""
            if not title.startswith(PREFIX):
                continue
            m = re.search(r"<!-- iptv-inactive-key:([0-9a-f]{20}) -->", body)
            if m:
                tracked[m.group(1)] = issue
        if len(items) < 100:
            break
        page += 1
    return tracked


def create_issue(repo, channel):
    title = f"{PREFIX} {channel['name']}"
    body = (
        f"{MARKER_PREFIX}{channel['key']} -->\n\n"
        "This issue was opened automatically by IPTV Self Heal because the channel is currently inactive.\n\n"
        f"- **Channel:** {channel['name']}\n"
        f"- **Group:** {channel['group'] or 'Unknown'}\n"
        f"- **TVG ID:** {channel['tvg_id'] or 'None'}\n"
        f"- **Last failed URL:** `{channel['url'] or 'Unknown'}`\n\n"
        "The healer checks the original stream and exact verified replacement sources every run. "
        "This issue will close automatically when a verified working stream makes the channel active again."
    )
    issue = request("POST", f"/repos/{repo}/issues", {"title": title, "body": body})
    print(f"opened issue #{issue['number']} for {channel['name']}")
    return issue


def close_issue(repo, issue):
    number = issue["number"]
    try:
        request(
            "POST",
            f"/repos/{repo}/issues/{number}/comments",
            {"body": "IPTV Self Heal found a verified working stream and reactivated this channel. Closing automatically."},
        )
    except Exception as e:
        print(f"warning: could not comment on issue #{number}: {e}")
    request("PATCH", f"/repos/{repo}/issues/{number}", {"state": "closed", "state_reason": "completed"})
    print(f"closed issue #{number}: channel active again")


def is_rate_limit_error(exc):
    return isinstance(exc, urllib.error.HTTPError) and exc.code in {403, 429}


def main():
    playlist = sys.argv[1] if len(sys.argv) > 1 else "IPTV-V008.m3u"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY is required")

    inactive = parse_inactive(playlist)
    existing = list_open_tracking_issues(repo)
    opened = 0
    closed = 0

    for key, channel in inactive.items():
        if key in existing:
            print(f"issue already open for {channel['name']}: #{existing[key]['number']}")
            continue
        try:
            existing[key] = create_issue(repo, channel)
            opened += 1
            time.sleep(CREATE_DELAY)
        except Exception as e:
            if is_rate_limit_error(e):
                print(f"warning: GitHub issue rate limit reached after opening {opened}; remaining channels will continue next run")
                break
            print(f"warning: could not open issue for {channel['name']}: {e}")

    for key, issue in list(existing.items()):
        if key in inactive:
            continue
        try:
            close_issue(repo, issue)
            closed += 1
            time.sleep(0.15)
        except Exception as e:
            print(f"warning: could not close issue #{issue.get('number')}: {e}")

    print(f"issue sync complete: inactive={len(inactive)}, opened={opened}, closed={closed}")


if __name__ == "__main__":
    main()
