"""Refresh the generated channel status table in the repository README."""

import json
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


def last_updated(record):
    value = record.get("last_checked")
    if not value:
        return "—"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(DHAKA).strftime(
        "%d %b %Y, %I:%M %p"
    )


def refresh(readme_path, playlist_path, state_path):
    entries = parse_playlist(playlist_path.read_text(encoding="utf-8"))
    if not entries:
        raise ValueError("Cannot generate a channel table from an empty playlist.")
    records = json.loads(state_path.read_text(encoding="utf-8"))["channels"]
    active = sum(not entry["inactive"] for entry in entries)
    rows = [
        START,
        "## Channel Status",
        "",
        f"**Total: {len(entries)} | Active: {active} | Inactive: {len(entries) - active}**",
        "",
        "Active means enabled in the playlist. Last updated shows the latest recorded health check in Dhaka time.",
        "",
        "| # | Channel | Category | Status | Last updated |",
        "| ---: | --- | --- | --- | --- |",
    ]
    for number, entry in enumerate(entries, start=1):
        name = table_cell(entry["display"])
        category = table_cell(entry.get("group") or "—")
        status = "🔴 Inactive" if entry["inactive"] else "🟢 Active"
        updated = last_updated(records.get(catalog_key(entry), {}))
        rows.append(f"| {number} | {name} | {category} | {status} | {updated} |")
    rows.extend(["", "[Latest health report](../../blob/iptv-state/IPTV-V008-health.md)", END])
    readme = readme_path.read_text(encoding="utf-8")
    before, remainder = readme.split(START, 1)
    _, after = remainder.split(END, 1)
    readme_path.write_text(before + "\n".join(rows) + after, encoding="utf-8")


if __name__ == "__main__":
    refresh(*(Path(argument) for argument in sys.argv[1:]))
