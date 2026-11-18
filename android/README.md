# MFA Gate Authenticator для Android

Приложение-аутентификатор для сервера MFA Gate. Оно подтверждает входы по запросу (Device API v1, подпись ключом из Android Keystore после биометрии или PIN) и показывает независимые одноразовые коды TOTP без сети. Лицензия Apache-2.0.

Архитектура, тема «Стекло», работа по HTTP, хранение ключей, push и сценарий ручной приёмки: [`../docs/android.md`](../docs/android.md). Протокол: [`../docs/protocol/device-api.md`](../docs/protocol/device-api.md). Журнал изменений: [`CHANGELOG.md`](CHANGELOG.md).

| Параметр | Значение |
| --- | --- |
| applicationId и пакет Kotlin | `io.github.calleograph.mfagate` |
| Ссылка привязки | `mfagate://enroll?v=1&url=…&token=…&label=…` |
| Gradle / AGP / Kotlin | 9.8.2 (wrapper) / 9.5.0 (Kotlin встроен) / 2.4.21 |
| compileSdk / minSdk / targetSdk | 37 / 26 / 36 |
| Версия | файл `VERSION`; `versionCode = major*10000 + minor*100 + patch` (0.2.0 → 200) |

## Требования

- JDK 17 или новее (проверено на OpenJDK 21), Android SDK с `platforms;android-37.0` и `build-tools;37.0.0`.
- Путь к SDK задаётся в `ANDROID_HOME` или в `local.properties` (`sdk.dir=...`). Файл `local.properties` в Git не попадает.
- Первая сборка скачивает зависимости из Google Maven и Maven Central.

## Сборка

```bash
export JAVA_HOME=/path/to/jdk-21
export ANDROID_HOME=/path/to/android-sdk
./gradlew :app:testFossDebugUnitTest :app:testGmsDebugUnitTest   # JVM unit-тесты
./gradlew :app:lintFossDebug :app:lintGmsDebug
./gradlew :app:assembleFossDebug :app:assembleGmsDebug
./gradlew :app:assembleFossRelease :app:assembleGmsRelease
```

Результат: `app/build/outputs/apk/<flavor>/<buildType>/app-<flavor>-<buildType>.apk`.

## Варианты (product flavors)

| Flavor | Доставка запросов | Зависимости Google |
| --- | --- | --- |
| `foss` | long-poll `GET /requests?status=pending&wait=25`, пока открыт экран «Запросы»; в фоне WorkManager раз в 15 минут показывает уведомление о новых запросах | нет |
| `gms` | Firebase Cloud Messaging: сигнал `{type, request_id, device_id}` без данных о входе, затем загрузка запроса с сервера и уведомление; WorkManager как запасной путь | `firebase-messaging` |

В сборке `gms` нет `google-services.json` и плагина google-services. Параметры проекта Firebase организации (`project_id`, `application_id`, `api_key`, `sender_id`) приходят с сервера в `push_config`, и приложение создаёт `FirebaseApp` через `FirebaseOptions.Builder`. Один и тот же APK работает с серверами разных организаций.

Проверка, что в `foss` нет Google: `$ANDROID_HOME/build-tools/37.0.0/dexdump app/build/outputs/apk/foss/release/app-foss-release.apk | grep 'Class descriptor' | grep -cE 'Lcom/google/(firebase|android/gms)/'` должно вывести `0`.

## Сеть: HTTP и сертификаты

- **HTTP разрешён и в release.** Первичная установка сервера идёт по `http://IP:порт`, и телефон должен к нему привязаться. Защиту даёт само приложение: если в ссылке адрес `http://`, при привязке показывается предупреждение «Соединение без шифрования — только для изолированной тестовой сети», и кнопка «Привязать» доступна только после отметки «Понимаю риск». В списке серверов такой сервер помечен «Без шифрования (HTTP)».
- **HTTPS работает как раньше.** Release доверяет только системным центрам сертификации, пользовательские CA не принимаются. Debug-сборка дополнительно доверяет пользовательским CA, это нужно для тестового стенда с самоподписанным сертификатом.
- Перенаправления выключены: заголовок `Authorization: Device …` не уходит на другой адрес.

