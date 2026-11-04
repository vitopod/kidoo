# Kidoo setup and operations

Run commands from the repository root unless noted. Return to the [README](../README.md) for an overview. The Community migration is tested on isolated databases; test an upgrade on a copy first and do not replace a Premium installation you want to keep.

## Offline Community migration

Only use this for an earlier installation that you intend to convert to Community. The conversion preserves profiles, local and Jellyfin approvals, pairing credentials, resume positions, viewing records, limits, favourites and compatible channel settings. Online records remain archived in the database but are unavailable in Community. Online-only channels are disabled and online queue entries are removed from the converted database. Configuration, certificates, Jellyfin data and media files are not modified.

1. Stop **every** Kidoo server process and any other writer to the database. The tool cannot independently prove the service is stopped. It refuses to run if a SQLite WAL, shared-memory or journal sidecar is present; resolve this by stopping and checkpointing the database cleanly, not by deleting sidecars.
2. Create a full private backup in a new directory outside the data directory. It contains credentials and must be kept private. Separately back up your media and Jellyfin database; the Kidoo backup does not include them.

   ```sh
   python3 server/backup.py create --from /path/to/kidoo-data --to /private/path/kidoo-full-backup
   ```

3. Optionally rehearse against a copy using `python3 server/community_migration.py /path/to/source.db /private/path/rehearsal.db`. Inspect and test the result in a separate environment.
4. With the service still stopped, explicitly upgrade the intended database. The second path is a **new** database backup file and must not already exist. The tool checks database integrity and supported tables, makes and checks the backup, converts a staged database, checks the source has not changed, and atomically replaces it. It refuses existing backup or stage paths.

   ```sh
   python3 server/community_migration.py /path/to/kidoo-data/kidoo.db /private/path/kidoo-before-community.db --upgrade-stopped --server-stopped
   ```

5. Start the matching Community server and client, then verify profiles, approvals, playback, resume, limits and pairing before relying on it. Keep the original backups until you have verified the new installation. A failed operation leaves its backup and any staged file for inspection.

To roll back without overwriting the current installation, stop Kidoo and restore the full backup to a **new** directory with `python3 server/backup.py restore --from /private/path/kidoo-full-backup --to /private/path/kidoo-restored`; point the earlier server at that restored directory. The earlier database backup is also retained independently. Restoring an older backup does not include viewing changes made after it was created.

## Your TV playlists

Each channel keeps up to three eligible local or Jellyfin titles. Channel selection never grants approval; permissions and viewing limits still apply. Community does not download or prepare online videos.

## Guided setup

