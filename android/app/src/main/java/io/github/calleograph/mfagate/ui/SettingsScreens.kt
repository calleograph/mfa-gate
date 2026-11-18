package io.github.calleograph.mfagate.ui

import android.Manifest
import android.content.Intent
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import kotlinx.coroutines.launch
import io.github.calleograph.mfagate.App
import io.github.calleograph.mfagate.BuildConfig
import io.github.calleograph.mfagate.push.Notifier
import io.github.calleograph.mfagate.ui.theme.MinTap
import io.github.calleograph.mfagate.ui.theme.Space

/**
 * Плашка про уведомления с пояснением «зачем» и запросом POST_NOTIFICATIONS (Android 13+).
 * Повторный отказ система не показывает окном — тогда кнопка ведёт в настройки уведомлений приложения.
 * [firstRun] — сразу после первой привязки: просьба, а не упрёк.
 */
@Composable
fun NotificationPermissionBanner(firstRun: Boolean = false) {
    val ctx = LocalContext.current
    var enabled by remember { mutableStateOf(Notifier.canNotify(ctx)) }
    var asked by rememberSaveable { mutableStateOf(false) }
    LifecycleResumeEffect(Unit) {
        enabled = Notifier.canNotify(ctx)
        onPauseOrDispose { }
    }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { enabled = Notifier.canNotify(ctx); asked = true }
    if (enabled) return
    val canAsk = Build.VERSION.SDK_INT >= 33 && !asked
    Panel {
        Text(if (firstRun) "Разрешите уведомления" else "Уведомления выключены", style = MaterialTheme.typography.titleMedium)
        Text(
            if (firstRun) {
                "Когда кто-то входит под вашей учётной записью, телефон сразу покажет запрос, даже если приложение закрыто. " +
                    "Подробностей входа в уведомлении нет: они открываются только в приложении."
            } else {
                "Без них вы узнаете о запросе входа, только открыв приложение."
            },
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (asked) {
            Text(
                "Уведомления запрещены. Их можно включить в настройках приложения в любой момент.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        SecondaryButton(if (canAsk) "Разрешить уведомления" else "Открыть настройки уведомлений", {
            if (canAsk) {
                launcher.launch(Manifest.permission.POST_NOTIFICATIONS)
            } else {
                ctx.startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, ctx.packageName))
            }
        }, Modifier.fillMaxWidth().padding(top = Space.s))
    }
}

@Composable
fun SettingsScreen(app: App) {
    val ctx = LocalContext.current
    val activity = ctx.findActivity()
    val scope = rememberCoroutineScope()
    var lock by remember { mutableStateOf(app.vault.appLock) }
    var error by remember { mutableStateOf<String?>(null) }

    fun toggleLock(on: Boolean) {
        error = null
        if (!on) { app.vault.appLock = false; lock = false; return }
        if (!Biometrics.canLock(ctx)) { error = "Сначала настройте блокировку экрана телефона (PIN, пароль или биометрию)."; return }
        scope.launch {
            try {
                Biometrics.unlockApp(activity)
                app.vault.appLock = true
                lock = true
            } catch (e: AuthCancelled) {
                error = e.message
            }
        }
    }

    ScreenScaffold("Настройки") { pad ->
        Column(
            Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(horizontal = Space.l, vertical = Space.s),
            verticalArrangement = Arrangement.spacedBy(Space.s),
        ) {
            SectionTitle("Безопасность")
            GlassSurface {
                Row(
                    Modifier.fillMaxWidth().heightIn(min = MinTap).toggleable(lock, role = Role.Switch, onValueChange = ::toggleLock).padding(Space.l),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(Space.l),
                ) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Space.xs)) {
                        Text("Блокировка приложения", style = MaterialTheme.typography.titleMedium)
                        Text(
                            "Запрашивать биометрию или PIN при открытии. Подтверждение входа требует их всегда, независимо от этой настройки.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Switch(checked = lock, onCheckedChange = null)
                }
            }
            error?.let { ErrorText(it) }
            Text(
                "Коды TOTP скрыты от скриншотов и записи экрана.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            SectionTitle("Уведомления")
            NotificationPermissionBanner()
            Panel {
                Text(
                    if (BuildConfig.PUSH_PROVIDER == "fcm") {
                        "Запросы приходят через Firebase Cloud Messaging, если сервер его настроил. В уведомлении нет данных о входе: подробности приложение загружает с сервера. Запасной путь — проверка каждые 15 минут."
                    } else {
                        "Сборка без сервисов Google. Пока приложение открыто, запросы появляются сразу; в фоне приложение проверяет их каждые 15 минут и показывает уведомление."
                    },
                    style = MaterialTheme.typography.bodyMedium,
                )
            }

            SectionTitle("О приложении")
            Panel {
                InfoRow("Приложение", "MFA Gate Authenticator")
                InfoRow("Версия", "${BuildConfig.VERSION_NAME} (${BuildConfig.FLAVOR})")
                InfoRow("Лицензия", "Apache-2.0")
            }
        }
    }
}

/** Экран блокировки приложения. Сразу показывает системный запрос; кнопка — для повтора. */
@Composable
fun LockScreen(app: App) {
    val activity = LocalContext.current.findActivity()
    val scope = rememberCoroutineScope()
    var error by remember { mutableStateOf<String?>(null) }
    fun unlock() = scope.launch {
        // блокировку экрана сняли в системе: проверить нечем, иначе пользователь застрянет
        if (!Biometrics.canLock(activity)) { app.locked.value = false; return@launch }
        try {
            Biometrics.unlockApp(activity)
            app.locked.value = false
        } catch (e: AuthCancelled) {
            error = e.message
        }
    }
    LaunchedEffect(Unit) { unlock() }
    Surface(Modifier.fillMaxSize().glassBackground(), color = Color.Transparent) {
        Column(
            Modifier.fillMaxSize().padding(Space.xl),
            verticalArrangement = Arrangement.spacedBy(Space.l, Alignment.CenterVertically),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("MFA Gate заблокирован", style = MaterialTheme.typography.headlineSmall)
            Text("Подтвердите, что это вы.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            error?.let { ErrorText(it, Modifier.widthIn(max = 420.dp)) }
            PrimaryButton("Разблокировать", { unlock() })
        }
    }
}
