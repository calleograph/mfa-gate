package twofold.authenticator.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import androidx.activity.compose.rememberLauncherForActivityResult
import twofold.authenticator.data.Account
import twofold.authenticator.ui.theme.MinTap
import twofold.authenticator.ui.theme.MonoSmall
import twofold.authenticator.ui.theme.Space

/** Параметры сканера QR (zxing-android-embedded, без сервисов Google). */
fun qrScanOptions(prompt: String): ScanOptions = ScanOptions()
    .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
    .setPrompt(prompt)
    .setBeepEnabled(false)
    .setOrientationLocked(false)

@Composable
fun ServersScreen(vm: AccountsViewModel, onEnroll: () -> Unit, onHistory: (Account) -> Unit) {
    val accounts by vm.accounts.collectAsStateWithLifecycle()
    val busy by vm.busy.collectAsStateWithLifecycle()
    val unlinkError by vm.unlinkError.collectAsStateWithLifecycle()
    var confirm by rememberSaveable { mutableStateOf<String?>(null) }

    ScreenScaffold("Серверы") { pad ->
        if (accounts.isEmpty()) {
            StateMessage(
                "Серверов пока нет",
                "Администратор или портал Twofold покажет QR-код привязки. После привязки сюда будут приходить запросы входа.",
                Modifier.padding(pad), "Привязать сервер", onEnroll,
            )
        } else {
            LazyColumn(Modifier.fillMaxSize().padding(pad), contentPadding = PaddingValues(Space.l), verticalArrangement = Arrangement.spacedBy(Space.m)) {
                items(accounts, key = { it.id }) { a ->
                    Panel {
                        Text(a.title, style = MaterialTheme.typography.titleMedium)
                        Text(a.host, style = MonoSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (a.label.isNotBlank() && a.label != a.title) {
                            Text(a.label, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Row(Modifier.fillMaxWidth().padding(top = Space.s), horizontalArrangement = Arrangement.spacedBy(Space.m)) {
                            SecondaryButton("История", { onHistory(a) }, Modifier.weight(1f))
                            SecondaryButton(if (busy == a.id) "Отвязка…" else "Отвязать", { confirm = a.id }, Modifier.weight(1f), enabled = busy == null)
                        }
                    }
                }
                item { PrimaryButton("Привязать ещё сервер", onEnroll, Modifier.fillMaxWidth().padding(top = Space.s)) }
            }
        }
    }

    accounts.firstOrNull { it.id == confirm }?.let { a ->
        AlertDialog(
            onDismissRequest = { confirm = null },
            title = { Text("Отвязать ${a.host}?") },
            text = { Text("Телефон перестанет получать запросы входа для ${a.title}. Ключ устройства будет удалён. Чтобы вернуть, понадобится новый QR-код.") },
            confirmButton = { TextButton({ confirm = null; vm.unlink(a) }, Modifier.heightIn(min = MinTap)) { Text("Отвязать") } },
            dismissButton = { TextButton({ confirm = null }, Modifier.heightIn(min = MinTap)) { Text("Отмена") } },
        )
    }
    unlinkError?.let { (a, msg) ->
        AlertDialog(
            onDismissRequest = { vm.unlinkError.value = null },
            title = { Text("Сервер не ответил") },
            text = { Text("$msg\n\nМожно удалить учётную запись только с телефона. На сервере устройство останется активным, пока его не отзовёт администратор.") },
            confirmButton = { TextButton({ vm.forgetLocally(a) }, Modifier.heightIn(min = MinTap)) { Text("Удалить на телефоне") } },
            dismissButton = { TextButton({ vm.unlinkError.value = null }, Modifier.heightIn(min = MinTap)) { Text("Отмена") } },
        )
    }
}

@Composable
fun EnrollScreen(vm: EnrollViewModel, onBack: () -> Unit) {
    val state by vm.state.collectAsStateWithLifecycle()
    val scan = rememberLauncherForActivityResult(ScanContract()) { r -> r.contents?.let(vm::submit) }
    var text by rememberSaveable { mutableStateOf("") }

    ScreenScaffold("Привязка к серверу", onBack = onBack) { pad ->
        when (val s = state) {
            is EnrollViewModel.State.Done -> StateMessage(
                "Телефон привязан",
                "Запросы входа для ${s.account.title} будут приходить в это приложение. Подтверждение всегда требует биометрии или PIN.",
                Modifier.padding(pad), "Готово", onBack,
            )
            is EnrollViewModel.State.Input -> Column(
                Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(Space.l),
                verticalArrangement = Arrangement.spacedBy(Space.l),
            ) {
                Text(
                    "Отсканируйте QR-код привязки, который показал администратор или портал Twofold.",
                    style = MaterialTheme.typography.bodyLarge,
                )
                PrimaryButton("Сканировать QR-код", { scan.launch(qrScanOptions("Наведите камеру на QR-код привязки")) }, Modifier.fillMaxWidth())
                HorizontalDivider(Modifier.padding(vertical = Space.s))
                Text("Или вставьте ссылку вручную", style = MaterialTheme.typography.titleMedium)
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it.take(2048) },
                    label = { Text("Ссылка twofold://enroll…") },
                    modifier = Modifier.fillMaxWidth(),
                    minLines = 2,
                    maxLines = 4,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Done, autoCorrectEnabled = false),
                )
                s.error?.let { ErrorText(it) }
                SecondaryButton("Продолжить", { vm.submit(text) }, Modifier.fillMaxWidth(), enabled = text.isNotBlank())
            }
            is EnrollViewModel.State.Confirm -> Column(
                Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(Space.l),
                verticalArrangement = Arrangement.spacedBy(Space.l),
            ) {
                Text("Проверьте сервер", style = MaterialTheme.typography.headlineSmall)
                Panel {
                    InfoRow("Адрес", s.link.baseUrl, mono = true)
                    InfoRow("Учётная запись", s.link.label)
                }
                Text(
                    "Привязывайте, только если вы сами запросили привязку и узнаёте адрес. Чужой сервер сможет присылать вам запросы входа.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (s.link.baseUrl.startsWith("http://")) ErrorText("Соединение без шифрования (HTTP). Допустимо только для тестового стенда в отладочной сборке.")
                s.error?.let { ErrorText(it) }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(Space.m), verticalAlignment = Alignment.CenterVertically) {
                    SecondaryButton("Отмена", vm::reset, Modifier.weight(1f), enabled = !s.busy)
                    PrimaryButton(if (s.busy) "Привязка…" else "Привязать", { vm.confirm() }, Modifier.weight(1f), enabled = !s.busy)
                }
            }
        }
    }
}
