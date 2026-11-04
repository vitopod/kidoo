package org.kidoo.tv

import android.app.Activity
import android.app.AlertDialog
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.*
import android.widget.*
import androidx.media3.common.*
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.PlayerView
import java.util.concurrent.Executors

@androidx.media3.common.util.UnstableApi
class PlayerActivity : Activity() {
    internal var player: ExoPlayer? = null
    private lateinit var view: PlayerView
    private lateinit var ui: TvUi
    private lateinit var store: SecureStore
    private lateinit var api: KidooApi
    private lateinit var journal: ProgressJournal
    private lateinit var jellyfinJournal: JellyfinJournal
    private lateinit var activityJournal: ActivityJournal
    private val network = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private var profile = ""
    private var media = ""
    private var jellyfin = false
    private var channelSession = ""
    private var channelName = ""
    private var channelFailures = 0
    private var favourite = false
    @Volatile private var advancing = false
    private var switching = false
    @Volatile private var activitySession = ""
    @Volatile private var limitsEnabled = false
    @Volatile private var timeBlocked = false
    private var activitySequence = 0L
    private val watchTracker = WatchTimeTracker()
    private var jellyfinSession = ""
    private var jellyfinSequence = 0L
    private var streamPath = ""
    internal var useTranscode = false
    private var restart = false
    private var savedPosition = 0L
    private var savedDuration = 0L
    private var ended = false
    private var session: androidx.media3.session.MediaSession? = null
    private var unchangedTicks = 0
    private var lastPosition = -1L
    private var retry: TextView? = null
    private var resolve: TextView? = null
    private var status: TextView? = null
    private var clock: TextView? = null
    @Volatile private var alive = true
    private var ready = false
    private val tick =
        object : Runnable {
            override fun run() {
                save()
                val p = player
                updateClock(p)
                if (ready && p != null && p.playWhenReady && !ended) {
                    val position = p.currentPosition
                    unchangedTicks = if (position == lastPosition) unchangedTicks + 1 else 0
                    lastPosition = position
                    if (unchangedTicks == 3)
                        error(
                            "Playback is waiting for output. Turn on the TV, select its Fire TV input and check the audio connection, then reconnect."
                        )
                }
                handler.postDelayed(this, 2000)
            }
        }

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        window.addFlags(
            WindowManager.LayoutParams.FLAG_FULLSCREEN or
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
        )
        ui = TvUi(this)
        store = SecureStore(this)
        journal = ProgressJournal(store)
        jellyfinJournal = JellyfinJournal(store)
        activityJournal = ActivityJournal(store)
        val c =
            store.read("connection")
                ?: run {
                    finish()
                    return
                }
        api = KidooApi(c.getString("url"), c.optString("password"), c.optString("deviceCredential"), c.optString("localAddress"))
        profile = intent.getStringExtra("profile") ?: ""
        media = intent.getStringExtra("media") ?: ""
        jellyfin = intent.getStringExtra("kind") == "jellyfin"
        if (intent.getStringExtra("kind") !in listOf("local", "jellyfin")) { finish(); return }
        channelSession = intent.getStringExtra("channelSession") ?: ""
        channelName = intent.getStringExtra("channelName") ?: ""
        channelFailures = intent.getIntExtra("channelFailures",0)
        favourite = intent.getBooleanExtra("favourite",false)
        restart = intent.getBooleanExtra("restart", false)
        savedPosition = state?.getLong("position") ?: intent.getLongExtra("position", 0)
        savedDuration = intent.getLongExtra("duration",0L)
        val root = FrameLayout(this)
        view = PlayerView(this)
        view.controllerShowTimeoutMs = 4000
        view.setShowSubtitleButton(true)
        view.setShowNextButton(false)
        view.setShowPreviousButton(false)
        root.addView(view, FrameLayout.LayoutParams(-1, -1))
        val top =
            ui.line().apply {
                setPadding(ui.dp(28), ui.dp(18), ui.dp(28), ui.dp(14))
                background = ui.shape(0xDA091124.toInt(), 0)
            }
        val titles=ui.column(0)
        titles.addView(ui.text((if(channelName.isNotBlank()) "$channelName · " else "") + (intent.getStringExtra("title") ?: "Family video"), 21f).apply { setTextColor(android.graphics.Color.WHITE) })
        val nextTitle=intent.getStringExtra("nextTitle") ?: ""
        if(channelSession.isNotBlank() && nextTitle.isNotBlank()) titles.addView(ui.text("Up next: $nextTitle", 16f, true).apply { setTextColor(0xffd5e1f0.toInt()) })
        top.addView(titles, LinearLayout.LayoutParams(0, -2, 1f))
        if(channelSession.isNotBlank()) ui.addButton(top, "Next video") { advanceChannel(false) }
        val favouriteButton=ui.addButton(top, if (favourite) "★ Favourited" else "☆ Favourite") {}
        favouriteButton.setOnClickListener {
            favouriteButton.isEnabled=false
            val target=!favourite
            network.execute {
                try {
                    api.post("/api/tv/favourite",org.json.JSONObject().put("profileId",profile)
                        .put("kind",if(jellyfin) "jellyfin" else "local")
                        .put("itemId",media).put("favourite",target))
                    runOnUiThread { if(alive) {
                        favourite=target
                        favouriteButton.text=if(target) "★ Favourited" else "☆ Favourite"
                        favouriteButton.contentDescription=if(target) "Remove from favourites" else "Add to favourites"
                        favouriteButton.isEnabled=true
                    } }
                } catch (_: Exception) {
                    runOnUiThread { if(alive) { favouriteButton.isEnabled=true;Toast.makeText(this,"Couldn’t update favourite. Try again.",Toast.LENGTH_LONG).show() } }
                }
            }
        }
        favouriteButton.contentDescription=if(favourite) "Remove from favourites" else "Add to favourites"
        ui.addButton(top, "Audio & subtitles") { tracks() }
        root.addView(top, FrameLayout.LayoutParams(-1, -2, Gravity.TOP))
        clock=ui.text("0:00 / --:--", 16f).apply {
            setTextColor(android.graphics.Color.WHITE)
            setPadding(ui.dp(12),ui.dp(6),ui.dp(12),ui.dp(6))
            background=ui.shape(0xD9091124.toInt(),8)
            contentDescription="Playback time"
        }
        root.addView(clock,FrameLayout.LayoutParams(-2,-2,Gravity.TOP or Gravity.END).apply { topMargin=ui.dp(72);rightMargin=ui.dp(28) })
        view.setControllerVisibilityListener(
            PlayerView.ControllerVisibilityListener { visibility -> top.visibility = visibility }
        )
        val message = ui.column(22).apply { background = ui.shape(Palette.surface) }
        status = ui.text("Connecting…", 19f)
        message.addView(status)
        retry = ui.addButton(message, "Reconnect and resume", true) { connect() }
        retry!!.visibility = View.GONE
        resolve = ui.addButton(message, "Use Jellyfin's saved position") {
            AlertDialog.Builder(this).setTitle("Use Jellyfin's position?")
                .setMessage("This discards conflicting pending progress on this device and resumes from Jellyfin.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Use Jellyfin") { _, _ ->
                    network.execute {
                        try {
                            jellyfinJournal.useServerProgress(api)
                            runOnUiThread { if (alive) { resolve?.visibility = View.GONE; connect() } }
                        } catch (e: Exception) {
                            runOnUiThread { if (alive) error((e as? ApiFailure)?.explanation() ?: "Could not resolve progress. Try again.") }
                        }
                    }
                }.show()
        }
        resolve!!.visibility = View.GONE
        root.addView(message, FrameLayout.LayoutParams(ui.dp(500), -2, Gravity.CENTER))
        message.tag = "error"
        setContentView(root)
        connect()
        handler.postDelayed(tick, 1000)
    }

    private fun connect() {
        retry?.visibility = View.GONE
        status?.text = "Connecting…"
        status?.parent?.let { (it as View).visibility = View.VISIBLE }
        network.execute {
            try {
                api.signIn()
                journal.flush(api)
                jellyfinJournal.flush(api)
                activityJournal.flush(api)
                if (jellyfin) {
                    val result = org.json.JSONObject(api.post("/api/jellyfin/start", org.json.JSONObject().put("profileId", profile).put("itemId", media).put("transcode",useTranscode)))
                    jellyfinSession = result.getString("sessionId")
                    jellyfinSequence = 0
                    streamPath = result.getString("streamUrl")
                    savedPosition = if (if (channelSession.isNotBlank()) result.optBoolean("completed") else restart) 0 else (result.getDouble("position") * 1000).toLong()
                    restart = false
                }
                if (activitySession.isBlank()) {
                    run {
                        val payload=org.json.JSONObject().put("profileId",profile)
                            .put("kind",if (jellyfin) "jellyfin" else "local")
                            .put("itemId",media)
                        if (jellyfin) payload.put("playbackSession",jellyfinSession)
                        if (channelSession.isNotBlank()) payload.put("channelSession",channelSession)
                        val started=org.json.JSONObject(api.post("/api/activity/start",payload))
                        activitySession=started.getString("sessionId")
                        limitsEnabled=started.getJSONObject("limit").getBoolean("enabled")
                        if (channelSession.isNotBlank() && !jellyfin) {
                            val progress=started.optJSONObject("progress")
                            savedPosition=ProgressPolicy.resume(
                                ((progress?.optDouble("position") ?: 0.0)*1000).toLong(),
                                ((progress?.optDouble("duration") ?: 0.0)*1000).toLong(),
                                progress?.optBoolean("completed") == true)
                        }
                    }
                }
                val allowance=org.json.JSONObject(api.get("/api/viewing-limits/status/"+android.net.Uri.encode(profile)+"/"+android.net.Uri.encode(activitySession)))
                limitsEnabled=allowance.getBoolean("enabled")
                if (allowance.getBoolean("blocked")) throw ApiFailure("limit",429)
                timeBlocked=false
                runOnUiThread { if (alive) startPlayer() }
            } catch (e: Exception) {
                runOnUiThread {
                    if (alive && !advancing) {
                        if (channelSession.isNotBlank() && (e as? ApiFailure)?.status != 429 && channelFailures < 2) {
                            advanceChannel(true)
                            return@runOnUiThread
                        }
                        resolve?.visibility = if ((e as? ApiFailure)?.status == 409) View.VISIBLE else View.GONE
                        error(
                            (e as? ApiFailure)?.explanation()
                                ?: "Connection failed. Retry when the server is available."
                        )
                    }
                }
            }
        }
    }

    private fun startPlayer() {
        watchTracker.resetObservation()
        session?.release()
        session = null
        player?.release()
        ready = false
        ended = false
        unchangedTicks = 0
        lastPosition = -1
        val http =
            OkHttpDataSource.Factory(api.http)
                .setDefaultRequestProperties(api.headers())
        val source = DefaultMediaSourceFactory(DefaultDataSource.Factory(this, http))
        player =
            ExoPlayer.Builder(this).setMediaSourceFactory(source).build().also { p ->
                view.player = p
                p.addListener(
                    object : Player.Listener {
                        override fun onPlaybackStateChanged(state: Int) {
                            if (state == Player.STATE_READY) {
                                ready = true
                                status?.parent?.let { (it as View).visibility = View.GONE }
                                updateClock(player)
                                view.requestFocus()
                            }
                            if (state == Player.STATE_ENDED) {
                                ended = true
                                save()
                                if(channelSession.isNotBlank()) advanceChannel(false)
                            }
                        }

                        override fun onPlayerError(e: PlaybackException) {
                            val unsupported = e.errorCode in 3000..3999 || e.errorCode in 4000..4999
                            if (jellyfin && !useTranscode && unsupported) {
                                if (ready) save(stop=true)
                                else if (jellyfinSession.isNotBlank()) {
                                    jellyfinSequence++
                                    jellyfinJournal.add(api.base, profile, jellyfinSession, jellyfinSequence, savedPosition, true, true)
                                }
                                useTranscode=true
                                status?.text="Preparing a compatible version…"
                                status?.parent?.let { (it as View).visibility=View.VISIBLE }
                                connect()
                                return
                            }
                            save()
                            if(channelSession.isNotBlank() && channelFailures < 2 && !timeBlocked) { advanceChannel(true);return }
                            error(
                                "Playback was interrupted. Check the server, network or media format, then reconnect to resume."
                            )
                        }

                        override fun onIsPlayingChanged(playing: Boolean) {
                            if (playing && timeBlocked) { player?.pause(); return }
                            if (ready) save()
                        }

                        override fun onPositionDiscontinuity(
                            old: Player.PositionInfo,
                            new: Player.PositionInfo,
                            reason: Int,
                        ) {
                            if (ready) save()
                        }
                    }
                )
                session = androidx.media3.session.MediaSession.Builder(this, p).build()
                p.setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(C.USAGE_MEDIA)
                        .setContentType(C.AUDIO_CONTENT_TYPE_MOVIE)
                        .build(),
                    true,
                )
                val path=if (jellyfin) streamPath else "/media/" + android.net.Uri.encode(media) + "?profileId=" + android.net.Uri.encode(profile)
                val item=MediaItem.Builder().setUri(api.base + path + (if (path.contains('?')) "&" else "?") + "activity=" + android.net.Uri.encode(activitySession))
                if (jellyfin && useTranscode) item.setMimeType(MimeTypes.APPLICATION_M3U8)
                p.setMediaItem(item.build())
                p.seekTo(savedPosition)
                p.prepare()
                p.playWhenReady = true
            }
    }

    private fun advanceChannel(failed: Boolean) {
        if(channelSession.isBlank() || advancing || timeBlocked) return
        advancing=true
        if(failed) channelFailures++ else channelFailures=0
        player?.pause()
        save(stop=jellyfin)
        status?.text="Finding the next video…"
        status?.parent?.let { (it as View).visibility=View.VISIBLE }
        network.execute {
            try {
                val result=org.json.JSONObject(api.post("/api/your-tv/next",org.json.JSONObject()
                    .put("profileId",profile).put("sessionId",channelSession)
                    .put("requestId",java.util.UUID.randomUUID().toString().replace("-",""))))
                val next=result.optJSONObject("item")
                runOnUiThread {
                    if(!alive)return@runOnUiThread
                    if(next==null || channelFailures>=3) {
                        advancing=false
                        error("No more videos are ready. Ask a grown-up for help.")
                    } else {
                        val kind=next.getString("kind")
                        val progress=next.optJSONObject("progress")
                        val completed=progress?.optBoolean("completed") == true
                        val position=ProgressPolicy.resume(
                            ((progress?.optDouble("position") ?: 0.0)*1000).toLong(),
                            ((progress?.optDouble("duration") ?: 0.0)*1000).toLong(),completed)
                        val intent=android.content.Intent(this,PlayerActivity::class.java)
                            .putExtra("profile",profile).putExtra("media",next.get("id").toString())
                            .putExtra("kind",kind)
                            .putExtra("title",next.getString("title")).putExtra("restart",completed)
                            .putExtra("position",position)
                            .putExtra("nextTitle",result.optJSONObject("next")?.optString("title") ?: "")
                            .putExtra("favourite",next.optBoolean("favourite"))
                            .putExtra("duration",((next.optDouble("durationSeconds",0.0).takeIf { it>0 && it.isFinite() } ?: progress?.optDouble("duration",0.0) ?: 0.0)*1000).toLong())
                            .putExtra("channelSession",channelSession).putExtra("channelName",channelName)
                            .putExtra("channelFailures",channelFailures)
                        switching=true
                        startActivity(intent)
                        finish()
                    }
                }
            } catch (_: Exception) {
                runOnUiThread { if(alive) { advancing=false;error("Couldn’t find the next video. Ask a grown-up for help.") } }
            }
        }
    }

    private fun error(text: String) {
        status?.text = text
        (status?.parent as? View)?.visibility = View.VISIBLE
        retry?.visibility = View.VISIBLE
        retry?.requestFocus()
    }

    private fun updateClock(p: ExoPlayer?) {
        if (p == null) return
        val length=p.duration.takeIf { it>0 } ?: savedDuration
        val position=p.currentPosition.coerceAtLeast(0)
        clock?.text=VideoTime.clock(position)+" / "+(if(length>0) VideoTime.clock(length) else "--:--")
        clock?.contentDescription="Playback time "+clock?.text
    }

    internal fun save(stop: Boolean = false) {
        val p = player ?: return
        if (!ready) return
        savedPosition = p.currentPosition.coerceAtLeast(0)
        savedDuration = p.duration.takeIf { it > 0 } ?: savedDuration
        val watchedMs = watchTracker.observe(savedPosition,SystemClock.elapsedRealtime(),p.isPlaying)
        if (activitySession.isNotBlank()) {
            activitySequence++
            activityJournal.add(api.base,profile,activitySession,activitySequence,watchedMs,savedDuration,savedPosition,ended)
            network.execute {
                try {
                    activityJournal.flush(api, failClosed = limitsEnabled)
                    val allowance=org.json.JSONObject(api.get("/api/viewing-limits/status/"+android.net.Uri.encode(profile)+"/"+android.net.Uri.encode(activitySession)))
                    limitsEnabled=allowance.getBoolean("enabled")
                    if (allowance.getBoolean("blocked")) runOnUiThread {
                        if (alive && !timeBlocked) {
                            timeBlocked=true
                            player?.pause()
                            error("Your viewing time is finished for today. Ask a grown-up for help.")
                        }
                    }
                } catch (_: Exception) {
                    if (limitsEnabled) runOnUiThread {
                        if (alive && !timeBlocked) {
                            timeBlocked=true
                            player?.pause()
                            error("Kidoo needs to reconnect before watching can continue.")
                        }
                    }
                }
            }
        }
        if (jellyfin) {
            if (jellyfinSession.isBlank()) return
            jellyfinSequence++
            jellyfinJournal.add(api.base, profile, jellyfinSession, jellyfinSequence, savedPosition, !p.isPlaying, stop)
            network.execute {
                try { jellyfinJournal.flush(api) }
                catch (e: Exception) {
                    if ((e as? ApiFailure)?.status == 409) runOnUiThread {
                        if (alive) { player?.pause(); resolve?.visibility = View.VISIBLE; error((e as ApiFailure).explanation()) }
                    }
                }
            }
            return
        }
        journal.add(
            api.base,
            profile,
            media,
            savedPosition,
            savedDuration,
            ProgressPolicy.completed(savedPosition, savedDuration, ended),
        )
        network.execute {
            try {
                journal.flush(api)
            } catch (_: Exception) {}
        }
    }

    private fun tracks() {
        val p = player ?: return
        val choices = mutableListOf("Subtitles off")
        val actions =
            mutableListOf<() -> Unit>({
                p.trackSelectionParameters =
                    p.trackSelectionParameters
                        .buildUpon()
                        .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true)
                        .build()
            })
        for (group in p.currentTracks.groups) if (
            group.type == C.TRACK_TYPE_AUDIO || group.type == C.TRACK_TYPE_TEXT
        ) {
            for (i in 0 until group.length) if (group.isTrackSupported(i)) {
                val f = group.getTrackFormat(i)
                choices.add(
                    (if (group.type == C.TRACK_TYPE_AUDIO) "Audio" else "Subtitles") +
                        " · " +
                        (f.label ?: f.language ?: "Track ${i+1}")
                )
                actions.add {
                    p.trackSelectionParameters =
                        p.trackSelectionParameters
                            .buildUpon()
                            .setTrackTypeDisabled(group.type, false)
                            .setOverrideForType(
                                TrackSelectionOverride(group.mediaTrackGroup, listOf(i))
                            )
                            .build()
                }
            }
        }
        if (choices.size == 1) {
            Toast.makeText(
                    this,
                    "This file has no selectable audio or subtitle tracks",
                    Toast.LENGTH_LONG,
                )
                .show()
            return
        }
        AlertDialog.Builder(this)
            .setTitle("Available tracks in this file")
            .setItems(choices.toTypedArray()) { _, i -> actions[i]() }
            .setNegativeButton("Back", null)
            .show()
    }

    override fun onSaveInstanceState(out: Bundle) {
        out.putLong("position", player?.currentPosition ?: savedPosition)
        super.onSaveInstanceState(out)
    }

    override fun onKeyDown(code: Int, event: KeyEvent): Boolean {
        val p = player
        when (code) {
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
            KeyEvent.KEYCODE_MEDIA_PAUSE,
            KeyEvent.KEYCODE_MEDIA_PLAY -> {
                if (p != null) {
                    if (code == KeyEvent.KEYCODE_MEDIA_PLAY) p.play()
                    else if (code == KeyEvent.KEYCODE_MEDIA_PAUSE) p.pause()
                    else if (p.isPlaying) p.pause() else p.play()
                    view.showController()
                    save()
                }
                return true
            }
            KeyEvent.KEYCODE_MEDIA_FAST_FORWARD,
            KeyEvent.KEYCODE_MEDIA_REWIND -> {
                p?.seekTo(
                    (p.currentPosition +
                            if (code == KeyEvent.KEYCODE_MEDIA_FAST_FORWARD) 10000 else -10000)
                        .coerceAtLeast(0)
                )
                view.showController()
                return true
            }
        }
        return super.onKeyDown(code, event)
    }

    override fun onPause() {
        save()
        player?.pause()
        super.onPause()
    }

    override fun onDestroy() {
        alive = false
        handler.removeCallbacks(tick)
        if(!switching) save(stop = jellyfin)
        session?.release()
        session = null
        player?.release()
        player = null
        network.shutdown()
        super.onDestroy()
    }
}
