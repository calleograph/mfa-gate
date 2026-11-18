package io.github.calleograph.mfagate.push

import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import io.github.calleograph.mfagate.App
import io.github.calleograph.mfagate.core.ApiException
import io.github.calleograph.mfagate.core.PushInfo
import io.github.calleograph.mfagate.data.Account
import io.github.calleograph.mfagate.data.forSignal
import kotlin.coroutines.resume

/**
 * Сборка gms: Firebase Cloud Messaging без google-services.json. FirebaseApp создаётся вручную из push_config сервера.
 * WorkManager остаётся запасным путём: push может задержаться или потеряться, а сервер может не настроить FCM.
 */
object Push {
    /** При привязке токена ещё нет: параметры Firebase приходят в ответе /enroll, токен уходит следом через PUT /push. */
    fun enrollInfo() = PushInfo("none", null)

    fun start(app: App) {
        PollWorker.schedule(app)
        firebase(app) ?: return
        app.scope.launch { app.vault.accounts.value.forEach { register(app, it) } }
    }

    /**
     * shortcut: один FirebaseApp по умолчанию на процесс — из первой учётной записи с FCM. Серверы с другим проектом Firebase
     * получают запросы через WorkManager и long-poll; для нескольких проектов нужен мульти-sender (протокол этого не описывает).
     */
    @Synchronized
    fun firebase(app: App): FirebaseApp? {
        FirebaseApp.getApps(app).firstOrNull { it.name == FirebaseApp.DEFAULT_APP_NAME }?.let { return it }
        val cfg = app.vault.accounts.value.firstNotNullOfOrNull { it.fcm } ?: return null
        return runCatching {
            FirebaseApp.initializeApp(
                app,
                FirebaseOptions.Builder()
                    .setProjectId(cfg.projectId)
                    .setApplicationId(cfg.applicationId)
                    .setApiKey(cfg.apiKey)
                    .setGcmSenderId(cfg.senderId)
                    .build(),
            )
        }.getOrNull()
    }

    fun covers(app: App, a: Account): Boolean {
        val o = a.fcm ?: return false
        val fb = firebase(app)?.options ?: return false
        return fb.gcmSenderId == o.senderId && fb.projectId == o.projectId
    }

    suspend fun register(app: App, account: Account) {
        if (!covers(app, account)) return
        send(app, account, token() ?: return)
    }

    suspend fun send(app: App, a: Account, token: String) {
        val auth = app.vault.auth(a) ?: return
        try {
            app.api.updatePush(auth, PushInfo("fcm", token))
        } catch (e: ApiException) {
            app.onApiError(a, e)
        }
    }

    // shortcut: getToken() помечен устаревшим в firebase-messaging 26 (замена — register()/onRegistered), но работает;
    // перейти полностью на register(), когда его поведение для уже выданного токена будет задокументировано.
    @Suppress("DEPRECATION")
    private suspend fun token(): String? = suspendCancellableCoroutine { c ->
        FirebaseMessaging.getInstance().token.addOnCompleteListener { t -> c.resume(if (t.isSuccessful) t.result else null) }
    }
}

/** Получает только сигнал {type, request_id, device_id, v}; данные запроса загружаются с сервера. Методы вызываются в фоновом потоке. */
class MfaGateMessagingService : FirebaseMessagingService() {
    private val app get() = application as App

    @Deprecated("Firebase 26: onRegistered")
    override fun onNewToken(token: String) = onRegistered(token)

    override fun onRegistered(token: String) {
        runBlocking { withTimeoutOrNull(20_000) { app.vault.accounts.value.filter { Push.covers(app, it) }.forEach { Push.send(app, it, token) } } }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        if (message.data["type"] != "auth_request") return
        val id = message.data["request_id"] ?: return
        val targets = app.vault.accounts.value.forSignal(message.data["device_id"]) { Push.covers(app, it) }
        runBlocking { withTimeoutOrNull(15_000) { Notifier.checkOne(app, id, targets) } }
    }
}
