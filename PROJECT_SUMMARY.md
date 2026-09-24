# IPTV Tuner Project Summary

## Purpose

IPTV Tuner maintains the published IPTV-V008 playlist and provides clients that can play its active channels.

## Architecture

- `scripts/` and `.github/workflows/iptv-self-heal.yml` maintain the playlist automatically.
- `gh-pages` publishes `IPTV-V008.m3u`.
- `android-app/` is a native Android and Android TV client using AndroidX Media3 ExoPlayer.
- The Android client reads the published playlist directly and supports both HTTP and HTTPS media sources.

## Verified State

- The existing Python/GitHub Actions self-healing automation remains unchanged.
- Android project source and hosted-runner build workflow are present.
- GitHub Actions run `35954204621` passed unit tests and built the debug APK on Android API 36.
- Artifact `xiptv-debug-apk` was uploaded successfully and is retained by GitHub Actions until 23-Dec-2026.
- Local APK compilation remains unavailable until a JDK and Android SDK are installed; the verified hosted workflow builds without changing the local machine.
