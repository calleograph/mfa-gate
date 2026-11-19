// Подписи и статусы предметной области. Статус всегда = цвет + значок + текст (ui.statusBadge).
export const REQUEST_STATUS = {
  approved: ['ok', 'Подтверждён'],
  allowed: ['ok', 'Пропущен политикой'],
  pending: ['pending', 'Ожидает ответа'],
  denied: ['error', 'Отклонён на телефоне'],
  rejected: ['error', 'Отказ'],
  expired: ['warning', 'Истекло время'],
  consumed: ['ok', 'Подтверждён'],
};
export const CHANNEL = { radius: 'RADIUS', api: 'API', admin: 'Панель', portal: 'Личный кабинет' };
export const METHOD = { push: 'Push', totp: 'TOTP-код', policy: 'Политика', emergency: 'Аварийный код', backup_code: 'Резервный код' };
// Совпадает с authflow.REASONS на сервере: нужен для фильтра (в ответе списка есть reason_text)
export const REASONS = {
  local_password: 'Неверный пароль (локальная запись)',
  ldap_password: 'Каталог не принял пароль',
  unknown_user: 'Пользователь не найден',
  user_disabled: 'Учётная запись отключена',
  locked: 'Временная блокировка',
  directory_unavailable: 'Каталог недоступен',
  directory_denied: 'Каталог отклонил вход',
  policy_deny: 'Запрещено политикой',
  no_factor: 'Нет второго фактора',
  invalid_otp: 'Неверный одноразовый код',
  push_denied: 'Push отклонён',
  push_expired: 'Нет ответа на push',
  resource_disabled: 'Ресурс отключён',
};
export const SOURCE = { local: 'Локальный', ldap: 'Каталог' };
export const ROLES = { superadmin: 'Главный администратор', admin: 'Администратор', operator: 'Оператор', auditor: 'Аудитор' };
export const RESOURCE_KIND = { vpn: 'VPN', app: 'Приложение', other: 'Другое' };
export const POLICY_ACTION = {
  mfa: ['ok', 'Второй фактор'],
  allow: ['warning', 'Пропустить без MFA'],
  deny: ['error', 'Запретить'],
};
export const SEVERITY = { info: ['neutral', 'Обычное'], warning: ['warning', 'Внимание'], critical: ['error', 'Критичное'] };
export const DIAG = { ok: 'В порядке', warning: 'Требует внимания', error: 'Ошибка' };
export const ENCRYPTION = { ldaps: 'LDAPS', starttls: 'StartTLS', none: 'Без шифрования' };

export const AUDIT_EVENT = {
  'admin.login': 'Вход в панель',
  'admin.mfa': 'Второй фактор в панели',
  'admin.emergency_access': 'Аварийный вход в панель',
  'admin.emergency_code_issued': 'Выпущен аварийный код',
  'admin.created_cli': 'Администратор создан в консоли',
  'auth.accept': 'Вход разрешён',
  'auth.reject': 'Вход отклонён',
  'auth.push_sent': 'Отправлен push',
  'device.bad_signature': 'Неверная подпись устройства',
  'device.unbound': 'Устройство отвязано с телефона',
  'radius.bad_authenticator': 'Неверный RADIUS-аутентификатор',
  'user.created': 'Создан пользователь',
  'user.updated': 'Изменён пользователь',
  'user.deleted': 'Удалён пользователь',
  'user.unlocked': 'Снята блокировка',
  'device.enrollment_issued': 'Выдана ссылка привязки',
  'device.enrolled': 'Привязан телефон',
  'device.revoked': 'Отозвано устройство',
  'portal.login': 'Вход в личный кабинет',
  'portal.mfa': 'Второй фактор в личном кабинете',
  'portal.enroll_denied': 'Отказ в самостоятельной настройке',
  'portal.backup_codes_issued': 'Выпущены резервные коды',
  'portal.backup_code_used': 'Вход по резервному коду',
  'portal.session_closed': 'Завершён сеанс личного кабинета',
  'user.reenroll_allowed': 'Разрешена повторная настройка',
  'user.backup_codes_reset': 'Сброшены резервные коды',
  'totp.issued': 'Выпущен TOTP',
  'totp.enrolled': 'Подключён TOTP',
  'totp.revoked': 'Удалён TOTP',
  'group.created': 'Создана группа', 'group.updated': 'Изменена группа', 'group.deleted': 'Удалена группа',
  'resource.created': 'Создан ресурс', 'resource.updated': 'Изменён ресурс', 'resource.deleted': 'Удалён ресурс',
  'radius_client.created': 'Добавлен RADIUS-клиент', 'radius_client.updated': 'Изменён RADIUS-клиент', 'radius_client.deleted': 'Удалён RADIUS-клиент',
  'api_client.created': 'Создан API-клиент', 'api_client.updated': 'Изменён API-клиент', 'api_client.rotated': 'Сменён секрет API-клиента', 'api_client.deleted': 'Удалён API-клиент',
  'policy.created': 'Создана политика', 'policy.updated': 'Изменена политика', 'policy.deleted': 'Удалена политика',
  'ldap.created': 'Добавлен каталог', 'ldap.updated': 'Изменён каталог', 'ldap.deleted': 'Удалён каталог', 'ldap.diagnosed': 'Диагностика каталога',
};
export const AUDIT_GROUPS = [
  ['', 'Все события'], ['admin.', 'Администраторы'], ['auth.', 'Входы пользователей'], ['radius.', 'RADIUS'], ['user.', 'Пользователи'], ['device.', 'Устройства'], ['totp.', 'TOTP'],
  ['group.', 'Группы'], ['resource.', 'Ресурсы'], ['radius_client.', 'RADIUS-клиенты'], ['api_client.', 'API-клиенты'],
  ['policy.', 'Политики'], ['ldap.', 'Каталоги'],
];
