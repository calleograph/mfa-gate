# Исследование: Android-приложение эталонный проект как образец для MFA Gate

Дата: 2026-11-18. Источник (только чтение): `<эталонный проект>/mobile/android` (версия клиента 0.1.9),
CI: `<эталонный проект>/.github/workflows/android.yml`, документы: `rel70/docs/ANDROID.md`, `rel70/docs/ANDROID_SIGNING.md`,
скрипт выпуска: `rel70/scripts/android_release_check.py`. Ниже `PG` = `<эталонный проект>/mobile/android`.

## 1. Структура проекта и Gradle

Один модуль `:app`, около 3,1 тыс. строк Kotlin в 22 файлах. Пакеты: `data/` (модели, хранилище, шифрование), `net/` (клиент API, разбор адресов),
`ui/` (экраны Compose, ViewModel, тема), `call/` (звонок, служба переднего плана, плавающее окно).

| Параметр | Значение |
| --- | --- |
| Gradle | wrapper 9.8.2 (`gradle/wrapper/gradle-wrapper.properties`); дистрибутив уже лежит в `~/.gradle/wrapper/dists` |
| AGP | **9.5.0** (`com.android.application`) |
| Kotlin | **2.4.21**. Плагина `kotlin-android` нет: в AGP 9 Kotlin встроен. Подключены только `org.jetbrains.kotlin.plugin.compose` и `...plugin.serialization` |
| compileSdk / minSdk / targetSdk | **37 / 26 / 36** |
| Java | source/target 17, сборка на JDK 21 |
| Compose BOM | 2026.09.00, Material3, material-icons-extended |
| Прочее | core-ktx 1.19.1, activity-compose 1.13.0, lifecycle 2.11.0, OkHttp 5.5.1, kotlinx-serialization-json 1.12.0, coroutines 1.11.0, webkit 1.18.0, LiveKit 2.29.2 |

- **Version catalog**: `PG/gradle/libs.versions.toml`: `[versions]`, `[libraries]`, `[plugins]`. Комментарий в шапке: версии закреплены, обновлять осознанно.
- **settings.gradle.kts**: `RepositoriesMode.FAIL_ON_PROJECT_REPOS`, репозитории google(), mavenCentral(). Сторонний Maven-репозиторий разрешён
  через `content { includeGroup(...) }` только для одной группы (зависимость LiveKit).
- **Корневой build.gradle.kts**: только `alias(...) apply false`.
- **gradle.properties**: `-Xmx3g`, parallel и caching включены, `android.nonTransitiveRClass=true`,
  `android.onlyEnableUnitTestForTheTestedBuildType=false`. Это обходной путь AGP 9: без него `gradlew tasks --all` и импорт в IDE падают на release-варианте
  из-за задачи Compose Preview. С ним же связан `testOptions { unitTests.isIncludeAndroidResources = true }`.
- **Версия**: `VERSION` в корне клиента (`0.1.9`), отдельно от версии сервера. В `app/build.gradle.kts`:
  `versionName = VERSION`, `versionCode = major*10000 + minor*100 + patch + versionCodeOffset`. Свойство `-PversionCodeOffset=-1` нужно только тесту
  «обновление поверх», он собирает «предыдущую» сборку той же версии.
- **buildTypes**: только `release` (debug по умолчанию): `isMinifyEnabled = true`, `isShrinkResources = true`,
  `proguard-android-optimize.txt` + `proguard-rules.pro`. Signing config `release`, если ключ задан, иначе debug-ключ.
- **Flavors**: нет.
- **ProGuard** (`PG/app/proguard-rules.pro`): keep для LiveKit/WebRTC (JNI) и для kotlinx.serialization
  (`*** Companion`, `KSerializer serializer(...)`).
- `buildFeatures { compose = true; buildConfig = true }`, `lint { abortOnError = true; checkReleaseBuilds = true }`.
- `.gitignore`: `local.properties`, `signing.properties`, `*.jks`, `*.keystore`, `.kotlin/`.

## 2. UI-технология и архитектура

- **Jetpack Compose + Material3**, одна Activity (`MainActivity: ComponentActivity`, `enableEdgeToEdge()`, `launchMode="singleTask"`). Навигационной библиотеки нет:
  экраны переключаются через `enum Step { Boot, Welcome, AddServer, Login, Main }` и `enum Tab` в `UiState` (`PG/app/src/main/java/org/reference/android/ui/AppRoot.kt`).
