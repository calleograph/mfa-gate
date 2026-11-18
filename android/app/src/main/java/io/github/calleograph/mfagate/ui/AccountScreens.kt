package io.github.calleograph.mfagate.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import com.google.zxing.client.android.Intents
import io.github.calleograph.mfagate.data.Account
import io.github.calleograph.mfagate.ui.theme.Glass
import io.github.calleograph.mfagate.ui.theme.MinTap
import io.github.calleograph.mfagate.ui.theme.MonoSmall
import io.github.calleograph.mfagate.ui.theme.Space

/** Параметры сканера QR (zxing-android-embedded, без сервисов Google). */
fun qrScanOptions(prompt: String): ScanOptions = ScanOptions()
    .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
    .setPrompt(prompt)
    .setBeepEnabled(false)
    .setOrientationLocked(false)

/**
 * Сканер QR с запросом камеры. Нет камеры, отказ в разрешении (в том числе «больше не спрашивать») — [onDenied]:
 * экран показывает ручной ввод. Отмена сканирования пользователем ничего не делает.
 */
@Composable
fun rememberQrScanner(prompt: String, onDenied: () -> Unit, onResult: (String) -> Unit): () -> Unit {
    val ctx = LocalContext.current
    val scan = rememberLauncherForActivityResult(ScanContract()) { r ->
        val text = r.contents
        when {
            text != null -> onResult(text)
            r.originalIntent?.hasExtra(Intents.Scan.MISSING_CAMERA_PERMISSION) == true -> onDenied()
        }
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) scan.launch(qrScanOptions(prompt)) else onDenied()
    }
    return {
        when {
            !ctx.packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY) -> onDenied()
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED -> scan.launch(qrScanOptions(prompt))
            else -> permission.launch(Manifest.permission.CAMERA)
        }
    }
}

