package io.github.calleograph.mfagate.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleStartEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.delay
import io.github.calleograph.mfagate.core.AuthRequest
import io.github.calleograph.mfagate.core.Decision
import io.github.calleograph.mfagate.core.remainingText
import io.github.calleograph.mfagate.data.Account
import io.github.calleograph.mfagate.ui.theme.Glass
import io.github.calleograph.mfagate.ui.theme.MonoSmall
import io.github.calleograph.mfagate.ui.theme.Space
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

private val timeFormat = DateTimeFormatter.ofPattern("d MMM, HH:mm:ss", Locale.forLanguageTag("ru")).withZone(ZoneId.systemDefault())

fun formatTime(millis: Long?): String? = millis?.let { timeFormat.format(Instant.ofEpochMilli(it)) }

/** Текущее время с обновлением раз в секунду — для обратного отсчёта. */
@Composable
fun rememberNow(): Long {
    val now by produceState(System.currentTimeMillis()) {
        while (true) {
            delay(1_000 - System.currentTimeMillis() % 1_000)
            value = System.currentTimeMillis()
        }
    }
    return now
}

@Composable
fun RequestsScreen(vm: RequestsViewModel, onOpen: (Pending) -> Unit, onEnroll: () -> Unit) {
    LifecycleStartEffect(vm) {
        vm.start()
        onStopOrDispose { vm.stop() }
    }
    val accounts by vm.accounts.collectAsStateWithLifecycle()
    val pending by vm.pending.collectAsStateWithLifecycle()
    val errors by vm.errors.collectAsStateWithLifecycle()
    val loaded by vm.loaded.collectAsStateWithLifecycle()
    val now = rememberNow()

    ScreenScaffold("Запросы входа") { pad ->
        when {
            accounts.isEmpty() -> StateMessage(
                "Нет привязанных серверов",
                "Привяжите телефон к серверу MFA Gate по QR-коду от администратора, чтобы подтверждать входы.",
                Modifier.padding(pad), "Сканировать QR-код", onEnroll,
            )
            !loaded -> Loading(Modifier.padding(pad))
            else -> LazyColumn(
                Modifier.fillMaxSize().padding(pad),
                contentPadding = PaddingValues(Space.l),
                verticalArrangement = Arrangement.spacedBy(Space.m),
            ) {
                item { NotificationPermissionBanner() }
                items(errors.values.toList()) { msg ->
                    Notice(msg, Tone.Error, action = { QuietButton("Повторить", vm::retry) })
                }
                if (pending.isEmpty()) {
                    item {
                        StateMessage(
                            "Нет запросов",
                            "Когда кто-то попытается войти под вашей учётной записью, запрос появится здесь. Экран обновляется сам, тянуть вниз не нужно.",
                            Modifier.heightIn(min = 320.dp),
                        )
                    }
                }
                items(pending, key = { it.account.id + "/" + it.request.id }) { p -> RequestCard(p, now) { onOpen(p) } }
            }
        }
    }
}