- **ViewModel**: `AppViewModel(app, SavedStateHandle) : AndroidViewModel` с одним `MutableStateFlow<UiState>` и `_s.update { it.copy(...) }`.
  В UI данные читаются через `collectAsStateWithLifecycle()`.
- **Корутины**: `viewModelScope`, `lifecycleScope`. OkHttp обёрнут в `suspend fun Call.await()` через `suspendCancellableCoroutine` с отменой.
- **DI нет.** Зависимости живут в `App : Application` (`lateinit var store`, `calls`, плюс `App.instance`) и берутся оттуда.
  Для тестов выделены интерфейсы: `Sessions` реализует `Store` в приложении и `MemSessions` в JVM-тестах.
- Логика вынесена из Compose в чистые объекты, которые тестируются JVM-тестами: `PermissionPlan`, `JoinPermissionFlow`, `PipModel`, `ZoomMath`, `Urls`.
- Тема `PG/.../ui/Theme.kt`: свои `lightColorScheme`/`darkColorScheme` по токенам веба, без dynamic color, `isSystemInDarkTheme()`.

## 3. Взаимодействие с сервером

- **HTTP**: OkHttp, один ленивый клиент на процесс. Таймауты 10/20/20 с, `followRedirects(false)`: cookie не должны уйти на чужой адрес.
  JSON через kotlinx.serialization: `AppJson = Json { ignoreUnknownKeys; coerceInputValues; explicitNulls = false }`.
- **Клиент API** `PG/.../net/ServerApi.kt`: один экземпляр на сервер, база `server.url + "/api/v1"`. Каждый запрос несёт заголовки
  `User-Agent: reference projectAndroid/<версия> (Android <x>)` и `X-Client-Id` (случайный id установки).
  Ошибки разбираются в `ApiError(status, code, message, retryAfter)` с текстами для человека, отдельно `SessionExpired`.
- **Совместимость протокола**: `GET /desktop/info` возвращает `product`, `api`, `server_version`. Приложение сверяет поле продукта и `SUPPORTED_API`
  и отвечает «сервер слишком новый/старый». Для MFA Gate удобный образец рукопожатия.
- **Адрес сервера**: вводит пользователь или приходит в ссылке. `normalizeServerUrl()` в `PG/.../net/Urls.kt` пропускает только HTTPS,
  отбрасывает userinfo, пробелы, длину больше 300 и имя хоста без точки. HTTP разрешён только локальным адресам и только в debug.
  Deep link `reference://open?url=...` разбирается строгим белым списком путей.
- **TLS**: `network_security_config.xml`, `cleartextTrafficPermitted="false"`, trust anchors `system` + `user` (корпоративные CA через MDM).
  **Pinning нет**, сознательно, из-за корпоративных прокси. В `src/debug/res/xml/` своя версия конфига, где cleartext разрешён только для localhost
  и адреса хоста из эмулятора (для MockWebServer).
- **Авторизация**: вход идёт в WebView на странице сервера, там выполняются пароль, TOTP, CAPTCHA и SSO. Приложение забирает cookie сессии из `CookieManager`
  и проверяет её через `/auth/me`, оттуда же берёт CSRF. После этого получает «токен доверенного устройства» (`/desktop/trust`).
  При 401 вызывается `/desktop/resume`: одноразовый токен с ротацией, новый токен сохраняется сразу.
- **Хранение**: обычный `SharedPreferences` (`MODE_PRIVATE`). Секреты (cookie, trust-токен) шифруются классом `SecretBox`
  (`PG/.../data/SecretBox.kt`): AES-256-GCM, ключ создаётся в **AndroidKeyStore** и не покидает его. Формат `base64(iv || ct)`.
  **AAD = `"<вид>.<id сервера>"`**, поэтому секрет одного сервера не расшифруется под другим. Любая ошибка расшифровки даёт `null`, и приложение просит войти заново.
  EncryptedSharedPreferences и DataStore **не используются** (Security-crypto устарела). BiometricPrompt нет.
