package twofold.authenticator

import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import twofold.authenticator.core.ApiException
import twofold.authenticator.core.DecisionBody
import twofold.authenticator.core.EnrollBody
import twofold.authenticator.core.PushInfo
import twofold.authenticator.core.remainingText
import twofold.authenticator.net.DeviceAuth
import twofold.authenticator.net.HttpDeviceApi
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue

class DeviceApiTest {
    private lateinit var web: MockWebServer
    private lateinit var api: HttpDeviceApi
    private lateinit var auth: DeviceAuth

    @BeforeTest fun up() {
        web = MockWebServer().apply { start() }
        api = HttpDeviceApi("Test/1")
        auth = DeviceAuth(web.url("/base").toString().trimEnd('/'), "dev-1", "c2VjcmV0")
    }

    @AfterTest fun down() = web.shutdown()

    private fun json(body: String, code: Int = 200) = MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body)

    @Test fun `привязка отправляет поля протокола и разбирает ответ`() = runTest {
        web.enqueue(json("""{"device_id":"d1","device_secret":"c2Vj","user":{"login":"ivanov","display_name":"Иванов И."},
            "server":{"name":"Twofold","url":"https://mfa.example.org"},"poll_interval":20,"extra":1,
            "push_config":{"fcm":{"project_id":"p","application_id":"1:2:android:3","api_key":"k","sender_id":"2"}}}"""))
        val r = api.enroll(auth.baseUrl, EnrollBody("tok", "Phone", appVersion = "0.1.0", publicKey = "pk", push = PushInfo("none", null)))
        assertEquals("d1", r.deviceId)
        assertEquals("Иванов И.", r.user.displayName)
        assertEquals(20, r.pollInterval)
        assertEquals("2", r.pushConfig?.fcm?.senderId)
        val req = web.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/base/api/device/v1/enroll", req.path)
        assertNull(req.getHeader("Authorization"))
        val body = Json.parseToJsonElement(req.body.readUtf8()).jsonObject
        assertEquals("android", body["platform"]!!.jsonPrimitive.content)
        assertEquals("pk", body["public_key"]!!.jsonPrimitive.content)
        assertEquals("none", body["push"]!!.jsonObject["provider"]!!.jsonPrimitive.content)
        assertTrue("token" in body["push"]!!.jsonObject) // token: null передаётся явно
    }

    @Test fun `long-poll с заголовком Device`() = runTest {
        web.enqueue(json("""{"items":[{"id":"r1","login":"ivanov","resource":"VPN","source_ip":"203.0.113.10","location":null,
            "client":"OpenVPN","created_at":"2026-11-18T21:00:00Z","expires_at":"2026-11-18T21:01:00Z","challenge":"Y2g","status":"pending"}]}"""))
        val items = api.pending(auth, 25)
        assertEquals(1, items.size)
        assertEquals("203.0.113.10", items[0].sourceIp)
        assertEquals(60_000L, items[0].expiresAtMillis!! - items[0].createdAtMillis!!)
        val req = web.takeRequest()
        assertEquals("/base/api/device/v1/requests?status=pending&wait=25", req.path)
        assertEquals("Device dev-1:c2VjcmV0", req.getHeader("Authorization"))
    }

    @Test fun `решение и история`() = runTest {
        web.enqueue(json("""{"status":"approved"}"""))
        assertEquals("approved", api.decide(auth, "r 1", DecisionBody("approve", "c2ln")))
        val req = web.takeRequest()
        assertEquals("/base/api/device/v1/requests/r%201/decision", req.path)
        assertEquals("""{"decision":"approve","signature":"c2ln"}""", req.body.readUtf8())

        web.enqueue(json("""{"items":[]}"""))
        assertEquals(0, api.history(auth).size)
        assertEquals("/base/api/device/v1/requests?limit=50", web.takeRequest().path)
    }

    @Test fun `ошибки протокола с понятными текстами`() = runTest {
        web.enqueue(json("""{"error":"already_decided"}""", 409))
        val e = assertFailsWith<ApiException> { api.decide(auth, "r1", DecisionBody("deny", "x")) }
        assertEquals(409, e.status)
        assertEquals("already_decided", e.code)
        assertEquals("Решение по этому запросу уже принято.", e.userMessage)

        web.enqueue(json("""{"detail":"device_revoked"}""", 401))
        assertTrue(assertFailsWith<ApiException> { api.pending(auth, 0) }.revoked)

        web.enqueue(json("""{"error":"expired"}""", 410))
        assertTrue(assertFailsWith<ApiException> { api.request(auth, "r1") }.userMessage.contains("истекло"))

        web.enqueue(json("<html>bad gateway</html>", 502))
        assertEquals(502, assertFailsWith<ApiException> { api.request(auth, "r1") }.status)

        web.enqueue(json("not json"))
        assertEquals("bad_response", assertFailsWith<ApiException> { api.history(auth) }.code)
    }

    @Test fun `перенаправление не выполняется`() = runTest {
        web.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "https://elsewhere.example.net/"))
        assertEquals(302, assertFailsWith<ApiException> { api.history(auth) }.status)
        assertEquals(1, web.requestCount)
    }

    @Test fun `нет связи — статус 0`() = runTest {
        val dead = DeviceAuth("http://127.0.0.1:1", "d", "s")
        assertEquals(0, assertFailsWith<ApiException> { api.history(dead) }.status)
    }

    @Test fun `обратный отсчёт`() {
        assertEquals("Осталось 1:05", remainingText(65_000, 0))
        assertEquals("Срок истёк", remainingText(1_000, 2_000))
        assertNull(remainingText(null, 0))
    }
}