@Composable
private fun RequestCard(p: Pending, now: Long, onClick: () -> Unit) {
    val r = p.request
    GlassSurface(onClick = onClick, onClickLabel = "Открыть запрос") {
        Column(Modifier.padding(Space.l), verticalArrangement = Arrangement.spacedBy(Space.xs)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(r.resource ?: "Вход", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                remainingText(r.expiresAtMillis, now + r.clockOffset)?.let {
                    Text(it, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
                }
            }
            Text("${r.login} · ${p.account.host}", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            listOfNotNull(r.sourceIp, r.client, r.location).joinToString(" · ").takeIf { it.isNotEmpty() }?.let {
                Text(it, style = MonoSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
fun RequestScreen(vm: RequestViewModel, onBack: () -> Unit) {
    val state by vm.state.collectAsStateWithLifecycle()
    val busy by vm.busy.collectAsStateWithLifecycle()
    val message by vm.message.collectAsStateWithLifecycle()
    val activity = LocalContext.current.findActivity()
    val now = rememberNow()

    ScreenScaffold("Запрос на вход", onBack = onBack) { pad ->
        when (val s = state) {
            RequestViewModel.State.Loading -> Loading(Modifier.padding(pad))
            is RequestViewModel.State.Failed -> StateMessage("Не удалось открыть запрос", s.message, Modifier.padding(pad), "Повторить") { vm.load() }
            is RequestViewModel.State.Closed -> StateMessage("Решение не нужно", s.message, Modifier.padding(pad), "Готово", onBack)
            is RequestViewModel.State.Done -> StateMessage(
                if (s.decision == Decision.APPROVE) "Вход подтверждён" else "Вход отклонён",
                if (s.decision == Decision.APPROVE) "Можно вернуться к устройству, на котором вы входите." else "Попытка входа заблокирована. Если это были не вы, смените пароль и сообщите администратору.",
                Modifier.padding(pad), "Готово", onBack,
            )
            is RequestViewModel.State.Ready -> RequestDetails(s.account, s.request, now, busy, message, Modifier.padding(pad)) { d ->
                val subtitle = "${s.request.login} → ${s.request.resource ?: s.account.host}"
                vm.decide(d) { sig ->
                    Biometrics.unlockSignature(activity, if (d == Decision.APPROVE) "Подтвердить вход" else "Отклонить вход", subtitle, sig)
                }
            }
        }
    }
}

@Composable
private fun RequestDetails(a: Account, r: AuthRequest, now: Long, busy: Boolean, message: String?, modifier: Modifier, onDecide: (Decision) -> Unit) {
    // таймер по времени сервера (server_time / Date): сбитые часы телефона не прячут и не продлевают запрос
    val expired = r.expiresAtMillis?.let { it <= now + r.clockOffset } == true
    val pending = r.status == "pending" && !expired
    Column(modifier.fillMaxSize()) {
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = Space.l, vertical = Space.s),
            verticalArrangement = Arrangement.spacedBy(Space.l),
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(Space.xs)) {
                Text(
                    if (pending) "Это вы входите?" else if (expired && r.status == "pending") "Время на подтверждение истекло" else statusText(r.status),
                    style = MaterialTheme.typography.headlineSmall,
                )
                remainingText(r.expiresAtMillis, now + r.clockOffset)?.takeIf { pending }?.let {
                    Text(it, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
                }
            }
            Panel {
                InfoRow("Учётная запись", r.login)
                InfoRow("Ресурс", r.resource)
                InfoRow("IP-адрес", r.sourceIp, mono = true)
                InfoRow("Место", r.location)
                InfoRow("Клиент", r.client)
                InfoRow("Время", formatTime(r.createdAtMillis))
                InfoRow("Сервер", a.host, mono = true)
            }
            if (pending) {
                Text(
                    "Если вы сейчас не входили, нажмите «Отклонить»: кто-то знает ваш пароль.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            message?.let { ErrorText(it) }
        }
        if (pending) {
            Row(
                Modifier.fillMaxWidth().padding(Space.l).navigationBarsPadding(),
                horizontalArrangement = Arrangement.spacedBy(Space.m),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // после нажатия обе кнопки заблокированы до ответа сервера: второе решение по тому же запросу не уйдёт
                SecondaryButton(if (busy) "Отправка…" else "Отклонить", { onDecide(Decision.DENY) }, Modifier.weight(1f), enabled = !busy)
                PrimaryButton(if (busy) "Отправка…" else "Подтвердить", { onDecide(Decision.APPROVE) }, Modifier.weight(1f), enabled = !busy)
            }
        }
    }
}

fun statusText(status: String) = when (status) {
    "pending" -> "Ожидает решения"
    "approved" -> "Подтверждён"
    "denied" -> "Отклонён"
    "expired" -> "Истёк"
    "cancelled", "canceled" -> "Отменён"
    else -> status
}

@Composable
fun HistoryScreen(vm: HistoryViewModel, onBack: () -> Unit) {
    val state by vm.state.collectAsStateWithLifecycle()
    ScreenScaffold("История · ${vm.account?.host.orEmpty()}", onBack = onBack) { pad ->
        when (val s = state) {
            HistoryViewModel.State.Loading -> Loading(Modifier.padding(pad))
            is HistoryViewModel.State.Failed -> StateMessage("Не удалось загрузить историю", s.message, Modifier.padding(pad), "Повторить") { vm.load() }
            is HistoryViewModel.State.Ready -> if (s.items.isEmpty()) {
                StateMessage("История пуста", "Здесь появятся запросы входа и решения по ним.", Modifier.padding(pad))
            } else {
                LazyColumn(
                    Modifier.fillMaxSize().padding(pad),
                    contentPadding = PaddingValues(Space.l),
                    verticalArrangement = Arrangement.spacedBy(Space.s),
                ) {
                    items(s.items, key = { it.id }) { r ->
                        GlassSurface(shape = MaterialTheme.shapes.medium) {
                            Row(Modifier.padding(Space.l), verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Space.xs)) {
                                    Text(r.resource ?: r.login, style = MaterialTheme.typography.titleMedium)
                                    Text(
                                        listOfNotNull(r.login, formatTime(r.createdAtMillis), r.sourceIp).joinToString(" · "),
                                        style = MaterialTheme.typography.bodyMedium,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                                Text(
                                    statusText(r.status),
                                    style = MaterialTheme.typography.labelLarge,
                                    color = when (r.status) {
                                        "approved" -> Glass.colors.onOkContainer
                                        "denied" -> MaterialTheme.colorScheme.error
                                        else -> MaterialTheme.colorScheme.onSurfaceVariant
                                    },
                                    modifier = Modifier.padding(start = Space.m),
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}
