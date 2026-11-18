package twofold.authenticator

import twofold.authenticator.core.Base64Url
import twofold.authenticator.core.Decision
import twofold.authenticator.core.DecisionString
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.security.spec.X509EncodedKeySpec
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class DecisionTest {
    private val rid = "6f1c2b9e-7d0a-4a51-9d0e-2a1b3c4d5e6f"
    private val did = "0a9b8c7d-6e5f-4a3b-2c1d-0e9f8a7b6c5d"
    private val challenge = Base64Url.encode(ByteArray(32) { it.toByte() })

    @Test fun `строка подписи точно по протоколу`() {
        val s = String(DecisionString.build(rid, did, challenge, Decision.APPROVE), Charsets.UTF_8)
        assertEquals("twofold-decision-v1\n$rid\n$did\n$challenge\napprove", s)
        assertTrue(!s.endsWith("\n"))
        assertTrue(String(DecisionString.build(rid, did, challenge, Decision.DENY)).endsWith("\ndeny"))
    }

    @Test fun `перевод строки в полях запрещён`() {
        assertFailsWith<IllegalArgumentException> { DecisionString.build("$rid\napprove", did, challenge, Decision.DENY) }
        assertFailsWith<IllegalArgumentException> { DecisionString.build(rid, did, "", Decision.DENY) }
        assertFailsWith<IllegalArgumentException> { DecisionString.build(rid, "x\r", challenge, Decision.DENY) }
    }

    /** То, что делает телефон (SHA256withECDSA, DER, base64url) и сервер (проверка по SPKI из /enroll). */
    @Test fun `подпись P-256 в DER и base64url проверяется по SPKI`() {
        val kp = KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()
        val payload = DecisionString.build(rid, did, challenge, Decision.APPROVE)
        val der = Signature.getInstance("SHA256withECDSA").apply { initSign(kp.private); update(payload) }.sign()
        assertEquals(0x30, der[0].toInt() and 0xFF, "DER SEQUENCE")
        val sig = Base64Url.encode(der)
        assertTrue(sig.none { it == '=' || it == '+' || it == '/' })

        val spki = Base64Url.decode(Base64Url.encode(kp.public.encoded))!!
        val pub = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(spki))
        val ok = Signature.getInstance("SHA256withECDSA").apply { initVerify(pub); update(payload) }.verify(Base64Url.decode(sig))
        assertTrue(ok)
        val wrong = Signature.getInstance("SHA256withECDSA").apply { initVerify(pub); update(DecisionString.build(rid, did, challenge, Decision.DENY)) }
        assertTrue(!wrong.verify(Base64Url.decode(sig)))
    }
}
