package io.github.calleograph.mfagate

import io.github.calleograph.mfagate.core.OtpAlgorithm
import io.github.calleograph.mfagate.core.Totp
import kotlin.test.Test
import kotlin.test.assertEquals

/** Векторы RFC 6238, приложение B (8 цифр, период 30 с). */
class TotpTest {
    private val sha1Seed = "12345678901234567890".toByteArray()
    private val sha256Seed = "12345678901234567890123456789012".toByteArray()
    private val sha512Seed = "1234567890123456789012345678901234567890123456789012345678901234".toByteArray()
    private val times = longArrayOf(59, 1111111109, 1111111111, 1234567890, 2000000000, 20000000000)

    private fun check(seed: ByteArray, alg: OtpAlgorithm, expected: List<String>) =
        times.zip(expected).forEach { (t, code) -> assertEquals(code, Totp.code(seed, t, alg, 8, 30), "$alg t=$t") }

    @Test fun `SHA1 по RFC 6238`() = check(sha1Seed, OtpAlgorithm.SHA1, listOf("94287082", "07081804", "14050471", "89005924", "69279037", "65353130"))

    @Test fun `SHA256 по RFC 6238`() = check(sha256Seed, OtpAlgorithm.SHA256, listOf("46119246", "68084774", "67062674", "91819424", "90698825", "77737706"))

    @Test fun `SHA512 по RFC 6238`() = check(sha512Seed, OtpAlgorithm.SHA512, listOf("90693936", "25091201", "99943326", "93441116", "38618901", "47863826"))

    @Test fun `6 цифр — младшие разряды того же значения`() {
        assertEquals("287082", Totp.code(sha1Seed, 59))
        assertEquals("005924", Totp.code(sha1Seed, 1234567890))
    }

    @Test fun `оставшееся время в периоде`() {
        assertEquals(30, Totp.secondsLeft(0, 30))
        assertEquals(1, Totp.secondsLeft(59, 30))
        assertEquals(60, Totp.secondsLeft(120, 60))
    }
}
