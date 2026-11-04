# Kidoo Community native API

The Community server provides local and Jellyfin media to the TV client. JSON requests use UTF-8 and require a parent session or an authorized paired-device credential as applicable. `GET /api/tv/info` reports the server capabilities without exposing profile data. Existing `apiVersion: 1` identifies the server protocol; it does **not** imply compatibility with older clients that relied on removed online-video endpoints. Update the server and Community client together.

## Profiles and home

- `GET /api/profiles` lists the profiles visible to the caller. `POST /api/profiles` creates a profile when authorized.
- `GET /api/tv/home/{profileId}?view=family` returns approved local and Jellyfin items, resume rows, recommendations and Movies, Series and More sections. Series include their eligible episodes. A `view=parent` request requires parent authority on a paired device.
- Local keys have the `local:` prefix, Jellyfin keys use `jellyfin:`, and grouped shows use `series:`. Profile approval and current availability are checked again when playback starts.
- Parents can list local files with `GET /api/local/catalogue` and save shared title, description, type, series, episode and topic metadata with `POST /api/local/metadata`. Metadata does not grant approval; child devices cannot access either endpoint.
- `POST /api/tv/favourite` changes a supported, approved title's favourite state. `POST /api/tv/local-preferences` updates local favourites and hidden items for a profile.

## Playback and viewing time

- `GET /media/{id}` serves a local file with single-range HTTP support. `GET /api/tv/artwork/{id}` serves eligible local artwork. Child requests require the current profile's approval.
- `POST /api/progress` records local resume position and completion in seconds. Jellyfin remains the source of truth for Jellyfin playback state.
- `GET /api/jellyfin/catalogue/{profileId}` and `/api/jellyfin/artwork/{profileId}/{itemId}` expose authorized metadata and artwork. `POST /api/jellyfin/start` opens an approved playback session and returns a Kidoo stream URL and the current position. `POST /api/jellyfin/event` reports sequenced progress or stop events. Conflicting progress can return 409. Stream requests and transcoding resources recheck authorization.
- `POST /api/activity/start` and `/api/activity/event` report supported local and Jellyfin playback for the dashboard and viewing limits. `GET /api/viewing-limits/status/{profileId}/{activityId}` checks the active allowance. Limits are approximate and depend on client reporting.

## Your TV and parental controls

- `GET /api/your-tv/{profileId}` lists eligible channels; `POST /api/your-tv/start` and `/api/your-tv/next` select from each profile's persistent queue. Channel selection never grants approval. The server rechecks eligibility and returns an up-next title when one exists.
- Parent endpoints manage per-profile approvals, screen-time limits, devices, channel definitions and review notes. Paired child requests cannot use parent routes or access another profile. Protected mode requires direct HTTPS, a parent administrator and PIN, and a revocable device credential. The shared-password HTTP mode is only for trusted evaluation and is not a child security boundary.

## Removed functionality

Community does not provide the former online discovery, channel review, import, temporary download or prefetch APIs. Those paths return 404 for an authenticated parent; an unprivileged caller may receive 401 or 403 before routing. Clients must not open a source website as a fallback.
