package io.github.calleograph.mfagate.push

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import io.github.calleograph.mfagate.App
import io.github.calleograph.mfagate.MainActivity
import io.github.calleograph.mfagate.R
import io.github.calleograph.mfagate.core.ApiException
import io.github.calleograph.mfagate.core.AuthRequest
import io.github.calleograph.mfagate.data.Account
import java.util.concurrent.TimeUnit

/**
 * Уведомления о запросах входа. Уведомление только открывает экран запроса:
 * подтверждение всегда идёт через BiometricPrompt внутри приложения.
 */
object Notifier {
    const val CHANNEL = "auth_requests"
    const val EXTRA_ACCOUNT = "mfagate.account_id"
    const val EXTRA_REQUEST = "mfagate.request_id"

    fun createChannel(ctx: Context) {
        val ch = NotificationChannel(CHANNEL, ctx.getString(R.string.channel_requests), NotificationManager.IMPORTANCE_HIGH).apply {
            description = ctx.getString(R.string.channel_requests_desc)
            lockscreenVisibility = android.app.Notification.VISIBILITY_PRIVATE
        }
        ctx.getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
    }

    fun canNotify(ctx: Context) = (Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) &&
        NotificationManagerCompat.from(ctx).areNotificationsEnabled()

    fun show(ctx: Context, account: Account, r: AuthRequest) {
        if (!canNotify(ctx)) return
        val open = Intent(ctx, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            .putExtra(EXTRA_ACCOUNT, account.id).putExtra(EXTRA_REQUEST, r.id)
        val pi = PendingIntent.getActivity(ctx, r.id.hashCode(), open, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val text = listOfNotNull(r.login.ifBlank { null }, r.resource).joinToString(" → ")
        // на заблокированном экране — без подробностей
        val public = NotificationCompat.Builder(ctx, CHANNEL).setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(ctx.getString(R.string.notif_title)).build()
        val b = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(ctx.getString(R.string.notif_title))
            .setContentText(text.ifBlank { account.host })
            .setSubText(account.host)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(public)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .addAction(0, ctx.getString(R.string.notif_open), pi)
        r.expiresAtMillis?.let { exp -> (exp - System.currentTimeMillis() - r.clockOffset).takeIf { it > 0 }?.let { b.setTimeoutAfter(it) } }
        try {
            NotificationManagerCompat.from(ctx).notify(r.id.hashCode(), b.build())
        } catch (_: SecurityException) { /* разрешение отозвано между проверкой и показом */ }
    }

    fun cancel(ctx: Context, requestId: String) = NotificationManagerCompat.from(ctx).cancel(requestId.hashCode())

    /** Фоновая проверка: pending-запросы всех учётных записей, уведомление о ещё не показанных. */
    suspend fun checkAll(app: App) {
        for (a in app.vault.accounts.value) {
            val auth = app.vault.auth(a) ?: continue
            try {
                app.api.pending(auth, 0).filter { it.status == "pending" && app.vault.markNotified(it.id) }.forEach { show(app, a, it) }
            } catch (e: ApiException) {
                app.onApiError(a, e)
            }
        }
    }

    /** По push-сигналу: [accounts] уже отобраны по device_id ([forSignal]); у старого сервера без device_id — перебор с 404 у чужих. */
    suspend fun checkOne(app: App, requestId: String, accounts: List<Account>) {
        if (!requestId.matches(Regex("[A-Za-z0-9-]{1,64}"))) return
        for (a in accounts) {
            val auth = app.vault.auth(a) ?: continue
            try {
                val r = app.api.request(auth, requestId)
                if (r.status == "pending" && app.vault.markNotified(r.id)) show(app, a, r)
                return
            } catch (e: ApiException) {
                app.onApiError(a, e) // 404 — запрос другого сервера, пробуем следующий
            }
        }
    }
}

/** Постоянное соединение (foss) нужно, только если оно включено и есть хотя бы одна учётная запись. */
fun needsConnection(enabled: Boolean, accounts: List<Account>) = enabled && accounts.isNotEmpty()

/** Пауза перед повтором после ошибки: от 2 с с удвоением до минуты; после успеха снова 2 с. */
fun nextBackoff(ms: Long) = (ms * 2).coerceIn(2_000L, 60_000L)

/** Периодическая проверка (минимальный период WorkManager — 15 минут). */
class PollWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        Notifier.checkAll(applicationContext as App)
        return Result.success()
    }

    companion object {
        fun schedule(ctx: Context) {
            val req = PeriodicWorkRequestBuilder<PollWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(ctx).enqueueUniquePeriodicWork("mfagate-poll", ExistingPeriodicWorkPolicy.KEEP, req)
        }
    }
}
