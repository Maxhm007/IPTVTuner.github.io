# Changelog

## v0.9.0 - 24-Sep-2026 - 10:10 AM - Native Android ExoPlayer client

- Added a native Android and Android TV client powered by AndroidX Media3 ExoPlayer.
- Added direct HTTP and HTTPS HLS playback with Android cleartext networking enabled.
- Added playlist parsing, channel filtering, responsive phone/TV layout, playback status, and lifecycle cleanup.
- Added unit tests and an isolated GitHub Actions APK build workflow.
- Updated the Android workflow to Node 24-compatible action releases after the initial setup action failed before compilation.
- Verified GitHub Actions run `35954204621`: Android SDK setup, unit tests, debug compilation, and APK artifact upload all passed.
- Preserved the existing IPTV self-healing workflow and playlist branches.
