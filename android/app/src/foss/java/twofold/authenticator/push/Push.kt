package twofold.authenticator.push

import twofold.authenticator.App
import twofold.authenticator.core.PushInfo
import twofold.authenticator.data.Account

/** Сборка foss: без сервисов Google. Запросы приходят через long-poll на открытом экране и проверку WorkManager. */
object Push {
    /** Значение поля push при привязке. */
    fun enrollInfo() = PushInfo("none", null)

    fun start(app: App) = PollWorker.schedule(app)

    @Suppress("UNUSED_PARAMETER")
    suspend fun register(app: App, account: Account) = Unit
}
