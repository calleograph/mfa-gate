package io.github.calleograph.mfagate.ui

import android.os.Build
import android.security.keystore.KeyPermanentlyInvalidatedException
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.supervisorScope
import kotlinx.coroutines.withContext
import io.github.calleograph.mfagate.App
import io.github.calleograph.mfagate.BuildConfig
import io.github.calleograph.mfagate.core.ApiException
import io.github.calleograph.mfagate.core.AuthRequest
import io.github.calleograph.mfagate.core.Base64Url
import io.github.calleograph.mfagate.core.Decision
import io.github.calleograph.mfagate.core.DecisionBody
import io.github.calleograph.mfagate.core.DecisionString
import io.github.calleograph.mfagate.core.EnrollBody
import io.github.calleograph.mfagate.core.EnrollLink
import io.github.calleograph.mfagate.core.LinkException
import io.github.calleograph.mfagate.core.Links
import io.github.calleograph.mfagate.core.OtpParams
import io.github.calleograph.mfagate.data.Account
import io.github.calleograph.mfagate.data.DeviceKeys
import io.github.calleograph.mfagate.data.TotpEntry
import io.github.calleograph.mfagate.net.DeviceAuth
import io.github.calleograph.mfagate.push.Notifier
import io.github.calleograph.mfagate.push.Push
import io.github.calleograph.mfagate.push.nextBackoff
import java.security.Signature
import java.util.UUID

data class Pending(val account: Account, val request: AuthRequest)

/** Список ожидающих запросов всех серверов: long-poll, пока экран на переднем плане. */
class RequestsViewModel(private val app: App) : ViewModel() {
    private val byAccount = MutableStateFlow<Map<String, List<AuthRequest>>>(emptyMap())
    private val _errors = MutableStateFlow<Map<String, String>>(emptyMap())
    private val _loaded = MutableStateFlow(false)
    val errors: StateFlow<Map<String, String>> = _errors.asStateFlow()
    val loaded: StateFlow<Boolean> = _loaded.asStateFlow()
    val accounts = app.vault.accounts

