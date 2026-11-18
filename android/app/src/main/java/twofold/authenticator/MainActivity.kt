package twofold.authenticator

import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.fragment.app.FragmentActivity
import kotlinx.coroutines.flow.MutableStateFlow
import twofold.authenticator.push.Notifier
import twofold.authenticator.ui.AppRoot
import twofold.authenticator.ui.theme.TwofoldTheme

/** Единственная Activity. FragmentActivity нужна BiometricPrompt. */
class MainActivity : FragmentActivity() {
    private val openRequest = MutableStateFlow<Pair<String, String>?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) handle(intent)
        val app = application as App
        setContent { TwofoldTheme { AppRoot(app, openRequest) } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    private fun handle(intent: Intent?) {
        val a = intent?.getStringExtra(Notifier.EXTRA_ACCOUNT) ?: return
        val r = intent.getStringExtra(Notifier.EXTRA_REQUEST) ?: return
        openRequest.value = a to r
    }

    override fun onStart() {
        super.onStart()
        val app = application as App
        // повторная блокировка после 30 с в фоне (короткие уходы — системный запрос PIN, сканер — не блокируют)
        if (app.vault.appLock && app.stoppedAt != 0L && SystemClock.elapsedRealtime() - app.stoppedAt > 30_000) app.locked.value = true
    }

    override fun onStop() {
        super.onStop()
        (application as App).stoppedAt = SystemClock.elapsedRealtime()
    }
}
