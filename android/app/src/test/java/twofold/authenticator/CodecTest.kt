package twofold.authenticator

import twofold.authenticator.core.Base32
import twofold.authenticator.core.Base64Url
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertNull

class CodecTest {
    @Test fun `base64url по RFC 4648 без выравнивания`() {
        assertEquals("", Base64Url.encode(ByteArray(0)))
        assertEquals("Zg", Base64Url.encode("f".toByteArray()))
        assertEquals("Zm8", Base64Url.encode("fo".toByteArray()))
        assertEquals("Zm9v", Base64Url.encode("foo".toByteArray()))
        assertEquals("-_8", Base64Url.encode(byteArrayOf(0xfb.toByte(), 0xff.toByte())))
    }

    @Test fun `base64url обратное преобразование`() {
        assertContentEquals(byteArrayOf(0xfb.toByte(), 0xff.toByte()), Base64Url.decode("-_8"))
        assertContentEquals("fo".toByteArray(), Base64Url.decode("Zm8="))
        val raw = ByteArray(32) { it.toByte() }
        assertContentEquals(raw, Base64Url.decode(Base64Url.encode(raw)))
    }

    @Test fun `base64url отвергает обычный алфавит и мусор`() {
        assertNull(Base64Url.decode("+/8"))
        assertNull(Base64Url.decode("Zm 9v"))
        assertNull(Base64Url.decode(""))
        assertNull(Base64Url.decode("Z"))
    }

    @Test fun `base32 по RFC 4648`() {
        assertEquals("foobar", String(Base32.decode("MZXW6YTBOI")!!))
        assertEquals("foo", String(Base32.decode("mzxw6===")!!))
        assertEquals("foob", String(Base32.decode("MZXW 6YQ")!!))
        assertNull(Base32.decode("MZXW1"))
        assertNull(Base32.decode(""))
    }
}