- `allowBackup="false"` + `data_extraction_rules.xml` исключают root, sharedpref и database из облачной копии и из переноса между устройствами,
  потому что ключ Keystore на другом устройстве не восстановится.

## 4. Разрешения, фон, восстановление, уведомления

- **Разрешения** (`AndroidManifest.xml`): INTERNET, ACCESS_NETWORK_STATE, RECORD_AUDIO, CAMERA, MODIFY_AUDIO_SETTINGS, BLUETOOTH_CONNECT,
  FOREGROUND_SERVICE (+ `_MICROPHONE`, `_MEDIA_PLAYBACK`), POST_NOTIFICATIONS, WAKE_LOCK. Камера и микрофон помечены как `required="false"`.
- **Запрос разрешений**: `PermissionPlan` выбирает набор по SDK. Уведомления спрашиваются только на 33+, Bluetooth только на 31+.
  Перед системным окном показывается пояснение «зачем» с кнопкой «Позже». После отказа без повторного запроса приложение ведёт в настройки
  (`ACTION_APPLICATION_DETAILS_SETTINGS`), а при `ON_RESUME` перечитывает состояние разрешений.
  Файлы: `PG/.../ui/Permissions.kt`, `ui/SystemPermissions.kt`, тест `PermissionsTest.kt`.
- **Фон**: `CallService` — foreground service типа `microphone|mediaPlayback`, существует только на время звонка. `ServiceCompat.startForeground` вызывается
  сразу в `onStartCommand`, даже если звонок уже кончился, иначе система убивает процесс. Тип службы выбирается по выданному разрешению (Android 14).
  Есть `PARTIAL_WAKE_LOCK` с предохранителем на 12 ч, `START_NOT_STICKY`. Остановка через `stopService`, а не `startService`, потому что запуск из фона запрещён.
  **WorkManager не используется.**
- **Восстановление**: прерванное действие хранится в ViewModel (переживает пересоздание Activity) и в `SavedStateHandle` (переживает смерть процесса),
  пароль туда не попадает. Модель — `restorePendingJoin()` в `AppViewModel.kt`. Для локального состояния UI используется `rememberSaveable`.
  `configChanges` перечислены в манифесте, поэтому поворот и смена темы Activity не пересоздают. Жизненный цикл пишется в logcat под тегом без личных данных.
- **Уведомления**: один канал `call` с `IMPORTANCE_LOW`, создаётся в `App.onCreate()`. Уведомление службы: `setOngoing`, `CATEGORY_CALL`,
  `FOREGROUND_SERVICE_IMMEDIATE`, кнопки-действия через `PendingIntent.getService(... FLAG_IMMUTABLE | FLAG_UPDATE_CURRENT)`,
  открытие приложения через `PendingIntent.getActivity(... FLAG_IMMUTABLE)`.
- **FCM нет.** В `docs/ANDROID.md` прямо сказано: push о встречах и сообщениях не сделан, «приложение ничего не отправляет в сторонние сервисы».
  Готового образца доставки push у эталонный проект **нет**.

## 5. Сборка, подписание, выпуск

- Команды из `PG`: `./gradlew :app:testDebugUnitTest :app:lintDebug`, `:app:assembleDebug`, `:app:assembleRelease`, `:app:bundleRelease` (AAB для магазинов).
- **Подпись** (`app/build.gradle.kts`): функция `sign(name, env)` сначала читает переменную окружения, затем `signing.properties` в корне клиента
  (файл в `.gitignore`, в репозитории отсутствует). Переменные: `REFERENCE_KEYSTORE` (путь к .jks), `REFERENCE_KEYSTORE_PASSWORD`,
  `REFERENCE_KEY_ALIAS`, `REFERENCE_KEY_PASSWORD`. Ключи properties: `storeFile/storePassword/keyAlias/keyPassword`.
  Без ключа release подписывается debug-ключом: для проверки годится, для публикации нет.
- **Где ключ на сервере** (содержимое не читалось):
  - `<каталог ключей подписи вне репозитория>/` (права 700): `reference-release.jks`, `signing.env` с теми же четырьмя `REFERENCE_*`
    (подключается `source`-ом перед сборкой), `set-github-secrets.sh` (заливает секреты CI), README владельцу, ключ обновлений Windows.
  - `~/.local/share/reference-signing/` относится **не к Android**: там сертификат издателя (pfx/pem/cer + пароль) и ключ подписи обновлений настольного клиента.
