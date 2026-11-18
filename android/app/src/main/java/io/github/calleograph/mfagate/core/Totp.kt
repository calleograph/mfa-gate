package io.github.calleograph.mfagate.core

import java.nio.ByteBuffer
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

enum class OtpAlgorithm(val jca: String) { SHA1("HmacSHA1"), SHA256("HmacSHA256"), SHA512("HmacSHA512") }

/** TOTP по RFC 6238 (HOTP по RFC 4226 со счётчиком времени). Только javax.crypto, без библиотек. */
object Totp {
    private val POW10 = intArrayOf(1, 10, 100, 1_000, 10_000, 100_000, 1_000_000, 10_000_000, 100_000_000)

    fun code(secret: ByteArray, unixSeconds: Long, algorithm: OtpAlgorithm = OtpAlgorithm.SHA1, digits: Int = 6, period: Int = 30): String {
        require(digits in 6..8) { "digits" }
        require(period > 0) { "period" }
        val counter = Math.floorDiv(unixSeconds, period.toLong())
        val mac = Mac.getInstance(algorithm.jca).apply { init(SecretKeySpec(secret, "RAW")) }
        val h = mac.doFinal(ByteBuffer.allocate(8).putLong(counter).array())
        val off = h[h.size - 1].toInt() and 0x0F
        val bin = ((h[off].toInt() and 0x7F) shl 24) or ((h[off + 1].toInt() and 0xFF) shl 16) or
            ((h[off + 2].toInt() and 0xFF) shl 8) or (h[off + 3].toInt() and 0xFF)
        return (bin % POW10[digits]).toString().padStart(digits, '0')
    }

    /** Секунд до смены кода. */
    fun secondsLeft(unixSeconds: Long, period: Int): Int = (period - Math.floorMod(unixSeconds, period.toLong())).toInt()
}
