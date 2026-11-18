package io.github.calleograph.mfagate.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Шифрование секретов (device_secret, TOTP) для хранения. Интерфейс — шов для JVM-тестов. */
interface Sealer {
    fun seal(plain: String, aad: String): String
    /** null — значение повреждено или ключ утрачен. */
    fun open(sealed: String, aad: String): String?
}

/**
 * AES-256-GCM ключом из Android Keystore (ключ не покидает защищённое хранилище). Формат base64(iv || ct).
 * AAD = "<вид>.<id>": секрет одной записи не расшифруется под другой.
 */
class SecretBox(private val alias: String = "mfa_gate_secrets") : Sealer {
    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(alias, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return gen.generateKey()
    }

    override fun seal(plain: String, aad: String): String {
        val c = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()); updateAAD(aad.toByteArray()) }
        return Base64.getEncoder().encodeToString(c.iv + c.doFinal(plain.toByteArray()))
    }

    override fun open(sealed: String, aad: String): String? = try {
        val raw = Base64.getDecoder().decode(sealed)
        val c = Cipher.getInstance("AES/GCM/NoPadding").apply {
            init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, raw, 0, 12)); updateAAD(aad.toByteArray())
        }
        String(c.doFinal(raw, 12, raw.size - 12))
    } catch (_: Exception) {
        null
    }
}
