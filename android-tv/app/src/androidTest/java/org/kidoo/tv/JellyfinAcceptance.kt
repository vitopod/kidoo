package org.kidoo.tv

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.media3.common.Player
import java.io.File
import java.util.concurrent.atomic.AtomicReference
import org.json.JSONObject

/** Opt-in real Jellyfin test: uses only a previously approved test item. */
@androidx.media3.common.util.UnstableApi
object JellyfinAcceptance {
    fun run(instrumentation: Instrumentation) {
        val result = Bundle()
        var player: PlayerActivity? = null
        fun <T> read(f: () -> T): T {
            val value = AtomicReference<T>()
            instrumentation.runOnMainSync { value.set(f()) }
            return value.get()
        }
        fun await(label: String, test: () -> Boolean) {
            val until = System.currentTimeMillis() + 35000
            while (System.currentTimeMillis() < until) { if (test()) return; Thread.sleep(200) }
            throw AssertionError(label)
        }
        try {
            val ctx = instrumentation.targetContext
            val file = File(ctx.filesDir, "jellyfin-acceptance.json")
            val config = JSONObject(file.readText()); file.writeText("")
            val store = SecureStore(ctx)
            store.write("connection", JSONObject().put("url", config.getString("url")).put("password", config.getString("password")).put("profile", "family").put("profileName", "Family"))
            val api = KidooApi(config.getString("url"), config.getString("password")); api.signIn()
            val profile = "family"
            val home = JSONObject(api.get("/api/tv/home/$profile?view=family"))
            val video = home.array("jellyfin").objects().firstOrNull { it.optBoolean("approved") && it.optString("title").contains(config.getString("title")) }
                ?: throw AssertionError("No approved Jellyfin test item")
            val id = video.getString("id")
            val prior = video.getJSONObject("progress").getDouble("position")
            if (prior < 60) throw AssertionError("Expected previously saved test position")
            val intent = Intent(ctx, PlayerActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                .putExtra("profile", profile).putExtra("media", id).putExtra("kind", "jellyfin")
                .putExtra("title", video.getString("title"))
            val main = instrumentation.startActivitySync(Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
            await("TV home did not load") { read { main.page == "home" } }
            instrumentation.runOnMainSync { main.details(Video(video)) }
            fun nodes(view: View): List<View> = listOf(view) + if (view is ViewGroup) (0 until view.childCount).flatMap { nodes(view.getChildAt(it)) } else emptyList()
            val monitor = instrumentation.addMonitor(PlayerActivity::class.java.name, null, false)
            instrumentation.runOnMainSync {
                val button = nodes(main.window.decorView).filterIsInstance<TextView>().firstOrNull { it.text.toString() == "Resume" && it.isClickable }
                    ?: throw AssertionError("Jellyfin Resume button is missing")
                button.performClick()
            }
            player = instrumentation.waitForMonitorWithTimeout(monitor, 15000) as? PlayerActivity ?: throw AssertionError("TV Resume did not open player")
            val first = player
            await("Jellyfin player did not become ready") { read { first.player?.playbackState == Player.STATE_READY } }
            if (read { first.player!!.currentPosition } < (prior - 10) * 1000) throw AssertionError("Player did not resume")
            val initial = read { first.player!!.currentPosition }
            await("Jellyfin playback did not advance") { read { first.player?.isPlaying == true && first.player!!.currentPosition > initial + 2000 } }
            val target = prior + 20
            instrumentation.runOnMainSync { first.player!!.seekTo((target * 1000).toLong()); first.player!!.pause(); first.save() }
            await("Jellyfin did not save updated position") {
                val latest = JSONObject(api.get("/api/jellyfin/catalogue/$profile?limit=100"))
                    .array("items").objects().first { it.getString("id") == "jellyfin:$id" }
                latest.getJSONObject("progress").getDouble("position") >= target - 3
            }
            instrumentation.runOnMainSync { first.finish() }
            player = null
            Thread.sleep(2500)
            val again = instrumentation.startActivitySync(intent) as PlayerActivity; player = again
            await("Second Jellyfin playback was not ready") { read { again.player?.playbackState == Player.STATE_READY } }
            if (read { again.player!!.currentPosition } < (target - 10) * 1000) throw AssertionError("Second playback did not resume at the latest position")
            instrumentation.runOnMainSync { again.finish() }; player = null
            result.putString("result", "PASS: Jellyfin native player resumed, saved a seek, and resumed again from the new position")
            instrumentation.finish(Activity.RESULT_OK, result)
        } catch (e: Throwable) {
            player?.let { instrumentation.runOnMainSync { it.finish() } }
            result.putString("error", (e.message ?: e.javaClass.simpleName).take(300))
            instrumentation.finish(Activity.RESULT_CANCELED, result)
        }
    }
}
