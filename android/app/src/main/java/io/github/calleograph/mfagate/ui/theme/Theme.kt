package io.github.calleograph.mfagate.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/*
 * Единственное место с токенами дизайна: цвет, типографика, форма, отступы.
 * Тема «Стекло» — та же, что в веб-панели (server/web/assets/css/app.css):
 * голубовато-белый градиентный фон, полупрозрачные светлые панели с тонкой светлой обводкой и мягкой синеватой тенью, один синий акцент.
 * Тёмный вариант — тёмные токены того же файла, стекло выводится из них так же (surface с прозрачностью).
 * Размытия под панелями (backdrop-filter) нет: в Compose его нет до API 31, и оно тратит батарею.
 * Прозрачность панелей подобрана так, чтобы весь текст был не ниже 4.5:1 (расчёт — docs/android.md, раздел «Тема»).
 */

/** Шкала отступов 4·8·12·16·24·32·48: других значений в интерфейсе нет. */
object Space {
    val xs = 4.dp
    val s = 8.dp
    val m = 12.dp
    val l = 16.dp
    val xl = 24.dp
    val xxl = 32.dp
    val xxxl = 48.dp
}

/** Минимальная тап-цель. */
val MinTap = 48.dp

/** Радиусы: web --radius-sm 8, --radius 12, --card-radius 20. */
object Radius {
    val s = 8.dp
    val m = 12.dp
    val l = 20.dp
}

/** Палитры темы «Стекло»: светлая и тёмная. */
private object Light {
    val bgTop = Color(0xFFF4F8FF)
    val bgBottom = Color(0xFFE8F0FB)
    val surface = Color(0xFFFFFFFF)
    val soft = Color(0xFFEDF3FC)
    val line = Color(0xFFD8E3F3)
    val text = Color(0xFF0F1B3D)
    val muted = Color(0xFF4C5C7A)
    val accent = Color(0xFF2563EB)
    val accentSoft = Color(0xFFE3EDFF)
    val onAccentSoft = Color(0xFF0F2A6B)
    val outline = Color(0xFF74839F) // обводка полей ввода: ≥ 3:1 к панели
    val danger = Color(0xFFCC3535)
    val dangerBg = Color(0xFFFBE4E1)
    val dangerText = Color(0xFFA52A22)
    val okBg = Color(0xFFDCF3E6)
    val okText = Color(0xFF14693A)
    val warnBg = Color(0xFFFBEFCF)
    val warnText = Color(0xFF6B4E0E)
    val shadow = Color(0xFF14327A) // rgba(20, 50, 120) из --shadow-glass
}

private object Dark {
    val bgTop = Color(0xFF131A26)
    val bgBottom = Color(0xFF0E1218)
    val surface = Color(0xFF161B24)
    val soft = Color(0xFF1E2530)
    val line = Color(0xFF2B3441)
    val text = Color(0xFFE9EDF3)
    val muted = Color(0xFF9BA6B8)
    val accent = Color(0xFF6F98FF)
    val onAccent = Color(0xFF0A1226)
    val accentSoft = Color(0xFF1B2A4C)
    val onAccentSoft = Color(0xFFD7E2FF)
    val outline = Color(0xFF66738A)
    val danger = Color(0xFFE0605A)
    val dangerBg = Color(0xFF3A1F1D)
    val dangerText = Color(0xFFF4A9A3)
    val okBg = Color(0xFF16301F)
    val okText = Color(0xFF8FDCA9)
    val warnBg = Color(0xFF352C16)
    val warnText = Color(0xFFE3CD8F)
    val shadow = Color(0xFF000000)
}

/** Токены стекла, которых нет в Material ColorScheme. */
@Immutable
data class GlassColors(
    val bgTop: Color,
    val bgBottom: Color,
    /** Карточка (web --glass-strong): читаемость важнее прозрачности. */
    val panel: Color,
    /** Лёгкая панель и нижняя навигация (web --glass). */
    val veil: Color,
    /** Обводка стекла (web --glass-line). */
    val line: Color,
    val shadow: Color,
    val okContainer: Color,
    val onOkContainer: Color,
    val warnContainer: Color,
    val onWarnContainer: Color,
)

private val LightGlass = GlassColors(
    bgTop = Light.bgTop, bgBottom = Light.bgBottom,
    panel = Light.surface.copy(alpha = 0.94f), veil = Light.surface.copy(alpha = 0.72f),
    line = Color(0xFFEBF1F9), // смесь surface 55% и line, как color-mix в glass.css
    shadow = Light.shadow,
    okContainer = Light.okBg, onOkContainer = Light.okText,
    warnContainer = Light.warnBg, onWarnContainer = Light.warnText,
)

private val DarkGlass = GlassColors(
    bgTop = Dark.bgTop, bgBottom = Dark.bgBottom,
    panel = Dark.surface.copy(alpha = 0.94f), veil = Dark.surface.copy(alpha = 0.72f),
    line = Color(0xFF212833),
    shadow = Dark.shadow,
    okContainer = Dark.okBg, onOkContainer = Dark.okText,
    warnContainer = Dark.warnBg, onWarnContainer = Dark.warnText,
)

