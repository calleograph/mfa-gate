package io.github.calleograph.mfagate.push

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.supervisorScope
import io.github.calleograph.mfagate.App
import io.github.calleograph.mfagate.MainActivity
import io.github.calleograph.mfagate.R
import io.github.calleograph.mfagate.core.ApiException
import io.github.calleograph.mfagate.data.Account

/**
 * Постоянное соединение (foss): long-poll к каждому серверу, уведомление о новых запросах за секунды при закрытом приложении.
 * Уведомление о запросе — то же, что от FCM в сборке gms ([Notifier.show]).
 */
class WatchService : Service() {
    private val app get() = application as App
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onBind(intent: Intent?) = null

    override fun onCreate() {
        super.onCreate()
        val ch = NotificationChannel(CHANNEL, getString(R.string.channel_connection), NotificationManager.IMPORTANCE_LOW).apply {
            description = getString(R.string.channel_connection_desc)
            setShowBadge(false)
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.watch_title))
            .setContentText(getString(R.string.watch_text))
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .setShowWhen(false)
            .setContentIntent(open)
            .build()
        try {
            // тип берётся из манифеста (specialUse); без POST_NOTIFICATIONS служба работает, уведомление просто скрыто
            startForeground(ID, n)
        } catch (_: IllegalStateException) {
            stopSelf() // Android 12+ запрещает запуск из фона (например, перезапуск системой); остаётся PollWorker
            return
        }
        scope.launch {
            app.vault.accounts.map { list -> list.map { it.id } }.distinctUntilChanged().collectLatest {
                val list = app.vault.accounts.value
                if (list.isEmpty()) { stopSelf(); return@collectLatest }
                supervisorScope { list.forEach { a -> launch { watch(a) } } }
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int) = START_STICKY

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private suspend fun watch(a: Account) {
        val auth = app.vault.auth(a) ?: return
        var backoff = 2_000L
        while (true) {
            try {
                val items = app.api.pending(auth, a.pollInterval.coerceIn(5, 30)).filter { it.status == "pending" }
                items.filter { app.vault.markNotified(it.id) }.forEach { Notifier.show(app, a, it) }
                backoff = 2_000L
                // при наличии pending сервер отвечает сразу; пауза не даёт превратить long-poll в непрерывный опрос
                if (items.isNotEmpty()) delay(3_000)
            } catch (e: ApiException) {
                app.onApiError(a, e) // 401 device_revoked удаляет учётную запись, список выше перезапустит циклы
                if (e.revoked) return
                delay(backoff)
                backoff = nextBackoff(backoff)
            }
        }
    }

    companion object {
        private const val CHANNEL = "background_connection"
        private const val ID = 1

        /** Запускает службу, если она нужна ([needsConnection]), иначе останавливает. */
        fun sync(app: App) {
            val i = Intent(app, WatchService::class.java)
            if (!needsConnection(app.vault.instantNotifications, app.vault.accounts.value)) {
                app.stopService(i)
                return
            }
            try {
                ContextCompat.startForegroundService(app, i)
            } catch (_: IllegalStateException) { /* Android 12+: из фона нельзя (процесс поднял WorkManager); запустится при открытии приложения */ }
        }
    }
}

/** После перезагрузки и обновления приложения: система разрешает запуск foreground-службы из этих событий. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            WatchService.sync(context.applicationContext as App)
        }
    }
}
