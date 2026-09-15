# IPTV Tuner — Self-Healing IPTV

An automated, self-maintaining IPTV playlist system designed to keep channel streams healthy and usable with minimal manual maintenance.

## Current Version

**IPTV-V008**

Live playlist:

`https://raw.githubusercontent.com/Maxhm007/IPTVTuner.github.io/gh-pages/IPTV-V008.m3u`

## How It Works

IPTV Tuner continuously monitors the V008 playlist using GitHub Actions.

The self-healing system:

- Runs automatically every 10 minutes
- Tests IPTV stream availability
- Keeps working stream URLs active
- Tracks unhealthy or unavailable channels
- Keeps dead channels inactive while a working replacement is unavailable
- Can replace failed stream URLs when a valid replacement is found
- Removes duplicate channels automatically
- Maintains persistent channel health history
- Creates and synchronizes GitHub issues for inactive channels
- Continues checking channels cyclically instead of stopping at the end of the playlist
- Commits playlist repairs automatically

## Architecture

| Branch | Purpose |
| --- | --- |
| `main` | Automation scripts, configuration and GitHub Actions |
| `gh-pages` | Published `IPTV-V008.m3u` playlist |
| `iptv-state` | Persistent channel health state and reports |

## Self-Healing Cycle

Every scheduled run performs the following maintenance cycle:

1. Check out the automation code from `main`
2. Check out the live playlist from `gh-pages`
3. Load persistent healer state from `iptv-state`
4. Remove duplicate channels
5. Test streams and update channel health state
6. Synchronize inactive-channel GitHub issues
7. Commit any repaired playlist changes to `gh-pages`
8. Save updated health state to `iptv-state`
9. Continue from the next channel on the following cycle

The GitHub Actions scheduler runs every **10 minutes**.

## Channel States

- **Active** — the current stream is working and remains in the live playlist
- **Inactive** — the stream is unavailable and should not be treated as a working channel
- **Healing** — the system continues checking for recovery or a valid replacement stream
- **Recovered** — a previously failed channel becomes usable again and can return to active service

A dead channel is not permanently removed simply because its current URL fails. The system can keep the channel identity and continue trying to recover it until a working source is available.

## Automation Files

Key components include:

- `.github/workflows/iptv-self-heal.yml`
- `scripts/iptv_self_heal.py`
- `scripts/iptv_self_heal_runner.py`
- `scripts/iptv_issue_sync.py`
- `scripts/iptv_dedupe.py`
- `config/iptv-approved-free.json`

## Project Goal

Build an increasingly autonomous IPTV playlist that can detect stream failures, temporarily deactivate unavailable channels, find and validate replacement streams, recover channels automatically, and maintain the playlist continuously with minimal manual intervention.

## Roadmap

Planned improvements include:

- Better automatic replacement-source discovery
- Faster recovery of inactive channels
- Source-quality scoring before replacement
- Protection against repeatedly selecting the same failed URL
- Per-channel retry and cooldown logic
- Automatic channel statistics and health summaries
- Live project status in the README
- Additional channels while preserving the V007 channel catalogue
- Stronger validation before a replacement URL is published

## Suggested Repository Topics

`iptv` · `m3u` · `playlist` · `self-healing` · `stream-checker` · `github-actions` · `automation` · `python` · `live-tv`

---

**IPTV Tuner — Self-Healing IPTV**  
Current generation: **V008**
