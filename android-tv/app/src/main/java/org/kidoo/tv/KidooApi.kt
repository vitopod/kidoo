package org.kidoo.tv

import java.io.IOException
import java.net.InetAddress
import java.net.URI
import java.util.concurrent.TimeUnit
import okhttp3.Dns
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

/** A client per connection; session tokens are never attached to an external URL. */
class KidooApi(val base: String, private val password: String, private val deviceCredential: String = "", private val localAddress: String = "") {
    val http: OkHttpClient = buildHttpClient(base, localAddress)
    @Volatile private var cookie = ""
    @Volatile private var parentSession = ""
    val isPaired: Boolean get() = deviceCredential.isNotBlank()

    fun redeem(code: String): JSONObject {
        require(base.startsWith("https://")) { "Pairing requires HTTPS" }
        val result = raw("POST", "/api/security/redeem", JSONObject().put("code", code), false)
        if (result.first != 200) throw ApiFailure("credentials", result.first)
        return JSONObject(result.second)
    }

    fun unlockParent(pin: String) {
        require(isPaired && base.startsWith("https://"))
        val result = raw("POST", "/api/security/unlock", JSONObject().put("credential", deviceCredential).put("pin", pin), false)
        if (result.first != 200) throw ApiFailure("credentials", result.first)
        parentSession = JSONObject(result.second).getString("session")
    }

    fun lockParent() {
        val old = parentSession
        parentSession = ""
        if (old.isNotBlank()) try {
            raw("POST", "/api/security/lock", JSONObject().put("credential", deviceCredential).put("session", old), false)
        } catch (_: Exception) { /* Session also expires on the server. */ }
    }

    @Synchronized
    fun signIn() {
        val info = raw("GET", "/api/tv/info", null, false)
        if (
            info.first != 200 ||
                try {
                    JSONObject(info.second).optString("product") != "Kidoo"
                } catch (_: Exception) {
                    true
                }
        )
            throw ApiFailure("protocol")
        if (isPaired) {
            require(base.startsWith("https://")) { "Paired devices require HTTPS" }
            if (raw("GET", "/api/profiles", null, true).first != 200) throw ApiFailure("credentials")
            return
        }
        val result = raw("POST", "/api/login", JSONObject().put("password", password), false)
        if (result.first != 200)
            throw ApiFailure(if (result.first == 401) "credentials" else "server", result.first)
    }

    fun headers(): Map<String, String> =
        if (isPaired) buildMap {
            put("Authorization", "Bearer $deviceCredential")
            if (parentSession.isNotBlank()) put("X-Kidoo-Parent-Session", parentSession)
        } else if (cookie.isBlank()) emptyMap() else mapOf("Cookie" to cookie)

    fun get(path: String): String = request("GET", path, null)

    fun post(path: String, data: JSONObject): String = request("POST", path, data)

    private fun request(method: String, path: String, data: JSONObject?): String {
        var response = raw(method, path, data, true)
        if (response.first == 401) {
            signIn()
            response = raw(method, path, data, true)
        }
        if (response.first !in 200..299)
            throw ApiFailure(if (response.first == 401) "credentials" else "server", response.first)
        return response.second
    }

    private fun raw(
        method: String,
        path: String,
        data: JSONObject?,
        authenticated: Boolean,
    ): Pair<Int, String> {
        require(path.startsWith("/") && !path.startsWith("//"))
        try {
            val builder = Request.Builder().url(base + path).header("Accept", "application/json")
            if (authenticated) headers().forEach { (key, value) -> builder.header(key, value) }
            val body = data?.toString()?.toRequestBody("application/json; charset=utf-8".toMediaType())
            builder.method(method, body)
            http.newCall(builder.build()).execute().use { response ->
                val status = response.code
                response.header("Set-Cookie")?.let { cookie = it.substringBefore(';') }
                val bytes = response.body?.byteStream()?.use { s ->
                    val out = java.io.ByteArrayOutputStream()
                    val b = ByteArray(8192)
                    while (true) {
                        val n = s.read(b)
                        if (n < 0) break
                        if (out.size() + n > 4 * 1024 * 1024) throw ApiFailure("protocol")
                        out.write(b, 0, n)
                    }
                    out.toByteArray()
                } ?: byteArrayOf()
                return status to String(bytes, Charsets.UTF_8)
            }
        } catch (e: ApiFailure) {
            throw e
        } catch (_: IOException) {
            throw ApiFailure("network")
        }
    }
}

internal fun localRoute(base: String, localAddress: String): Pair<String, InetAddress>? {
    if (localAddress.isBlank()) return null
    val uri = URI(base)
    require(uri.scheme == "https" && !uri.host.isNullOrBlank()) { "A local address requires HTTPS" }
    val parts = localAddress.trim().split('.')
    require(parts.size == 4 && parts.all { it.isNotEmpty() && it.all(Char::isDigit) && (it.toIntOrNull() ?: -1) in 0..255 }) { "Enter a local IPv4 address" }
    val address = InetAddress.getByAddress(parts.map { it.toInt().toByte() }.toByteArray())
    require(address.isSiteLocalAddress || address.isLinkLocalAddress) { "Enter a private network address" }
    return uri.host.lowercase() to address
}

