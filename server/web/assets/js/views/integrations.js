// Раздел «Интеграции»: ресурсы, RADIUS-клиенты, API-клиенты, каталоги LDAP/AD.
import { get, post, put, del } from '../api.js';
import {
  h, icon, pageHead, panel, button, busy, statusBadge, statusIcon, chip, risk, cellMain, timeEl, empty, errorState, loadingRegion, actionMenu,
  toast, toastError, formDialog, confirmDialog, secretDialog, notice, modal, form, num, clear, fill } from '../ui.js';
import { crudPage } from './crud.js';
import { RESOURCE_KIND, ENCRYPTION } from '../labels.js';

const enabledBadge = (on) => (on ? statusBadge('ok', 'Включён') : statusBadge('neutral', 'Выключен'));
const switchBadge = (on) => (on ? chip('Включён') : statusBadge('neutral', 'Выключен'));
const resourceOptions = (items, none) => [...(none ? [{ value: '', label: none }] : []), ...items.map((r) => ({ value: r.id, label: r.name + (r.enabled ? '' : ' (выключен)') }))];
const loadResources = async (ctx) => (ctx.can('resources:read') ? (await get('/resources')).items : []);

// Секрет генерируется в браузере криптостойким генератором и передаётся серверу один раз
function randomSecret() {
  const b = crypto.getRandomValues(new Uint8Array(24));
  return btoa(String.fromCharCode(...b)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
const CIDR_RE = /^[0-9a-fA-F:.]+(\/\d{1,3})?$/;
const cidrCheck = (v) => (CIDR_RE.test(v) ? null : `«${v}» — не IP-адрес и не сеть`);

// ------------------------------------------------------------------ ресурсы
export function resourcesPage(ctx) {
  return crudPage(ctx, {
    area: 'resources', endpoint: '/resources', title: 'Ресурсы',
    desc: 'То, что защищает MFA Gate: VPN-шлюзы и приложения. На ресурсы ссылаются RADIUS-клиенты, API-клиенты и политики.',
    createLabel: 'Добавить ресурс', createTitle: 'Новый ресурс', createdText: 'Ресурс создан',
    name: (r) => r.name,
    empty: { iconName: 'server', title: 'Ресурсов пока нет', text: 'Добавьте VPN или приложение — затем подключите к нему RADIUS-клиент и настройте политику.' },
    columns: () => [
      { label: 'Ресурс', primary: true, render: (r) => cellMain(r.name, r.description) },
      { label: 'Тип', render: (r) => RESOURCE_KIND[r.kind] || r.kind },
      { label: 'Статус', render: (r) => switchBadge(r.enabled) },
      { label: 'Создан', opt: true, render: (r) => timeEl(r.created_at) },
    ],
    fields: () => [
      { name: 'name', label: 'Название', required: true, placeholder: 'VPN головного офиса' },
      { name: 'kind', type: 'choices', label: 'Тип', default: 'vpn', options: [
        { value: 'vpn', label: 'VPN', icon: 'router', hint: 'Шлюз проверяет вход по RADIUS.' },
        { value: 'app', label: 'Приложение', icon: 'server', hint: 'Сервис проверяет вход через REST API.' },
        { value: 'other', label: 'Другое', icon: 'circle-dashed', hint: 'Любая другая система.' },
      ] },
      { name: 'description', label: 'Описание', type: 'textarea', rows: 2 },
      { name: 'enabled', type: 'switch', label: 'Ресурс включён', default: true, hint: 'Выключенный ресурс отклоняет все входы с причиной «Ресурс отключён».' },
    ],
    toBody: (v) => ({ name: v.name, kind: v.kind, description: v.description || '', enabled: v.enabled }),
    deleteText: () => 'Связанные RADIUS-клиенты перестанут работать, а политики, привязанные к ресурсу, перестанут к нему применяться.',
  });
}

// ------------------------------------------------------------------ RADIUS
export function radiusPage(ctx) {
  const showSecret = (c, secret) => secretDialog({
    title: `Секрет RADIUS-клиента «${c.name}»`,
    intro: `Укажите этот общий секрет (shared secret) в настройках RADIUS на шлюзе ${c.address}. Запросы с другим секретом сервер отбрасывает.`,
    items: [{ label: 'Общий секрет', value: secret }],
  });
  return crudPage(ctx, {
    area: 'radius', endpoint: '/radius-clients', title: 'RADIUS-клиенты',
    desc: 'VPN-шлюзы и другие устройства, которые проверяют вход через RADIUS. Запросы принимаются только с адресов из этого списка.',
    createLabel: 'Добавить клиент', createTitle: 'Новый RADIUS-клиент', createdText: 'RADIUS-клиент добавлен',
    name: (c) => c.name,
    extras: async (c) => ({ resources: await loadResources(c) }),
    empty: { iconName: 'router', title: 'RADIUS-клиентов пока нет', text: 'Добавьте VPN-шлюз: укажите его адрес и ресурс. Общий секрет будет показан один раз после создания.' },
    columns: ({ resources }) => [
      { label: 'Клиент', primary: true, render: (c) => cellMain(c.name, [c.first_factor === 'gateway' ? 'пароль проверяет шлюз' : null, c.nas_identifier ? `NAS-Identifier: ${c.nas_identifier}` : null].filter(Boolean).join(' · ') || null) },
      { label: 'Адрес', render: (c) => h('span', { class: 'td-mono', text: c.address }) },
      { label: 'Ресурс', render: (c) => resources.find((r) => r.id === c.resource_id)?.name || h('span', { class: 'faint', text: 'нет доступа' }) },
      { label: 'Статус', render: (c) => switchBadge(c.enabled) },
      { label: 'Последний запрос', opt: true, render: (c) => timeEl(c.last_seen_at) },
    ],
    fields: ({ resources }, row) => [
      resources.length ? null : { type: 'node', node: notice('warning', 'Сначала создайте ресурс в разделе «Ресурсы» — клиент всегда относится к ресурсу.') },
      { name: 'name', label: 'Название', required: true, placeholder: 'Шлюз VPN в офисе', half: true },
      { name: 'resource_id', type: 'select', label: 'Ресурс', required: true, half: true, options: resourceOptions(resources, resources.length ? null : 'Нет ресурсов') },
      { name: 'address', label: 'Адрес шлюза', required: true, mono: true, half: true, placeholder: '192.0.2.10', hint: 'IP-адрес или сеть, например 198.51.100.0/28. Один адрес — один клиент.' },
      { name: 'nas_identifier', label: 'NAS-Identifier', mono: true, half: true, hint: 'Необязательно. Если задан, должен совпадать с атрибутом в запросе.' },
      { name: 'first_factor', type: 'choices', label: 'Кто проверяет доменный пароль', default: 'mfa_gate', options: [
        { value: 'mfa_gate', label: 'MFA Gate', icon: 'shield-check', hint: 'Шлюз передаёт логин и пароль (PAP), MFA Gate проверяет их в каталоге и запрашивает второй фактор.' },
        { value: 'gateway', label: 'Сам шлюз', icon: 'router', risk: true, hint: 'Шлюз уже проверил пароль и присылает только логин или логин и OTP-код. MFA Gate доверяет этому только для этого клиента.' },
      ] },
      { name: 'secret', label: row ? 'Новый общий секрет' : 'Общий секрет', type: 'password', autocomplete: 'new-password', mono: true, trim: false,
        hint: row ? 'Оставьте пустым, чтобы не менять. Или используйте «Сменить секрет» в меню — MFA Gate сгенерирует новый.' : 'Оставьте пустым — MFA Gate сгенерирует надёжный секрет и покажет его один раз. Свой — от 16 символов.' },
      { name: 'enabled', type: 'switch', label: 'Клиент включён', default: true },
    ].filter(Boolean),
    toBody: (v) => ({ name: v.name, resource_id: v.resource_id || null, address: v.address, nas_identifier: v.nas_identifier || null, first_factor: v.first_factor, secret: v.secret || null, enabled: v.enabled }),
    afterSave: (res) => { if (res.secret) showSecret(res, res.secret); },
    rowMenu: (c, { reload, writable }) => (writable ? [{ label: 'Сменить секрет', icon: 'refresh-cw', onSelect: async () => {
      const secret = randomSecret();
      const res = await confirmDialog({
        title: `Сменить секрет «${c.name}»?`, confirmLabel: 'Сменить секрет', danger: true,
        text: 'Старый секрет перестанет действовать сразу. Пока вы не укажете новый на шлюзе, MFA Gate будет молча отбрасывать его запросы: шлюз увидит таймаут, в истории входов записей не будет.',
        action: () => put(`/radius-clients/${c.id}`, { resource_id: c.resource_id, name: c.name, address: c.address, nas_identifier: c.nas_identifier, first_factor: c.first_factor, enabled: c.enabled, secret }),
      });
      if (res) { showSecret(c, secret); reload(); }
    } }] : []),
    deleteText: (c) => `Запросы с адреса ${c.address} перестанут приниматься.`,
  });
}

// ------------------------------------------------------------------ API-клиенты
const AREA = { auth: 'Запросы доступа', users: 'Пользователи', groups: 'Группы', devices: 'Устройства', resources: 'Ресурсы', policies: 'Политики', radius: 'RADIUS-клиенты', ldap: 'Каталоги', apiclients: 'API-клиенты', requests: 'История входов', audit: 'Журнал', system: 'Состояние системы' };
const scopeLabel = (s) => { const [a, m] = s.split(':'); return `${AREA[a] || a}: ${m === 'write' ? 'изменение' : 'чтение'}`; };

export function apiClientsPage(ctx) {
  const showSecret = (c, secret) => secretDialog({
    title: `Ключ API «${c.name}»`,
    intro: 'Интеграция передаёт ключ в заголовке Authorization: Basic base64(key_id:secret).',
    items: [{ label: 'Идентификатор ключа (key_id)', value: c.key_id }, { label: 'Секрет', value: secret }],
  });
  return crudPage(ctx, {
    area: 'apiclients', endpoint: '/api-clients', title: 'API-клиенты',
    desc: 'Ключи для интеграций через REST API: проверка входа в приложениях, автоматизация, выгрузка журнала. Права ключа ограничены выбранными областями.',
    headActions: () => [button('Документация API', { kind: 'secondary', iconName: 'book-open', href: '/api/docs', target: '_blank' })],
    createLabel: 'Создать ключ', createTitle: 'Новый API-клиент', createdText: 'Ключ создан', formSize: 'lg',
    name: (c) => c.name,
    extras: async (c) => ({ resources: await loadResources(c) }),
    empty: { iconName: 'key-round', title: 'Ключей API пока нет', text: 'Создайте ключ для приложения, которое будет проверять вход через MFA Gate. Секрет покажем один раз.' },
    columns: ({ resources }) => [
      { label: 'Клиент', primary: true, render: (c) => cellMain(c.name, c.key_id, { mono: true }) },
      { label: 'Права', render: (c) => h('div', { class: 'chips' }, c.scopes.slice(0, 3).map((s) => h('span', { class: 'chip mono', text: s, title: scopeLabel(s) })), c.scopes.length > 3 ? chip(`+${c.scopes.length - 3}`) : null) },
      { label: 'Ресурс', opt: true, render: (c) => (c.resource_id ? resources.find((r) => r.id === c.resource_id)?.name || '—' : h('span', { class: 'faint', text: 'любой' })) },
      { label: 'Адреса', opt: true, render: (c) => (c.allowed_cidrs.length ? h('div', { class: 'chips' }, c.allowed_cidrs.map((x) => h('span', { class: 'chip mono', text: x }))) : statusBadge('warning', 'Любые', 'plain')) },
      { label: 'Статус', render: (c) => switchBadge(c.enabled) },
      { label: 'Использован', opt: true, render: (c) => timeEl(c.last_used_at) },
    ],
    fields: ({ resources }, row, data) => [
      { name: 'name', label: 'Название', required: true, placeholder: 'Портал самообслуживания' },
      { name: 'scopes', type: 'checks', label: 'Права ключа', default: ['auth:write'],
        options: (data?.scopes || ['auth:write']).map((s) => ({ value: s, label: scopeLabel(s), hint: s, risk: s.endsWith(':write') && s !== 'auth:write' })),
        hint: 'Выдавайте минимум. Для проверки входа достаточно «Запросы доступа: изменение» (auth:write).' },
      { name: 'resource_id', type: 'select', label: 'Ресурс по умолчанию', half: true, options: resourceOptions(resources, 'Не задан — указывается в запросе') },
      { name: 'allowed_cidrs', type: 'tags', label: 'Разрешённые адреса', mono: true, half: true, placeholder: '203.0.113.0/24', validate: cidrCheck, hint: 'Пусто — с любого адреса. Enter или запятая добавляют значение.' },
      { name: 'enabled', type: 'switch', label: 'Ключ действует', default: true },
    ],
    toForm: (c) => ({ ...c, resource_id: c.resource_id || '' }),
    toBody: (v) => ({ name: v.name, scopes: v.scopes, resource_id: v.resource_id || null, allowed_cidrs: v.allowed_cidrs || [], enabled: v.enabled }),
    afterSave: (res, isNew) => { if (isNew && res.secret) showSecret(res, res.secret); },
    rowMenu: (c, { reload, writable }) => (writable ? [{ label: 'Сменить секрет', icon: 'refresh-cw', onSelect: async () => {
      const res = await confirmDialog({
        title: `Сменить секрет «${c.name}»?`, confirmLabel: 'Сменить секрет', danger: true,
        text: 'Старый секрет перестанет действовать сразу — интеграция будет получать ошибку 401, пока вы не укажете новый.',
        action: () => post(`/api-clients/${c.id}/rotate`),
      });
      if (res) { showSecret(res, res.secret); reload(); }
    } }] : []),
    deleteText: () => 'Интеграции с этим ключом сразу перестанут работать.',
  });
}

// ------------------------------------------------------------------ LDAP / AD
const KIND_DEFAULTS = {
  ad: { user_filter: '(&(objectClass=user)(objectCategory=person))', login_attribute: 'sAMAccountName', id_attribute: 'objectGUID', name_attribute: 'displayName', email_attribute: 'mail', phone_attribute: 'mobile' },
  openldap: { user_filter: '(objectClass=inetOrgPerson)', login_attribute: 'uid', id_attribute: 'entryUUID', name_attribute: 'cn', email_attribute: 'mail', phone_attribute: 'mobile' },
};
const STEP = { dns: 'Имя контроллера (DNS)', tcp: 'Соединение с портом', tls: 'Шифрование (TLS)', bind: 'Сервисная учётная запись', base_dn: 'База поиска (Base DN)', user_search: 'Поиск пользователя', user_bind: 'Пароль пользователя', groups: 'Группы пользователя' };

export async function ldapPage(ctx) {
  const { main, can, signal } = ctx;
  const writable = can('ldap:write');
  const head = pageHead({
    title: 'LDAP / AD',
    desc: 'Каталоги, в которых MFA Gate проверяет доменный пароль, профиль и группы. Каталоги опрашиваются по возрастанию приоритета.',
    actions: [writable ? button('Подключить каталог', { kind: 'primary', iconName: 'plus', onClick: () => ldapForm(ctx, null, load) }) : null],
  });
  const list = h('div', { class: 'stack', 'aria-live': 'polite' });
  main.append(head, list);

  async function load() {
    fill(list, h('div', { class: 'panel' }, loadingRegion(3)));
    let data;
    try { data = await get('/ldap', null, signal); } catch (err) {
      if (err.name === 'AbortError') return;
      fill(list, h('div', { class: 'panel' }, errorState(err, load)));
      return;
    }
    const h1 = head.querySelector('h1');
    h1.querySelector('.count')?.remove();
    h1.append(h('span', { class: 'count', text: num(data.total) }));
    if (!data.items.length) {
      fill(list, h('div', { class: 'panel' }, empty({
        iconName: 'network', title: 'Каталог не подключён',
        text: 'Подключите Active Directory или OpenLDAP, чтобы сотрудники входили доменным паролем, а политики учитывали группы каталога. Без каталога работают только локальные пользователи.',
        action: writable ? button('Подключить каталог', { kind: 'primary', iconName: 'plus', onClick: () => ldapForm(ctx, null, load) }) : null,
      })));
      return;
    }
    fill(list, data.items.map((d) => dirCard(d)));
    if (can('system:read')) loadHealth(data.items);
  }

  // связь с контроллерами — из общей диагностики (TCP-проверка каждого включённого каталога)
  const health = {};
  async function loadHealth(items) {
    try {
      const r = await get('/system/diagnostics', null, signal);
      for (const d of items) {
        const c = r.checks.find((x) => x.name === `Каталог «${d.name}»`);
        if (c && health[d.id]) fill(health[d.id], statusBadge(c.status, c.status === 'ok' ? 'Контроллеры доступны' : c.status === 'warning' ? 'Часть контроллеров недоступна' : 'Каталог недоступен'));
        if (c && c.status !== 'ok' && health[d.id + 'm']) fill(health[d.id + 'm'], notice(c.status, c.message, writable ? button('Запустить диагностику', { size: 'sm', iconName: 'stethoscope', onClick: () => diagnose(d) }) : null));
      }
    } catch (err) {
      if (err.name !== 'AbortError') items.forEach((d) => health[d.id] && fill(health[d.id], statusBadge('neutral', 'Связь не проверена')));
    }
  }

  function dirCard(d) {
    health[d.id] = h('span', null, d.enabled ? (can('system:read') ? statusBadge('pending', 'Проверяем связь…') : chip('Включён')) : statusBadge('neutral', 'Выключен'));
    health[d.id + 'm'] = h('div');
    const insecure = d.encryption === 'none' || !d.verify_tls;
    const fact = (k, v) => h('div', null, h('dt', { text: k }), h('dd', null, v));
    return panel({
      cls: 'dir-card',
      title: h('span', null, d.name),
      actions: [
        h('div', { class: 'chips' }, health[d.id], chip(d.kind === 'ad' ? 'Active Directory' : 'OpenLDAP'), chip(`приоритет ${d.priority}`)),
        writable ? button('Диагностика', { size: 'sm', iconName: 'stethoscope', onClick: () => diagnose(d) }) : null,
        writable ? actionMenu([
          { label: 'Изменить', icon: 'pencil', onSelect: () => ldapForm(ctx, d, load) },
          '-',
          { label: 'Удалить', icon: 'trash-2', danger: true, onSelect: async () => {
            const ok = await confirmDialog({ title: `Отключить каталог «${d.name}»?`, danger: true, confirmLabel: 'Удалить', text: 'Доменные пользователи этого каталога не смогут войти, пока каталог не будет подключён снова.', action: () => del(`/ldap/${d.id}`) });
            if (ok) { toast('Каталог удалён'); load(); }
          } },
        ], `Действия: ${d.name}`) : null,
      ],
      body: h('div', { class: 'panel-body' },
        health[d.id + 'm'],
        insecure ? notice('warning', d.encryption === 'none' ? 'Шифрование выключено: пароли пользователей передаются в каталог открытым текстом. Включите LDAPS.' : 'Сертификат контроллера не проверяется: возможен перехват паролей. Включите проверку и укажите корневой сертификат.') : null,
        h('dl', { class: 'facts' },
          fact('Контроллеры', h('div', { class: 'chips' }, d.hosts.map((x) => h('span', { class: 'chip mono', text: `${x}:${d.port}` })))),
          fact('Шифрование', d.encryption === 'none' ? risk('Без шифрования') : h('span', null, ENCRYPTION[d.encryption], d.verify_tls ? ', сертификат проверяется' : '')),
          fact('Base DN', h('span', { class: 'mono', text: d.base_dn })),
          fact('Сервисная запись', d.bind_dn ? h('span', { class: 'mono', text: d.bind_dn }) : h('span', { class: 'muted', text: 'нет — поиск от имени пользователя' })),
          fact('Вход по атрибуту', h('span', { class: 'mono', text: d.login_attribute })),
          fact('Разрешённые группы', d.allowed_groups.length ? h('div', { class: 'chips' }, d.allowed_groups.map((g) => chip(g))) : h('span', { class: 'muted', text: 'все пользователи каталога' })),
          fact('Вложенные группы', d.nested_groups ? 'учитываются' : 'только прямые'),
          fact('Тайм-аут', `${d.timeout} с`))),
    });
  }

  function diagnose(d) {
    const f = form([
      { name: 'login', label: 'Логин для проверки', half: true, autocomplete: 'off', hint: 'Необязательно. Без логина проверяются сеть, TLS и сервисная запись.' },
      { name: 'password', label: 'Пароль', type: 'password', half: true, autocomplete: 'new-password', trim: false, hint: 'Пароль никуда не сохраняется и не пишется в журнал.' },
    ]);
    const out = h('div', { 'aria-live': 'polite' });
    const run = button('Запустить проверку', { kind: 'primary', type: 'submit', iconName: 'play' });
    const close = button('Закрыть', { kind: 'ghost' });
    const frm = h('form', { id: 'diag-form', novalidate: true, class: 'stack' }, f.el, out);
    run.setAttribute('form', 'diag-form');
    const m = modal({ title: `Диагностика «${d.name}»`, subtitle: 'Пошаговая проверка: DNS → TCP → TLS → bind → поиск → пароль → группы. Останавливается на первой ошибке.', size: 'lg', body: frm, actions: [close, run] });
    close.addEventListener('click', () => m.close());
    frm.addEventListener('submit', (e) => {
      e.preventDefault();
      busy(run, async () => {
        fill(out, h('div', { role: 'status', class: 'hint', text: 'Проверяем… Это может занять до ' + (d.timeout * d.hosts.length + 2) + ' с.' }));
        try {
          const v = f.values();
          const r = await post(`/ldap/${d.id}/diagnose`, { login: v.login || null, password: v.password || null });
          fill(out, 
            notice(r.ok ? (r.steps.some((x) => x.warn) ? 'warning' : 'ok') : 'error', r.ok
              ? (r.steps.some((x) => x.warn) ? 'Все шаги пройдены, но соединение с каталогом не защищено — для эксплуатации включите LDAPS и проверку сертификата'
                : 'Все шаги пройдены — каталог готов к входу пользователей')
              : `Есть ошибки: ${r.steps.filter((x) => !x.ok).length} из ${r.steps.length} шагов. Подробности — в шагах с крестиком.`),
            h('ol', { class: 'checklist inset' }, r.steps.map((s) => h('li', null,
              h('span', { class: 'ck-ic', 'data-s': s.warn ? 'warning' : (s.ok ? 'ok' : 'error') }, statusIcon(s.warn ? 'warning' : (s.ok ? 'ok' : 'error')),
                h('span', { class: 'sr-only', text: s.warn ? 'Предупреждение' : (s.ok ? 'Успешно' : 'Ошибка') })),
              h('span', { class: 'ck-title', text: STEP[s.step] || s.step }),
              h('span', { class: 'ck-meta', text: [s.host, `${s.ms} мс`].filter(Boolean).join(', ') }),
              h('p', { class: 'ck-msg', text: s.detail })))));
        } catch (err) {
          fill(out, notice('error', err.message));
        }
      });
    });
  }

  await load();
}

function ldapForm(ctx, d, reload) {
  const isNew = !d;
  const fields = [
    { type: 'section', label: 'Подключение' },
    { name: 'name', label: 'Название', required: true, half: true, placeholder: 'Домен corp.example.org' },
    { name: 'kind', type: 'select', label: 'Тип каталога', half: true, options: [{ value: 'ad', label: 'Active Directory' }, { value: 'openldap', label: 'OpenLDAP и совместимые' }] },
    { name: 'hosts', type: 'tags', label: 'Контроллеры', required: true, mono: true, placeholder: 'dc1.corp.example.org', hint: 'Имена или адреса в порядке опроса. Недоступный контроллер пропускается на 60 секунд.' },
    { name: 'encryption', type: 'choices', label: 'Шифрование', default: 'ldaps', options: [
      { value: 'ldaps', label: 'LDAPS', icon: 'lock', hint: 'Рекомендуется. Порт 636.' },
      { value: 'starttls', label: 'StartTLS', icon: 'lock', hint: 'Шифрование поверх порта 389.' },
      { value: 'none', label: 'Без шифрования', icon: 'shield-alert', risk: true, hint: 'Пароли идут открытым текстом. Только для стенда.' },
    ] },
    { name: 'port', type: 'number', label: 'Порт', half: true, min: 1, max: 65535, default: 636 },
    { name: 'timeout', type: 'number', label: 'Тайм-аут, с', half: true, min: 1, max: 60, default: 5 },
    { name: 'verify_tls', type: 'switch', label: 'Проверять сертификат контроллера', default: true, hint: 'Выключайте только для диагностики: без проверки пароли можно перехватить.' },
    { name: 'ca_cert', type: 'textarea', label: 'Корневой сертификат (PEM)', mono: true, rows: 3, placeholder: '-----BEGIN CERTIFICATE-----', hint: 'Нужен, если сертификат контроллера выпущен внутренним центром сертификации.' },
    { type: 'section', label: 'Сервисная учётная запись', hint: 'Для поиска пользователей и групп. Без неё поиск выполняется правами входящего пользователя, а заранее завести доменного пользователя нельзя.' },
    { name: 'bind_dn', label: 'DN или UPN', mono: true, placeholder: 'CN=svc-mfa-gate,OU=Service,DC=corp,DC=example,DC=org' },
    { name: 'bind_password', label: 'Пароль', type: 'password', autocomplete: 'new-password', trim: false,
      hint: d?.has_bind_password ? 'Пароль сохранён. Оставьте пустым, чтобы не менять.' : 'Хранится зашифрованным и никогда не показывается.' },
    { type: 'section', label: 'Поиск пользователей' },
    { name: 'base_dn', label: 'Base DN', required: true, mono: true, placeholder: 'DC=corp,DC=example,DC=org' },
    { name: 'user_filter', label: 'Фильтр пользователей', mono: true },
    { name: 'login_attribute', label: 'Атрибут логина', mono: true, half: true },
    { name: 'id_attribute', label: 'Атрибут идентификатора', mono: true, half: true },
    { name: 'name_attribute', label: 'Атрибут имени', mono: true, half: true },
    { name: 'email_attribute', label: 'Атрибут почты', mono: true, half: true },
    { name: 'phone_attribute', label: 'Атрибут телефона', mono: true, half: true },
    { type: 'section', label: 'Доступ' },
    { name: 'allowed_groups', type: 'tags', label: 'Разрешённые группы', placeholder: 'VPN Users', hint: 'Пусто — входить могут все найденные пользователи. Можно указать CN или DN группы.' },
    { name: 'nested_groups', type: 'switch', label: 'Учитывать вложенные группы', default: true },
    { name: 'priority', type: 'number', label: 'Приоритет', half: true, min: 0, max: 1000, default: 10, hint: 'Меньше — раньше опрашивается.' },
    { name: 'enabled', type: 'switch', label: 'Каталог включён', default: true, half: true },
  ];
  formDialog({
    title: isNew ? 'Подключить каталог' : `Изменить: ${d.name}`, size: 'lg', fields, submitLabel: isNew ? 'Подключить' : 'Сохранить',
    values: d ? { ...d, ca_cert: d.ca_cert || '' } : { kind: 'ad', ...KIND_DEFAULTS.ad },
    onReady(f) {
      const c = f.controls;
      c.encryption.addEventListener('change', () => {
        const enc = c.encryption.getValue();
        if (['636', '389', ''].includes(c.port.value)) c.port.value = enc === 'ldaps' ? '636' : '389';
      });
      c.kind.addEventListener('change', () => {
        const next = KIND_DEFAULTS[c.kind.value], prev = KIND_DEFAULTS[c.kind.value === 'ad' ? 'openldap' : 'ad'];
        for (const [k, v] of Object.entries(next)) if (!c[k].value || c[k].value === prev[k]) c[k].value = v;
      });
    },
    async onSubmit(v) {
      const body = { ...v, ca_cert: v.ca_cert || null };
      if (!v.bind_password) delete body.bind_password;
      const res = isNew ? await post('/ldap', body) : await put(`/ldap/${d.id}`, body);
      toast(isNew ? 'Каталог подключён — запустите диагностику' : 'Изменения сохранены');
      reload();
      return res;
    },
  });
}