- **CI** (`rel70/.github/workflows/android.yml`), три job:
  1. `build`: JDK 21 temurin + `gradle/actions/setup-gradle`. Секрет `ANDROID_KEYSTORE_B64` декодируется в `$RUNNER_TEMP/release.jks`,
     а `ANDROID_KEYSTORE_PASSWORD/KEY_ALIAS/KEY_PASSWORD` передаются как `REFERENCE_*`. **Тег без секретов роняет сборку.**
     Дальше unit-тесты и lint, сборка APK/AAB, `apksigner verify --print-certs`, `aapt2 dump badging`,
     `android_release_check.py --expect-cert "$(cat release-cert.sha256)"` и сравнение с прошлым выпуском (пакет, рост versionCode, тот же сертификат).
     Затем SHA256SUMS и артефакты.
  2. `emulator`: матрица API 29/34/36, `reactivecircus/android-emulator-runner` (x86_64, google_apis, pixel_6, KVM через udev-правило).
     Запускаются `update-over-test.sh` и `run-instrumented.sh`, logcat и `dumpsys` сохраняются в артефакты.
  3. `release` по тегу `android-vX.Y.Z`: заметки берутся из `CHANGELOG.md` через awk по заголовку `## [X.Y.Z]`, затем `gh release create`.
- **release-cert.sha256**: SHA-256 сертификата постоянного ключа в виде `AA:BB:...`, информация открытая. Тот же отпечаток зашит в приложение (`Home.kt`, `BuildCard`):
  экран «Сборка и подпись» показывает, постоянный ключ стоит или временный (`GET_SIGNING_CERTIFICATES`).
- **update-over-test.sh**: собирает APK с `versionCodeOffset=-1`, ставит его, через `run-as` кладёт метку в `files/`, ставит текущую сборку через `adb install -r`
  и проверяет, что versionCode вырос, а метка на месте. Повторная установка проверяется так же. Для release-ключа вместо метки сравнивается `firstInstallTime`.
- **CHANGELOG.md**: написан для пользователей, по-русски. В каждом выпуске есть строка «Ставится поверх X с сохранением… (тот же ключ подписи)».

## 6. Тесты

- **Unit (JVM)**, `app/src/test`: `ServerApiTest` (OkHttp MockWebServer + `MemSessions`, `runTest`), `UrlsTest`, `PermissionsTest`, `PipModelTest`,
  `EndMessageTest`, `ZoomMathTest`. Используются JUnit4 + kotlin-test, `unitTests.isReturnDefaultValues = true`.
  Запуск: `./gradlew :app:testDebugUnitTest`. Имена тестов — русские фразы в обратных кавычках.
- **Instrumented**, `app/src/androidTest`: `KeystoreTest` (настоящий AndroidKeyStore: round-trip, чужой AAD, подмена данных), `OnboardingTest`, `KeyboardTest`,
  `ZoomSurfaceTest`, `JoinFlowTest`, `LongCallTest`, `ScreenShareTest`. Инструменты: Compose ui-test, UiAutomator, MockWebServer на устройстве
  (`Harness.kt`, класс `Fakereference project` — подставной сервер с нужным минимумом API). Параметры передаются через
  `-Pandroid.testInstrumentationRunnerArguments.<k>=<v>`.
- `run-instrumented.sh` работает в два прохода: детерминированные тесты гоняются один раз (`notClass=...`), сетевые с LiveKit до трёх попыток (`class=...`),
  результаты попыток сохраняются. `ci/peer.py` — второй участник звонка на Python, к MFA Gate не относится.

## 7. Что берём в MFA Gate

