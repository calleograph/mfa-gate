package twofold.authenticator.data

import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyPairGenerator
import java.security.ProviderException
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * Ключ подтверждения ECDSA P-256 в AndroidKeyStore. Закрытый ключ не экспортируется,
 * каждая подпись требует биометрии (или PIN устройства на Android 11+) через BiometricPrompt.CryptoObject.
 */
object DeviceKeys {
    private fun store() = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    /** Создаёт ключ и возвращает открытую часть: SubjectPublicKeyInfo в DER. */
    fun generate(alias: String): ByteArray {
        fun spec(strongBox: Boolean): KeyGenParameterSpec {
            val b = KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setUserAuthenticationRequired(true)
            if (Build.VERSION.SDK_INT >= 30) {
                b.setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG or KeyProperties.AUTH_DEVICE_CREDENTIAL)
            } else {
                // -1: подтверждение на каждую операцию, только сильная биометрия через CryptoObject.
                @Suppress("DEPRECATION")
                b.setUserAuthenticationValidityDurationSeconds(-1)
            }
            if (strongBox && Build.VERSION.SDK_INT >= 28) b.setIsStrongBoxBacked(true)
            return b.build()
        }
        val gen = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
        val pair = if (Build.VERSION.SDK_INT >= 28) {
            try {
                gen.initialize(spec(strongBox = true)); gen.generateKeyPair()
            } catch (_: ProviderException) { // StrongBoxUnavailableException и отказы StrongBox на отдельных моделях
                gen.initialize(spec(strongBox = false)); gen.generateKeyPair()
            }
        } else {
            gen.initialize(spec(strongBox = false)); gen.generateKeyPair()
        }
        return pair.public.encoded
    }

    /**
     * Подпись, готовая к передаче в BiometricPrompt. Бросает KeyPermanentlyInvalidatedException,
     * если ключ стал недействителен (например, изменён набор отпечатков): устройство нужно привязать заново.
     */
    fun signature(alias: String): Signature {
        val key = store().getKey(alias, null) as? PrivateKey ?: throw IllegalStateException("Ключ устройства не найден")
        return Signature.getInstance("SHA256withECDSA").apply { initSign(key) }
    }

    fun delete(alias: String) {
        runCatching { store().deleteEntry(alias) }
    }
}
