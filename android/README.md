# Twofold Authenticator для Android

Приложение-аутентификатор для сервера Twofold: подтверждение входов по запросу (Device API v1, подпись ключом из Android Keystore после биометрии или PIN) и независимые одноразовые коды TOTP без сети. Лицензия Apache-2.0.

Архитектура, хранение ключей, push и состояние проверки: [`../docs/android.md`](../docs/android.md). Протокол: [`../docs/protocol/device-api.md`](../docs/protocol/device-api.md).

## Требования

- JDK 17 или новее (проверено на OpenJDK 21), Android SDK с `platforms;android-37.0` и `build-tools;37.0.0`.
- Путь к SDK — в `ANDROID_HOME` или в `local.properties` (`sdk.dir=...`). Файл `local.properties` в Git не попадает.
- Первая сборка скачивает зависимости из Google Maven и Maven Central.

| Параметр | Значение |
| --- | --- |
| Gradle / AGP / Kotlin | 9.8.2 (wrapper) / 9.5.0 (Kotlin встроен) / 2.4.21 |
| compileSdk / minSdk / targetSdk | 37 / 26 / 36 |
| Версия | файл `VERSION`; `versionCode = major*10000 + minor*100 + patch` |

## Сборка

```bash
export JAVA_HOME=/path/to/jdk-21
export ANDROID_HOME=/path/to/android-sdk
./gradlew :app:testFossDebugUnitTest            # JVM unit-тесты
./gradlew :app:assembleFossDebug :app:assembleGmsDebug
./gradlew :app:lintFossDebug :app:lintGmsDebug
./gradlew :app:assembleFossRelease :app:assembleGmsRelease
```

Результат: `app/build/outputs/apk/<flavor>/<buildType>/app-<flavor>-<buildType>.apk`.

## Варианты (product flavors)

| Flavor | Доставка запросов | Зависимости Google |
| --- | --- | --- |
| `foss` | long-poll `GET /requests?status=pending&wait=25`, пока открыт экран «Запросы»; в фоне WorkManager раз в 15 минут показывает уведомление о новых запросах | нет |
| `gms` | Firebase Cloud Messaging: сигнал `{type, request_id}` без данных, затем загрузка запроса по HTTPS и уведомление; WorkManager как запасной путь | `firebase-messaging` |

В сборке `gms` нет `google-services.json` и плагина google-services: параметры проекта Firebase организации (`project_id`, `application_id`, `api_key`, `sender_id`) приходят с сервера в `push_config`, и приложение создаёт `FirebaseApp` через `FirebaseOptions.Builder`. Один и тот же APK работает с серверами разных организаций.

`debug`-сборка разрешает HTTP (тестовый стенд) и пользовательские сертификаты CA. `release` — только HTTPS и системные CA.

## Подпись release

Ключ подписи в репозитории не хранится. Источники по порядку:

1. Переменные окружения:
   - `TWOFOLD_KEYSTORE` — путь к `.jks`;
   - `TWOFOLD_KEYSTORE_PASSWORD`;
   - `TWOFOLD_KEY_ALIAS`;
   - `TWOFOLD_KEY_PASSWORD`.
2. Файл `signing.properties` в каталоге `android/` (в `.gitignore`):
   ```properties
   storeFile=/абсолютный/путь/twofold-release.jks
   storePassword=...
   keyAlias=...
   keyPassword=...
   ```
3. Если ничего не задано, release подписывается отладочным ключом. Такая сборка годится для проверки, но не для публикации: обновление поверх неё постоянным ключом невозможно.

Создать ключ (один раз, хранить вне репозитория, сделать резервную копию):

```bash
keytool -genkeypair -v -keystore twofold-release.jks -alias twofold -keyalg RSA -keysize 4096 -validity 10000
```

Проверка подписи: `$ANDROID_HOME/build-tools/37.0.0/apksigner verify --print-certs app/build/outputs/apk/foss/release/app-foss-release.apk`.

## Тесты

JVM unit-тесты (`app/src/test`): TOTP по векторам RFC 6238 (SHA1/SHA256/SHA512), base64url и Base32, разбор `otpauth://` и `twofold://enroll` (включая некорректные), строка подписи `twofold-decision-v1` и проверка подписи P-256 по SPKI, HTTP-клиент Device API на MockWebServer (заголовок `Device`, пути, ошибки 401/404/409/410, запрет перенаправлений).

Instrumented-тестов пока нет: Keystore, BiometricPrompt и FCM проверяются на устройстве (см. `docs/android.md`).