/** Переход в системные настройки приложения (разрешения камеры и уведомлений). */
fun openAppSettings(ctx: android.content.Context) {
    ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", ctx.packageName, null)))
}

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
                "Администратор или портал MFA Gate покажет QR-код привязки. После привязки сюда будут приходить запросы входа.",
                Modifier.padding(pad), "Сканировать QR-код", onEnroll,
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
                        if (a.baseUrl.startsWith("http://")) {
                            Text("Без шифрования (HTTP) — только для изолированной тестовой сети", style = MaterialTheme.typography.labelLarge, color = Glass.colors.onWarnContainer)
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

/**
 * Привязка к серверу. [welcome] — первый запуск: вместо заголовка экрана — приветствие с одной главной кнопкой
 * «Сканировать QR-код», ручной ввод спрятан за текстовой кнопкой; [onSkip] — «только коды TOTP».
 * [onBack] null — кнопки «Назад» нет.
 */
@Composable
fun EnrollScreen(vm: EnrollViewModel, onBack: (() -> Unit)?, onFinish: () -> Unit, welcome: Boolean = false, onSkip: (() -> Unit)? = null) {
    val state by vm.state.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    val scan = rememberQrScanner("Наведите камеру на QR-код привязки", onDenied = vm::cameraUnavailable, onResult = vm::submit)
    var text by rememberSaveable { mutableStateOf("") }
    var manual by rememberSaveable { mutableStateOf(!welcome) }
    val title = if (welcome && state is EnrollViewModel.State.Input) "" else "Привязка к серверу"

    ScreenScaffold(title, onBack = onBack.takeIf { state !is EnrollViewModel.State.Done }) { pad ->
        when (val s = state) {
            is EnrollViewModel.State.Done -> Column(
                Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(Space.l),
                verticalArrangement = Arrangement.spacedBy(Space.l),
            ) {
                Notice(
                    "Запросы входа для ${s.account.title} будут приходить в это приложение. Подтверждение всегда требует биометрии или PIN.",
                    Tone.Ok, title = "Телефон привязан",
                )
                NotificationPermissionBanner(firstRun = true)
                PrimaryButton("Готово", onFinish, Modifier.fillMaxWidth())
            }
            is EnrollViewModel.State.Input -> Column(
                Modifier.fillMaxSize().padding(pad).imePadding().verticalScroll(rememberScrollState()).padding(horizontal = Space.l, vertical = Space.s),
                verticalArrangement = Arrangement.spacedBy(Space.l),
            ) {
                if (welcome) WelcomeHeader()
                else Text("Отсканируйте QR-код привязки, который показал администратор или портал MFA Gate.", style = MaterialTheme.typography.bodyLarge)
                PrimaryButton("Сканировать QR-код", scan, Modifier.fillMaxWidth())
                s.error?.let { msg ->
                    Notice(msg, Tone.Warning, action = if (s.camera) {
                        { QuietButton("Открыть настройки разрешений", { openAppSettings(ctx) }) }
                    } else null)
                }
                if (!manual && s.error == null) {
                    QuietButton("Вставить ссылку вручную", { manual = true }, Modifier.fillMaxWidth())
                } else {
                    HorizontalDivider(Modifier.padding(vertical = Space.xs), color = Glass.colors.line)
                    Text("Ссылка вручную", style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
                    OutlinedTextField(
                        value = text,
                        onValueChange = { text = it.take(2048) },
                        label = { Text("Ссылка mfagate://enroll…") },
                        modifier = Modifier.fillMaxWidth(),
                        minLines = 2,
                        maxLines = 4,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Done, autoCorrectEnabled = false),
                        keyboardActions = KeyboardActions(onDone = { if (text.isNotBlank()) vm.submit(text) }),
                    )
                    SecondaryButton("Продолжить", { vm.submit(text) }, Modifier.fillMaxWidth(), enabled = text.isNotBlank())
                }
                if (onSkip != null) QuietButton("Пропустить: только одноразовые коды", onSkip, Modifier.fillMaxWidth())
            }
            is EnrollViewModel.State.Confirm -> Column(
                Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(Space.l),
                verticalArrangement = Arrangement.spacedBy(Space.l),
            ) {
                Text("Проверьте сервер", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.semantics { heading() })
                Panel {
                    InfoRow("Адрес", s.link.baseUrl, mono = true)
                    InfoRow("Учётная запись", s.link.label)
                }
                Text(
                    "Привязывайте, только если вы сами запросили привязку и узнаёте адрес. Чужой сервер сможет присылать вам запросы входа.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (s.link.insecure) InsecureWarning(s.acceptedInsecure, enabled = !s.busy, onChange = vm::acceptInsecure)
                s.error?.let { ErrorText(it) }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(Space.m), verticalAlignment = Alignment.CenterVertically) {
                    SecondaryButton("Отмена", vm::reset, Modifier.weight(1f), enabled = !s.busy)
                    PrimaryButton(
                        if (s.busy) "Привязка…" else if (s.error != null) "Повторить" else "Привязать",
                        { vm.confirm() }, Modifier.weight(1f),
                        enabled = !s.busy && (!s.link.insecure || s.acceptedInsecure),
                    )
                }
            }
        }
    }
}

/** Приветствие первого запуска: знак, название, одна фраза о том, что делать. */
@Composable
private fun WelcomeHeader() {
    Column(Modifier.fillMaxWidth().padding(top = Space.xxl), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(Space.m)) {
        Surface(Modifier.size(72.dp).clearAndSetSemantics { }, shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.primary) {
            Box(contentAlignment = Alignment.Center) {
                Text("MFA", style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.onPrimary)
            }
        }
        Text(
            "MFA Gate Authenticator", style = MaterialTheme.typography.headlineMedium, textAlign = TextAlign.Center,
            modifier = Modifier.semantics { heading() },
        )
        Text(
            "Подтверждайте входы в VPN и рабочие системы касанием и отпечатком. Отсканируйте QR-код привязки, который покажет администратор или портал MFA Gate.",
            style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center,
            modifier = Modifier.widthIn(max = 420.dp).padding(bottom = Space.l),
        )
    }
}

/** Адрес http://: явное предупреждение и отметка «понимаю риск»; без отметки «Привязать» недоступна. */
@Composable
private fun InsecureWarning(accepted: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    Notice(
        "Адрес сервера начинается с http://. Данные привязки и запросы входа передаются в открытом виде, и их может подменить любой участник сети. " +
            "Подходит только для первичной настройки в изолированной тестовой сети. В рабочей сети попросите администратора включить HTTPS.",
        Tone.Warning,
        title = "Соединение без шифрования — только для изолированной тестовой сети",
        action = {
            Row(
                Modifier.fillMaxWidth().heightIn(min = MinTap)
                    .toggleable(accepted, enabled = enabled, role = Role.Checkbox, onValueChange = onChange),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(Space.s),
            ) {
                Checkbox(checked = accepted, onCheckedChange = null, enabled = enabled)
                Text("Понимаю риск, это тестовая сеть", style = MaterialTheme.typography.labelLarge)
            }
        },
    )
}
