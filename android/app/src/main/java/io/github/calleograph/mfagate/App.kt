package io.github.calleograph.mfagate

import android.app.Application
import android.os.Build
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import io.github.calleograph.mfagate.core.ApiException
import io.github.calleograph.mfagate.data.Account
import io.github.calleograph.mfagate.data.DeviceKeys
import io.github.calleograph.mfagate.data.PrefsVault
import io.github.calleograph.mfagate.data.SecretBox
import io.github.calleograph.mfagate.data.Vault
import io.github.calleograph.mfagate.net.DeviceApi
import io.github.calleograph.mfagate.net.HttpDeviceApi
import io.github.calleograph.mfagate.push.Notifier
import io.github.calleograph.mfagate.push.Push

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
        api = HttpDeviceApi("MfaGateAuthenticator/${BuildConfig.VERSION_NAME} (Android ${Build.VERSION.RELEASE}; ${BuildConfig.FLAVOR})")
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