| Что | Образец |
| --- | --- |
| Каркас Gradle: version catalog, AGP 9.5.0 со встроенным Kotlin, плагин compose (+ serialization), `FAIL_ON_PROJECT_REPOS`, gradle.properties с обходом AGP 9 для unit-тестов | `PG/gradle/libs.versions.toml`, `PG/settings.gradle.kts`, `PG/build.gradle.kts`, `PG/gradle.properties`, `PG/gradle/wrapper/` |
| versionCode/versionName из `VERSION`, `versionCodeOffset`, minify+shrink в release, `lint abortOnError` | `PG/app/build.gradle.kts` |
| Подпись: env → `signing.properties` → debug-ключ. Для MFA Gate — свои имена переменных (`MFA_GATE_KEYSTORE*`) и свой каталог ключа вне репозитория | `PG/app/build.gradle.kts`, `PG/.gitignore` |
| Закреплённый отпечаток сертификата, проверка при выпуске и экран «подпись: постоянный/временный» | `PG/release-cert.sha256`, `rel70/scripts/android_release_check.py`, `PG/.../ui/Home.kt` (`signingDigests`) |
| Тест «обновление поверх» с сохранением данных | `PG/update-over-test.sh` |
| CI: build → emulator (матрица API) → release по тегу; запрет публикации временным ключом; заметки из CHANGELOG | `rel70/.github/workflows/android.yml` |
| Compose + M3, одна Activity, `UiState` + `StateFlow`, `SavedStateHandle` для действия, прерванного системой (например, одобрение push, прерванное BiometricPrompt или смертью процесса) | `PG/.../MainActivity.kt`, `ui/AppViewModel.kt`, `ui/AppRoot.kt` |
| Без DI: `Application` как контейнер, интерфейс-шов для JVM-тестов (`Sessions` → у MFA Gate `Accounts/DeviceKeys`) | `PG/.../App.kt`, `data/Store.kt` |
| OkHttp + `await()` с отменой, `followRedirects(false)`, разбор ошибок в `ApiError`, рукопожатие версии протокола (`product` + `api`) | `PG/.../net/ServerApi.kt` |
| Проверка адреса сервера из QR или ввода: только HTTPS, без userinfo, лимит длины, HTTP только для localhost в debug | `PG/.../net/Urls.kt`, `UrlsTest.kt` |
| `network_security_config` (release: только HTTPS; debug: cleartext только для localhost/эмулятора) | `PG/app/src/main/res/xml/`, `PG/app/src/debug/res/xml/` |
| `SecretBox`: AES-GCM ключом Keystore с AAD = вид + id аккаунта. Подходит для шифрования TOTP-секретов и токенов | `PG/.../data/SecretBox.kt`, `androidTest/.../KeystoreTest.kt` |
| `allowBackup=false` + `data_extraction_rules` (для MFA обязательно: ключи Keystore не переносятся) | `PG/app/src/main/AndroidManifest.xml`, `res/xml/data_extraction_rules.xml` |
| Разрешения: план по SDK, пояснение перед запросом, переход в настройки, перепроверка на `ON_RESUME` (у MFA Gate это POST_NOTIFICATIONS и CAMERA для QR) | `PG/.../ui/Permissions.kt`, `ui/SystemPermissions.kt`, `PermissionsTest.kt` |
| Канал уведомлений в `App.onCreate`, `PendingIntent` только `FLAG_IMMUTABLE`, действия в уведомлении | `PG/.../App.kt`, `call/CallService.kt` |
| Тесты: MockWebServer в JVM и на устройстве (подставной сервер `Harness.kt`), `KeystoreTest` на настоящем Keystore | `PG/app/src/test/...`, `PG/app/src/androidTest/.../Harness.kt` |

**Чего в образце нет, MFA Gate пишет сам:**
- **Подпись запроса ключом Keystore + BiometricPrompt**: EC P-256 (`KeyPairGenerator` "AndroidKeyStore", `PURPOSE_SIGN`, `DIGEST_SHA256`,
  `setUserAuthenticationRequired(true)` + `setUserAuthenticationParameters(0, BIOMETRIC_STRONG | DEVICE_CREDENTIAL)`,
  `setInvalidatedByBiometricEnrollment`, при наличии StrongBox — `setIsStrongBoxBacked`). Подпись через `BiometricPrompt.CryptoObject(Signature)`.
  Открытый ключ и key attestation уходят серверу при привязке. Нужна зависимость `androidx.biometric`, её нет в кэше Gradle.
- **TOTP (RFC 6238)**: `javax.crypto.Mac` HmacSHA1/256/512, без библиотек. Подход к HMAC есть в `Harness.kt` (JWT для LiveKit).
- **Сканер QR**: CameraX + декодер. ZXing core работает без Google Play services и подходит для F-Droid. ML Kit тянет GMS или bundled-модель.
  Разбор `otpauth://` и своей ссылки привязки делается по аналогии с `parseMeetingLink` (строгий белый список).
