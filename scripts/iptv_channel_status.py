"""Refresh the generated channel status table in the repository README."""

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from iptv_self_heal import catalog_key, parse_playlist


START = "<!-- CHANNEL-STATUS:START -->"
END = "<!-- CHANNEL-STATUS:END -->"
DHAKA = ZoneInfo("Asia/Dhaka")


def table_cell(value):
    return str(value).replace("|", r"\|").replace("\n", " ")


def playlist_url_dates(playlist_path):
    """Track when a channel/URL pair first appeared in its current stint."""
    def git(*arguments):
        return subprocess.check_output(
            ["git", "-C", str(playlist_path.parent), *arguments], text=True, encoding="utf-8"
        )

    if git("rev-parse", "--is-shallow-repository").strip() == "true":
        raise ValueError("Full playlist history is required for link update dates.")
    filename = playlist_path.name
    commits = git("log", "--first-parent", "--reverse", "--format=%H %cI", "--", filename)
    dates = {}
    for line in commits.splitlines():
        commit, timestamp = line.split(maxsplit=1)
        files = git("ls-tree", "--name-only", commit, "--", filename)
        if not files.strip():
            dates = {}
            continue
        entries = parse_playlist(git("show", f"{commit}:./{filename}"))
        current = {(catalog_key(entry), entry["url"]) for entry in entries}
        dates = {pair: dates.get(pair, timestamp) for pair in current}
    return dates


def format_date(value):
    if not value:
        return "—"
    return datetime.fromisoformat(value).astimezone(DHAKA).strftime("%d %b %Y, %I:%M %p")


def refresh(readme_path, playlist_path):
    entries = parse_playlist(playlist_path.read_text(encoding="utf-8"))
    if not entries:
        raise ValueError("Cannot generate a channel table from an empty playlist.")
    dates = playlist_url_dates(playlist_path)
    active = sum(not entry["inactive"] for entry in entries)
    rows = [
        START,
        "## Channel Status",
        "",
        f"**Total: {len(entries)} | Active: {active} | Inactive: {len(entries) - active}**",
        "",
        "Active means enabled in the playlist. Last updated shows when the current stream URL was added or changed in the playlist, in Dhaka time.",
        "",
        "| # | Channel | Category | Status | Last updated |",
        "| ---: | --- | --- | --- | --- |",
    ]
    for number, entry in enumerate(entries, start=1):
        name = table_cell(entry["display"])
        category = table_cell(entry.get("group") or "—")
        status = "🔴 Inactive" if entry["inactive"] else "🟢 Active"
        updated = format_date(dates.get((catalog_key(entry), entry["url"])))
        rows.append(f"| {number} | {name} | {category} | {status} | {updated} |")
    rows.extend(["", "[Latest health report](../../blob/iptv-state/IPTV-V008-health.md)", END])
    readme = readme_path.read_text(encoding="utf-8")
    before, remainder = readme.split(START, 1)
    _, after = remainder.split(END, 1)
    readme_path.write_text(before + "\n".join(rows) + after, encoding="utf-8")


if __name__ == "__main__":
    refresh(*(Path(argument) for argument in sys.argv[1:3]))
