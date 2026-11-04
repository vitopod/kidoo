# Your TV in Kidoo Community

Your TV gives each child a set of topic channels built from their approved local and Jellyfin collection. Channels are personal playlists, not synchronized broadcasts. Parents can create, edit, disable and reset channels for each profile.

The TV displays eligible channels as a grid with topic artwork and a channel name. Selecting a channel starts the first eligible item and shows the next title when available. A channel holds up to three items; its queue persists across server restarts, and completion removes the finished queue entry so it can be replaced. When the approved collection is small, a channel may safely repeat an item.

Selecting a channel never grants approval. Each offer and playback start checks the profile's current eligibility, parent Show less choices, source availability and viewing limits. Revoking approval prevents a subsequent start. Unfinished titles can appear in Continue watching; the channel itself does not. Jellyfin remains authoritative for Jellyfin progress, while local progress is stored in Kidoo.

The selector uses available topic and genre metadata and modest signals from the profile's viewing. Missing language metadata is not inferred. Online-only legacy channel definitions are disabled in Community; the migration preserves their original records in the pre-upgrade backup. Community does not import, cache or prepare online videos.

The Community server tests cover queue retention, refill, safe repeats and device-scoped offers. The Fire TV was checked with a separate test app, a disposable Jellyfin server and one licensed film; a one-item library exercises the safe-repeat case but does not validate every multi-item transition on a physical device.
