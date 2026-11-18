package twofold.authenticator

import twofold.authenticator.core.Base64Url
import twofold.authenticator.core.LinkException
import twofold.authenticator.core.Links
import twofold.authenticator.core.OtpAlgorithm
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class LinksTest {
    private val secret = "JBSWY3DPEHPK3PXP" // "Hello!\xde\xad\xbe\xef"

    @Test fun `otpauth в формате Google Authenticator`() {
        val p = Links.parseOtpAuth("otpauth://totp/Example:alice@example.com?secret=$secret&issuer=Example")
        assertEquals("Example", p.issuer)
        assertEquals("alice@example.com", p.account)
        assertEquals(OtpAlgorithm.SHA1, p.algorithm)
        assertEquals(6, p.digits)
        assertEquals(30, p.period)
        assertContentEquals("Hello!".toByteArray() + byteArrayOf(0xde.toByte(), 0xad.toByte(), 0xbe.toByte(), 0xef.toByte()), p.secret)
    }

    @Test fun `otpauth с параметрами и кодированием`() {
        val p = Links.parseOtpAuth("OTPAUTH://TOTP/ACME%20Co%3Ajohn%2Bdoe?secret=${secret.lowercase()}&algorithm=sha512&digits=8&period=60")
        assertEquals("ACME Co", p.issuer)
        assertEquals("john+doe", p.account)
        assertEquals(OtpAlgorithm.SHA512, p.algorithm)
        assertEquals(8, p.digits)
        assertEquals(60, p.period)
    }

    @Test fun `issuer из параметра важнее префикса`() {
        val p = Links.parseOtpAuth("otpauth://totp/Old:bob?secret=$secret&issuer=New&algorithm=SHA256")
        assertEquals("New", p.issuer)
        assertEquals("bob", p.account)
        assertEquals(OtpAlgorithm.SHA256, p.algorithm)
    }

    @Test fun `некорректные otpauth`() {
        val bad = listOf(
            "https://example.com/?secret=$secret",
            "otpauth://hotp/x?secret=$secret&counter=1",
            "otpauth://totp/x?issuer=a",
            "otpauth://totp/x?secret=not-base32!!",
            "otpauth://totp/x?secret=JBSWY3DP",
            "otpauth://totp/x?secret=$secret&digits=7",
            "otpauth://totp/x?secret=$secret&algorithm=MD5",
            "otpauth://totp/x?secret=$secret&period=0",
            "otpauth://totp/x?secret=$secret&period=abc",
            "otpauth://totp/?secret=$secret",
            "otpauth://totp/x?secret=$secret&issuer=%zz",
            "otpauth://totp/x?secret=" + "A".repeat(3000),
        )
        bad.forEach { assertFailsWith<LinkException>(it) { Links.parseOtpAuth(it) } }
    }

    @Test fun `ручной ввод`() {
        val p = Links.manual("Сервис", "", "jbsw y3dp ehpk 3pxp", "SHA256", "8", "30")
        assertEquals(OtpAlgorithm.SHA256, p.algorithm)
        assertEquals(8, p.digits)
        assertFailsWith<LinkException> { Links.manual("", "", secret) }
    }

    private fun b64(s: String) = Base64Url.encode(s.toByteArray())
    private val token = Base64Url.encode(ByteArray(32) { 7 })

    @Test fun `ссылка привязки`() {
        val l = Links.parseEnroll("twofold://enroll?v=1&url=${b64("https://MFA.example.org/")}&token=$token&label=${b64("ivanov@mfa.example.org")}")
        assertEquals("https://mfa.example.org", l.baseUrl)
        assertEquals(token, l.token)
        assertEquals("ivanov@mfa.example.org", l.label)
    }

    @Test fun `ссылка привязки с путём и портом, без label`() {
        val l = Links.parseEnroll("twofold://enroll?v=1&url=${b64("https://mfa.example.org:8443/twofold")}&token=$token")
        assertEquals("https://mfa.example.org:8443/twofold", l.baseUrl)
        assertEquals("", l.label)
    }

    @Test fun `http разрешён только флагом отладочной сборки`() {
        val link = "twofold://enroll?v=1&url=${b64("http://10.0.2.2:8000")}&token=$token"
        assertFailsWith<LinkException> { Links.parseEnroll(link) }
        assertEquals("http://10.0.2.2:8000", Links.parseEnroll(link, allowHttp = true).baseUrl)
    }

    @Test fun `некорректные ссылки привязки`() {
        val ok = "url=${b64("https://mfa.example.org")}&token=$token"
        val bad = listOf(
            "twofold://other?v=1&$ok",
            "twofold://enrollment?v=1&$ok",
            "https://mfa.example.org/enroll?v=1&$ok",
            "twofold://enroll?v=2&$ok",
            "twofold://enroll?$ok",
            "twofold://enroll?v=1&token=$token",
            "twofold://enroll?v=1&url=${b64("https://mfa.example.org")}",
            "twofold://enroll?v=1&url=${b64("https://mfa.example.org")}&token=${Base64Url.encode(ByteArray(16))}",
            "twofold://enroll?v=1&url=${b64("https://mfa.example.org")}&token=abc+def",
            "twofold://enroll?v=1&url=${b64("ftp://mfa.example.org")}&token=$token",
            "twofold://enroll?v=1&url=${b64("https://user:pw@mfa.example.org")}&token=$token",
            "twofold://enroll?v=1&url=${b64("https://mfa.example.org/?next=x")}&token=$token",
            "twofold://enroll?v=1&url=${b64("https://")}&token=$token",
            "twofold://enroll?v=1&url=%%%&token=$token",
            "twofold://enroll?v=1&url=${b64("https://mfa.example.org")}&token=$token&label=${b64("a\nb")}",
        )
        bad.forEach { assertFailsWith<LinkException>(it) { Links.parseEnroll(it, allowHttp = true) } }
    }
}
