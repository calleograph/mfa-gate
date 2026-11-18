package twofold.authenticator.data

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import twofold.authenticator.core.ApiJson
import twofold.authenticator.core.Base64Url
import twofold.authenticator.core.FcmConfig
import twofold.authenticator.core.OtpAlgorithm
import twofold.authenticator.core.OtpParams
import twofold.authenticator.net.DeviceAuth
import java.util.UUID

/** Привязанное устройство на одном сервере. Секрет устройства хранится только зашифрованным. */
@Serializable
data class Account(
    val id: String,
    val baseUrl: String,
    val serverName: String,
    val login: String,
    val displayName: String,
    val label: String,
    val deviceId: String,
    val sealedSecret: String,
    val keyAlias: String,
    val pollInterval: Int = 25,
    val fcm: FcmConfig? = null,
) {
    val title get() = displayName.ifBlank { login.ifBlank { label } }
    val host get() = baseUrl.substringAfter("://")
}

@Serializable
data class TotpEntry(
    val id: String,
    val issuer: String,
    val account: String,
    val algorithm: OtpAlgorithm,
    val digits: Int,
    val period: Int,
    val sealedSecret: String,
)

/** Локальное хранилище приложения. Интерфейс — шов для тестов и для смены реализации. */
interface Vault {
    val accounts: StateFlow<List<Account>>
    val totp: StateFlow<List<TotpEntry>>
    var appLock: Boolean

    fun saveAccount(a: Account)
    fun removeAccount(id: String)
    fun auth(a: Account): DeviceAuth?

    fun addTotp(p: OtpParams): TotpEntry
    fun removeTotp(id: String)
    fun totpSecret(e: TotpEntry): ByteArray?

    /** true — уведомление о запросе ещё не показывалось (и теперь отмечено). */
    fun markNotified(requestId: String): Boolean

    fun sealDeviceSecret(accountId: String, secret: String): String
}

class PrefsVault(context: Context, private val box: Sealer) : Vault {
    private val prefs = context.getSharedPreferences("twofold", Context.MODE_PRIVATE)
    private val accSer = ListSerializer(Account.serializer())
    private val totpSer = ListSerializer(TotpEntry.serializer())

    private val _accounts = MutableStateFlow(load("accounts") { ApiJson.decodeFromString(accSer, it) })
    private val _totp = MutableStateFlow(load("totp") { ApiJson.decodeFromString(totpSer, it) })
    override val accounts = _accounts.asStateFlow()
    override val totp = _totp.asStateFlow()

    private fun <T> load(key: String, parse: (String) -> List<T>): List<T> =
        prefs.getString(key, null)?.let { runCatching { parse(it) }.getOrNull() } ?: emptyList()

    override var appLock: Boolean
        get() = prefs.getBoolean("app_lock", false)
        set(v) { prefs.edit().putBoolean("app_lock", v).apply() }

    // commit(), а не apply(): секрет устройства должен лечь на диск до того, как интерфейс скажет «привязано».
    @Synchronized override fun saveAccount(a: Account) {
        val list = _accounts.value.filterNot { it.id == a.id } + a
        prefs.edit().putString("accounts", ApiJson.encodeToString(accSer, list)).commit()
        _accounts.value = list
    }

    @Synchronized override fun removeAccount(id: String) {
        val list = _accounts.value.filterNot { it.id == id }
        prefs.edit().putString("accounts", ApiJson.encodeToString(accSer, list)).commit()
        _accounts.value = list
    }

    override fun sealDeviceSecret(accountId: String, secret: String) = box.seal(secret, "device.$accountId")

    override fun auth(a: Account): DeviceAuth? = box.open(a.sealedSecret, "device.${a.id}")?.let { DeviceAuth(a.baseUrl, a.deviceId, it) }

    @Synchronized override fun addTotp(p: OtpParams): TotpEntry {
        val id = UUID.randomUUID().toString()
        val e = TotpEntry(id, p.issuer, p.account, p.algorithm, p.digits, p.period, box.seal(Base64Url.encode(p.secret), "totp.$id"))
        val list = _totp.value + e
        prefs.edit().putString("totp", ApiJson.encodeToString(totpSer, list)).commit()
        _totp.value = list
        return e
    }

    @Synchronized override fun removeTotp(id: String) {
        val list = _totp.value.filterNot { it.id == id }
        prefs.edit().putString("totp", ApiJson.encodeToString(totpSer, list)).commit()
        _totp.value = list
    }

    override fun totpSecret(e: TotpEntry): ByteArray? = box.open(e.sealedSecret, "totp.${e.id}")?.let(Base64Url::decode)

    @Synchronized override fun markNotified(requestId: String): Boolean {
        val seen = prefs.getString("notified", "").orEmpty().split(',').filter { it.isNotEmpty() }
        if (requestId in seen) return false
        prefs.edit().putString("notified", (seen + requestId).takeLast(200).joinToString(",")).apply()
        return true
    }
}