## Подпись release

Ключ подписи в репозитории не хранится. Источники проверяются по порядку:

1. Переменные окружения:
   - `MFA_GATE_KEYSTORE` — путь к `.jks`;
   - `MFA_GATE_KEYSTORE_PASSWORD`;
   - `MFA_GATE_KEY_ALIAS`;
   - `MFA_GATE_KEY_PASSWORD`.
2. Файл `signing.properties` в каталоге `android/` (он в `.gitignore`):
   ```properties
   storeFile=/абсолютный/путь/mfa-gate-release.jks
   storePassword=...
   keyAlias=...
   keyPassword=...
   ```
3. Если ничего не задано, release подписывается отладочным ключом. Такая сборка годится для проверки, но не для публикации: поверх неё нельзя поставить обновление, подписанное постоянным ключом.

Ключ создаётся один раз. Храните его вне репозитория и сделайте резервную копию:

```bash
keytool -genkeypair -v -keystore mfa-gate-release.jks -alias mfagate -keyalg RSA -keysize 4096 -validity 10000
```

Проверка подписи: `$ANDROID_HOME/build-tools/37.0.0/apksigner verify --print-certs app/build/outputs/apk/foss/release/app-foss-release.apk`.

## Выпуск версии

Выпуск собирает `.github/workflows/release.yml` по **общему тегу `vX.Y.Z`**. Отдельного тега `android-vX.Y.Z` нет: workflow запускается только на `v*` и требует, чтобы версия в теге, `server/mfa_gate/config.py` и `android/VERSION` совпадали.

1. Поднимите версию в `android/VERSION` по SemVer: patch для исправлений, minor для новых возможностей. `versionCode` считается из неё сам, и каждая новая версия должна давать больший `versionCode`.
2. Перенесите пункты из «Не выпущено» в раздел новой версии в `android/CHANGELOG.md` и в корневом `CHANGELOG.md`. Описание GitHub Release берётся из корневого файла.
3. Убедитесь, что версия сервера в `server/mfa_gate/config.py` такая же.
4. Локально прогоните тесты, lint и обе release-сборки (раздел «Сборка»), затем дождитесь зелёного CI на `main`.
5. Поставьте и отправьте тег: `git tag -a vX.Y.Z -m "MFA Gate X.Y.Z" && git push origin vX.Y.Z`.
6. Workflow проверит версии, соберёт `foss` и `gms` ключом из секретов репозитория (`MFA_GATE_KEYSTORE_B64`, `MFA_GATE_KEYSTORE_PASSWORD`, `MFA_GATE_KEY_ALIAS`, `MFA_GATE_KEY_PASSWORD`). Если APK подписан отладочным ключом, выпуск остановится. Файлы `mfa-gate-authenticator-X.Y.Z-<flavor>.apk` и `SHA256SUMS` будут приложены к GitHub Release.

Ключ подписи никогда не меняйте: Android не установит обновление, подписанное другим ключом.

## Тесты

JVM unit-тесты лежат в `app/src/test`:

- TOTP по векторам RFC 6238 (SHA1/SHA256/SHA512), base64url и Base32;
- разбор `otpauth://` и `mfagate://enroll`, включая некорректные ссылки: адрес `http://` помечается как небезопасный, ссылка со старой схемой отклоняется;
- строка подписи `mfa-gate-decision-v1` и проверка подписи P-256 по SPKI;
- HTTP-клиент Device API на MockWebServer: заголовок `Device`, пути, `server_time`, ошибки `{code, message}` (401/404/409/410/422), запрет перенаправлений;
- выбор учётной записи по `device_id` из push-сигнала.

Instrumented-тестов пока нет. Keystore, BiometricPrompt, камеру и FCM нужно проверять на устройстве по сценарию из `docs/android.md`.
