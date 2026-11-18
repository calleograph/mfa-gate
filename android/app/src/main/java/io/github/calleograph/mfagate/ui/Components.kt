package io.github.calleograph.mfagate.ui

import android.content.Context
import android.content.ContextWrapper
import android.view.WindowManager
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.fragment.app.FragmentActivity
import io.github.calleograph.mfagate.ui.theme.Glass
import io.github.calleograph.mfagate.ui.theme.MinTap
import io.github.calleograph.mfagate.ui.theme.MonoSmall
import io.github.calleograph.mfagate.ui.theme.Space

fun Context.findActivity(): FragmentActivity {
    var c = this
    while (c is ContextWrapper) {
        if (c is FragmentActivity) return c
        c = c.baseContext
    }
    error("FragmentActivity не найдена")
}

/** Фон всего приложения: вертикальный градиент темы «Стекло» (web: body linear-gradient 180deg). */
@Composable
fun Modifier.glassBackground(): Modifier {
    val g = Glass.colors
    return background(Brush.verticalGradient(listOf(g.bgTop, g.bgBottom)))
}

/** Экран с заголовком; кнопка «Назад» — если задан [onBack]. Фон прозрачный: под ним градиент из AppRoot. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ScreenScaffold(
    title: String,
    onBack: (() -> Unit)? = null,
    actions: @Composable RowScope.() -> Unit = {},
    content: @Composable (PaddingValues) -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(title, style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { if (title.isNotBlank()) heading() }) },
                navigationIcon = {
                    if (onBack != null) IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Назад") }
                },
                actions = actions,
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Color.Transparent, scrolledContainerColor = Glass.colors.veil),
            )
        },
        containerColor = Color.Transparent,
        content = content,
    )
}

@Composable
fun PrimaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, danger: Boolean = false) {
    val c = MaterialTheme.colorScheme
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.heightIn(min = MinTap),
        shape = MaterialTheme.shapes.medium,
        colors = if (danger) ButtonDefaults.buttonColors(containerColor = c.error, contentColor = c.onError) else ButtonDefaults.buttonColors(),
        contentPadding = PaddingValues(horizontal = Space.xl, vertical = Space.m),
    ) { Text(text, style = MaterialTheme.typography.labelLarge, textAlign = TextAlign.Center) }
}

/** Вторая по важности кнопка: светлое стекло с обводкой (web .cta:not(.primary)). */
@Composable
fun SecondaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    OutlinedButton(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.heightIn(min = MinTap),
        shape = MaterialTheme.shapes.medium,
        colors = ButtonDefaults.outlinedButtonColors(containerColor = Glass.colors.panel, contentColor = MaterialTheme.colorScheme.primary),
        border = BorderStroke(1.dp, if (enabled) MaterialTheme.colorScheme.outline else MaterialTheme.colorScheme.outlineVariant),
        contentPadding = PaddingValues(horizontal = Space.xl, vertical = Space.m),
    ) { Text(text, style = MaterialTheme.typography.labelLarge, textAlign = TextAlign.Center) }
}

/** Третья по важности: текстовая кнопка без фона. */
@Composable
fun QuietButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    TextButton(onClick, modifier.heightIn(min = MinTap), enabled = enabled) { Text(text, style = MaterialTheme.typography.labelLarge, textAlign = TextAlign.Center) }
}