- **Доставка push**: решения у эталонный проект нет, нужен отдельный ADR. Варианты: FCM (зависимость от Google, нужен проект Firebase),
  UnifiedPush (дистрибьютор ставит пользователь), опрос при открытии приложения (не настоящий push), WorkManager (минимум 15 мин, для подтверждения входа слишком медленно),
  постоянная foreground-служба с долгим соединением (батарея, постоянное уведомление). Канал уведомлений для запроса входа нужен с `IMPORTANCE_HIGH`.

## 8. Чего не берём

- LiveKit, WebRTC, audioswitch и сторонний Maven-репозиторий, их ProGuard-правила; `CallService` (типы microphone/mediaPlayback, wake lock), PiP, `ZoomMath`, `ci/peer.py`.
- Вход через WebView и cookie/CSRF-сессии с «доверенным устройством». Устройство MFA Gate аутентифицируется своим ключом (подпись challenge), а не cookie.
- `trust-anchors src="user"`. Для MFA-аутентификатора доверие пользовательским CA стоит пересмотреть (ADR): MITM корпоративным прокси
  для фактора аутентификации опаснее, чем для видеосвязи. Подпись ответа ключом устройства частично защищает и без этого.
- Схема deep link `reference://open?url=...`, проверка версии сервера `SERVER_WITH_APP_PAGES`, разрешения на камеру/микрофон/Bluetooth для звонка.
- `configChanges` с перечнем всего подряд: в MFA Gate без звонка проще позволить пересоздание и держать состояние в ViewModel и SavedStateHandle.
- Эмулятор-job с LiveKit и тремя попытками: у MFA Gate тесты должны быть детерминированными, достаточно MockWebServer.

## 9. Окружение сборки на этом сервере

| Компонент | Состояние |
| --- | --- |
| JDK | OpenJDK 21.0.13, `/usr/lib/jvm/java-21-openjdk-amd64`. `JAVA_HOME` в окружении **не задан** (java есть в PATH) |
| Gradle | `<gradle>/bin/gradle`. В `~/.gradle/wrapper/dists` есть `gradle-9.8.2-bin`, в кэше есть AGP 9.5.0 |
| Android SDK | `<android-sdk>`. `ANDROID_HOME`/`ANDROID_SDK_ROOT` **не заданы**, `local.properties` у эталонный проект отсутствует |
| platforms | `android-36`, `android-37.0` (compileSdk = 37 разрешается в `android-37.0`) |
| build-tools | `36.0.0`, `37.0.0` |
| Прочее в SDK | `cmdline-tools/latest`, `platform-tools` (adb), `licenses` |
| Эмулятор | **нет**: нет `$SDK/emulator`, `$SDK/system-images`, `~/.android/avd` |
| KVM | **нет** (`/dev/kvm` отсутствует): аппаратного ускорения эмулятора на сервере нет. Instrumented-тесты — только в CI (GitHub-раннер с KVM) или на подключённом устройстве |

Проверка: копия проекта эталонный проект во временном каталоге собрала `:app:assembleDebug` командой ниже **офлайн** за 2 мин 50 с (APK 77 МБ), копия затем удалена.

**Команда сборки debug APK для нового проекта** (каталог `android/` в репозитории MFA Gate):

```bash
<проверка ресурсов сервера>            # память >= 4 ГБ, диск > 5 ГБ
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
export ANDROID_HOME=<android-sdk>           # или local.properties: sdk.dir=<android-sdk> (файл в .gitignore)
cd "<каталог проектов>"
# один раз создать wrapper (затем коммитить gradlew, gradlew.bat, gradle/wrapper/*):
<gradle>/bin/gradle wrapper --gradle-version 9.8.2
./gradlew --no-daemon --max-workers=4 :app:assembleDebug
# результат: app/build/outputs/apk/debug/app-debug.apk
# unit-тесты и lint:
./gradlew --no-daemon --max-workers=4 :app:testDebugUnitTest :app:lintDebug
```

Для новых зависимостей (`androidx.biometric`, CameraX, ZXing) первая сборка требует сети: в кэше Gradle их нет, `--offline` сработает только после первой загрузки.
