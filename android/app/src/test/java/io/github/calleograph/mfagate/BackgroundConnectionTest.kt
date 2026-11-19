package io.github.calleograph.mfagate

import io.github.calleograph.mfagate.data.Account
import io.github.calleograph.mfagate.push.needsConnection
import io.github.calleograph.mfagate.push.nextBackoff
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class BackgroundConnectionTest {
    private val acc = Account("a", "https://a.example.org", "", "u", "", "", "dev-a", "s", "k")

    @Test fun `соединение нужно, только если включено и есть учётная запись`() {
        assertTrue(needsConnection(true, listOf(acc)))
        assertFalse(needsConnection(true, emptyList()))
        assertFalse(needsConnection(false, listOf(acc)))
    }

    @Test fun `пауза удваивается от 2 секунд до минуты`() {
        assertEquals(listOf(4_000L, 8_000L, 16_000L, 32_000L, 60_000L, 60_000L), generateSequence(2_000L, ::nextBackoff).drop(1).take(6).toList())
        assertEquals(2_000L, nextBackoff(0)) // нулевая пауза не превращает повтор в непрерывный опрос
    }
}
