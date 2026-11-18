package twofold.authenticator.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import kotlinx.coroutines.flow.MutableStateFlow
import twofold.authenticator.App

private data class Tab(val route: String, val label: String, val icon: ImageVector)

private val tabs = listOf(
    Tab("requests", "Запросы", Icons.Filled.Notifications),
    Tab("codes", "Коды", Icons.Filled.Lock),
    Tab("servers", "Серверы", Icons.AutoMirrored.Filled.List),
    Tab("settings", "Настройки", Icons.Filled.Settings),
)

/** Корень интерфейса: нижняя навигация, NavHost и экран блокировки поверх. [openRequest] — (accountId, requestId) из уведомления. */
@Composable
fun AppRoot(app: App, openRequest: MutableStateFlow<Pair<String, String>?>) {
    val locked by app.locked.collectAsStateWithLifecycle()
    val target by openRequest.collectAsStateWithLifecycle()
    val nav = rememberNavController()
    val entry by nav.currentBackStackEntryAsState()
    val route = entry?.destination?.route

    LaunchedEffect(target, locked) {
        val t = target ?: return@LaunchedEffect
        if (locked) return@LaunchedEffect
        openRequest.value = null
        nav.navigate("request/${t.first}/${t.second}") { launchSingleTop = true }
    }

    Box(Modifier.fillMaxSize()) {
        Scaffold(
            modifier = if (locked) Modifier.clearAndSetSemantics { } else Modifier,
            containerColor = MaterialTheme.colorScheme.background,
            bottomBar = {
                if (tabs.any { it.route == route }) {
                    NavigationBar(containerColor = MaterialTheme.colorScheme.surfaceContainer) {
                        tabs.forEach { t ->
                            NavigationBarItem(
                                selected = route == t.route,
                                onClick = {
                                    nav.navigate(t.route) {
                                        popUpTo(nav.graph.findStartDestination().id) { saveState = true }
                                        launchSingleTop = true
                                        restoreState = true
                                    }
                                },
                                icon = { Icon(t.icon, contentDescription = null) },
                                label = { Text(t.label) },
                            )
                        }
                    }
                }
            },
        ) { pad ->
            NavHost(nav, startDestination = "requests", modifier = Modifier.padding(bottom = pad.calculateBottomPadding()).consumeWindowInsets(pad)) {
                composable("requests") {
                    RequestsScreen(
                        viewModel { RequestsViewModel(app) },
                        onOpen = { nav.navigate("request/${it.account.id}/${it.request.id}") },
                        onEnroll = { nav.navigate("enroll") },
                    )
                }
                composable("request/{account}/{request}") { e ->
                    val a = e.arguments?.getString("account").orEmpty()
                    val r = e.arguments?.getString("request").orEmpty()
                    RequestScreen(viewModel { RequestViewModel(app, a, r) }, onBack = { nav.popBackStack() })
                }
                composable("codes") { CodesScreen(viewModel { TotpViewModel(app) }, onAdd = { nav.navigate("codes/add") }) }
                composable("codes/add") { AddTotpScreen(viewModel { TotpViewModel(app) }, onDone = { nav.popBackStack() }) }
                composable("servers") {
                    ServersScreen(
                        viewModel { AccountsViewModel(app) },
                        onEnroll = { nav.navigate("enroll") },
                        onHistory = { nav.navigate("history/${it.id}") },
                    )
                }
                composable("history/{account}") { e ->
                    val a = e.arguments?.getString("account").orEmpty()
                    HistoryScreen(viewModel { HistoryViewModel(app, a) }, onBack = { nav.popBackStack() })
                }
                composable("enroll") { EnrollScreen(viewModel { EnrollViewModel(app) }, onBack = { nav.popBackStack() }) }
                composable("settings") { SettingsScreen(app) }
            }
        }
        if (locked) LockScreen(app)
    }
}
