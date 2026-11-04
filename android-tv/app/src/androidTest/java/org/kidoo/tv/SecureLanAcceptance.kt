package org.kidoo.tv

import android.app.Activity
import android.app.Instrumentation
import android.os.Bundle
import android.content.Intent
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.TextView
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.Player
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import java.io.File
import org.json.JSONObject

/** Opt-in test. Connection details and the one-time code stay in app-private storage. */
@androidx.media3.common.util.UnstableApi
object SecureLanAcceptance {
    private fun nodes(view: View): List<View> = listOf(view) +
        if (view is ViewGroup) (0 until view.childCount).flatMap { nodes(view.getChildAt(it)) } else emptyList()

    fun run(instrumentation: Instrumentation, mode: String) {
        val result = Bundle()
        try {
            val file = File(instrumentation.targetContext.filesDir, "secure-lan-test.json")
            val config = JSONObject(file.readText())
            file.writeText("{}")
            val base = config.getString("url")
            val local = config.getString("localAddress")
            if (mode == "secure-lan-ui") {
                val activity = instrumentation.startActivitySync(
                    Intent(instrumentation.targetContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                ) as MainActivity
                val until = System.currentTimeMillis() + 30000
                while (activity.page !in listOf("home", "error") && System.currentTimeMillis() < until) Thread.sleep(100)
                check(activity.page in listOf("home", "error")) { "Connection screen did not become available" }
                instrumentation.runOnMainSync {
                    activity.connection()
                    val views = nodes(activity.window.decorView)
                    fun field(hint: String) = views.filterIsInstance<EditText>().first { it.hint.toString() == hint }
                    field("http://your-server:8765").setText(base)
                    field("Home network IP (optional)").setText(local)
                    field("Pairing code from a grown-up").setText(config.getString("code"))
                    views.filterIsInstance<TextView>().first { it.text.toString() == "Pair this TV" && it.isClickable }.performClick()
                }
                val deadline = System.currentTimeMillis() + 30000
                while ((activity.page != "home" || activity.api?.isPaired != true) && System.currentTimeMillis() < deadline) Thread.sleep(100)
                check(activity.page == "home" && activity.api?.isPaired == true) { "TV pairing screen did not reach child home" }
                instrumentation.runOnMainSync { activity.finish() }
                result.putString("result", "PASS: Fire TV pairing screen reached protected child home over HTTPS LAN")
                instrumentation.finish(Activity.RESULT_OK, result)
                return
            }
            val info = KidooApi(base, "", localAddress = local).redeem(config.getString("code"))
            val api = KidooApi(base, "", info.getString("credential"), local)
            api.signIn()
            val profile = info.getString("profileId")
            if (mode == "secure-boundary") {
                val allowed=config.getString("allowedId")
                val denied=config.getString("deniedId")
                val pin=config.getString("pin")
                val replacementPin=config.getString("replacementPin")
                fun deniedRequest(label: String, call: () -> Unit) {
                    try { call(); throw AssertionError("Child unexpectedly accessed $label") }
                    catch (failure: ApiFailure) { check(failure.status in listOf(401,403)) { "Unexpected response for $label" } }
                }
                val home=JSONObject(api.get("/api/tv/home/$profile?view=family"))
                val localItems=home.getJSONArray("local")
                check((0 until localItems.length()).any { localItems.getJSONObject(it).getString("id")==allowed }) { "Approved test video missing" }
                check((0 until localItems.length()).none { localItems.getJSONObject(it).getString("id")==denied }) { "Unapproved test video visible" }
                deniedRequest("parent dashboard") { api.get("/api/dashboard/$profile") }
                deniedRequest("another profile") { api.get("/api/tv/home/other?view=family") }
                deniedRequest("unapproved stream") { api.get("/media/$denied") }
                check(api.get("/media/$allowed")=="approved test video") { "Approved test stream unavailable" }
                val approval=JSONObject().put("profileId",profile).put("kind","local").put("itemId",allowed).put("approved",false)
                deniedRequest("approval change") { api.post("/api/security/approval",approval) }
                api.unlockParent(pin)
                api.post("/api/security/approval",approval)
                api.lockParent()
                deniedRequest("revoked stream") { api.get("/media/$allowed") }
                api.unlockParent(pin)
                api.post("/api/security/pin",JSONObject().put("pin",replacementPin))
                deniedRequest("old PIN") { api.unlockParent(pin) }
                api.unlockParent(replacementPin)
                val replacementCode=JSONObject(api.post("/api/security/pairing",JSONObject()
                    .put("profileId",profile).put("label","Replacement test TV"))).getString("code")
                api.post("/api/security/revoke",JSONObject().put("deviceId",info.getString("deviceId")))
                try { api.get("/api/profiles"); throw AssertionError("Revoked device retained access") }
                catch (_: ApiFailure) { /* Revoked credentials must fail, including a failed re-sign-in. */ }
                val replacement=KidooApi(base,"",localAddress=local).redeem(replacementCode)
                val recovered=KidooApi(base,"",replacement.getString("credential"),local)
                recovered.signIn()
                check(org.json.JSONArray(recovered.get("/api/profiles")).length()==1) { "Replacement device could not recover access" }
                result.putString("result","PASS: child restrictions, approval revocation, PIN reset, device revocation and replacement pairing on Fire TV")
                instrumentation.finish(Activity.RESULT_OK,result)
                return
            }
            if (mode == "secure-fallback") {
                val store=SecureStore(instrumentation.targetContext)
                val previous=store.read("connection")
                var activity: PlayerActivity? = null
                try {
                    store.write("connection",JSONObject().put("url",base).put("localAddress",local).put("deviceCredential",info.getString("credential")))
                    val intent=Intent(instrumentation.targetContext,PlayerActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        .putExtra("profile",profile).putExtra("media",config.getString("itemId")).putExtra("kind","jellyfin")
                    activity=instrumentation.startActivitySync(intent) as PlayerActivity
                    val until=System.currentTimeMillis()+60000
                    var ready=false;var playing=false
                    while(System.currentTimeMillis()<until && !(ready && playing)) {
                        instrumentation.runOnMainSync { ready=activity!!.useTranscode && activity!!.player?.playbackState==Player.STATE_READY;playing=activity!!.player?.isPlaying==true }
                        if(!(ready && playing)) Thread.sleep(150)
                    }
                    check(ready && playing) { "TV did not fall back to transcoded playback" }
                    result.putString("result","PASS: Fire TV automatically recovered from unsupported direct playback")
                } finally {
                    instrumentation.runOnMainSync { activity?.finish() }
                    if(previous!=null) store.write("connection",previous)
                }
                instrumentation.finish(Activity.RESULT_OK,result)
                return
            }
            if (mode == "secure-transcode") {
                val start=JSONObject(api.post("/api/jellyfin/start",JSONObject().put("profileId",profile).put("itemId",config.getString("itemId")).put("transcode",true)))
                var player: ExoPlayer? = null
                try {
                    instrumentation.runOnMainSync {
                        val source=DefaultMediaSourceFactory(DefaultDataSource.Factory(instrumentation.targetContext,OkHttpDataSource.Factory(api.http).setDefaultRequestProperties(api.headers())))
                        player=ExoPlayer.Builder(instrumentation.targetContext).setMediaSourceFactory(source).build()
                        player!!.setMediaItem(MediaItem.Builder().setUri(base+start.getString("streamUrl")).setMimeType(MimeTypes.APPLICATION_M3U8).build())
                        player!!.seekTo(10000);player!!.prepare();player!!.playWhenReady=false
                    }
                    val until=System.currentTimeMillis()+60000
                    var state=0;var position=0L;var failure=""
                    while (System.currentTimeMillis()<until) {
                        instrumentation.runOnMainSync { state=player!!.playbackState;position=player!!.currentPosition;failure=player!!.playerError?.errorCodeName ?: "" }
                        if(state==Player.STATE_READY || failure.isNotEmpty()) break
                        Thread.sleep(150)
                    }
                    check(failure.isEmpty()) { "HLS playback failed: $failure" }
                    check(state==Player.STATE_READY && position>=9000) { "HLS playback did not resume at the requested position" }
                    instrumentation.runOnMainSync { player!!.playWhenReady=true }
                    val playingUntil=System.currentTimeMillis()+15000
                    while (System.currentTimeMillis()<playingUntil && position<12000 && failure.isEmpty()) {
                        Thread.sleep(150)
                        instrumentation.runOnMainSync { position=player!!.currentPosition;failure=player!!.playerError?.errorCodeName ?: "" }
                    }
                    check(failure.isEmpty() && position>=12000) { "HLS playback did not advance" }
                    instrumentation.runOnMainSync { player!!.seekTo(30000) }
                    val seekUntil=System.currentTimeMillis()+15000
                    while (System.currentTimeMillis()<seekUntil && position<29000 && failure.isEmpty()) {
                        Thread.sleep(150)
                        instrumentation.runOnMainSync { position=player!!.currentPosition;failure=player!!.playerError?.errorCodeName ?: "" }
                    }
                    check(failure.isEmpty() && position>=29000) { "HLS playback did not seek" }
                    result.putString("result","PASS: Fire TV transcoded HLS playback resumed, advanced and sought")
                } finally { instrumentation.runOnMainSync { player?.release() } }
                instrumentation.finish(Activity.RESULT_OK,result)
                return
            }
            val home = JSONObject(api.get("/api/tv/home/$profile?view=family"))
            check(home.has("sections")) { "Missing family library" }
            try {
                api.get("/api/dashboard/$profile")
                error("Child device accessed the parent dashboard")
            } catch (failure: ApiFailure) {
                check(failure.status == 403) { "Unexpected dashboard response" }
            }
            result.putString("result", "PASS: verified HTTPS LAN pairing, library and parent access denial")
            instrumentation.finish(Activity.RESULT_OK, result)
        } catch (error: Throwable) {
            result.putString("error", (error.message ?: error.javaClass.simpleName).take(200))
            instrumentation.finish(Activity.RESULT_CANCELED, result)
        }
    }
}
