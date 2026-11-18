package io.github.calleograph.mfagate.core

import java.net.URI
import java.net.URLDecoder

/** Ошибка разбора ссылки; [message] показывается пользователю как есть. */
class LinkException(message: String) : Exception(message)

/** Учётная запись TOTP, разобранная из otpauth:// или из ручного ввода. */
data class OtpParams(
    val issuer: String,
    val account: String,
    val secret: ByteArray,
    val algorithm: OtpAlgorithm = OtpAlgorithm.SHA1,
    val digits: Int = 6,
    val period: Int = 30,
) {
    override fun equals(other: Any?) = other is OtpParams && issuer == other.issuer && account == other.account &&
        secret.contentEquals(other.secret) && algorithm == other.algorithm && digits == other.digits && period == other.period
    override fun hashCode() = issuer.hashCode() * 31 + account.hashCode()
}

/** Данные из QR привязки mfagate://enroll. */
data class EnrollLink(val baseUrl: String, val token: String, val label: String) {
    /** Адрес http:// — соединение без шифрования; привязка только после явного подтверждения пользователя. */
    val insecure get() = baseUrl.startsWith("http://")
}

private fun query(q: String): Map<String, String> = q.split('&').filter { it.isNotEmpty() }.associate {
    val i = it.indexOf('=')
    if (i < 0) pct(it).lowercase() to "" else pct(it.substring(0, i)).lowercase() to pct(it.substring(i + 1))
}

// «+» в otpauth не означает пробел (это не форма HTML), поэтому сохраняем его.
private fun pct(s: String): String = try {
    URLDecoder.decode(s.replace("+", "%2B"), "UTF-8")
} catch (_: IllegalArgumentException) {
    throw LinkException("Ссылка повреждена: неверное %-кодирование.")
}

object Links {
    const val MAX_LEN = 2048

    /** otpauth://totp/[issuer:]account?secret=…&issuer=…&algorithm=…&digits=…&period=… (формат Google Authenticator). */
    fun parseOtpAuth(raw: String): OtpParams {
        val s = raw.trim()
        if (s.length > MAX_LEN) throw LinkException("Ссылка слишком длинная.")
        if (!s.startsWith("otpauth://", ignoreCase = true)) throw LinkException("Это не ссылка otpauth://.")
        val rest = s.substring("otpauth://".length)
        val slash = rest.indexOf('/')
        val type = (if (slash < 0) rest.substringBefore('?') else rest.substring(0, slash)).lowercase()
        if (type == "hotp") throw LinkException("Коды по счётчику (HOTP) не поддерживаются, только TOTP.")
        if (type != "totp") throw LinkException("Неизвестный тип кода: $type.")
        val labelPart = if (slash < 0) "" else rest.substring(slash + 1).substringBefore('?')
        val q = query(rest.substringAfter('?', ""))
        val label = pct(labelPart).trim()
        val labelIssuer = label.substringBefore(':', "").trim()
        val account = label.substringAfter(':').trim()
        val issuer = q["issuer"]?.trim()?.takeIf { it.isNotEmpty() } ?: labelIssuer
        return manual(
            issuer = issuer,
            account = account,
            secret = q["secret"] ?: throw LinkException("В ссылке нет секрета."),
            algorithm = q["algorithm"] ?: "SHA1",
            digits = q["digits"] ?: "6",
            period = q["period"] ?: "30",
        )
    }

    /** Проверка ручного ввода (и общая часть разбора otpauth). */
    fun manual(issuer: String, account: String, secret: String, algorithm: String = "SHA1", digits: String = "6", period: String = "30"): OtpParams {
        val key = Base32.decode(secret) ?: throw LinkException("Секрет должен быть в Base32 (буквы A–Z и цифры 2–7).")
        if (key.size < 10) throw LinkException("Секрет слишком короткий: нужно не меньше 16 символов Base32.")
        val alg = OtpAlgorithm.entries.firstOrNull { it.name.equals(algorithm.trim(), ignoreCase = true) }
            ?: throw LinkException("Алгоритм $algorithm не поддерживается (SHA1, SHA256, SHA512).")
        val d = digits.trim().toIntOrNull()?.takeIf { it == 6 || it == 8 } ?: throw LinkException("Длина кода должна быть 6 или 8 цифр.")
        val p = period.trim().toIntOrNull()?.takeIf { it in 1..300 } ?: throw LinkException("Период должен быть от 1 до 300 секунд.")
        if (issuer.length > 100 || account.length > 200) throw LinkException("Название слишком длинное.")
        if (issuer.isBlank() && account.isBlank()) throw LinkException("Укажите сервис или учётную запись.")
        return OtpParams(issuer.trim(), account.trim(), key, alg, d, p)
    }

    /**
     * mfagate://enroll?v=1&url=<base64url(BASE_URL)>&token=<TOKEN>&label=<base64url(label)>.
     * Адрес http:// принимается (первичная установка сервера идёт по http://IP:порт), но помечается [EnrollLink.insecure].
     */
    fun parseEnroll(raw: String): EnrollLink {
        val s = raw.trim()
        if (s.length > MAX_LEN) throw LinkException("Ссылка слишком длинная.")
        val prefix = "mfagate://enroll"
        if (!s.startsWith(prefix, ignoreCase = true) || s.length > prefix.length && s[prefix.length] != '?' && s[prefix.length] != '/') {
            throw LinkException("Это не ссылка привязки MFA Gate (mfagate://enroll).")
        }
        val q = query(s.substringAfter('?', ""))
        if (q["v"] != "1") throw LinkException("Версия ссылки не поддерживается. Обновите приложение.")
        val urlBytes = q["url"]?.let(Base64Url::decode) ?: throw LinkException("В ссылке нет адреса сервера.")
        val baseUrl = normalizeBaseUrl(String(urlBytes, Charsets.UTF_8))
        val token = q["token"] ?: throw LinkException("В ссылке нет кода привязки.")
        if (Base64Url.decode(token)?.size != 32) throw LinkException("Код привязки повреждён.")
        val label = q["label"]?.takeIf { it.isNotEmpty() }?.let {
            val b = Base64Url.decode(it) ?: throw LinkException("Название учётной записи повреждено.")
            String(b, Charsets.UTF_8).trim()
        }.orEmpty()
        if (label.length > 200 || label.any { it.isISOControl() }) throw LinkException("Название учётной записи повреждено.")
        return EnrollLink(baseUrl, token, label)
    }

    /** Адрес сервера: https или http, без userinfo, query и fragment; без завершающего «/». */
    fun normalizeBaseUrl(raw: String): String {
        val s = raw.trim()
        if (s.isEmpty() || s.length > 300 || s.any { it.isWhitespace() || it.isISOControl() }) throw LinkException("Адрес сервера некорректен.")
        val u = try { URI(s) } catch (_: Exception) { throw LinkException("Адрес сервера некорректен.") }
        val scheme = u.scheme?.lowercase()
        if (scheme != "https" && scheme != "http") throw LinkException("Адрес сервера должен начинаться с https:// или http://.")
        if (u.host.isNullOrEmpty() || u.rawUserInfo != null || u.rawQuery != null || u.rawFragment != null) {
            throw LinkException("Адрес сервера некорректен.")
        }
        return "$scheme://${u.rawAuthority.lowercase()}${u.rawPath.orEmpty().trimEnd('/')}"
    }
}
