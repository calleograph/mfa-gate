package twofold.authenticator.net

import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.encodeToString
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import twofold.authenticator.core.ApiException
import twofold.authenticator.core.ApiJson
import twofold.authenticator.core.AuthRequest
import twofold.authenticator.core.ConfigResponse
import twofold.authenticator.core.DecisionBody
import twofold.authenticator.core.EnrollBody
import twofold.authenticator.core.EnrollResponse
import twofold.authenticator.core.PushInfo
import twofold.authenticator.core.RequestList
import twofold.authenticator.core.StatusResponse
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** Данные для заголовка `Authorization: Device <device_id>:<device_secret>`. */
data class DeviceAuth(val baseUrl: String, val deviceId: String, val secret: String)

/** Device API v1. Ошибки — [ApiException] (status 0 — нет связи). */
interface DeviceApi {
    suspend fun enroll(baseUrl: String, body: EnrollBody): EnrollResponse
    suspend fun config(a: DeviceAuth): ConfigResponse
    suspend fun pending(a: DeviceAuth, waitSeconds: Int): List<AuthRequest>
    suspend fun history(a: DeviceAuth, limit: Int = 50): List<AuthRequest>
    suspend fun request(a: DeviceAuth, id: String): AuthRequest
    suspend fun decide(a: DeviceAuth, id: String, body: DecisionBody): String
    suspend fun updatePush(a: DeviceAuth, push: PushInfo)
    suspend fun unlink(a: DeviceAuth)
}

private val JSON_MT = "application/json".toMediaType()

private suspend fun Call.await(): Response = suspendCancellableCoroutine { c ->
    c.invokeOnCancellation { cancel() }
    enqueue(object : Callback {
        override fun onFailure(call: Call, e: IOException) { if (!c.isCancelled) c.resumeWithException(e) }
        override fun onResponse(call: Call, response: Response) { c.resume(response) }
    })
}

class HttpDeviceApi(private val userAgent: String, base: OkHttpClient = OkHttpClient()) : DeviceApi {
    // Без перенаправлений: заголовок Authorization с секретом устройства не должен уйти на другой адрес.
    private val client = base.newBuilder()
        .followRedirects(false).followSslRedirects(false)
        .connectTimeout(15, TimeUnit.SECONDS).writeTimeout(20, TimeUnit.SECONDS).readTimeout(20, TimeUnit.SECONDS)
        .build()
    // long-poll: сервер держит ответ до 30 с, читаем с запасом.
    private val longPoll = client.newBuilder().readTimeout(45, TimeUnit.SECONDS).build()

    private fun url(base: String, path: String, vararg query: Pair<String, String>): HttpUrl {
        val b = (base + "/api/device/v1").toHttpUrl().newBuilder()
        path.split('/').filter { it.isNotEmpty() }.forEach { b.addPathSegment(it) }
        query.forEach { (k, v) -> b.addQueryParameter(k, v) }
        return b.build()
    }

    private suspend fun exec(c: OkHttpClient, method: String, url: HttpUrl, auth: DeviceAuth?, body: String? = null): String {
        val rb = Request.Builder().url(url).header("User-Agent", userAgent).header("Accept", "application/json")
        auth?.let { rb.header("Authorization", "Device ${it.deviceId}:${it.secret}") }
        rb.method(method, body?.toRequestBody(JSON_MT))
        val resp = try { c.newCall(rb.build()).await() } catch (e: IOException) { throw ApiException(0, e.javaClass.simpleName) }
        return resp.use {
            val text = try { it.body.string() } catch (_: IOException) { throw ApiException(0, "read") }
            if (!it.isSuccessful) throw ApiException.fromBody(it.code, text)
            text
        }
    }

    private inline fun <reified T> parse(s: String): T = try { ApiJson.decodeFromString<T>(s) } catch (_: Exception) {
        throw ApiException(502, "bad_response")
    }

    override suspend fun enroll(baseUrl: String, body: EnrollBody): EnrollResponse =
        parse(exec(client, "POST", url(baseUrl, "enroll"), null, ApiJson.encodeToString(body)))

    override suspend fun config(a: DeviceAuth): ConfigResponse = parse(exec(client, "GET", url(a.baseUrl, "config"), a))

    override suspend fun pending(a: DeviceAuth, waitSeconds: Int): List<AuthRequest> {
        val w = waitSeconds.coerceIn(0, 30)
        return parse<RequestList>(exec(if (w > 0) longPoll else client, "GET", url(a.baseUrl, "requests", "status" to "pending", "wait" to "$w"), a)).items
    }

    override suspend fun history(a: DeviceAuth, limit: Int): List<AuthRequest> =
        parse<RequestList>(exec(client, "GET", url(a.baseUrl, "requests", "limit" to "$limit"), a)).items

    override suspend fun request(a: DeviceAuth, id: String): AuthRequest {
        val r = parse<AuthRequest>(exec(client, "GET", url(a.baseUrl, "requests").newBuilder().addPathSegment(id).build(), a))
        if (r.id != id) throw ApiException(502, "bad_response")
        return r
    }

    override suspend fun decide(a: DeviceAuth, id: String, body: DecisionBody): String {
        val u = url(a.baseUrl, "requests").newBuilder().addPathSegment(id).addPathSegment("decision").build()
        return parse<StatusResponse>(exec(client, "POST", u, a, ApiJson.encodeToString(body))).status
    }

    override suspend fun updatePush(a: DeviceAuth, push: PushInfo) {
        exec(client, "PUT", url(a.baseUrl, "push"), a, ApiJson.encodeToString(push))
    }

    override suspend fun unlink(a: DeviceAuth) {
        exec(client, "DELETE", url(a.baseUrl, "self"), a)
    }
}
