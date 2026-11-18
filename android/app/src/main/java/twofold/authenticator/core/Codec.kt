package twofold.authenticator.core

import java.util.Base64

/** base64url без выравнивания (RFC 4648 §5), как во всех полях Device API. */
object Base64Url {
    fun encode(bytes: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)

    /** null — строка не base64url. Выравнивание «=» допускается, обычный алфавит (+ /) — нет. */
    fun decode(s: String): ByteArray? {
        if (s.isEmpty() || s.any { !(it.isLetterOrDigit() && it.code < 128) && it != '-' && it != '_' && it != '=' }) return null
        return try { Base64.getUrlDecoder().decode(s) } catch (_: IllegalArgumentException) { null }
    }
}

/** Base32 (RFC 4648) для секретов TOTP. Пробелы, дефисы, регистр и выравнивание игнорируются. */
object Base32 {
    private const val ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

    fun decode(input: String): ByteArray? {
        val s = input.uppercase().filterNot { it == ' ' || it == '-' }.trimEnd('=')
        if (s.isEmpty()) return null
        val out = java.io.ByteArrayOutputStream(s.length * 5 / 8)
        var buffer = 0
        var bits = 0
        for (ch in s) {
            val v = ALPHABET.indexOf(ch)
            if (v < 0) return null
            buffer = (buffer shl 5) or v
            bits += 5
            if (bits >= 8) {
                bits -= 8
                out.write((buffer shr bits) and 0xFF)
            }
        }
        return out.toByteArray()
    }
}
