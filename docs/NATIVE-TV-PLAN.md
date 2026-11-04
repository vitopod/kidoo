# Native TV implementation plan

This is the original native-client plan, retained as historical context. The Jellyfin integration was added later; see the README and NATIVE-API.md for current behavior.

The inspected baseline has a Python/SQLite server, 75 online research candidates, nine mapped channels, browser-side topic ranking, family-wide approvals, per-profile discovery settings and local playback progress. The Android app is a Java WebView shell. There is no Jellyfin adapter or transcoder.

1. Preserve the existing browser endpoints and SQLite data. Add a versioned native home API with server-owned eligibility and deterministic ranking equivalent to the browser algorithm; expose capability and honest security/source metadata. Add per-profile local favourites/hidden items and safe authenticated artwork.
2. Replace the WebView with Kotlin native TV screens: connection, profiles, cinematic Home rows, details, parent review/channels, profile preferences and settings. Keep Family/Parent a presentation distinction rather than an access-control promise. Use the repository logo, deep blue surfaces, restrained cyan focus and warm white type.
3. Use AndroidX Media3 ExoPlayer for authenticated local HTTP range playback, embedded tracks/subtitles, remote transport, resume/restart and server progress. Persist a bounded retry journal and reconnect with encrypted credentials when server sessions expire. External videos open at their source without downloads or claimed progress control.
4. Test API eligibility/ranking parity, approvals/revocation, profile isolation and restart persistence; test Kotlin URL/error/progress logic. Build a debug APK and exercise real D-pad paths, curation with exact state restoration, playback/resume with a clearly identified synthetic sample if no owned sample exists, and source handoff.
5. Deploy only Kidoo server changes with a database backup and rollback source; preserve unrelated services. Install on the authorized stick, capture actual screenshots, document supported and unverified behavior and provide the APK. No credentials, household addresses, data or build artifacts enter tracked source.

Jellyfin, disc import/transcoding, secure parent roles and online playback progress are future backend features, not part of this first native client.
