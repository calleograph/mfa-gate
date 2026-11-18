package twofold.authenticator.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.os.PersistableBundle
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.journeyapps.barcodescanner.ScanContract
import twofold.authenticator.core.LinkException
import twofold.authenticator.core.Links
import twofold.authenticator.core.OtpAlgorithm
import twofold.authenticator.core.Totp
import twofold.authenticator.ui.theme.CodeStyle
import twofold.authenticator.ui.theme.MinTap
import twofold.authenticator.ui.theme.Space

/** «123 456» / «1234 5678» — так код легче прочитать и переписать. */
fun groupCode(code: String) = code.chunked(code.length / 2).joinToString(" ")

@Composable
fun CodesScreen(vm: TotpViewModel, onAdd: () -> Unit) {
    SecureWindow()
    val items by vm.items.collectAsStateWithLifecycle()
    val now = rememberNow()
    val ctx = LocalContext.current
    var confirm by rememberSaveable { mutableStateOf<String?>(null) }

    ScreenScaffold("Коды", actions = {
        IconButton(onClick = onAdd) { Icon(Icons.Filled.Add, contentDescription = "Добавить код") }
    }) { pad ->
        val list = items
        when {
            list == null -> Loading(Modifier.padding(pad))
            list.isEmpty() -> StateMessage(
                "Кодов пока нет",
                "Добавьте учётную запись сервиса, который поддерживает одноразовые коды (TOTP). Коды работают без интернета.",
                Modifier.padding(pad), "Добавить код", onAdd,
            )
            else -> LazyColumn(Modifier.fillMaxSize().padding(pad), contentPadding = PaddingValues(Space.l), verticalArrangement = Arrangement.spacedBy(Space.m)) {
                items(list, key = { it.entry.id }) { item ->
                    val e = item.entry
                    val sec = now / 1000
                    val code = item.secret?.let { Totp.code(it, sec, e.algorithm, e.digits, e.period) }
                    val left = Totp.secondsLeft(sec, e.period)
                    Surface(
                        Modifier.fillMaxWidth().clickable(enabled = code != null, role = Role.Button, onClickLabel = "Скопировать код") {
                            val cm = ctx.getSystemService(ClipboardManager::class.java)
                            val clip = ClipData.newPlainText("Код", code)
                            // не показывать код в превью буфера обмена (Android 13+)
                            clip.description.extras = PersistableBundle().apply { putBoolean("android.content.extra.IS_SENSITIVE", true) }
                            cm.setPrimaryClip(clip)
                            Toast.makeText(ctx, "Код скопирован", Toast.LENGTH_SHORT).show()
                        },
                        shape = MaterialTheme.shapes.large,
                        color = MaterialTheme.colorScheme.surfaceContainerLow,
                    ) {
                        Row(Modifier.padding(start = Space.l, top = Space.m, bottom = Space.m, end = Space.xs), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Space.xs)) {
                                Text(e.issuer.ifBlank { e.account }, style = MaterialTheme.typography.titleMedium)
                                if (e.issuer.isNotBlank() && e.account.isNotBlank()) {
                                    Text(e.account, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                                Text(
                                    code?.let(::groupCode) ?: "Секрет недоступен",
                                    style = if (code != null) CodeStyle else MaterialTheme.typography.bodyMedium,
                                    color = if (code != null) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.error,
                                )
                            }
                            Box(Modifier.size(MinTap).semantics { contentDescription = "Осталось $left с" }, contentAlignment = Alignment.Center) {
                                CircularProgressIndicator(
                                    progress = { left / e.period.toFloat() },
                                    modifier = Modifier.size(32.dp),
                                    strokeWidth = 3.dp,
                                    color = if (left <= 5) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
                                    trackColor = MaterialTheme.colorScheme.outlineVariant,
                                )
                                Text("$left", style = MaterialTheme.typography.labelSmall)
                            }
                            IconButton(onClick = { confirm = e.id }) { Icon(Icons.Filled.Delete, contentDescription = "Удалить ${e.issuer} ${e.account}") }
                        }
                    }
                }
            }
        }
    }

    items?.firstOrNull { it.entry.id == confirm }?.let { item ->
        AlertDialog(
            onDismissRequest = { confirm = null },
            title = { Text("Удалить код?") },
            text = { Text("${item.entry.issuer} ${item.entry.account}\n\nПрежде чем удалять, отключите вход по кодам в самом сервисе, иначе вы потеряете доступ к нему.") },
            confirmButton = { TextButton({ vm.remove(item.entry.id); confirm = null }, Modifier.heightIn(min = MinTap)) { Text("Удалить") } },
            dismissButton = { TextButton({ confirm = null }, Modifier.heightIn(min = MinTap)) { Text("Отмена") } },
        )
    }
}

