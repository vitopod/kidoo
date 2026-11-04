package org.kidoo.tv

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.TextUtils
import android.view.*
import android.widget.*
import androidx.leanback.widget.HorizontalGridView
import androidx.recyclerview.widget.RecyclerView
import java.text.DateFormat
import java.text.Normalizer
import java.util.Date
import java.util.concurrent.Executors
import org.json.JSONArray
import org.json.JSONObject

class MainActivity : Activity() {
    lateinit var ui: TvUi
    lateinit var store: SecureStore
    internal var api: KidooApi? = null
    internal var home: JSONObject? = null
    internal var profile = ""
    internal var profileName = ""
    internal var parent = false
    internal var page = "connect"
    private var ticket = 0
    private val work = Executors.newSingleThreadExecutor()
    private val playlistWork = Executors.newSingleThreadExecutor()
    @Volatile private var playlistBootstrapBusy = false
    private val grids = mutableListOf<HorizontalGridView>()
    private var navFirst: View? = null
    private var selected = ""
    private var profileButton: View? = null
    private var root: LinearLayout? = null
    private var detailsKey = ""
    private var hasResumed = false
    private var suggestionSession = 0
    private var refreshSuggestionsOnResume = false
    private var relockPending = false
    private var jellyfinPage = 0
    private var activeSection = ""
    private var currentSeries = ""
    private var searchQuery = ""
    private var allSearchActive = false
    private var onlinePageStart = 0
    private val parentTimer = Handler(Looper.getMainLooper())
    private fun displayCreator(video: Video): String = video.creator.takeUnless {
        it.isBlank() || it.equals("null", true) || it.equals("Jellyfin", true) ||
            it.equals("Family library", true) || it.equals("Series", true)
    } ?: ""
    private val expireParent = Runnable {
        if (parent && api?.isPaired == true) {
            val client = api
            parent = false
            work.execute { client?.lockParent() }
            loadHome()
        }
    }

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        suggestionSession = state?.getInt("suggestionSession") ?: nextSuggestionSession()
        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        ui = TvUi(this)
        store = SecureStore(this)
        val saved = store.read("connection")
        if (saved == null) connection()
        else {
            profile = state?.getString("profile") ?: saved.optString("profile")
            profileName = saved.optString("profileName")
            api = KidooApi(saved.getString("url"), saved.optString("password"), saved.optString("deviceCredential"), saved.optString("localAddress"))
            connectExisting()
        }
    }

    override fun onSaveInstanceState(out: Bundle) {
        out.putString("profile", profile)
        out.putInt("suggestionSession", suggestionSession)
        super.onSaveInstanceState(out)
    }

    private fun nextSuggestionSession(): Int {
        val prefs = getPreferences(MODE_PRIVATE)
        val next = (prefs.getInt("suggestionSession", 0) % 1_000_000_000) + 1
        prefs.edit().putInt("suggestionSession", next).apply()
        return next
    }

    override fun onResume() {
        super.onResume()
        if (refreshSuggestionsOnResume) {
            suggestionSession = nextSuggestionSession()
            refreshSuggestionsOnResume = false
        }
        if (relockPending) {
            relockPending = false; activeSection = ""; currentSeries = ""; searchQuery = "";
            hasResumed = true
            loadHome()
            return
        }
        if (page == "player") {
            page = "home"
            loadHome {
                val show = home?.optJSONObject("sections")?.array("series")?.objects()?.firstOrNull { it.optString("key") == currentSeries }
                if (show != null) showSeries(Video(show))
                else if (allSearchActive) showAllSearchResults(searchQuery, onlinePageStart)
                else if (activeSection == "your-tv") showYourTv()
                else if (activeSection.isNotEmpty()) showSection(activeSection) else renderHome()
            }
        } else if (hasResumed && page == "home") {
            loadHome()
        } else if (hasResumed && page == "detail") {
            refreshDetail(detailsKey)
        }
        hasResumed = true
    }

    private fun screen(title: String, subtitle: String = "", adult: Boolean = false, compact: Boolean = false): LinearLayout {
        Palette.childMode = !(parent || adult)
        ticket++
        page = title.lowercase()
        val c = ui.column(if (compact) 24 else 38)
        c.addView(if (compact) ui.logo(100, 42) else ui.logo())
        ui.space(c, if (compact) 8 else 14)
        c.addView(ui.text(title, 32f))
        if (subtitle.isNotBlank()) {
            ui.space(c, 8)
            c.addView(ui.text(subtitle, 17f, true))
        }
        ui.space(c, if (compact) 14 else 22)
        root = c
        setContentView(ui.scroll(c))
        return c
    }

    private fun loading(message: String) {
        screen(message, "One moment…")
    }

    private fun <T> async(task: () -> T, done: (T) -> Unit) {
        val id = ticket
        work.execute {
            try {
                val value = task()
                runOnUiThread { if (!isFinishing && id == ticket) done(value) }
            } catch (e: Exception) {
                runOnUiThread { if (!isFinishing && id == ticket) failure(e) }
            }
        }
    }

    private fun failure(e: Exception) {
        val c =
            screen(
                "Let’s reconnect",
                (e as? ApiFailure)?.explanation()
                    ?: "The request could not be completed. Try again.",
            )
        page = "error"
        if ((e as? ApiFailure)?.status == 409) {
            ui.addButton(c, "Use Jellyfin's saved position") {
                AlertDialog.Builder(this).setTitle("Use Jellyfin's position?")
                    .setMessage("This discards conflicting pending progress on this device.")
                    .setNegativeButton("Cancel", null)
                    .setPositiveButton("Use Jellyfin") { _, _ ->
                        async({ JellyfinJournal(store).useServerProgress(api!!) }) { loadHome() }
                    }.show()
            }
            ui.space(c, 12)
        }
        ui.addButton(c, "Retry", true) { connectExisting() }
        if (api?.isPaired != true || parent) {
            ui.space(c, 12)
            ui.addButton(c, "Change server or password") { connection() }
        }
    }

    internal fun connection() {
        val saved = store.read("connection")
        val c =
            screen(
                "Your own TV starts here",
                "Connect to your Kidoo home server. A Jellyfin address is not a Kidoo server.",
            )
        page = "connect"
        val address = ui.field("http://your-server:8765", value = saved?.optString("url") ?: "")
        val localAddress = ui.field("Home network IP (optional)", value = saved?.optString("localAddress") ?: "")
        val password = ui.field("Shared server password", true)
        c.addView(address, LinearLayout.LayoutParams(ui.dp(570), -2))
        ui.space(c, 12)
        c.addView(localAddress, LinearLayout.LayoutParams(ui.dp(570), -2))
        ui.space(c, 12)
        c.addView(password, LinearLayout.LayoutParams(ui.dp(570), -2))
        ui.space(c, 18)
        ui.addButton(c, "Connect", true) {
            val base =
                try {
                    ConnectionPolicy.normalise(address.text.toString())
                } catch (e: Exception) {
                    address.error = e.message
                    address.requestFocus()
                    return@addButton
                }
            val pass = password.text.toString()
            val route = localAddress.text.toString().trim()
            val candidate = try { KidooApi(base, pass, localAddress = route) }
                catch (e: IllegalArgumentException) { localAddress.error = e.message; return@addButton }
            loading("Connecting to Kidoo")
            async({
                candidate.signIn()
                candidate
            }) {
                api = it
                profile = ""
                parent = false
                store.write("connection", JSONObject().put("url", base).put("password", pass).put("localAddress", route))
                profiles()
            }
        }
        ui.space(c, 14)
        val pairingCode = ui.field("Pairing code from a grown-up", true)
        c.addView(pairingCode, LinearLayout.LayoutParams(ui.dp(570), -2))
        ui.space(c, 10)
        ui.addButton(c, "Pair this TV") {
            val base = try { ConnectionPolicy.normalise(address.text.toString()) }
                catch (e: Exception) { address.error = e.message; return@addButton }
            if (!base.startsWith("https://")) { address.error = "Pairing needs HTTPS"; return@addButton }
            val code = pairingCode.text.toString().trim()
            if (code.isBlank()) { pairingCode.error = "Enter the pairing code"; return@addButton }
            val route = localAddress.text.toString().trim()
            try { localRoute(base, route) } catch (e: IllegalArgumentException) { localAddress.error = e.message; return@addButton }
            loading("Pairing this TV")
            async({
                val info = KidooApi(base, "", localAddress = route).redeem(code)
                val credential = info.getString("credential")
                val client = KidooApi(base, "", credential, route)
                client.signIn()
                Pair(client, info)
            }) { (client, info) ->
                api = client; parent = false; profile = info.getString("profileId")
                store.write("connection", JSONObject().put("url", base).put("localAddress", route).put("deviceCredential", info.getString("credential")).put("deviceId", info.getString("deviceId")).put("profile", profile))
                connectExisting()
            }
        }
        ui.space(c, 20)
        c.addView(
            ui.text(
                    "Saved with device-bound encryption. This prototype uses a shared password. HTTP is for a trusted home network only; do not expose it to the internet.",
                    16f,
                    true,
                )
                .apply { maxWidth = ui.dp(690) }
        )
        address.requestFocus()
    }

    private fun connectExisting() {
        if (api == null) {
            connection()
            return
        }
        loading("Connecting to your library")
        async({
            api!!.signIn()
            ProgressJournal(store).flush(api!!)
            JellyfinJournal(store).flush(api!!)
            ActivityJournal(store).flush(api!!)
            JSONArray(api!!.get("/api/profiles"))
        }) {
            val current = it.objects().firstOrNull { p -> p.getString("id") == profile }
            if (profile.isNotBlank() && current != null) {
                profileName = current.getString("name")
                saveProfile()
                loadHome()
            } else profiles(it)
        }
    }

    internal fun profiles(known: JSONArray? = null) {
        if (known == null) {
            loading("Who’s watching?")
            async({ JSONArray(api!!.get("/api/profiles")) }) { profiles(it) }
            return
        }
        val c =
            screen(
                "Who’s watching?",
                if (api?.isPaired == true) "A grown-up can switch this TV to another profile." else "Each person has their own interests, favourites and place in a video. Profiles are not password-protected.",
            )
        page = "profiles"
        val row = ui.line()
        var first: View? = null
        known.objects().forEachIndexed { index, p ->
            val tile = ui.column(10)
            tile.background = ui.shape(Palette.surface)
            tile.addView(ui.text(listOf("✦", "◒", "◈", "◎")[index % 4], 42f))
            ui.space(tile, 12)
            val b =
                ui.button(p.getString("name")) {
                    val selectedId = p.getString("id")
                    val selectedName = p.getString("name")
                    val client = api
                    if (client?.isPaired == true && selectedId != profile) {
                        val deviceId = store.read("connection")?.optString("deviceId").orEmpty()
                        if (!parent || deviceId.isBlank()) { Toast.makeText(this, "Ask a grown-up to change profiles", Toast.LENGTH_LONG).show(); return@button }
                        async({
                            client.post("/api/security/assign",JSONObject().put("deviceId",deviceId).put("profileId",selectedId))
                            client.lockParent()
                        }) {
                            parentTimer.removeCallbacks(expireParent)
                            profile=selectedId; profileName=selectedName; parent=false; selected=""; saveProfile(); loadHome()
                        }
                    } else {
                        profile=selectedId; profileName=selectedName; selected=""
                        if (parent && client?.isPaired == true) { work.execute { client.lockParent() } }
                        parent=false; saveProfile(); loadHome()
                    }
                }
            tile.addView(b)
            if (first == null) first = b
            row.addView(
                tile,
                LinearLayout.LayoutParams(ui.dp(185), -2).apply { rightMargin = ui.dp(16) },
            )
        }
        val horizontal =
            HorizontalScrollView(this).apply {
                addView(row)
                clipToPadding = false
            }
        c.addView(horizontal)
        ui.space(c, 24)
        if (api?.isPaired != true || parent) ui.addButton(c, "Add profile") {
            val name = ui.field("Profile name")
            name.inputType = android.text.InputType.TYPE_CLASS_TEXT
            AlertDialog.Builder(this)
                .setTitle("A new place in the family")
                .setView(name)
                .setPositiveButton("Create") { _, _ ->
                    val n = name.text.toString().trim()
                    if (n.isBlank() || n.length > 40) {
                        Toast.makeText(this, "Use a name of 1–40 characters", Toast.LENGTH_LONG)
                            .show()
                        return@setPositiveButton
                    }
                    async({ api!!.post("/api/profiles", JSONObject().put("name", n)) }) {
                        profiles()
                    }
                }
                .setNegativeButton("Cancel", null)
                .show()
        }
        first?.requestFocus()
    }

    private fun saveProfile() {
        val c = store.read("connection") ?: return
        c.put("profile", profile).put("profileName", profileName)
        store.write("connection", c)
    }

    internal fun loadHome(after: (() -> Unit)? = null) {
        if (profile.isBlank()) {
            profiles()
            return
        }
        loading("Your library")
        async({
            ProgressJournal(store).flush(api!!)
            ActivityJournal(store).flush(api!!)
            JSONObject(
                api!!.get(
                    "/api/tv/home/" +
                        Uri.encode(profile) +
                        "?view=" +
                        (if (parent) "parent" else "family") + "&paged=1&suggestionSession=" + suggestionSession
                )
            )
        }) {
            home = it
            if (after != null) after() else renderHome()
            val client=api;val currentProfile=profile
            if(client!=null && !playlistBootstrapBusy && !playlistWork.isShutdown) {
                playlistBootstrapBusy=true
                playlistWork.execute {
                    try { client.get("/api/your-tv/"+Uri.encode(currentProfile)) }
                    catch (_: Exception) { /* Browsing stays available while preparation is offline. */ }
                    finally { playlistBootstrapBusy=false }
                }
            }
        }
    }

    internal fun renderHome() {
        Palette.childMode = !parent
        searchQuery = ""
        allSearchActive = false
        ticket++
        page = "home"
        grids.clear()
        val c = ui.column(0)
        c.setPadding(ui.dp(36), ui.dp(16), ui.dp(36), ui.dp(28))
        root = c
        val toolbar = ui.line()
        toolbar.addView(ui.logo(104, 48).apply {
            (layoutParams as LinearLayout.LayoutParams).rightMargin=ui.dp(12)
        })
        navFirst = ui.addButton(toolbar, "Home") { selected = ""; activeSection = ""; currentSeries = ""; loadHome() }
        ui.addButton(toolbar, "Your TV") { showYourTv() }.apply {
            val gold=android.graphics.Color.rgb(255,211,95)
            val focusedGold=android.graphics.Color.rgb(255,229,153)
            val border=android.graphics.Color.rgb(0,111,133)
            background=ui.shape(gold)
            setTextColor(Palette.focusInk)
            setOnFocusChangeListener { _,focused ->
                background=ui.shape(if(focused) focusedGold else gold,12,if(focused) border else 0)
                setTextColor(Palette.focusInk)
                elevation=if(focused) ui.dp(3).toFloat() else 0f
            }
        }
        ui.addButton(toolbar, "Movies") { showSection("movies") }
        ui.addButton(toolbar, "Series") { showSection("series") }
        ui.addButton(toolbar, "More") { showSection("other") }
        toolbar.addView(View(this), LinearLayout.LayoutParams(0, 1, 1f))
        val searchButton = ui.addButton(toolbar, "⌕ Search") { askAllSearch() }
        val settingsButton = ui.addButton(toolbar, "⚙︎") { grownUps() }.apply {
            contentDescription = "Grown-up settings"
        }
        profileButton = settingsButton
        listOf(searchButton,settingsButton).forEach { button ->
            val backgroundColor=android.graphics.Color.rgb(52,74,98)
            val lightInk=android.graphics.Color.rgb(248,246,239)
            button.background=ui.shape(backgroundColor)
            button.setTextColor(lightInk)
            button.setOnFocusChangeListener { _,focused ->
                button.background=ui.shape(if (focused) Palette.cyan else backgroundColor)
                button.setTextColor(if (focused) Palette.focusInk else lightInk)
                button.elevation=if (focused) ui.dp(3).toFloat() else 0f
            }
        }
        val toolbarButtons=(0 until toolbar.childCount).mapNotNull { toolbar.getChildAt(it) as? TextView }
        toolbarButtons.forEach { button ->
            button.id=View.generateViewId()
            button.setSingleLine(true)
            button.textSize=15f
            button.setPadding(ui.dp(10),ui.dp(10),ui.dp(10),ui.dp(10))
            (button.layoutParams as LinearLayout.LayoutParams).rightMargin=ui.dp(6)
        }
        toolbarButtons.forEachIndexed { index,button ->
            if(index>0) button.nextFocusLeftId=toolbarButtons[index-1].id
            if(index<toolbarButtons.lastIndex) button.nextFocusRightId=toolbarButtons[index+1].id
        }
        c.addView(toolbar)
        ui.space(c, 12)
        val h = home ?: return
        val rows = h.array("rows").objects()
        val previews = h.array("homeShelves").objects()
        if (rows.isEmpty() && previews.isEmpty()) {
            c.addView(ui.text("Nothing approved for these preferences yet.", 23f))
            ui.space(c, 8)
            c.addView(
                ui.text(
                    "A parent can approve individual videos or mapped channels. Languages, hidden items and age preferences can also narrow this view.",
                    17f,
                    true,
                )
            )
            ui.space(c, 16)
            ui.addButton(c, "Open Parent review", true) { enterParent() }
        }
        rows.filter { parent || it.optString("id") in listOf("continue", "recommended") }.forEach { r ->
            val items=r.array("items").objects().filter(CommunityCatalog::supports).map(::Video)
            if (items.isEmpty()) return@forEach
            if (r.optString("id") == "continue") addShelf(c, r.getString("title"), items, grids.size)
            else addArtworkShelves(c, r.getString("title"), items)
        }
        if (!parent) previews.forEach { r ->
            addArtworkShelves(c, r.getString("title"), r.array("items").objects().filter(CommunityCatalog::supports).map(::Video))
        }
        ui.space(c, 12)
        if (parent) c.addView(
            ui.text(
                "Updated " +
                    DateFormat.getTimeInstance(DateFormat.SHORT)
                        .format(Date((h.optDouble("updatedAt") * 1000).toLong())) +
                    "  ·  " +
                    (if (parent && api?.isPaired == true) "Parent controls are unlocked temporarily."
                    else if (parent) "Parent view is shared access, not a lock."
                    else "Choose Movies, Series or More videos above."),
                13f,
                true,
            )
        )
        if (parent && h.getJSONObject("empty").optBoolean("local")) {
            ui.space(c, 8)
            c.addView(
                ui.text(
                    "Your family media folder has no available videos here. Add owned media on the server to enable native playback and Continue watching.",
                    14f,
                    true,
                )
            )
        }
        setContentView(ui.scroll(c))
        val chosen = selected
        c.postDelayed(
            {
                val target =
                    grids
                        .asSequence()
                        .mapNotNull { g ->
                            (0 until g.childCount)
                                .map { g.getChildAt(it) }
                                .firstOrNull { it.tag == chosen }
                        }
                        .firstOrNull()
                (target ?: grids.firstOrNull()?.getChildAt(0) ?: navFirst)?.requestFocus()
            },
            220,
        )
    }

    private fun enterParent() {
        val client = api ?: return
        if (!client.isPaired) { parent = true; activeSection = ""; currentSeries = ""; loadHome(); return }
        val input = ui.field("Parent PIN", true).apply {
            inputType = android.text.InputType.TYPE_CLASS_NUMBER or android.text.InputType.TYPE_NUMBER_VARIATION_PASSWORD
        }
        AlertDialog.Builder(this).setTitle("Grown-ups")
            .setMessage("Enter your parent PIN.")
            .setView(input)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Unlock") { _, _ ->
                val pin = input.text.toString()
                async({ client.unlockParent(pin) }) {
                    parent = true; activeSection = ""; currentSeries = ""
                    parentTimer.removeCallbacks(expireParent)
                    parentTimer.postDelayed(expireParent, 290_000)
                    loadHome()
                }
            }.show()
        input.requestFocus()
    }

    private fun grownUps() {
        val c = screen("Grown-ups", "Choose videos and manage your family’s settings.", adult = true)
        page = "grownups"
        val first = ui.addButton(c, if (parent) "Back to family view" else "Parent review", true) {
            if (parent) {
                val client = api
                parentTimer.removeCallbacks(expireParent)
                parent = false; activeSection = ""; currentSeries = ""
                work.execute { client?.lockParent() }
                loadHome()
            } else enterParent()
        }
        if (api?.isPaired == true && !parent) {
            ui.space(c, 12)
            ui.addButton(c, "Back to watching") { renderHome() }
            first.requestFocus()
            return
        }
        ui.space(c, 12)
        ui.addButton(c, "Switch profile · " + profileName) { profiles() }
        ui.space(c, 12)
        ui.addButton(c, "Restore hidden family videos") {
            val settings=JSONObject(home!!.getJSONObject("localSettings").toString()).put("dismissedIds",JSONArray())
            savePreferences(settings) { loadHome() }
        }
        ui.space(c, 12)
        ui.addButton(c, "Settings") { settings() }
        first.requestFocus()
    }

    private fun showYourTv() {
        activeSection="your-tv"; currentSeries=""; allSearchActive=false
        loading("Your TV")
        async({ JSONObject(api!!.get("/api/your-tv/"+Uri.encode(profile))) }) { data ->
            Palette.childMode=!parent;ticket++;page="your-tv";grids.clear()
            val c=ui.column(0).apply { setPadding(ui.dp(28),ui.dp(20),ui.dp(28),ui.dp(24)) }
            root=c;setContentView(ui.scroll(c))
            val header=ui.line()
            header.addView(ui.logo(100,42))
            header.addView(ui.text("Your TV",30f))
            header.addView(View(this),LinearLayout.LayoutParams(0,1,1f))
            navFirst=ui.addButton(header,"← Home") { activeSection="";renderHome() }
            c.addView(header);ui.space(c,16)
            val channels=data.array("channels").objects()
            if(channels.isEmpty()) {
                c.addView(ui.text("No channels are ready yet. Ask a grown-up to choose some videos.",22f,true))
                navFirst?.requestFocus()
            } else {
                val grid=GridLayout(this).apply {
                    columnCount=4
                    clipChildren=false;clipToPadding=false
                    useDefaultMargins=false
                }
                val statusViews=mutableMapOf<String,TextView>()
                fun label(ready: Int,total: Int,problem: Boolean): String = when {
                    total<=0 -> "Preparing playlist"
                    ready>=total -> "$ready/$total ready"
                    problem -> "$ready/$total ready · needs attention"
                    else -> "$ready/$total ready · preparing"
                }
                channels.forEachIndexed { index,channel ->
                    val card=ui.column(8).apply { isFocusable=true;isClickable=true;background=ui.shape(Palette.surface) }
                    val art=FrameLayout(this).apply { clipToOutline=true;background=ui.shape(Palette.surface,9) }
                    val illustration=when(channel.optString("artworkKey")) {
                        "music" -> R.drawable.channel_music
                        "animals" -> R.drawable.channel_animals
                        "space" -> R.drawable.channel_space
                        "science" -> R.drawable.channel_science
                        "cartoons" -> R.drawable.channel_cartoons
                        "making" -> R.drawable.channel_making
                        else -> R.drawable.channel_surprise
                    }
                    art.addView(ImageView(this).apply { setImageResource(illustration);scaleType=ImageView.ScaleType.CENTER_CROP },FrameLayout.LayoutParams(-1,-1))
                    val channelTitle=ui.text(channel.getString("name"),20f).apply {
                        setTextColor(android.graphics.Color.WHITE);setPadding(ui.dp(8),ui.dp(5),ui.dp(8),ui.dp(5))
                        setSingleLine(true);ellipsize=TextUtils.TruncateAt.END;background=ui.shape(0xD91A2A44.toInt(),0)
                    }
                    art.addView(channelTitle,FrameLayout.LayoutParams(-1,-2,Gravity.BOTTOM))
                    card.addView(art,LinearLayout.LayoutParams(-1,ui.dp(112)))
                    ui.space(card,8)
                    val first=channel.optString("firstTitle").takeUnless { it.isBlank() || it=="null" }
                    card.addView(ui.text(if(first==null) "Play channel" else "Starts with: $first",15f,true).apply { maxLines=2;ellipsize=TextUtils.TruncateAt.END })
                    val status=ui.text(label(channel.optInt("readyCount"),channel.optInt("totalCount"),channel.optBoolean("cacheProblem")),13f,true).apply { maxLines=1;ellipsize=TextUtils.TruncateAt.END }
                    card.addView(status);statusViews[channel.getString("id")]=status
                    card.contentDescription=channel.getString("name")+(if(first==null) ", play channel" else ", starts with $first")
                    card.setOnFocusChangeListener { v,focused -> v.background=ui.shape(Palette.surface,12,if(focused) Palette.focusBorder else 0);v.scaleX=if(focused)1.025f else 1f;v.scaleY=if(focused)1.025f else 1f }
                    card.setOnClickListener { startYourTvChannel(channel.getString("id"),channel.optString("offerId")) }
                    val layout=GridLayout.LayoutParams(GridLayout.spec(index/4),GridLayout.spec(index%4,1f)).apply {
                        width=0;height=ui.dp(198)
                        setMargins(ui.dp(4),ui.dp(4),ui.dp(10),ui.dp(10))
                    }
                    grid.addView(card,layout)
                }
                c.addView(grid,LinearLayout.LayoutParams(-1,-2))
                grid.post { if(page=="your-tv") grid.getChildAt(0)?.requestFocus() }
                val screenTicket=ticket;val currentProfile=profile;val client=api
                fun pollStatus() {
                    grid.postDelayed({
                        if(page!="your-tv" || ticket!=screenTicket || profile!=currentProfile || client==null || playlistWork.isShutdown)return@postDelayed
                        playlistWork.execute {
                            try {
                                val data=JSONObject(client.get("/api/your-tv/status/"+Uri.encode(currentProfile)))
                                runOnUiThread {
                                    if(page=="your-tv" && ticket==screenTicket) {
                                        data.array("channels").objects().forEach { s ->
                                            statusViews[s.optString("id")]?.text=label(s.optInt("readyCount"),s.optInt("totalCount"),s.optBoolean("cacheProblem"))
                                        }
                                    }
                                }
                            } catch (_: Exception) { /* Keep the last known readiness. */ }
                            if(!playlistWork.isShutdown)runOnUiThread { if(page=="your-tv" && ticket==screenTicket)pollStatus() }
                        }
                    },8000)
                }
                pollStatus()
            }
        }
    }

    private fun startYourTvChannel(id: String, offer: String) {
        loading("Starting channel")
        async({
            val payload=JSONObject().put("profileId",profile).put("channelId",id)
            if(offer.isNotBlank())payload.put("offerId",offer)
            try { JSONObject(api!!.post("/api/your-tv/start",payload)) }
            catch (failure: ApiFailure) { if(failure.status==409) JSONObject().put("refresh",true) else throw failure }
        }) { result ->
            if(result.optBoolean("refresh")) {
                Toast.makeText(this,"That programme changed. Choose a channel again.",Toast.LENGTH_LONG).show();showYourTv();return@async
            }
            val item=result.optJSONObject("item")
            if(item==null) { Toast.makeText(this,"No videos are ready. Ask a grown-up for help.",Toast.LENGTH_LONG).show();showYourTv() }
            else {
                val video=Video(item)
                play(video,video.progress?.optBoolean("completed") == true,result.getString("sessionId"),result.optString("channel"),result.optJSONObject("next")?.optString("title") ?: "")
            }
        }
    }

    private fun showSection(section: String) {
        allSearchActive = false
        val h = home ?: return
        activeSection = section
        currentSeries = ""
        searchQuery = ""
        val title = when (section) {
            "movies" -> "Movies"
            "series" -> "Series"
            else -> "More videos"
        }
        val subtitle = when (section) {
            "movies" -> "Pick a movie for your next adventure."
            "series" -> "Pick a series, then choose an episode."
            else -> "More from your family’s collection."
        }
        val c = screen(title, subtitle, compact = true)
        page = "section"
        grids.clear()
        navFirst = ui.addButton(c, "← Home") { activeSection = ""; renderHome() }
        ui.space(c, 14)
        val entries = h.optJSONObject("sections")?.array(section)?.objects()?.map(::Video) ?: emptyList()
        if (entries.isEmpty()) {
            c.addView(ui.text(if (h.optBoolean("jellyfinError") && section in listOf("movies", "series", "other")) "Your library is unavailable. Ask a grown-up to check the connection." else "Nothing here yet.", 22f, true))
            navFirst?.requestFocus()
        } else {
            addArtworkShelves(c, "All $title", entries)
            c.postDelayed({ grids.firstOrNull()?.getChildAt(0)?.requestFocus() }, 220)
        }
    }

    private fun askAllSearch() {
        val input=ui.field("Find a movie, series or video", value=if (allSearchActive) searchQuery else "").apply {
            inputType=android.text.InputType.TYPE_CLASS_TEXT
            setSingleLine(true)
        }
        AlertDialog.Builder(this).setTitle("Search Kidoo").setView(input)
            .setPositiveButton("Search") { _,_ -> showAllSearchResults(input.text.toString(), 0) }
            .setNegativeButton("Cancel",null).show()
    }

    private fun showAllSearchResults(query: String, start: Int = 0) {
        searchQuery=query.trim(); allSearchActive=true; activeSection=""; currentSeries=""; onlinePageStart=start
        val selectedQuery=searchQuery
        val c=screen("Search Kidoo", if(selectedQuery.isBlank()) "Your available videos" else "Results for “$selectedQuery”",compact=true)
        page="all-search"; grids.clear()
        val controls=ui.line()
        navFirst=ui.addButton(controls,"← Home") { renderHome() }
        ui.addButton(controls,"Search again") { askAllSearch() }
        c.addView(controls);ui.space(c,14)
        var found=false
        val sections=home?.optJSONObject("sections")
        for ((key,title) in listOf("movies" to "Movies", "series" to "Series", "other" to "More videos")) {
            val matches=sections?.array(key)?.objects()?.filter(CommunityCatalog::supports)
                ?.map(::Video)?.filter { SearchCatalog.matches(it,selectedQuery) } ?: emptyList()
            if(matches.isNotEmpty()) { addArtworkShelves(c,title,matches);found=true }
        }
        if(!found) c.addView(ui.text("No matches yet. Try another search.",22f,true))
        c.postDelayed({ grids.firstOrNull()?.getChildAt(0)?.requestFocus() ?: navFirst?.requestFocus() },220)
    }

    private fun showSeries(series: Video) {
        currentSeries = series.key
        val c = screen(series.title, "Choose an episode.", compact = true)
        page = "series"
        grids.clear()
        navFirst = ui.addButton(c, "← Series") { showSection("series") }
        ui.space(c, 14)
        val episodes = series.raw.array("episodes").objects().map(::Video)
        if (episodes.isEmpty()) c.addView(ui.text("No approved episodes yet.", 22f, true))
        else {
            val inProgress = episodes.filter { (it.progress?.optDouble("position") ?: 0.0) > 0 && it.progress?.optBoolean("completed") != true }
            if (inProgress.isNotEmpty()) addShelf(c, "Continue watching", inProgress, 0)
            addShelf(c, "All episodes", episodes, if (inProgress.isEmpty()) 0 else 1)
            c.postDelayed({ grids.firstOrNull()?.getChildAt(0)?.requestFocus() }, 220)
        }
    }

    private fun addArtworkShelves(parentView: LinearLayout, title: String, videos: List<Video>) {
        ArtworkLayout.shelves(videos).forEachIndexed { index, (portrait, items) ->
            addShelf(parentView, if (index == 0) title else "$title · More", items, grids.size, portrait)
        }
    }

    private fun addShelf(
        parentView: LinearLayout,
        title: String,
        videos: List<Video>,
        rowIndex: Int,
        posterCards: Boolean = false,
    ) {
        parentView.addView(ui.text(title, 23f))
        ui.space(parentView, 8)
        val grid =
            HorizontalGridView(this).apply {
                setNumRows(1)
                setHorizontalSpacing(ui.dp(14))
                clipChildren = false
                clipToPadding = false
                setPadding(ui.dp(5), ui.dp(7), ui.dp(8), ui.dp(7))
                itemAnimator = null
                setHasFixedSize(true)
            }
        grids.add(grid)
        grid.adapter =
            object : RecyclerView.Adapter<CardHolder>() {
                override fun getItemCount() = videos.size

                override fun onCreateViewHolder(p: ViewGroup, type: Int): CardHolder {
                    val card =
                        ui.column(5).apply {
                            isFocusable = true
                            isClickable = true
                            background = ui.shape(Palette.surface)
                            layoutParams = ViewGroup.LayoutParams(ui.dp(if (posterCards) 150 else 212), ui.dp(if (posterCards) 280 else 190))
                        }
                    val image =
                        ImageView(this@MainActivity).apply {
                            clipToOutline = true
                            background = ui.shape(Palette.surface, 9)
                        }
                    card.addView(image, LinearLayout.LayoutParams(-1, ui.dp(if (posterCards) 190 else 100)))
                    val name =
                        ui.text("", 18f).apply {
                            maxLines = 2
                            ellipsize = TextUtils.TruncateAt.END
                        }
                    ui.space(card, 7)
                    card.addView(name)
                    val source = ui.text("", 14f, true).apply { maxLines=1; ellipsize=TextUtils.TruncateAt.END }
                    card.addView(source)
                    val progress=ProgressBar(this@MainActivity,null,android.R.attr.progressBarStyleHorizontal).apply {
                        max=100; visibility=View.INVISIBLE; progressTintList=android.content.res.ColorStateList.valueOf(Palette.focusBorder)
                    }
                    card.addView(progress, LinearLayout.LayoutParams(-1, ui.dp(4)).apply { topMargin=ui.dp(4) })
                    return CardHolder(card, image, name, source, progress)
                }

                override fun onBindViewHolder(holder: CardHolder, position: Int) {
                    val video = videos[position]
                    holder.itemView.tag = video.key
                    holder.name.text = video.title
                    val progress=video.progress
                    val playbackPosition=progress?.optDouble("position",0.0) ?: 0.0
                    val duration=progress?.optDouble("duration",0.0) ?: 0.0
                    val inProgress=playbackPosition>0 && progress?.optBoolean("completed") != true
                    holder.source.text = when {
                        inProgress && duration>playbackPosition -> "${kotlin.math.ceil((duration-playbackPosition)/60).toInt().coerceAtLeast(1)} min left"
                        inProgress -> "Continue watching"
                        video.series -> "${video.raw.array("episodes").length()} episodes"
                        video.durationSeconds > 0 -> VideoTime.durationLabel(video.durationSeconds)
                        video.raw.optString("mediaType") == "Episode" -> "Episode"
                        video.raw.optString("topic").isNotBlank() -> video.raw.optString("topic")
                        else -> ""
                    }
                    holder.progress.visibility=if(inProgress && duration>0) View.VISIBLE else View.INVISIBLE
                    holder.progress.progress=if(duration>0) ((playbackPosition/duration)*100).toInt().coerceIn(0,100) else 0
                    holder.itemView.contentDescription = video.title + ". " + holder.source.text
                    val backdrop=video.raw.optString("heroImage", "").takeUnless { it.isBlank() || it == "null" }
                    val useBackdrop=!posterCards && backdrop != null
                    Artwork.show(holder.image, if (useBackdrop) backdrop!! else video.thumbnail, api!!,
                        fit = !useBackdrop && (posterCards || video.local || video.jellyfin || video.series))
                    holder.itemView.setOnClickListener { if (video.series) showSeries(video) else details(video) }
                    holder.itemView.setOnFocusChangeListener { v, f ->
                        v.background = ui.shape(Palette.surface, 12, if (f) Palette.focusBorder else 0)
                        v.scaleX = if (f) 1.025f else 1f
                        v.scaleY = if (f) 1.025f else 1f
                        v.elevation = if (f) ui.dp(4).toFloat() else 0f
                        if (f) {
                            selected = video.key
                            v.requestRectangleOnScreen(
                                android.graphics.Rect(0, 0, v.width, v.height)
                            )
                        }
                    }
                    holder.itemView.setOnKeyListener { _, code, event ->
                        if (event.action != KeyEvent.ACTION_DOWN) false
                        else
                            when (code) {
                                KeyEvent.KEYCODE_DPAD_UP -> {
                                    if (rowIndex == 0) navFirst?.requestFocus()
                                    else focusRow(rowIndex - 1, position)
                                    true
                                }
                                KeyEvent.KEYCODE_DPAD_DOWN -> {
                                    if (rowIndex + 1 < grids.size) focusRow(rowIndex + 1, position)
                                    true
                                }
                                else -> false
                            }
                    }
                }
            }
        parentView.addView(grid, LinearLayout.LayoutParams(-1, ui.dp(if (posterCards) 298 else 210)))
        ui.space(parentView, 14)
    }

    private fun focusRow(index: Int, position: Int) {
        val g = grids[index]
        val pos = position.coerceAtMost((g.adapter?.itemCount ?: 1) - 1)
        g.setSelectedPosition(pos)
        g.post {
            g.findViewHolderForAdapterPosition(pos)?.itemView?.requestFocus() ?: g.requestFocus()
        }
    }

    class CardHolder(v: View, val image: ImageView, val name: TextView, val source: TextView, val progress: ProgressBar) :
        RecyclerView.ViewHolder(v)

    internal fun details(video: Video) {
        if (!video.hosted) { renderHome(); return }
        detailsKey = video.key
        selected = video.key
        val c =
            screen(
                video.title,
                if (!parent) displayCreator(video) else video.creator +
                    "  ·  " +
                    if (video.jellyfin) "From your Jellyfin library" else "Hosted by your family",
            )
        page = "detail"
        val line = ui.line()
        val art =
            ImageView(this).apply {
                clipToOutline = true
                background = ui.shape(Palette.surface)
            }
        line.addView(art, LinearLayout.LayoutParams(ui.dp(312), ui.dp(176)))
        Artwork.show(art, video.thumbnail, api!!, fit = video.local || video.jellyfin || video.series)
        val copy = ui.column(18)
        val summary=video.raw.optString("summary", "").takeUnless { it.isBlank() || it == "null" }
        if (video.durationSeconds > 0) copy.addView(ui.text("Length: " + VideoTime.durationLabel(video.durationSeconds), 18f, true))
        if (summary != null) copy.addView(ui.text(summary, 21f))
        if (parent) ui.space(copy, 14)
        if (parent) copy.addView(
            ui.text(
                if (video.jellyfin) "Progress is saved for $profileName in Jellyfin."
                else "Progress is saved for $profileName on your Kidoo server.",
                16f,
                true,
            )
        )
        line.addView(copy, LinearLayout.LayoutParams(0, -2, 1f))
        c.addView(line)
        ui.space(c, 20)
        val actions = ui.line()
        val playable = video.hosted
        val play =
            ui.addButton(
                actions,
                if (
                    playable &&
                        (video.progress?.optDouble("position") ?: 0.0) > 0 &&
                        video.progress?.optBoolean("completed") != true
                )
                    "Resume"
                else if (playable) "Play" else "Ask a grown-up",
                true,
            ) {
                if (playable) play(video, false)
                else AlertDialog.Builder(this).setTitle("Not available yet")
                    .setMessage("Ask a grown-up to check whether this video can be played on Kidoo.")
                    .setPositiveButton("OK", null).show()
            }
        if (playable && (video.progress?.optDouble("position") ?: 0.0)>0) ui.addButton(actions, "Start over") { play(video, true) }
        ui.addButton(actions, if (video.favourite) "Remove favourite" else "Favourite") {
            val kind=if(video.jellyfin) "jellyfin" else "local"
            async({ api!!.post("/api/tv/favourite",JSONObject().put("profileId",profile).put("kind",kind)
                .put("itemId",video.id.toString()).put("favourite",!video.favourite)) }) { refreshDetail(video.key) }
        }
        if (!video.jellyfin && (parent || api?.isPaired != true)) ui.addButton(actions, "Hide for $profileName") { changePreference(video, "dismissedIds") }
        c.addView(actions)
        ui.space(c, 14)
        if (parent) {
            val controls = ui.line()
            ui.addButton(
                controls,
                if (video.raw.optBoolean(if (video.hosted || api?.isPaired == true) "approved" else "individuallyApproved"))
                    "Remove approval"
                else "Approve video",
            ) {
                approve(video)
            }
            c.addView(controls)
            ui.space(c, 12)
            c.addView(
                ui.text(
                    if (video.raw.optJSONObject("channel") != null)
                        if (api?.isPaired == true) "Removing approval blocks this video for this profile, even if its channel is greenlit." else "A greenlit channel may still approve this video after its individual approval is removed. Hide affects this profile only."
                    else if (api?.isPaired == true) "Approval applies to this profile. Profile preferences still apply."
                    else "Approvals are shared by the family. Profile preferences still apply.",
                    15f,
                    true,
                )
            )
        }
        play.requestFocus()
    }

    @androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
    private fun play(video: Video, restart: Boolean, channelSession: String = "", channelName: String = "", nextTitle: String = "") {
        page = "player"
        startActivity(
            Intent(this, PlayerActivity::class.java)
                .putExtra("profile", profile)
                .putExtra("media", video.id.toString())
                .putExtra("kind", if (video.jellyfin) "jellyfin" else "local")
                .putExtra("restart", restart)
                .putExtra("title", video.title)
                .putExtra("channelSession",channelSession)
                .putExtra("channelName",channelName)
                .putExtra("nextTitle",nextTitle)
                .putExtra("favourite",video.favourite)
                .putExtra("duration",(video.durationSeconds*1000).toLong())
                .putExtra(
                    "position",
                    if (restart) 0L
                    else
                        ProgressPolicy.resume(
                            ((video.progress?.optDouble("position") ?: 0.0) * 1000).toLong(),
                            ((video.progress?.optDouble("duration") ?: 0.0) * 1000).toLong(),
                            video.progress?.optBoolean("completed") == true,
                        ),
                )
        )
    }

    private fun approve(v: Video) {
        if (api?.isPaired == true) {
            val kind = if (v.jellyfin) "jellyfin" else "local"
            val approved = !v.raw.optBoolean("approved")
            val data = JSONObject().put("profileId", profile).put("kind",kind).put("itemId",v.id.toString()).put("approved",approved)
            async({ api!!.post("/api/security/approval", data) }) { refreshDetail(v.key) }
            return
        }
        val endpoint = if (v.jellyfin) "/api/jellyfin/approval" else "/api/approval"
        val data =
            JSONObject()
                .put(if (v.jellyfin) "itemId" else "mediaId", v.id)
                .put("profileId", profile)
                .put(
                    "approved",
                    !v.raw.optBoolean(if (v.hosted) "approved" else "individuallyApproved"),
                )
        async({ api!!.post(endpoint, data) }) { if (v.jellyfin) browseJellyfin(jellyfinPage) else refreshDetail(v.key) }
    }

    private fun browseJellyfin(start: Int) {
        jellyfinPage = start
        loading("Jellyfin library")
        async({ JSONObject(api!!.get("/api/jellyfin/catalogue/" + Uri.encode(profile) + "?start=" + start + "&limit=50")) }) { result ->
            val total = result.optInt("total")
            val c = screen("Jellyfin library", "Showing ${start + 1}–${minOf(start + 50, total)} of $total. Family view includes approved videos only.")
            page = "jellyfin"
            val controls = ui.line()
            if (start > 0) ui.addButton(controls, "Previous") { browseJellyfin((start - 50).coerceAtLeast(0)) }
            if (start + 50 < total) ui.addButton(controls, "Next") { browseJellyfin(start + 50) }
            ui.addButton(controls, "Home") { loadHome() }
            c.addView(controls); ui.space(c, 12)
            val entries = result.array("items").objects().filter { parent || it.optBoolean("approved") }
            if (entries.isEmpty()) c.addView(ui.text("No approved videos on this page.", 20f, true))
            entries.forEach { item ->
                val id = item.getString("id").removePrefix("jellyfin:")
                val progress = item.optJSONObject("progress") ?: JSONObject()
                progress.put("duration", item.optDouble("duration", 0.0))
                val video = Video(JSONObject().put("id", id).put("key", "jellyfin:" + id).put("kind", "jellyfin")
                    .put("title", item.optString("title")).put("summary", item.optString("overview"))
                    .put("creator", item.optString("series", "Jellyfin")).put("approved", item.optBoolean("approved"))
                    .put("thumbnail", if (item.optBoolean("hasArtwork")) "/api/jellyfin/artwork/" + Uri.encode(profile) + "/" + id else "")
                    .put("progress", progress))
                ui.addButton(c, (if (video.raw.optBoolean("approved")) "✓ " else "") + video.title) { details(video) }
                ui.space(c, 8)
            }
        }
    }

    private fun refreshDetail(key: String) {
        loadHome {
            val all = home!!.array("local").objects() + home!!.array("jellyfin").objects()
            val v = all.firstOrNull { it.optString("key") == key }
            if (v == null) renderHome() else details(Video(v))
        }
    }

    private fun changePreference(v: Video, field: String) {
        val h = home ?: return
        val settings =
            JSONObject(h.getJSONObject("localSettings").toString())
        settings.put(field, settings.array(field).toggled(v.id))
        savePreferences(settings) {
            if (field == "dismissedIds") loadHome() else refreshDetail(v.key)
        }
    }

    private fun savePreferences(s: JSONObject, done: () -> Unit) {
        async({
            api!!.post(
                "/api/tv/local-preferences",
                JSONObject().put("profileId", profile).put("settings", s),
            )
        }) {
            done()
        }
    }

    internal fun settings() {
        val c = screen("Settings", "Kidoo native TV 0.2.0", adult = true)
        page = "settings"
        ui.addButton(c, "Refresh library", true) { loadHome() }
        ui.space(c, 10)
        ui.addButton(c, "Switch profile") { profiles() }
        ui.space(c, 10)
        ui.addButton(c, "Change server or password") { connection() }
        ui.space(c, 10)
        ui.addButton(c, "Sign out and forget connection") {
            AlertDialog.Builder(this)
                .setTitle("Forget this connection?")
                .setMessage(
                    "Server profiles, approvals and history stay on the server. Unsent progress on this device will be removed."
                )
                .setPositiveButton("Forget") { _, _ ->
                    store.clear()
                    api = null
                    profile = ""
                    connection()
                }
                .setNegativeButton("Cancel", null)
                .show()
        }
        ui.space(c, 22)
        c.addView(
            ui.text(
                (if (api?.isPaired == true) "This TV is paired securely to one profile. Parent access locks automatically."
                 else "Trusted network only\nShared password and HTTP are prototype limitations. Parent view and profile switching are not secure access controls.") +
                    "\n\nJellyfin library connections are managed on the Kidoo server.\n\nPending progress updates: " +
                    ProgressJournal(store).pending(),
                18f,
                true,
            )
        )
        c.getChildAt(4)?.requestFocus()
    }

    override fun onKeyDown(code: Int, event: KeyEvent): Boolean {
        if (code == KeyEvent.KEYCODE_MENU && home != null) {
            if (api?.isPaired == true && !parent) grownUps() else settings()
            return true
        }
        return super.onKeyDown(code, event)
    }

    @Deprecated("TV Back")
    override fun onBackPressed() {
        when (page) {
            "home" -> profiles()
            "section", "your-tv" -> { activeSection = ""; renderHome() }
            "all-search" -> renderHome()
            "series" -> if (allSearchActive) showAllSearchResults(searchQuery,onlinePageStart) else showSection("series")
            "detail" -> {
                val show = home?.optJSONObject("sections")?.array("series")?.objects()?.firstOrNull { it.optString("key") == currentSeries }
                if (show != null) showSeries(Video(show))
                else if (allSearchActive) showAllSearchResults(searchQuery,onlinePageStart)
                else if (activeSection.isNotEmpty()) showSection(activeSection) else renderHome()
            }
            "profiles" -> if (home != null) renderHome() else finish()
            "connect" -> if (home != null) renderHome() else finish()
            "player" -> loadHome()
            "error" -> connectExisting()
            else -> if (home != null) renderHome() else connection()
        }
    }

    override fun onStop() {
        if (page != "player" && !isChangingConfigurations) refreshSuggestionsOnResume = true
        if (parent && api?.isPaired == true) {
            val client = api
            parentTimer.removeCallbacks(expireParent)
            parent = false; relockPending = true
            work.execute { client?.lockParent() }
        }
        super.onStop()
    }

    override fun onDestroy() {
        parentTimer.removeCallbacks(expireParent)
        ticket++
        work.shutdownNow()
        playlistWork.shutdownNow()
        super.onDestroy()
    }
}
