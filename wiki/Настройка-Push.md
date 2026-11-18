# Настройка Push

Push — сигнал на телефон «есть запрос на вход». В самом сигнале нет логина, ресурса и IP: телефон получает только идентификатор запроса и сам забирает подробности с сервера. Решение всегда подписывается ключом телефона; сам сигнал ничего не разрешает.

Справочник (формат сигнала, файл FCM, HMS, UnifiedPush): [docs/push.md](https://github.com/calleograph/mfa-gate/blob/main/docs/push.md).

## Вариант 1. Без FCM (по умолчанию)

Ничего настраивать не нужно. Работает с обоими вариантами приложения (`foss` и `gms`):

- **приложение открыто** на экране «Запросы» — оно держит постоянный запрос к серверу (long-poll), и запрос на вход появляется сразу;
- **приложение закрыто** — фоновая проверка примерно раз в 15 минут. Для VPN этого мало: сотрудник после ввода пароля должен **сам открыть приложение**, пока не истекло время ответа (45 секунд по умолчанию).

Объясните сотрудникам порядок: «ввёл пароль в VPN → открыл MFA Gate → нажал „Подтвердить“».

**Как проверить:** `sudo mfagatectl logs worker | grep push-провайдеры` → `push-провайдеры: нет (только long-poll)`. В «Диагностике» строка «Доставка push» — предупреждение, это ожидаемо.

Если такой порядок неудобен и у сотрудников телефоны с сервисами Google, настройте FCM.

## Вариант 2. FCM (Firebase Cloud Messaging)

Уведомление приходит за секунды, даже когда приложение закрыто. Нужен вариант приложения `gms` и проект Firebase **вашей организации**: MFA Gate не пользуется общим проектом.

> Статус: реализовано, **требует проверки на реальном проекте Firebase и устройстве**.

Сервер должен иметь доступ к `oauth2.googleapis.com` и `fcm.googleapis.com` по HTTPS.

### Шаг 1. Проект Firebase и Android-приложение

1. Откройте <https://console.firebase.google.com> → «Создать проект» (Google Analytics не нужен).
2. В проекте: Project settings → General → Your apps → «Add app» → Android. Package name: `io.github.calleograph.mfagate`. Остальные поля можно не заполнять.
3. Скачайте `google-services.json` — из него возьмём публичные параметры приложения. Встраивать его в приложение не нужно: приложение получит параметры от сервера.

**Ожидаемый результат:** у вас есть файл `google-services.json`.

### Шаг 2. Service account

Project settings → Service accounts → «Generate new private key» → «Generate key».

**Ожидаемый результат:** скачан JSON с полями `"type": "service_account"`, `private_key`, `client_email`. **Это секрет сервера**: храните как пароль.

Проверьте, что в Google Cloud Console для проекта включён API «Firebase Cloud Messaging API (V1)» (APIs & Services → Enabled APIs).

### Шаг 3. Соберите файл для MFA Gate

MFA Gate читает один JSON: файл service account и раздел `client` с публичными параметрами приложения. Перенесите оба файла на сервер и выполните:

```bash
sudo apt-get install -y jq
jq --slurpfile g google-services.json '. + {client: {
  project_id:     $g[0].project_info.project_id,
  application_id: $g[0].client[0].client_info.mobilesdk_app_id,
  api_key:        $g[0].client[0].api_key[0].current_key,
  sender_id:      $g[0].project_info.project_number}}' service-account.json > fcm.json
jq '.client' fcm.json
```

**Ожидаемый результат:** четыре заполненных поля, например:

```json
{"project_id": "mfa-gate-example", "application_id": "1:000000000000:android:0000000000000000000000", "api_key": "…", "sender_id": "000000000000"}
```

Ни одно поле не должно быть `null`. Если в проекте несколько Android-приложений, замените `client[0]` на номер нужного.

Ограничьте API-ключ в Google Cloud Console (APIs & Services → Credentials): только Android-приложение `io.github.calleograph.mfagate` и только Firebase Cloud Messaging. Сам ключ не секретен — он есть в любом опубликованном приложении с Firebase.

### Шаг 4. Подключите файл к контейнерам

```bash
cd /opt/mfa-gate/deploy
sudo install -m 644 /путь/к/fcm.json data/secrets/fcm.json
rm -f /путь/к/fcm.json /путь/к/service-account.json
```

Каталог `data/secrets` закрыт (права 700); права 644 на файле нужны, чтобы его прочитал процесс в контейнере.

Создайте `deploy/compose.override.yml` (команда `mfagatectl` подхватывает его автоматически, в Git он не попадает):

```bash
sudo tee /opt/mfa-gate/deploy/compose.override.yml >/dev/null <<'EOF'
services:
  api:
    secrets: [master_key, fcm]
  worker:
    secrets: [master_key, fcm]

secrets:
  fcm:
    file: ${MFA_GATE_DATA:?}/secrets/fcm.json
EOF
```

Укажите путь к файлу внутри контейнера в `.env`:

```bash
sudo sed -i 's|^MFA_GATE_PUSH_FCM_CREDENTIALS_FILE=.*|MFA_GATE_PUSH_FCM_CREDENTIALS_FILE=/run/secrets/fcm|' /opt/mfa-gate/deploy/.env
sudo mfagatectl start
```

**Ожидаемый результат:** `запущено`.

### Шаг 5. Проверка

```bash
sudo mfagatectl logs worker | grep push-провайдеры
sudo mfagatectl diagnose | grep 'Доставка push'
```

**Ожидаемый результат:** `push-провайдеры: fcm` и `OK   Доставка push: …`.

Если в журнале `worker` строка `FCM не настроен: …` — файл не читается или в нём не хватает полей. Сервер при этом продолжает работать без push.

Затем на телефоне с приложением `gms`:

1. Привяжите телефон ([Регистрация Android-устройства](Регистрация-Android-устройства.md)) или, если он уже привязан, откройте приложение — оно получит параметры FCM при следующем обращении к серверу.
2. Закройте приложение (свайпом из списка недавних, но не «Остановить» в настройках).
3. Выполните вход в VPN.

**Ожидаемый результат:** за несколько секунд приходит уведомление «Запрос на вход». В карточке пользователя у телефона нет пометки «без push-канала».

Если уведомление не приходит: откройте приложение один раз, проверьте, что уведомления для него разрешены и для приложения отключена экономия заряда; при необходимости привяжите телефон заново. После принудительной остановки приложения Android не доставляет push, пока его не откроют вручную — это поведение системы.

## Ограничения

- Один телефон получает push только от одного проекта Firebase. Если телефон привязан к серверам двух организаций с разными проектами Firebase, второй сервер будет работать как без FCM.
- HMS (Huawei Push Kit) и UnifiedPush реализованы только на сервере, **экспериментально**, в приложении их нет. Подробности: [docs/push.md](https://github.com/calleograph/mfa-gate/blob/main/docs/push.md).