    val pending: StateFlow<List<Pending>> = byAccount.map { m ->
        accounts.value.flatMap { a -> m[a.id].orEmpty().filter { it.status == "pending" }.map { Pending(a, it) } }
            .sortedByDescending { it.request.createdAtMillis ?: 0 }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    private var job: Job? = null

    fun start() {
        if (job?.isActive == true) return
        job = viewModelScope.launch {
            app.vault.accounts.map { list -> list.map { it.id } }.distinctUntilChanged().collectLatest {
                val list = app.vault.accounts.value
                byAccount.update { m -> m.filterKeys { k -> list.any { it.id == k } } }
                if (list.isEmpty()) _loaded.value = true
                supervisorScope { list.forEach { a -> launch { poll(a) } } }
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
    }

    /** «Повторить» после сетевой ошибки: сразу, не дожидаясь паузы между попытками. */
    fun retry() {
        stop()
        _errors.value = emptyMap()
        start()
    }

    private suspend fun poll(a: Account) {
        val auth = app.vault.auth(a) ?: run { _errors.update { it + (a.id to "Секрет устройства недоступен. Привяжите сервер заново.") }; return }
        var backoff = 2_000L
        var wait = 0 // первый запрос — без ожидания, чтобы сразу показать список
        while (true) {
            try {
                val items = app.api.pending(auth, wait)
                byAccount.update { it + (a.id to items) }
                _errors.update { it - a.id }
                _loaded.value = true
                items.forEach { app.vault.markNotified(it.id) } // показано на экране — фоновая проверка не дублирует уведомлением
                backoff = 2_000L
                wait = a.pollInterval.coerceIn(5, 30)
                // при наличии pending сервер отвечает сразу; пауза не даёт превратить long-poll в непрерывный опрос
                if (items.isNotEmpty()) delay(3_000)
            } catch (e: ApiException) {
                app.onApiError(a, e)
                if (e.revoked) return
                _errors.update { it + (a.id to "${a.host}: ${e.userMessage}") }
                _loaded.value = true
                delay(backoff)
                backoff = nextBackoff(backoff)
            }
        }
    }

    override fun onCleared() = stop()
}

/** Карточка одного запроса и решение по нему. */
class RequestViewModel(private val app: App, private val accountId: String, private val requestId: String) : ViewModel() {
    sealed interface State {
        data object Loading : State
        data class Ready(val account: Account, val request: AuthRequest) : State
        data class Done(val decision: Decision) : State
        /** Решение больше невозможно (409 уже обработан, 410 истёк, 404): кнопок нет, только пояснение. */
        data class Closed(val message: String) : State
        data class Failed(val message: String) : State
    }

    private val _state = MutableStateFlow<State>(State.Loading)
    val state = _state.asStateFlow()
    /** true с момента нажатия до ответа сервера: кнопки решения заблокированы, повторное нажатие игнорируется. */
    val busy = MutableStateFlow(false)
    val message = MutableStateFlow<String?>(null)

    init { load() }

    private fun account() = app.vault.accounts.value.firstOrNull { it.id == accountId }

    fun load() = viewModelScope.launch {
        val a = account() ?: run { _state.value = State.Failed("Учётная запись не найдена: возможно, сервер отвязан."); return@launch }
        val auth = app.vault.auth(a) ?: run { _state.value = State.Failed("Секрет устройства недоступен. Привяжите сервер заново."); return@launch }
        _state.value = State.Loading
        _state.value = try {
            State.Ready(a, app.api.request(auth, requestId))
        } catch (e: ApiException) {
            app.onApiError(a, e)
            State.Failed(e.userMessage)
        }
    }

    /** [unlock] показывает BiometricPrompt с CryptoObject и возвращает разблокированную подпись. */
    fun decide(d: Decision, unlock: suspend (Signature) -> Signature) = viewModelScope.launch {
        val st = _state.value as? State.Ready ?: return@launch
        val (a, r) = st
        if (busy.value) return@launch
        busy.value = true
        message.value = null
        try {
            val auth: DeviceAuth = app.vault.auth(a) ?: throw IllegalStateException("Секрет устройства недоступен. Привяжите сервер заново.")
            val challenge = r.challenge ?: throw IllegalStateException("Сервер не прислал challenge: подписывать нечего.")
            val payload = DecisionString.build(r.id, a.deviceId, challenge, d)
            val sig = try {
                DeviceKeys.signature(a.keyAlias)
            } catch (_: KeyPermanentlyInvalidatedException) {
                throw IllegalStateException("Ключ устройства стал недействителен (изменились отпечатки или блокировка экрана). Отвяжите сервер и привяжите заново.")
            }
            val unlocked = unlock(sig)
            val der = withContext(Dispatchers.IO) { unlocked.update(payload); unlocked.sign() }
            app.api.decide(auth, r.id, DecisionBody(d.wire, Base64Url.encode(der)))
            Notifier.cancel(app, r.id)
            _state.value = State.Done(d)
        } catch (e: AuthCancelled) {
            message.value = e.message
        } catch (e: ApiException) {
            app.onApiError(a, e)
            if (e.requestClosed) {
                Notifier.cancel(app, r.id)
                _state.value = State.Closed(e.userMessage)
            } else {
                message.value = e.userMessage
            }
        } catch (e: IllegalStateException) {
            message.value = e.message
        } catch (e: Exception) {
            message.value = "Не удалось подписать решение: ${e.javaClass.simpleName}."
        } finally {
            busy.value = false
        }
    }
}

class HistoryViewModel(private val app: App, private val accountId: String) : ViewModel() {
    sealed interface State {
        data object Loading : State
        data class Ready(val items: List<AuthRequest>) : State
        data class Failed(val message: String) : State
    }

    private val _state = MutableStateFlow<State>(State.Loading)
    val state = _state.asStateFlow()
    val account get() = app.vault.accounts.value.firstOrNull { it.id == accountId }

    init { load() }

    fun load() = viewModelScope.launch {
        val a = account ?: run { _state.value = State.Failed("Учётная запись не найдена."); return@launch }
        val auth = app.vault.auth(a) ?: run { _state.value = State.Failed("Секрет устройства недоступен. Привяжите сервер заново."); return@launch }
        _state.value = State.Loading
        _state.value = try {
            State.Ready(app.api.history(auth, 50))
        } catch (e: ApiException) {
            app.onApiError(a, e)
            State.Failed(e.userMessage)
        }
    }
}

/** Привязка: ссылка → подтверждение адреса → ключ в Keystore → POST /enroll. */
class EnrollViewModel(private val app: App) : ViewModel() {
    sealed interface State {
        /** [camera] — ошибка из-за камеры: экран предлагает открыть настройки разрешений. */
        data class Input(val error: String? = null, val camera: Boolean = false) : State
        /** [acceptedInsecure] — пользователь отметил, что понимает риск адреса http://. */
        data class Confirm(val link: EnrollLink, val busy: Boolean = false, val error: String? = null, val acceptedInsecure: Boolean = false) : State
        data class Done(val account: Account) : State
    }

    private val _state = MutableStateFlow<State>(State.Input())
    val state = _state.asStateFlow()

    fun submit(text: String) {
        _state.value = try {
            State.Confirm(Links.parseEnroll(text))
        } catch (e: LinkException) {
            State.Input(e.message)
        }
    }

    fun reset() { _state.value = State.Input() }

    /** Камера недоступна или запрещена: экран сразу показывает ручной ввод с пояснением. */
    fun cameraUnavailable() {
        _state.value = State.Input("Камера недоступна или доступ к ней запрещён. Вставьте ссылку привязки вручную: её можно скопировать из письма или портала MFA Gate.", camera = true)
    }

    fun acceptInsecure(accepted: Boolean) {
        _state.update { if (it is State.Confirm && !it.busy) it.copy(acceptedInsecure = accepted, error = null) else it }
    }

    fun confirm() = viewModelScope.launch {
        val st = _state.value as? State.Confirm ?: return@launch
        if (st.busy) return@launch
        if (st.link.insecure && !st.acceptedInsecure) {
            _state.value = st.copy(error = "Отметьте, что понимаете риск соединения без шифрования.")
            return@launch
        }
        Biometrics.signUnavailableReason(app)?.let { _state.value = st.copy(error = it); return@launch }
        _state.value = st.copy(busy = true, error = null)
        val id = UUID.randomUUID().toString()
        val alias = "mfa_gate_device_$id"
        try {
            val pub = withContext(Dispatchers.Default) { DeviceKeys.generate(alias) }
            val resp = app.api.enroll(
                st.link.baseUrl,
                EnrollBody(
                    token = st.link.token,
                    deviceName = "${Build.MANUFACTURER} ${Build.MODEL}".trim().take(64),
                    appVersion = BuildConfig.VERSION_NAME,
                    publicKey = Base64Url.encode(pub),
                    push = Push.enrollInfo(),
                ),
            )
            if (resp.deviceId.isBlank() || resp.deviceId.length > 64 || Base64Url.decode(resp.deviceSecret) == null) throw ApiException(502, "bad_response")
            // Адрес берём из подтверждённой пользователем ссылки, а не из server.url ответа.
            var account = Account(
                id = id,
                baseUrl = st.link.baseUrl,
                serverName = resp.server.name.orEmpty().take(100),
                login = resp.user.login.take(100),
                displayName = resp.user.displayName.orEmpty().take(100),
                label = st.link.label,
                deviceId = resp.deviceId,
                sealedSecret = app.vault.sealDeviceSecret(id, resp.deviceSecret),
                keyAlias = alias,
                pollInterval = resp.pollInterval,
                fcm = resp.pushConfig?.fcm,
            )
            if (resp.pushConfig == null) {
                app.vault.auth(account)?.let { auth ->
                    runCatching { app.api.config(auth) }.getOrNull()?.let { account = account.copy(fcm = it.pushConfig?.fcm, pollInterval = it.pollInterval) }
                }
            }
            app.vault.saveAccount(account)
            app.vault.onboarded = true
            app.scope.launch { Push.register(app, account) }
            _state.value = State.Done(account)
        } catch (e: Exception) {
            DeviceKeys.delete(alias)
            val msg = when (e) {
                is ApiException -> e.userMessage
                else -> "Не удалось создать ключ устройства. Проверьте, что настроена блокировка экрана."
            }
            _state.value = st.copy(busy = false, error = msg)
        }
    }
}

class AccountsViewModel(private val app: App) : ViewModel() {
    val accounts = app.vault.accounts
    val busy = MutableStateFlow<String?>(null)
    /** Ошибка отвязки: аккаунт и текст; UI предлагает удалить только на телефоне. */
    val unlinkError = MutableStateFlow<Pair<Account, String>?>(null)

    fun unlink(a: Account) = viewModelScope.launch {
        busy.value = a.id
        try {
            app.vault.auth(a)?.let { app.api.unlink(it) }
            app.forgetAccount(a)
        } catch (e: ApiException) {
            if (e.status == 401) app.forgetAccount(a) else unlinkError.value = a to e.userMessage
        } finally {
            busy.value = null
        }
    }

    fun forgetLocally(a: Account) {
        unlinkError.value = null
        app.forgetAccount(a)
    }
}

class TotpViewModel(private val app: App) : ViewModel() {
    data class Item(val entry: TotpEntry, val secret: ByteArray?)

    /** null — ещё загружается (расшифровка в фоне). */
    val items: StateFlow<List<Item>?> = app.vault.totp.map { list -> list.map { Item(it, app.vault.totpSecret(it)) } }
        .flowOn(Dispatchers.Default)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    fun add(p: OtpParams) = app.vault.addTotp(p)
    fun remove(id: String) = app.vault.removeTotp(id)
}
