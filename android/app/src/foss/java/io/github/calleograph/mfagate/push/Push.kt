package io.github.calleograph.mfagate.push

import io.github.calleograph.mfagate.App
import io.github.calleograph.mfagate.core.PushInfo
import io.github.calleograph.mfagate.data.Account

/** Сборка foss: без сервисов Google. Запросы приходят через long-poll на открытом экране и проверку WorkManager. */
object Push {
    /** Значение поля push при привязке. */
    fun enrollInfo() = PushInfo("none", null)

    fun start(app: App) = PollWorker.schedule(app)

    @Suppress("UNUSED_PARAMETER")
    suspend fun register(app: App, account: Account) = Unit
}
