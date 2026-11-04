package org.kidoo.tv

import android.app.Activity
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.util.LruCache
import android.view.Gravity
import android.view.View
import android.widget.*
import okhttp3.Request

object Palette {
    var childMode = false
    val background get() = if (childMode) Color.rgb(248, 246, 239) else Color.rgb(9, 17, 36)
    val surface get() = if (childMode) Color.rgb(231, 237, 244) else Color.rgb(19, 33, 57)
    val ink get() = if (childMode) Color.rgb(20, 35, 57) else Color.rgb(245, 242, 233)
    val muted get() = if (childMode) Color.rgb(75, 96, 117) else Color.rgb(172, 190, 211)
    val cyan = Color.rgb(103, 220, 233)
    val focusInk = Color.rgb(9, 17, 36)
    val focusBorder get() = if (childMode) Color.rgb(0, 111, 133) else cyan
    val purple get() = if (childMode) Color.rgb(218, 213, 247) else Color.rgb(117, 94, 174)
}

class TvUi(val activity: Activity) {
    fun dp(n: Int) = (n * activity.resources.displayMetrics.density).toInt()

    private val font: Typeface =
        if (Build.VERSION.SDK_INT >= 26) activity.resources.getFont(R.font.outfit)
        else Typeface.create("sans-serif", Typeface.NORMAL)

    fun text(value: String, size: Float = 18f, muted: Boolean = false): TextView =
        TextView(activity).apply {
            text = value
            textSize = size
            setTextColor(if (muted) Palette.muted else Palette.ink)
            typeface =
                if (Build.VERSION.SDK_INT >= 28)
                    Typeface.create(font, if (size >= 23f) 600 else 500, false)
                else font
            if (Build.VERSION.SDK_INT >= 26)
                fontVariationSettings = "'wght' " + if (size >= 23f) 600 else 500
            includeFontPadding = false
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_YES
        }

    fun shape(color: Int, radius: Int = 12, stroke: Int = 0) =
        GradientDrawable().apply {
            setColor(color)
            cornerRadius = dp(radius).toFloat()
            if (stroke != 0) setStroke(dp(3), stroke)
        }

    fun column(padding: Int = 0) =
        LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(padding), dp(padding), dp(padding), dp(padding))
            clipChildren = false
            clipToPadding = false
        }

    fun line() =
        LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            clipChildren = false
            clipToPadding = false
        }

    fun space(parent: LinearLayout, n: Int) {
        parent.addView(View(activity), LinearLayout.LayoutParams(1, dp(n)))
    }

    fun button(label: String, primary: Boolean = false, action: () -> Unit): TextView =
        text(label, 17f).apply {
            gravity = Gravity.CENTER
            setPadding(dp(18), dp(11), dp(18), dp(11))
            isFocusable = true
            isClickable = true
            background = shape(if (primary) Palette.purple else Palette.surface)
            setOnFocusChangeListener { _, focused ->
                background =
                    shape(
                        if (focused) Palette.cyan
                        else if (primary) Palette.purple else Palette.surface
                    )
                setTextColor(if (focused) Palette.focusInk else Palette.ink)
                elevation = if (focused) dp(3).toFloat() else 0f
            }
            setOnClickListener { action() }
            contentDescription = label
        }

    fun addButton(
        parent: LinearLayout,
        label: String,
        primary: Boolean = false,
        action: () -> Unit,
    ): TextView {
        val b = button(label, primary, action)
        parent.addView(b, LinearLayout.LayoutParams(-2, -2).apply { setMargins(0, 0, dp(10), 0) })
        return b
    }

    fun scroll(content: View): ScrollView =
        ScrollView(activity).apply {
            isFillViewport = true
            clipChildren = false
            clipToPadding = false
            addView(content)
            setBackgroundColor(Palette.background)
        }

    fun logo(width: Int = 124, height: Int = 65): ImageView =
        ImageView(activity).apply {
            setImageResource(if (Build.VERSION.SDK_INT >= 24) R.drawable.kidoo_wordmark_vector else R.drawable.kidoo_wordmark)
            // Retain filtered scaling for the bitmap fallback on older devices.
            (drawable as? BitmapDrawable)?.apply {
                bitmap.setHasMipMap(true)
                paint.isFilterBitmap = true
                paint.isAntiAlias = true
            }
            scaleType = ImageView.ScaleType.FIT_CENTER
            layoutParams =
                LinearLayout.LayoutParams(dp(width), dp(height)).apply { rightMargin = dp(24) }
            contentDescription = "Kidoo, your own TV"
        }

    fun field(hintText: String, secret: Boolean = false, value: String = "") =
        EditText(activity).apply {
            hint = hintText
            setText(value)
            setTextColor(Palette.ink)
            setHintTextColor(Palette.muted)
            textSize = 19f
            typeface = font
            if (Build.VERSION.SDK_INT >= 26) fontVariationSettings = "'wght' 500"
            setSingleLine(true)
            inputType =
                if (secret)
                    android.text.InputType.TYPE_CLASS_TEXT or
                        android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
                else
                    android.text.InputType.TYPE_CLASS_TEXT or
                        android.text.InputType.TYPE_TEXT_VARIATION_URI
            setPadding(dp(16), dp(12), dp(16), dp(12))
            background = shape(Palette.surface)
            contentDescription = hintText
        }
}

