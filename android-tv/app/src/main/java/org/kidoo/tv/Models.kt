package org.kidoo.tv

import java.net.URI
import java.text.Normalizer
import org.json.JSONArray
import org.json.JSONObject

object ConnectionPolicy {
    fun normalise(value: String): String {
        val u =
            try {
                URI(value.trim())
            } catch (_: Exception) {
                throw IllegalArgumentException("Enter a complete http or https address")
            }
        require(
            u.scheme in listOf("http", "https") &&
                !u.host.isNullOrBlank() &&
                u.userInfo == null &&
                u.query == null &&
                u.fragment == null
        ) {
            "Use a server address without credentials, query or fragment"
        }
        require(u.port == -1 || u.port in 1..65535) { "Use a valid server port" }
        require(u.path.isNullOrEmpty() || u.path == "/") {
            "Enter the Kidoo server root, not a page or Jellyfin address"
        }
        return u.toASCIIString().trimEnd('/')
    }

    fun external(value: String): Boolean =
        try {
            val u = URI(value)
            u.scheme in listOf("http", "https") && u.host != null && u.userInfo == null
        } catch (_: Exception) {
            false
        }
}

object ProgressPolicy {
    fun completed(position: Long, duration: Long, ended: Boolean) =
        ended || duration > 0 && position >= duration * .95

    fun resume(position: Long, duration: Long, completed: Boolean): Long =
        if (completed) 0
        else position.coerceAtLeast(0).let { if (duration > 0) it.coerceAtMost(duration) else it }
}

object VideoTime {
    fun clock(milliseconds: Long): String {
        val seconds = (milliseconds.coerceAtLeast(0) / 1000)
        val hours = seconds / 3600
        val minutes = (seconds / 60) % 60
        return if (hours > 0) "%d:%02d:%02d".format(hours, minutes, seconds % 60)
        else "%d:%02d".format(minutes, seconds % 60)
    }
    fun durationLabel(seconds: Double): String = if (seconds > 0 && seconds.isFinite())
        clock((seconds * 1000).toLong()) else ""
}

/** Measures actual forward playback, ignoring pauses and seek jumps. */
class WatchTimeTracker {
    private var lastTime = -1L
    private var lastPosition = 0L
    private var wasPlaying = false
    var watchedMs = 0L
        private set

    fun resetObservation() { lastTime = -1L; wasPlaying = false }

    fun observe(positionMs: Long, realtimeMs: Long, playing: Boolean): Long {
        val elapsed = (realtimeMs - lastTime).coerceAtLeast(0)
        val advanced = positionMs - lastPosition
        if (wasPlaying && lastTime >= 0 && advanced >= 0 && advanced <= elapsed + 1500)
            watchedMs = (watchedMs + minOf(advanced,elapsed)).coerceAtMost(86400000)
        lastTime = realtimeMs
        lastPosition = positionMs
        wasPlaying = playing
        return watchedMs
    }
}

fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

fun JSONArray.strings(): List<String> = (0 until length()).map { getString(it) }

fun JSONObject.array(name: String): JSONArray = optJSONArray(name) ?: JSONArray()

fun JSONArray.toggled(value: Any): JSONArray {
    val values = (0 until length()).map { get(it) }.toMutableList()
    if (values.contains(value)) values.remove(value) else values.add(value)
    return JSONArray(values)
}

/** Tag filtering only receives the server's already eligible video list. */
object VideoTagFilter {
    data class Choice(val key: String, val label: String, val count: Int)

    fun normalise(value: String): String = Normalizer.normalize(value.trim(), Normalizer.Form.NFD)
        .replace(Regex("\\p{M}+"), "").lowercase(java.util.Locale.ROOT)

    private fun labels(video: JSONObject): List<String> =
        (listOf(video.optString("topic", "")) + video.array("tags").strings())
            .map { it.trim() }.filter { it.isNotEmpty() }.distinctBy(::normalise)

    fun matches(video: JSONObject, selected: Set<String>): Boolean {
        val keys=labels(video).map(::normalise).toSet()
        return selected.all { normalise(it) in keys }
    }

    fun choices(videos: List<JSONObject>): List<Choice> {
        val counts=linkedMapOf<String, Pair<String,Int>>()
        for (video in videos) for (label in labels(video)) {
            val key=normalise(label); val old=counts[key]
            counts[key]=(old?.first ?: label) to ((old?.second ?: 0)+1)
        }
        return counts.map { (key,value) -> Choice(key,value.first,value.second) }
            .sortedWith(compareByDescending<Choice> { it.count }.thenBy { it.key })
    }
}

data class Video(val raw: JSONObject) {
    val key
        get() = raw.getString("key")

    val id
        get() = raw.get("id")

    val local
        get() = raw.getString("kind") == "local"

    val jellyfin
        get() = raw.getString("kind") == "jellyfin"

    val series
        get() = raw.getString("kind") == "series"

    val hosted
        get() = local || jellyfin

    val title
        get() = raw.getString("title")

    val creator
        get() = raw.optString("creator")

    val thumbnail
        get() = raw.optString("thumbnail")

    val url
        get() = raw.optString("url")

    val favourite
        get() = raw.optBoolean("favourite")

    val progress
        get() = raw.optJSONObject("progress")

    val durationSeconds: Double
        get() = raw.optDouble("durationSeconds", 0.0).takeIf { it > 0 && it.isFinite() }
            ?: progress?.optDouble("duration", 0.0)?.takeIf { it > 0 && it.isFinite() } ?: 0.0
}

/** Community shelves and search accept family-hosted content only. */
object CommunityCatalog {
    fun supports(item: JSONObject): Boolean = item.optString("kind") in setOf("local", "jellyfin", "series")
}

/** Search only the library entries already returned for this profile. */
object SearchCatalog {
    fun matches(video: Video, query: String): Boolean {
        val needle=VideoTagFilter.normalise(query)
        if (needle.isBlank()) return true
        return listOf(video.title,video.creator,video.raw.optString("summary"))
            .any { VideoTagFilter.normalise(it).contains(needle) }
    }
}

/** Keep a shelf's covers in one shape, using supplied artwork metadata when present. */
object ArtworkLayout {
    fun portrait(video: Video): Boolean {
        val ratio = video.raw.optDouble("artworkAspectRatio", Double.NaN)
        if (ratio.isFinite() && ratio > 0) return ratio < 0.9
        return video.series || (video.hosted && video.raw.optString("mediaType") == "Movie")
    }

    fun shelves(videos: List<Video>): List<Pair<Boolean, List<Video>>> =
        videos.groupBy(::portrait).map { (portrait, items) -> portrait to items }
}

class ApiFailure(val category: String, val status: Int = 0) : Exception(category) {
    fun explanation() =
        if (status == 429) "Your viewing time is finished for today. Ask a grown-up for help."
        else if (status == 409) "Another device changed this video’s progress. Choose whether to use Jellyfin’s saved position."
        else
        when (category) {
            "credentials" -> "The server did not accept the password. Check it and sign in again."
            "network" ->
                "Cannot reach the server. Check Wi-Fi, its address and whether the server is running."
            "server" ->
                "The server could not complete the request. Your saved choices have not been changed."
            "protocol" -> "This is not a compatible Kidoo server. A Jellyfin address is different."
            else -> "The request could not be completed. Please retry."
        }
}