Download and unpack the [Kidoo Community repository](https://github.com/vitopod/kidoo) on the computer that will run Kidoo. Python 3.9 or newer is required; use a Python distribution you trust, such as [python.org](https://www.python.org/downloads/). The installer does not install packages or require administrator privileges.

**Mac:** Open Terminal, type `cd ` (including the space), drag the unpacked Kidoo folder into Terminal, and press Return. Then run `sh setup.sh`.

**Windows:** Open Command Prompt in the unpacked Kidoo folder and run `setup.cmd`. The script looks for the Python launcher or `python`. Windows local preview is an intended path but has not yet been tested on a Windows device. Protected installation on Windows is disabled until file-permission and credential handling have been verified on Windows.

**Linux:** Open a terminal in the unpacked Kidoo folder and run `sh setup.sh`.

Choose a setup option and a videos folder:

- **Local preview** runs a temporary Kidoo server on `127.0.0.1` only. Open the printed address in a browser on that computer and press Ctrl+C to stop. Preview profiles, approvals and viewing progress are discarded afterward; existing Kidoo data and video files are not modified. This mode does not pair a TV or enforce child access restrictions.
- **Protected home setup** on macOS or Linux creates a parent administrator and PIN, optionally connects an existing Jellyfin user, and can create a one-time pairing code for the TV. It creates a private start script and refuses to replace an existing Kidoo database. Choose this mode for a family TV setup.

Protected setup needs a matching HTTPS certificate and private key. You can provide existing PEM files or ask the wizard to obtain a Tailscale certificate. Tailscale must already be installed, signed in and permitted to issue certificates on the server. The wizard asks for explicit consent first: issuance publishes the device hostname and tailnet domain in public certificate transparency logs. Existing certificate files are never replaced by the setup wizard.

The certificate hostname must be trusted by and reachable from both the TV and the parent’s browser. Tailscale is not required on the TV if it can reach the server by another permitted route; Kidoo’s TV connection screen supports a local IPv4 address while validating the certificate hostname. The wizard checks that the key loads, checks validity dates and a chosen hostname when it can inspect them, and suggests a hostname when unambiguous. It does **not** establish device trust, confirm network reachability or schedule certificate renewal. A self-signed certificate is not trusted by the Android app by default. See [protected mode](#protected-mode-under-active-development).

Once the server is running, open the dashboard address shown by the wizard and approve content. Install the Android TV app separately, then pair it using the HTTPS address and a one-time code. A code expires after five minutes; create another in the dashboard if needed.

The wizard can optionally enable a per-user service and start Kidoo immediately: a LaunchAgent on macOS or a systemd user service on Linux. It refuses to overwrite an existing service file. The computer must be awake; the service generally needs the user to be logged in, and Linux requires an active systemd user manager. On macOS, disable it with `launchctl disable gui/$(id -u)/org.kidoo.server` and stop it with `launchctl bootout gui/$(id -u)/org.kidoo.server`. On Linux, use `systemctl --user disable --now kidoo.service`. If enabling fails, the created service file remains for inspection and the saved start script can still be used.

The parent dashboard reports certificate expiry where the certificate can be inspected and warns when renewal is due within 30 days. This check is informational: it does not verify trust or renew the certificate. Arrange renewal and private backups separately; replace certificates safely and restart Kidoo after renewal. The existing Tailscale renewal helper below is an optional Linux/systemd-oriented tool, not an automatically configured timer.

Use `sh setup.sh --data-dir /path/to/new-data` for a separate protected installation or `sh setup.sh --port 8766` to choose another port. On Windows, use `setup.cmd --port 8766` for a preview on another port. To keep an existing installation, follow its existing startup procedure; do not point a new setup at it. See the migration and backup sections before upgrading.

The current Mac and Windows browser interface plays local videos, but does not offer the full TV experience or Jellyfin playback. A browser signed in as a parent retains parent privileges: switching to Family view is a presentation setting, not a secure child mode.

## Getting started

Python 3.9 or newer is required. Choose a folder containing video files you have permission to use:

```sh
python3 server/kidoo_server.py --media-dir /path/to/your/videos
```

Open <http://127.0.0.1:8765/> on the same computer. The viewing dashboard is available from the parent view or at <http://127.0.0.1:8765/dashboard>. The server listens only on that computer by default. It stores profiles, viewing activity and review notes in `~/.kidoo/kidoo.db`; use `--data-dir` to choose another location. Back up this database and your video files, and keep the backup private.

### Testing on a trusted home network

```sh
export KIDOO_PASSWORD='choose-a-strong-password'
python3 server/kidoo_server.py --media-dir /path/to/your/videos --host 0.0.0.0
```

From a device on the same network, open `http://YOUR_COMPUTER_LAN_IP:8765/`. The server computer must remain on and port 8765 must be reachable. A password is required when binding beyond localhost.

**Prototype security:** connections use unencrypted HTTP, there is one shared server password, and Family view and individual profiles are not secure access controls. Do not expose the server to the internet or use it on an untrusted network. Do not rely on it to restrict what a child can access.

### Adding videos to your library

Put video files you are authorized to use in the folder passed as `--media-dir`. Kidoo scans that folder and its subfolders. Approve a file in the parent interface to show it in Family view; supported local playback positions are saved per profile. Available formats depend on the player and the video's codecs. In the parent dashboard, open **Organize local videos** to add titles, descriptions, topics, and movie or episode classification. Give episodes the same series name to group them, and optionally set season and episode numbers. Details are shared across profiles but do not approve a video; approve each title separately. Put a `.jpg`, `.jpeg` or `.png` file beside the video with the same filename for artwork. Series use artwork from an approved episode. Files without a type remain in More videos. Renaming or moving a file changes its identifier and can disconnect its metadata, approval and progress. Movies and episodes count toward their corresponding viewing-limit categories for new sessions; a category change during playback requires restarting that session.

### Protected mode (under active development)

The repository contains a protected-mode implementation for paired Android TV devices. In this mode the server requires direct HTTPS, an administrator name and parent PIN; the old shared-password login is disabled. A TV pairs to one profile with a short-lived, single-use code and keeps its revocable credential in Android's device-bound encrypted storage. A PIN temporarily unlocks parent controls; server checks apply to profiles, approvals, search, playback and direct media URLs. Parent access expires after five minutes, and paired TV parent mode also locks when the app stops. An administrator account may have no account password and use the separate parent PIN instead.

**The upgrade has not yet been verified end to end on every device.** Keep backups and test on a separate installation first. Protected mode does not restrict access to other TV apps. It keeps child playback in Kidoo and does not offer a source link in child mode; online videos that cannot be played by an authorized configured player remain unavailable. It cannot recall video bytes already buffered by a device after approval is revoked.

For a Jellyfin series, protected child mode allows its cover when at least one accessible episode in that series is approved for the profile. Removing the last approved episode also removes access to the cover.

To prepare a test installation as the Kidoo service user:

1. Back up the Kidoo database, configuration and library. Obtain a certificate and private key for a hostname trusted and reachable by both the TV and browser; protect the private key and plan certificate renewal. A self-signed certificate is not trusted by the Android app by default.
2. Create an administrator if needed: `python3 server/security_admin.py create-administrator --name "Parent"`. The account has no password. Set a separate parent PIN interactively with `python3 server/security_admin.py set-pin`; restart the server after changing it. The tool never prints the PIN.
3. Start the server with `--tls-cert /path/to/certificate-chain.pem --tls-key /path/to/private-key.pem --protected-mode`, along with the usual `--media-dir`, `--data-dir` and network options. Protected mode refuses to start without a TLS certificate, an administrator and a PIN.
4. Open the HTTPS dashboard and sign in with the administrator name and parent PIN. For each profile, use **Import existing family approvals** once if you want to copy the old shared approvals, then review individual video and channel choices. An individual video can be blocked even when its mapped channel is approved.
5. Create a pairing code for the selected profile, then enter the HTTPS server address and code on the TV using **Pair this TV**. Pairing replaces the old stored server password on that TV. Use the dashboard to revoke a device if needed. Switching a paired device to another profile requires parent unlock.

If the certificate hostname resolves to an address the TV cannot reach, the Android TV connection screen also accepts an optional private IPv4 address for the home server. Kidoo connects to that address while retaining the HTTPS hostname for normal certificate validation. The address is stored with the device's encrypted connection settings; it does not change DNS for other TV apps. Certificate issuance and renewal remain the server administrator's responsibility.

The old HTTP/shared-login setup remains available for existing installations unless protected mode is enabled; it is not a secure parent–child boundary. Never forward an HTTP port to the internet or rely on an untrusted proxy to authenticate credentials. If the PIN is forgotten, reset it locally with the `set-pin` command above and restart the server.

### Verify a new family setup

Use a separate test profile and device before relying on child mode. Pair the device, approve one test video, and confirm an unapproved video is absent from browsing and search. Remove approval and check that another playback request fails; already buffered video cannot be recalled immediately. Confirm that parent actions require a PIN and that a revoked device can no longer connect. Reset a forgotten PIN from the server computer using the command above, rather than sending the PIN to anyone.

For viewing limits, set a short allowance for the test profile, start an approved video and check that it stops after the allowance is used. Check a pause does not consume time and that the next local day resets the allowance. For multiple devices, pair each to the same test profile and check their viewing contributes to the shared total. Remove the test limits afterwards. These checks exercise Kidoo only; restrictions on launching other TV apps must be configured separately.

### Back up and restore

Run the following as the Kidoo service user, using a new destination outside the Kidoo data directory each time:

```sh
python3 server/backup.py create --from ~/.kidoo --to ~/kidoo-backups/snapshot-example
python3 server/backup.py restore --from ~/kidoo-backups/snapshot-example --to ~/kidoo-restore-test
```

The snapshot uses SQLite's online backup API and contains Kidoo's database, Jellyfin connection settings, server environment and TLS files when present. It contains private information and credentials: keep it private and protect any off-machine copies. It does **not** back up media files, the Jellyfin server's own database or playback state, the operating system, or service definitions. Back those up separately. Restore only creates a new directory; it never overwrites the running server. Check the restored copy before arranging a replacement during a maintenance window.

For installations using Tailscale certificates, `server/renew_certificate.py` can check expiry and renew the certificate 30 days before expiry, provided the service user has Tailscale certificate permission. Schedule it with a systemd user timer or equivalent, and monitor failures. The script restarts the protected Kidoo service when it installs a replacement certificate, which interrupts active playback. Tailscale certificate issuance and renewal can publish the machine name and tailnet domain in public certificate logs.

## Jellyfin integration

Connect each Kidoo profile to an existing Jellyfin user to read that user's catalogue and watch position. Run setup on the Kidoo server as the service user, on a trusted network or through an encrypted connection to Jellyfin:

```sh
python3 server/jellyfin.py --profile family --server http://JELLYFIN_HOST:8096 --username JELLYFIN_USER
```

It prompts for the Jellyfin password and stores an access token (not the password) in `jellyfin.json` inside the Kidoo data directory with owner-only file permissions. Use `--data-dir` if Kidoo uses a non-default data directory. Use a distinct Jellyfin user for each Kidoo profile that needs separate viewing history, and restrict that user's library access in Jellyfin. Profile IDs are available from Kidoo's authenticated `/api/profiles` endpoint.

An authenticated client can read `/api/jellyfin/catalogue/PROFILE_ID?start=0&limit=50` (maximum 100). Responses contain Jellyfin item IDs prefixed with `jellyfin:`, metadata, approval status and the Jellyfin user's existing watch position in seconds; they never contain the access token. The endpoint is intended for parent review: the current shared-login prototype does not provide a secure parent/child boundary. Do not expose it to untrusted users.

The in-development API allows an authenticated client to approve a Jellyfin item with `POST /api/jellyfin/approval` and `{ "profileId": "family", "itemId": "JELLYFIN_ITEM_ID", "approved": true }`. Use the raw Jellyfin ID, without the `jellyfin:` catalogue prefix. In legacy mode approvals are shared among Kidoo profiles for the same Jellyfin server; protected mode uses per-profile approvals. Access through Jellyfin still depends on the mapped user's permissions. The shared Kidoo login does not prevent a child who has it from changing approvals.

For an approved item, `POST /api/jellyfin/start` with `profileId` and `itemId` returns a `sessionId`, a relative authenticated `streamUrl` and the latest Jellyfin resume `position` in seconds. The stream supports byte ranges and is checked against the approval on each request. A client reports progress with `POST /api/jellyfin/event`: include `profileId`, `sessionId`, `event` (`progress` or `stop`), `position` in seconds, an increasing positive integer `sequence`, and optionally `paused`. Send a `stop` when playback ends. Retrying the same sequence is idempotent; older sequences are rejected. A new session for the same profile and item invalidates the previous session. Sessions are persisted across Kidoo restarts. Clients must queue unsent updates and handle reconnects.

The native TV client supports Jellyfin artwork, parent approvals, paginated browsing, playback and resume. Jellyfin remains the source of truth for Jellyfin watch history; Kidoo retains progress for its separately hosted local files. The client journals unsent Jellyfin progress and retries after reconnection. Sessions survive a Kidoo server restart. If another device has changed the position, the server rejects a conflicting update and the app offers to use Jellyfin's saved position instead of silently overwriting it.

The Jellyfin player starts with direct streaming. If the TV reports a container or decoder error, Kidoo retries once with Jellyfin transcoding to H.264/AAC HLS, provided the Jellyfin user and server permit it. The server proxies playlists and segments without exposing Jellyfin credentials; each request still checks the profile's approval. Automatic conflict detection compares reported watch states and cannot make Jellyfin and Kidoo updates atomic across devices. Artwork, catalogue retrieval, and playback/resume have been checked against Jellyfin 12.4 and a physical Fire TV using specific test videos; staged Fire TV tests verified automatic fallback, playback and seeking without changing the real Jellyfin viewing history. Broad codec support and long-duration interruption testing remain unverified. The parent dashboard can search the Jellyfin library and change approvals. Jellyfin decides watched status from its own playback rules. Restrict libraries and ratings in Jellyfin: Kidoo's shared-password parent view is not a security boundary.