/** Пустое состояние или ошибка: заголовок, пояснение, необязательные действия. */
@Composable
fun StateMessage(
    title: String,
    text: String,
    modifier: Modifier = Modifier,
    action: String? = null,
    onAction: (() -> Unit)? = null,
    secondary: String? = null,
    onSecondary: (() -> Unit)? = null,
) {
    Box(modifier.fillMaxSize().padding(Space.xl), contentAlignment = Alignment.Center) {
        Column(Modifier.widthIn(max = 420.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(Space.s)) {
            Text(title, style = MaterialTheme.typography.titleLarge, textAlign = TextAlign.Center, modifier = Modifier.semantics { heading() })
            Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
            if (action != null && onAction != null) {
                PrimaryButton(action, onAction, Modifier.padding(top = Space.l))
            }
            if (secondary != null && onSecondary != null) QuietButton(secondary, onSecondary)
        }
    }
}

@Composable
fun Loading(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
}

/**
 * Стеклянная поверхность (web .rc: --glass-strong, обводка --glass-line, мягкая синеватая тень).
 * [onClick] — вся карточка нажимается; волна нажатия обрезается по скруглению.
 */
@Composable
fun GlassSurface(
    modifier: Modifier = Modifier,
    shape: Shape = MaterialTheme.shapes.large,
    onClick: (() -> Unit)? = null,
    onClickLabel: String? = null,
    content: @Composable () -> Unit,
) {
    val g = Glass.colors
    Surface(
        modifier.fillMaxWidth()
            .shadow(3.dp, shape, ambientColor = g.shadow.copy(alpha = 0.25f), spotColor = g.shadow.copy(alpha = 0.25f))
            .clip(shape)
            .then(if (onClick != null) Modifier.clickable(onClickLabel = onClickLabel, role = Role.Button, onClick = onClick) else Modifier),
        shape = shape,
        color = g.panel,
        contentColor = MaterialTheme.colorScheme.onSurface,
        border = BorderStroke(1.dp, g.line),
        content = content,
    )
}

/** Карточка с содержимым в столбик. */
@Composable
fun Panel(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    GlassSurface(modifier) {
        Column(Modifier.padding(Space.l), verticalArrangement = Arrangement.spacedBy(Space.s)) { content() }
    }
}

@Composable
fun InfoRow(label: String, value: String?, mono: Boolean = false) {
    Row(Modifier.fillMaxWidth().heightIn(min = 24.dp), horizontalArrangement = Arrangement.spacedBy(Space.l)) {
        Text(label, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(0.38f))
        Text(
            value?.takeIf { it.isNotBlank() } ?: "—",
            style = if (mono) MonoSmall else MaterialTheme.typography.bodyMedium,
            modifier = Modifier.weight(0.62f),
        )
    }
}

@Composable
fun SectionTitle(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = modifier.padding(top = Space.l, bottom = Space.s).semantics { heading() },
    )
}

/** Цветная плашка состояния: ошибка, предупреждение или успех (цвет + фон + текст, контраст ≥ 4.5:1). */
enum class Tone { Error, Warning, Ok }

@Composable
fun Notice(text: String, tone: Tone, modifier: Modifier = Modifier, title: String? = null, action: (@Composable () -> Unit)? = null) {
    val c = MaterialTheme.colorScheme
    val g = Glass.colors
    val (bg, fg) = when (tone) {
        Tone.Error -> c.errorContainer to c.onErrorContainer
        Tone.Warning -> g.warnContainer to g.onWarnContainer
        Tone.Ok -> g.okContainer to g.onOkContainer
    }
    Surface(modifier.fillMaxWidth(), shape = MaterialTheme.shapes.medium, color = bg, contentColor = fg) {
        Column(Modifier.padding(Space.m), verticalArrangement = Arrangement.spacedBy(Space.xs)) {
            title?.let { Text(it, style = MaterialTheme.typography.titleMedium) }
            Text(text, style = MaterialTheme.typography.bodyMedium)
            action?.invoke()
        }
    }
}

@Composable
fun ErrorText(text: String, modifier: Modifier = Modifier) = Notice(text, Tone.Error, modifier)

/** FLAG_SECURE, пока экран на месте: коды не попадают в скриншоты, запись экрана и миниатюры «Недавних». */
// Счётчик: при переходе между двумя защищёнными экранами уходящий не должен снять флаг у пришедшего.
private var secureScreens = 0

@Composable
fun SecureWindow() {
    val activity = LocalContext.current.findActivity()
    DisposableEffect(activity) {
        if (secureScreens++ == 0) activity.window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        onDispose { if (--secureScreens == 0) activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE) }
    }
}
