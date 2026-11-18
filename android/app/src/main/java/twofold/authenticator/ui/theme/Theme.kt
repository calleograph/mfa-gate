package twofold.authenticator.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Единственное место с токенами дизайна. Характер — спокойный инструмент безопасности:
 * графитовый или светлый фон, один акцент (бирюзово-зелёный), без градиентов и декоративных теней.
 */
object Space {
    val xs = 4.dp
    val s = 8.dp
    val m = 12.dp
    val l = 16.dp
    val xl = 24.dp
    val xxl = 32.dp
}

/** Минимальная тап-цель. */
val MinTap = 48.dp

private object Palette {
    val graphite950 = Color(0xFF121417)
    val graphite900 = Color(0xFF1A1D21)
    val graphite850 = Color(0xFF22262B)
    val graphite700 = Color(0xFF3A4048)
    val graphite500 = Color(0xFF6B737D)
    val graphite300 = Color(0xFFA9B0B8)
    val graphite100 = Color(0xFFE6E8EB)
    val paper = Color(0xFFF5F6F7)
    val white = Color(0xFFFFFFFF)
    val accentLight = Color(0xFF1C6B58)
    val accentDark = Color(0xFF5FC4A8)
    val dangerLight = Color(0xFFB3261E)
    val dangerDark = Color(0xFFF2B8B5)
}

private val Light: ColorScheme = lightColorScheme(
    primary = Palette.accentLight,
    onPrimary = Palette.white,
    primaryContainer = Color(0xFFD3EDE5),
    onPrimaryContainer = Color(0xFF0A3329),
    secondary = Palette.graphite700,
    onSecondary = Palette.white,
    secondaryContainer = Palette.graphite100,
    onSecondaryContainer = Palette.graphite900,
    background = Palette.paper,
    onBackground = Palette.graphite900,
    surface = Palette.paper,
    onSurface = Palette.graphite900,
    surfaceVariant = Palette.graphite100,
    onSurfaceVariant = Color(0xFF4A5159),
    surfaceContainerLowest = Palette.white,
    surfaceContainerLow = Palette.white,
    surfaceContainer = Color(0xFFEDEFF1),
    surfaceContainerHigh = Color(0xFFE6E8EB),
    surfaceContainerHighest = Color(0xFFDFE2E5),
    outline = Color(0xFF8A929B),
    outlineVariant = Color(0xFFD5D9DD),
    error = Palette.dangerLight,
    onError = Palette.white,
    errorContainer = Color(0xFFF9DEDC),
    onErrorContainer = Color(0xFF410E0B),
)

private val Dark: ColorScheme = darkColorScheme(
    primary = Palette.accentDark,
    onPrimary = Color(0xFF00382D),
    primaryContainer = Color(0xFF14463A),
    onPrimaryContainer = Color(0xFFBDEBDD),
    secondary = Palette.graphite300,
    onSecondary = Palette.graphite950,
    secondaryContainer = Palette.graphite850,
    onSecondaryContainer = Palette.graphite100,
    background = Palette.graphite950,
    onBackground = Palette.graphite100,
    surface = Palette.graphite950,
    onSurface = Palette.graphite100,
    surfaceVariant = Palette.graphite850,
    onSurfaceVariant = Palette.graphite300,
    surfaceContainerLowest = Color(0xFF0D0F11),
    surfaceContainerLow = Palette.graphite900,
    surfaceContainer = Palette.graphite900,
    surfaceContainerHigh = Palette.graphite850,
    surfaceContainerHighest = Color(0xFF2A2F35),
    outline = Palette.graphite500,
    outlineVariant = Palette.graphite700,
    error = Palette.dangerDark,
    onError = Color(0xFF601410),
    errorContainer = Color(0xFF8C1D18),
    onErrorContainer = Color(0xFFF9DEDC),
)

private val AppTypography = Typography().let { t ->
    t.copy(
        headlineSmall = t.headlineSmall.copy(fontWeight = FontWeight.SemiBold, fontSize = 22.sp, lineHeight = 28.sp),
        titleLarge = t.titleLarge.copy(fontWeight = FontWeight.SemiBold, fontSize = 20.sp, lineHeight = 26.sp),
        titleMedium = t.titleMedium.copy(fontWeight = FontWeight.SemiBold, fontSize = 16.sp, lineHeight = 22.sp),
        bodyLarge = t.bodyLarge.copy(fontSize = 16.sp, lineHeight = 24.sp),
        bodyMedium = t.bodyMedium.copy(fontSize = 14.sp, lineHeight = 20.sp),
        labelLarge = t.labelLarge.copy(fontWeight = FontWeight.SemiBold, fontSize = 15.sp),
    )
}

/** Крупные цифры TOTP: моноширинный шрифт, чтобы цифры не «прыгали» при смене кода. */
val CodeStyle = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Medium, fontSize = 34.sp, lineHeight = 40.sp, letterSpacing = 1.sp)

/** Технические значения (IP, адрес сервера). */
val MonoSmall = TextStyle(fontFamily = FontFamily.Monospace, fontSize = 14.sp, lineHeight = 20.sp)

private val AppShapes = Shapes(
    small = RoundedCornerShape(8.dp),
    medium = RoundedCornerShape(12.dp),
    large = RoundedCornerShape(16.dp),
)

@Composable
fun TwofoldTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = if (dark) Dark else Light, typography = AppTypography, shapes = AppShapes, content = content)
}
