package org.kidoo.tv

import android.app.Activity
import android.app.Instrumentation
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Bundle

/** Bounded raw PCM output probe, independent of Media3, network, codecs and family data. */
object PlatformAudioProbe {
    fun run(test: Instrumentation) {
        val result = Bundle()
        var track: AudioTrack? = null
        val manager = test.targetContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val focus =
            AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build()
                )
                .setOnAudioFocusChangeListener {}
                .build()
        try {
            val activity =
                test.startActivitySync(
                    Intent(test.targetContext, MainActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            test.runOnMainSync {
                activity.window.addFlags(
                    android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                )
            }
            val granted = manager.requestAudioFocus(focus)
            val rate = 48000
            val format =
                AudioFormat.Builder()
                    .setSampleRate(rate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .build()
            track =
                AudioTrack.Builder()
                    .setAudioAttributes(
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_MEDIA)
                            .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                            .build()
                    )
                    .setAudioFormat(format)
                    .setBufferSizeInBytes(
                        maxOf(
                            16384,
                            AudioTrack.getMinBufferSize(
                                rate,
                                AudioFormat.CHANNEL_OUT_STEREO,
                                AudioFormat.ENCODING_PCM_16BIT,
                            ),
                        )
                    )
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .build()
            val tone =
                ShortArray(4096) { i ->
                    (kotlin.math.sin(2 * Math.PI * 660 * (i / 2) / rate) * 160).toInt().toShort()
                }
            track.play()
            var submitted = 0
            val end = System.currentTimeMillis() + 3500
            while (System.currentTimeMillis() < end) {
                val n = track.write(tone, 0, tone.size, AudioTrack.WRITE_NON_BLOCKING)
                if (n > 0) submitted += n
                Thread.sleep(15)
            }
            val played = track.playbackHeadPosition
            result.putString(
                "result",
                if (played > 0)
                    "PASS: raw Android PCM output clock advances independently of Media3"
                else
                    "FAIL: raw Android PCM output clock also remains at zero; not specific to Kidoo/Media3",
            )
            result.putInt("playedFrames", played)
            result.putInt("submittedSamples", submitted)
            result.putInt("focusResult", granted)
            result.putInt("playState", track.playState)
            val d = track.routedDevice
            result.putInt("routedDeviceType", d?.type ?: -1)
        } catch (e: Throwable) {
            result.putString("result", "FAIL: " + e.javaClass.simpleName)
        } finally {
            try {
                track?.stop()
                track?.release()
            } catch (_: Exception) {}
            manager.abandonAudioFocusRequest(focus)
        }
        test.finish(Activity.RESULT_OK, result)
    }
}
