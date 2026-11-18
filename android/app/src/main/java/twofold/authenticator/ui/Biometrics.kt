package twofold.authenticator.ui

import android.app.KeyguardManager
import android.content.Context
import android.os.Build
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_WEAK
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import kotlinx.coroutines.suspendCancellableCoroutine
import java.security.Signature
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** Пользователь отменил проверку или она невозможна; [message] — текст системы для показа. */
class AuthCancelled(message: String) : Exception(message)

object Biometrics {
    /** Как у ключа подписи (DeviceKeys): на Android 11+ биометрия или PIN, раньше — только сильная биометрия. */
    val signAuthenticators = if (Build.VERSION.SDK_INT >= 30) BIOMETRIC_STRONG or DEVICE_CREDENTIAL else BIOMETRIC_STRONG

    // До Android 11 сочетание BIOMETRIC_STRONG | DEVICE_CREDENTIAL не поддерживается; для блокировки хватает WEAK.
    private val lockAuthenticators = if (Build.VERSION.SDK_INT >= 30) BIOMETRIC_STRONG or DEVICE_CREDENTIAL else BIOMETRIC_WEAK or DEVICE_CREDENTIAL

    /** null — можно создавать ключ и подписывать; иначе объяснение, что настроить. */
    fun signUnavailableReason(ctx: Context): String? = when (BiometricManager.from(ctx).canAuthenticate(signAuthenticators)) {
        BiometricManager.BIOMETRIC_SUCCESS -> null
        BiometricManager.BIOMETRIC_ERROR_NONE_ENROLLED ->
            if (Build.VERSION.SDK_INT >= 30) "Настройте блокировку экрана (PIN, графический ключ или пароль) или отпечаток в настройках телефона."
            else "Добавьте отпечаток пальца в настройках телефона: без него подтверждать входы нельзя."
        BiometricManager.BIOMETRIC_ERROR_NO_HARDWARE, BiometricManager.BIOMETRIC_ERROR_HW_UNAVAILABLE ->
            "На телефоне нет доступного биометрического датчика нужного класса."
        BiometricManager.BIOMETRIC_ERROR_SECURITY_UPDATE_REQUIRED -> "Установите обновление безопасности системы."
        else -> "Телефон не поддерживает защищённое подтверждение."
    }

    fun canLock(ctx: Context) = ctx.getSystemService(KeyguardManager::class.java).isDeviceSecure

    private suspend fun prompt(activity: FragmentActivity, info: BiometricPrompt.PromptInfo, crypto: BiometricPrompt.CryptoObject?) =
        suspendCancellableCoroutine { c ->
            val prompt = BiometricPrompt(activity, ContextCompat.getMainExecutor(activity), object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) { if (c.isActive) c.resume(result) }
                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    val cancelled = errorCode == BiometricPrompt.ERROR_USER_CANCELED || errorCode == BiometricPrompt.ERROR_NEGATIVE_BUTTON ||
                        errorCode == BiometricPrompt.ERROR_CANCELED
                    if (c.isActive) c.resumeWithException(AuthCancelled(if (cancelled) "Проверка отменена." else errString.toString()))
                }
            })
            c.invokeOnCancellation { prompt.cancelAuthentication() }
            if (crypto != null) prompt.authenticate(info, crypto) else prompt.authenticate(info)
        }

    private fun info(title: String, subtitle: String, allowed: Int): BiometricPrompt.PromptInfo {
        val b = BiometricPrompt.PromptInfo.Builder().setTitle(title).setSubtitle(subtitle).setAllowedAuthenticators(allowed).setConfirmationRequired(true)
        if (allowed and DEVICE_CREDENTIAL == 0) b.setNegativeButtonText("Отмена")
        return b.build()
    }

    /** Разблокирует [signature] (ключ с подтверждением на каждую операцию) и возвращает её, готовую к sign(). */
    suspend fun unlockSignature(activity: FragmentActivity, title: String, subtitle: String, signature: Signature): Signature {
        val r = prompt(activity, info(title, subtitle, signAuthenticators), BiometricPrompt.CryptoObject(signature))
        return r.cryptoObject?.signature ?: throw AuthCancelled("Проверка не выдала подпись")
    }

    suspend fun unlockApp(activity: FragmentActivity) {
        prompt(activity, info("Twofold заблокирован", "Подтвердите, что это вы", lockAuthenticators), null)
    }
}