internal fun buildHttpClient(base: String, localAddress: String): OkHttpClient {
    val route = localRoute(base, localAddress)
    return OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
        .connectTimeout(8, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS)
        .dns(object : Dns {
            override fun lookup(hostname: String): List<InetAddress> =
                if (route != null && hostname.equals(route.first, ignoreCase = true)) listOf(route.second)
                else Dns.SYSTEM.lookup(hostname)
        })
        .build()
}

/**
 * Coalesced, encrypted, bounded journal. Failed writes stay pending; unknown/deleted IDs do not
 * loop.
 */
class ProgressJournal(private val store: SecureStore) {
    companion object {
        private val gate = Any()
    }

    fun add(
        server: String,
        profile: String,
        media: String,
        position: Long,
        duration: Long,
        completed: Boolean,
    ) =
        synchronized(gate) {
            val all = store.read("progress") ?: JSONObject()
            val key = server + "|" + profile + "|" + media
            all.put(
                key,
                JSONObject()
                    .put("server", server)
                    .put("profileId", profile)
                    .put("mediaId", media)
                    .put("position", position / 1000.0)
                    .put("duration", duration.coerceAtLeast(0) / 1000.0)
                    .put("completed", completed),
            )
            while (all.length() > 100) all.remove(all.keys().next())
            store.write("progress", all)
        }

    fun flush(api: KidooApi) {
        val snapshot = synchronized(gate) { store.read("progress") } ?: return
        for (key in snapshot.keys().asSequence().toList()) {
            val item = snapshot.getJSONObject(key)
            if (item.optString("server") != api.base) continue
            var delivered = false
            try {
                api.post("/api/progress", item)
                delivered = true
            } catch (e: ApiFailure) {
                if (e.status == 404) delivered = true else break
            }
            if (delivered)
                synchronized(gate) {
                    val current = store.read("progress") ?: JSONObject()
                    if (current.optJSONObject(key)?.toString() == item.toString())
                        current.remove(key)
                    store.write("progress", current)
                }
        }
    }

    fun pending() = synchronized(gate) { store.read("progress")?.length() ?: 0 }
}

/** Buffered, profile-scoped dashboard observations, independent of playback checkpoints. */
class ActivityJournal(private val store: SecureStore) {
    companion object { private val gate = Any() }
    fun add(server: String, profile: String, session: String, sequence: Long, watchedMs: Long, durationMs: Long, positionMs: Long, finished: Boolean) = synchronized(gate) {
        val all=store.read("activity-events") ?: JSONObject()
        all.put(session,JSONObject().put("server",server).put("profileId",profile).put("sessionId",session)
            .put("sequence",sequence).put("watchedSeconds",watchedMs.coerceIn(0,86400000)/1000.0)
            .put("duration",durationMs.coerceAtLeast(0)/1000.0).put("position",positionMs.coerceAtLeast(0)/1000.0)
            .put("finished",finished).put("observedAt",System.currentTimeMillis()/1000.0))
        while(all.length()>100) all.remove(all.keys().next())
        store.write("activity-events",all)
    }
    fun flush(api: KidooApi, failClosed: Boolean = false) {
        val snapshot=synchronized(gate){store.read("activity-events")} ?: return
        for(key in snapshot.keys().asSequence().toList()) {
            val entry=snapshot.getJSONObject(key)
            if(entry.optString("server")!=api.base) continue
            try { api.post("/api/activity/event",entry) }
            catch(e: ApiFailure) { if(e.status!=404) { if (failClosed) throw e; break } }
            synchronized(gate) {
                val current=store.read("activity-events") ?: JSONObject()
                if(current.optJSONObject(key)?.toString()==entry.toString()) current.remove(key)
                store.write("activity-events",current)
            }
        }
    }
}

/** Persist Jellyfin checkpoints before sending them, including stops. */
class JellyfinJournal(private val store: SecureStore) {
    companion object { private val gate = Any() }

    fun add(server: String, profile: String, session: String, sequence: Long, position: Long, paused: Boolean, stop: Boolean) = synchronized(gate) {
        val all = store.read("jellyfin-progress") ?: JSONObject()
        all.put(session, JSONObject().put("server", server).put("profileId", profile)
            .put("sessionId", session).put("sequence", sequence).put("position", position / 1000.0)
            .put("paused", paused).put("event", if (stop) "stop" else "progress"))
        store.write("jellyfin-progress", all)
    }

    fun flush(api: KidooApi) {
        val snapshot = synchronized(gate) { store.read("jellyfin-progress") } ?: return
        for (key in snapshot.keys().asSequence().toList()) {
            val item = snapshot.getJSONObject(key)
            if (item.optString("server") != api.base) continue
            api.post("/api/jellyfin/event", item)
            synchronized(gate) {
                val current = store.read("jellyfin-progress") ?: JSONObject()
                if (current.optJSONObject(key)?.toString() == item.toString()) current.remove(key)
                store.write("jellyfin-progress", current)
            }
        }
    }

    fun useServerProgress(api: KidooApi) {
        val snapshot = synchronized(gate) { store.read("jellyfin-progress") } ?: return
        for (key in snapshot.keys().asSequence().toList()) {
            val item = snapshot.getJSONObject(key)
            if (item.optString("server") != api.base) continue
            try { api.post("/api/jellyfin/event", item) }
            catch (e: ApiFailure) { if (e.status != 409) throw e }
            synchronized(gate) {
                val current = store.read("jellyfin-progress") ?: JSONObject()
                if (current.optJSONObject(key)?.toString() == item.toString()) current.remove(key)
                store.write("jellyfin-progress", current)
            }
        }
    }
}
