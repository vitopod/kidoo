package org.kidoo.tv

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import org.json.JSONObject

/** Device-bound AES-GCM; no backup, logs, Intent credentials or external files. */
class SecureStore(context: Context) {
    private val prefs = context.getSharedPreferences("native_secure", 0)

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        return (store.getKey("kidoo.native.v1", null) as? SecretKey)
            ?: KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
                init(
                    KeyGenParameterSpec.Builder(
                            "kidoo.native.v1",
                            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                        )
                        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .build()
                )
                generateKey()
            }
    }

    @Synchronized
    fun write(name: String, value: JSONObject) {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, key())
        c.updateAAD(name.toByteArray())
        prefs
            .edit()
            .putString(
                name,
                Base64.encodeToString(
                    c.iv + c.doFinal(value.toString().toByteArray()),
                    Base64.NO_WRAP,
                ),
            )
            .commit()
    }

    @Synchronized
    fun read(name: String): JSONObject? =
        try {
            val b = Base64.decode(prefs.getString(name, null) ?: return null, Base64.NO_WRAP)
            val c = Cipher.getInstance("AES/GCM/NoPadding")
            c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, b.copyOfRange(0, 12)))
            c.updateAAD(name.toByteArray())
            JSONObject(String(c.doFinal(b.copyOfRange(12, b.size))))
        } catch (_: Exception) {
            null
        }

    fun clear() {
        prefs.edit().clear().commit()
    }
}
