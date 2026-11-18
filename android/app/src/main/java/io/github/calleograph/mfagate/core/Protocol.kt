package io.github.calleograph.mfagate.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.Transient
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import java.time.Instant
import java.time.OffsetDateTime

/** Модели Device API v1 (docs/protocol/device-api.md). Незнакомые поля игнорируются: сервер может их добавлять. */
val ApiJson = Json { ignoreUnknownKeys = true; coerceInputValues = true; encodeDefaults = true }

@Serializable
data class PushInfo(val provider: String, val token: String?)

@Serializable
data class EnrollBody(
    val token: String,
    @SerialName("device_name") val deviceName: String,
    val platform: String = "android",
    @SerialName("app_version") val appVersion: String,
    @SerialName("public_key") val publicKey: String,
    val push: PushInfo,
)

@Serializable
data class UserInfo(val login: String = "", @SerialName("display_name") val displayName: String? = null)

@Serializable
data class ServerInfo(val name: String? = null, val url: String? = null)

@Serializable
data class FcmConfig(
    @SerialName("project_id") val projectId: String,
    @SerialName("application_id") val applicationId: String,
    @SerialName("api_key") val apiKey: String,
    @SerialName("sender_id") val senderId: String,
)

@Serializable
data class PushConfig(val fcm: FcmConfig? = null)

@Serializable
data class EnrollResponse(
    @SerialName("device_id") val deviceId: String,
    @SerialName("device_secret") val deviceSecret: String,
    val user: UserInfo = UserInfo(),
    val server: ServerInfo = ServerInfo(),
    @SerialName("poll_interval") val pollInterval: Int = 25,
    @SerialName("push_config") val pushConfig: PushConfig? = null,
)

@Serializable
data class ConfigResponse(
    @SerialName("push_config") val pushConfig: PushConfig? = null,
    @SerialName("poll_interval") val pollInterval: Int = 25,
)

@Serializable
data class AuthRequest(
    val id: String,
    val login: String = "",
    val resource: String? = null,
    @SerialName("source_ip") val sourceIp: String? = null,
    val location: String? = null,
    val client: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("expires_at") val expiresAt: String? = null,
    val challenge: String? = null,
    val status: String = "pending",
    /** Время сервера (§4): есть в ответе списка, в ответе на один запрос — если сервер его прислал. */
    @SerialName("server_time") val serverTime: String? = null,
    /** Поправка часов телефона: server_time − время получения ответа. Заполняет клиент API, не сервер. */
    @Transient val clockOffset: Long = 0,
) {
    val expiresAtMillis: Long? get() = parseTime(expiresAt)
    val createdAtMillis: Long? get() = parseTime(createdAt)
}

@Serializable
data class RequestList(
    val items: List<AuthRequest> = emptyList(),
    @SerialName("server_time") val serverTime: String? = null,
)

/** Поправка к часам телефона по server_time; 0 — сервер время не прислал (тогда отсчёт идёт по часам телефона). */
fun clockOffset(serverTime: String?, receivedAtMillis: Long): Long = parseTime(serverTime)?.let { it - receivedAtMillis } ?: 0

@Serializable
data class DecisionBody(val decision: String, val signature: String)

@Serializable
data class StatusResponse(val status: String = "")

fun parseTime(s: String?): Long? = s?.let {
    try { OffsetDateTime.parse(it).toInstant().toEpochMilli() } catch (_: Exception) {
        try { Instant.parse(it).toEpochMilli() } catch (_: Exception) { null }
    }
}

enum class Decision(val wire: String) { APPROVE("approve"), DENY("deny") }

object DecisionString {
    const val PREFIX = "mfa-gate-decision-v1"

    /** Строка для подписи: UTF-8, строки через \n, без завершающего перевода строки (§5 протокола). */
    fun build(requestId: String, deviceId: String, challenge: String, decision: Decision): ByteArray {
        val parts = listOf(requestId, deviceId, challenge)
        // перевод строки внутри поля сдвинул бы строки и сделал подпись неоднозначной
        require(parts.all { it.isNotEmpty() && '\n' !in it && '\r' !in it }) { "Некорректные поля запроса" }
        return listOf(PREFIX, requestId, deviceId, challenge, decision.wire).joinToString("\n").toByteArray(Charsets.UTF_8)
    }
}

/**
 * Ошибка API. [status] 0 — нет связи. [code] — машинный код, [serverMessage] — текст сервера
 * из тела `{"code": "...", "message": "..."}` (§8 протокола).
 */
class ApiException(val status: Int, val code: String?, val serverMessage: String? = null) : Exception("HTTP $status ${code.orEmpty()}") {
    val revoked get() = status == 401 && code == "device_revoked"
    /** Решение по запросу больше невозможно: уже принято, запрос не найден или истёк. Кнопки решения убираются. */
    val requestClosed get() = status == 404 || status == 409 || status == 410
    val userMessage: String get() = describe(status, code, serverMessage)

    companion object {
        fun fromBody(status: Int, body: String?): ApiException {
            val o = try { ApiJson.parseToJsonElement(body.orEmpty()) as? JsonObject } catch (_: Exception) { null }
            fun field(name: String) = (o?.get(name) as? JsonPrimitive)?.contentOrNull
            // "error" — запасной вариант для серверов до закрепления формата {code, message}
            val code = field("code") ?: field("error")
            val message = field("message")?.takeIf { it.isNotBlank() && it.none(Char::isISOControl) }
            return ApiException(status, code?.take(64), message?.take(300))
        }

        /** Свои тексты — для кодов, на которые приложение реагирует; для прочих — текст сервера, если он есть. */
        fun describe(status: Int, code: String?, serverMessage: String? = null): String = when {
            status == 0 -> "Нет связи с сервером. Проверьте сеть и нажмите «Повторить»."
            status == 400 && code == "bad_signature" -> "Сервер не принял подпись. Повторите; если ошибка останется, привяжите устройство заново."
            status == 400 && code == "invalid_public_key" -> "Сервер не принял ключ устройства. Обновите приложение или сообщите администратору."
            status == 401 && code == "device_revoked" -> "Устройство отвязано на сервере. Учётная запись удалена с телефона."
            status == 401 -> "Сервер не узнал это устройство. Привяжите его заново."
            status == 404 -> "Запрос не найден: он отменён или относится к другой учётной записи."
            status == 409 -> "Запрос уже обработан: решение по нему принято раньше."
            status == 410 && code == "token_expired_or_used" -> "Ссылка привязки устарела или уже использована. Попросите новую."
            status == 410 -> "Время на подтверждение истекло. Повторите вход заново."
            status == 429 -> "Слишком много попыток. Подождите немного и повторите."
            serverMessage != null -> serverMessage
            status == 400 || status == 422 -> "Сервер отклонил запрос как некорректный."
            status >= 500 -> "Сервер временно недоступен (код $status). Повторите позже."
            else -> "Неожиданный ответ сервера (код $status)."
        }
    }
}

/** «Осталось 0:42» для карточки запроса; null — срок неизвестен. */
fun remainingText(expiresAtMillis: Long?, nowMillis: Long): String? {
    expiresAtMillis ?: return null
    val left = (expiresAtMillis - nowMillis) / 1000
    if (left <= 0) return "Срок истёк"
    return "Осталось %d:%02d".format(left / 60, left % 60)
}