/** Bounded image loading, no credentials on external image hosts and no UI-thread I/O. */
object Artwork {
    private val work =
        java.util.concurrent.ThreadPoolExecutor(
            2,
            2,
            0,
            java.util.concurrent.TimeUnit.SECONDS,
            java.util.concurrent.ArrayBlockingQueue(64),
            java.util.concurrent.ThreadPoolExecutor.DiscardOldestPolicy(),
        )
    private val cache =
        object : LruCache<String, Bitmap>(12 * 1024 * 1024) {
            override fun sizeOf(key: String, value: Bitmap) = value.byteCount
        }

    fun show(view: ImageView, url: String, api: KidooApi, fit: Boolean = false) {
        view.tag = url
        view.setImageDrawable(PlaceholderArtwork())
        view.scaleType = if (fit) ImageView.ScaleType.FIT_CENTER else ImageView.ScaleType.CENTER_CROP
        if (url.isBlank()) return
        val full = if (url.startsWith("/")) api.base + url else url
        cache.get(full)?.let {
            view.setImageBitmap(it)
            return
        }
        work.execute {
            val image =
                try {
                    val request = Request.Builder().url(full)
                    if (full.startsWith(api.base + "/"))
                        api.headers().forEach { (k, v) -> request.header(k, v) }
                    api.http.newCall(request.build()).execute().use { response ->
                    if (response.code != 200 || response.body == null) null
                    else
                        response.body!!.byteStream().use { input ->
                            val out = java.io.ByteArrayOutputStream()
                            val buf = ByteArray(8192)
                            while (true) {
                                val n = input.read(buf)
                                if (n < 0) break
                                if (out.size() + n > 3 * 1024 * 1024) throw java.io.IOException()
                                out.write(buf, 0, n)
                            }
                            val bytes = out.toByteArray()
                            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                            if (
                                bounds.outWidth <= 0 ||
                                    bounds.outWidth > 8192 ||
                                    bounds.outHeight > 8192
                            )
                                null
                            else
                                BitmapFactory.decodeByteArray(
                                    bytes,
                                    0,
                                    bytes.size,
                                    BitmapFactory.Options().apply {
                                        inSampleSize =
                                            (maxOf(bounds.outWidth, bounds.outHeight) / 640)
                                                .coerceAtLeast(1)
                                    },
                                )
                        }
                    }
                } catch (_: Exception) {
                    null
                }
            if (image != null) {
                cache.put(full, image)
                view.post { if (view.tag == url) view.setImageBitmap(image) }
            }
        }
    }
}

/** Deliberately abstract fallback; never fabricated programme artwork. */
class PlaceholderArtwork : android.graphics.drawable.Drawable() {
    private val paint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG)

    override fun draw(c: android.graphics.Canvas) {
        val b = bounds
        paint.style = android.graphics.Paint.Style.FILL
        paint.color = Palette.surface
        c.drawRect(b, paint)
        paint.color = 0x284C7AA2
        val r = b.height() * .65f
        c.drawCircle(b.right * .85f, b.top.toFloat(), r, paint)
        paint.color = 0x33675D9A
        c.drawCircle(b.left + b.width() * .15f, b.bottom.toFloat(), r * .8f, paint)
        paint.color = 0x9987A8C1.toInt()
        val x = b.centerX().toFloat()
        val y = b.centerY().toFloat()
        val d = b.height() * .12f
        val path = android.graphics.Path()
        path.moveTo(x - d * .5f, y - d)
        path.lineTo(x + d, y)
        path.lineTo(x - d * .5f, y + d)
        path.close()
        c.drawPath(path, paint)
    }

    override fun setAlpha(alpha: Int) {}

    override fun setColorFilter(filter: android.graphics.ColorFilter?) {}

    @Deprecated("Deprecated in Java")
    override fun getOpacity() = android.graphics.PixelFormat.OPAQUE
}
