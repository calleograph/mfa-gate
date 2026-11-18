package io.github.calleograph.mfagate

import io.github.calleograph.mfagate.data.Account
import io.github.calleograph.mfagate.data.forSignal
import kotlin.test.Test
import kotlin.test.assertEquals

class PushSignalTest {
    private fun acc(id: String, device: String) = Account(id, "https://$id.example.org", "", "u", "", "", device, "s", "k")
    private val list = listOf(acc("a", "dev-a"), acc("b", "dev-b"), acc("c", "dev-c"))

    @Test fun `сигнал с device_id выбирает одну учётную запись`() {
        assertEquals(listOf("b"), list.forSignal("dev-b").map { it.id })
        assertEquals(emptyList(), list.forSignal("dev-x").map { it.id }) // чужой сигнал не трогает ни один сервер
    }

    @Test fun `сигнал без device_id — все подходящие кандидаты`() {
        assertEquals(listOf("a", "b", "c"), list.forSignal(null).map { it.id })
        assertEquals(listOf("a", "c"), list.forSignal("") { it.id != "b" }.map { it.id })
    }
}
