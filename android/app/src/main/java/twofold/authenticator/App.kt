package twofold.authenticator

import android.app.Application
import android.os.Build
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import twofold.authenticator.core.ApiException
import twofold.authenticator.data.Account
import twofold.authenticator.data.DeviceKeys
import twofold.authenticator.data.PrefsVault
import twofold.authenticator.data.SecretBox
import twofold.authenticator.data.Vault
import twofold.authenticator.net.DeviceApi
import twofold.authenticator.net.HttpDeviceApi
import twofold.authenticator.push.Notifier
import twofold.authenticator.push.Push

/** Контейнер зависимостей процесса (без DI-фреймворка). */
class App : Application() {
    lateinit var vault: Vault
    lateinit var api: DeviceApi
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /** true — показан экран блокировки приложения. */
    val locked = MutableStateFlow(false)

    /** Когда приложение ушло в фон (elapsedRealtime); живёт дольше Activity. */
    var stoppedAt = 0L

    override fun onCreate() {
        super.onCreate()
        vault = PrefsVault(this, SecretBox())
        api = HttpDeviceApi("TwofoldAuthenticator/${BuildConfig.VERSION_NAME} (Android ${Build.VERSION.RELEASE}; ${BuildConfig.FLAVOR})")
        locked.value = vault.appLock
        Notifier.createChannel(this)
        Push.start(this)
    }

    /** Удаляет учётную запись и её ключ только на телефоне. */
    fun forgetAccount(a: Account) {
        vault.removeAccount(a.id)
        DeviceKeys.delete(a.keyAlias)
    }

    /** Общая реакция на ошибку API: отозванное устройство удаляется локально (§3 протокола). */
    fun onApiError(a: Account, e: ApiException) {
        if (e.revoked) forgetAccount(a)
    }
}
