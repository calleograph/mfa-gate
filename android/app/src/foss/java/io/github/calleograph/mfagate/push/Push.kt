package io.github.calleograph.mfagate.push

import io.github.calleograph.mfagate.App
import io.github.calleograph.mfagate.core.PushInfo
import io.github.calleograph.mfagate.data.Account

/**
 * Сборка foss: без сервисов Google. Запросы приходят через long-poll: на открытом экране и в [WatchService]
 * (постоянное соединение, если включено). WorkManager остаётся запасным путём.
 */
object Push {
    const val supportsBackgroundConnection = true

    /** Значение поля push при привязке. */
    fun enrollInfo() = PushInfo("none", null)

    /** Также применяет настройку «Мгновенные уведомления»: запускает или останавливает [WatchService]. */
    fun start(app: App) {
        PollWorker.schedule(app)
        WatchService.sync(app)
    }

    @Suppress("UNUSED_PARAMETER")
    suspend fun register(app: App, account: Account) = WatchService.sync(app)
}