@Composable
fun AddTotpScreen(vm: TotpViewModel, onDone: () -> Unit) {
    SecureWindow()
    val ctx = LocalContext.current
    var issuer by rememberSaveable { mutableStateOf("") }
    var account by rememberSaveable { mutableStateOf("") }
    var secret by rememberSaveable { mutableStateOf("") }
    var algorithm by rememberSaveable { mutableStateOf(OtpAlgorithm.SHA1) }
    var digits by rememberSaveable { mutableIntStateOf(6) }
    var period by rememberSaveable { mutableStateOf("30") }
    var error by rememberSaveable { mutableStateOf<String?>(null) }

    fun saved(name: String) {
        Toast.makeText(ctx, "Добавлено: $name", Toast.LENGTH_SHORT).show()
        onDone()
    }

    val scan = rememberLauncherForActivityResult(ScanContract()) { r ->
        val text = r.contents ?: return@rememberLauncherForActivityResult
        try {
            val p = Links.parseOtpAuth(text)
            vm.add(p)
            saved(p.issuer.ifBlank { p.account })
        } catch (e: LinkException) {
            error = e.message
        }
    }

    ScreenScaffold("Новый код", onBack = onDone) { pad ->
        Column(
            Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(Space.l),
            verticalArrangement = Arrangement.spacedBy(Space.l),
        ) {
            Text("Отсканируйте QR-код из настроек двухфакторной защиты сервиса.", style = MaterialTheme.typography.bodyLarge)
            PrimaryButton("Сканировать QR-код", { scan.launch(qrScanOptions("Наведите камеру на QR-код otpauth")) }, Modifier.fillMaxWidth())
            HorizontalDivider(Modifier.padding(vertical = Space.s))
            Text("Или введите вручную", style = MaterialTheme.typography.titleMedium)
            OutlinedTextField(issuer, { issuer = it.take(100) }, Modifier.fillMaxWidth(), label = { Text("Сервис") }, singleLine = true)
            OutlinedTextField(account, { account = it.take(200) }, Modifier.fillMaxWidth(), label = { Text("Учётная запись") }, singleLine = true)
            OutlinedTextField(
                secret, { secret = it.take(256) }, Modifier.fillMaxWidth(),
                label = { Text("Секретный ключ (Base32)") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.Characters, autoCorrectEnabled = false,
                    keyboardType = KeyboardType.Password, imeAction = ImeAction.Done,
                ),
            )
            Text("Алгоритм", style = MaterialTheme.typography.labelLarge)
            Row(horizontalArrangement = Arrangement.spacedBy(Space.s)) {
                OtpAlgorithm.entries.forEach { a ->
                    FilterChip(algorithm == a, { algorithm = a }, { Text(a.name) }, Modifier.heightIn(min = MinTap))
                }
            }
            Text("Длина кода", style = MaterialTheme.typography.labelLarge)
            Row(horizontalArrangement = Arrangement.spacedBy(Space.s)) {
                listOf(6, 8).forEach { d -> FilterChip(digits == d, { digits = d }, { Text("$d цифр") }, Modifier.heightIn(min = MinTap)) }
            }
            OutlinedTextField(
                period, { period = it.filter(Char::isDigit).take(3) }, Modifier.fillMaxWidth(),
                label = { Text("Период, секунд") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            )
            error?.let { ErrorText(it) }
            PrimaryButton("Сохранить", {
                try {
                    val p = Links.manual(issuer, account, secret, algorithm.name, digits.toString(), period)
                    vm.add(p)
                    saved(p.issuer.ifBlank { p.account })
                } catch (e: LinkException) {
                    error = e.message
                }
            }, Modifier.fillMaxWidth(), enabled = secret.isNotBlank())
        }
    }
}