private val LightScheme: ColorScheme = lightColorScheme(
    primary = Light.accent, onPrimary = Light.surface,
    primaryContainer = Light.accentSoft, onPrimaryContainer = Light.onAccentSoft,
    secondary = Light.muted, onSecondary = Light.surface,
    secondaryContainer = Light.accentSoft, onSecondaryContainer = Light.onAccentSoft,
    background = Light.bgBottom, onBackground = Light.text,
    surface = Light.surface, onSurface = Light.text,
    surfaceVariant = Light.soft, onSurfaceVariant = Light.muted,
    surfaceContainerLowest = Light.surface, surfaceContainerLow = Light.surface,
    surfaceContainer = Light.soft, surfaceContainerHigh = Light.surface, surfaceContainerHighest = Light.soft,
    outline = Light.outline, outlineVariant = Light.line,
    error = Light.danger, onError = Light.surface,
    errorContainer = Light.dangerBg, onErrorContainer = Light.dangerText,
)

private val DarkScheme: ColorScheme = darkColorScheme(
    primary = Dark.accent, onPrimary = Dark.onAccent,
    primaryContainer = Dark.accentSoft, onPrimaryContainer = Dark.onAccentSoft,
    secondary = Dark.muted, onSecondary = Dark.bgBottom,
    secondaryContainer = Dark.accentSoft, onSecondaryContainer = Dark.onAccentSoft,
    background = Dark.bgBottom, onBackground = Dark.text,
    surface = Dark.surface, onSurface = Dark.text,
    surfaceVariant = Dark.soft, onSurfaceVariant = Dark.muted,
    surfaceContainerLowest = Dark.bgBottom, surfaceContainerLow = Dark.surface,
    surfaceContainer = Dark.surface, surfaceContainerHigh = Dark.soft, surfaceContainerHighest = Dark.soft,
    outline = Dark.outline, outlineVariant = Dark.line,
    error = Dark.danger, onError = Dark.onAccent,
    errorContainer = Dark.dangerBg, onErrorContainer = Dark.dangerText,
)

/** Системный шрифт (Roboto) вместо веб-гарнитур Inter/Manrope: без шрифтов в APK; иерархия — весом и плотностью, как в веб-теме. */
private val AppTypography = Typography().let { t ->
    t.copy(
        headlineMedium = t.headlineMedium.copy(fontWeight = FontWeight.ExtraBold, fontSize = 28.sp, lineHeight = 34.sp, letterSpacing = (-0.5).sp),
        headlineSmall = t.headlineSmall.copy(fontWeight = FontWeight.Bold, fontSize = 22.sp, lineHeight = 28.sp, letterSpacing = (-0.2).sp),
        titleLarge = t.titleLarge.copy(fontWeight = FontWeight.Bold, fontSize = 20.sp, lineHeight = 26.sp, letterSpacing = (-0.1).sp),
        titleMedium = t.titleMedium.copy(fontWeight = FontWeight.SemiBold, fontSize = 16.sp, lineHeight = 22.sp),
        bodyLarge = t.bodyLarge.copy(fontSize = 16.sp, lineHeight = 24.sp),
        bodyMedium = t.bodyMedium.copy(fontSize = 14.sp, lineHeight = 20.sp),
        labelLarge = t.labelLarge.copy(fontWeight = FontWeight.SemiBold, fontSize = 15.sp, lineHeight = 20.sp),
        labelSmall = t.labelSmall.copy(fontWeight = FontWeight.SemiBold, fontSize = 11.sp, lineHeight = 16.sp),
    )
}

/** Крупные цифры TOTP: моноширинный шрифт, чтобы цифры не «прыгали» при смене кода. */
val CodeStyle = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Medium, fontSize = 34.sp, lineHeight = 40.sp, letterSpacing = 1.sp)

/** Технические значения (IP, адрес сервера). */
val MonoSmall = TextStyle(fontFamily = FontFamily.Monospace, fontSize = 14.sp, lineHeight = 20.sp)

private val AppShapes = Shapes(
    small = RoundedCornerShape(Radius.s),
    medium = RoundedCornerShape(Radius.m),
    large = RoundedCornerShape(Radius.l),
)

private val LocalGlass = staticCompositionLocalOf { LightGlass }

/** Токены стекла текущей темы: `Glass.colors.panel` и т. д. */
object Glass {
    val colors: GlassColors
        @Composable @ReadOnlyComposable get() = LocalGlass.current
}

@Composable
fun MfaGateTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalGlass provides if (dark) DarkGlass else LightGlass) {
        MaterialTheme(colorScheme = if (dark) DarkScheme else LightScheme, typography = AppTypography, shapes = AppShapes, content = content)
    }
}
