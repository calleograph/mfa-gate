// Пользователи: список с поиском и фильтром источника, карточка с устройствами, TOTP и последними входами.
import { get, post, patch, del } from '../api.js';
import {
  h, icon, pageHead, panel, button, iconButton, busy, statusBadge, chip, table, cellMain, timeEl, empty, errorState, loadingRegion,
  pager, segmented, actionMenu, toast, toastError, formDialog, confirmDialog, modal, notice, qrImage, secretBox, fmtDate, num, plural, clear, fill } from '../ui.js';
import { REQUEST_STATUS, CHANNEL, METHOD, SOURCE, ROLES } from '../labels.js';

const canRoles = (can) => can('admins:write');
const userStatus = (u) => (u.status === 'disabled' ? statusBadge('neutral', 'Отключён') : u.locked_until ? statusBadge('warning', 'Заблокирован') : statusBadge('ok', 'Активен'));
const factorsCell = (u) => {
  if (!u.devices && !u.totp) return statusBadge('warning', 'Нет фактора', 'plain');
  return h('div', { class: 'chips' }, u.devices ? chip(`Телефон ${u.devices}`) : null, u.totp ? chip(`TOTP ${u.totp}`) : null);
};

// ------------------------------------------------------------------ список
export async function usersList(ctx) {
  const { main, query, can, setQuery, signal } = ctx;
  const st = { q: query.get('q') || '', source: query.get('source') || '', factor: query.get('factor') || '', page: Number(query.get('page')) || 1 };
  const SIZE = 50;
  const head = pageHead({
    title: 'Пользователи',
    actions: [can('users:write') ? button('Добавить пользователя', { kind: 'primary', iconName: 'user-plus', onClick: () => createUser(ctx) }) : null],
  });
  const searchInput = h('input', { class: 'input', type: 'search', id: 'u-q', placeholder: 'Логин, имя или почта', value: st.q, autocomplete: 'off' });
  const toolbar = h('div', { class: 'toolbar' },
    h('div', { class: 'field tb-4' }, h('label', { for: 'u-q', text: 'Поиск' }), h('div', { class: 'search' }, icon('search'), searchInput)),
    h('div', { class: 'field tb-4' }, h('span', { class: 'label', text: 'Источник' }),
      segmented([{ value: '', label: 'Все' }, { value: 'local', label: 'Локальные' }, { value: 'ldap', label: 'Из каталога' }], st.source, (v) => { st.source = v; st.page = 1; load(); }, 'Источник учётной записи')),
    h('div', { class: 'field tb-4' }, h('span', { class: 'label', text: 'Второй фактор' }),
      segmented([{ value: '', label: 'Любой' }, { value: 'none', label: 'Нет фактора' }], st.factor, (v) => { st.factor = v; st.page = 1; load(); }, 'Наличие второго фактора')));
  const results = h('div', { 'aria-live': 'polite' });
  const countEl = head.querySelector('h1');
  main.append(head, h('section', { class: 'panel' }, toolbar, results));

  let t = 0;
  searchInput.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { st.q = searchInput.value.trim(); st.page = 1; load(); }, 300); });

  async function load() {
    setQuery({ q: st.q, source: st.source, factor: st.factor, page: st.page });
    fill(results, loadingRegion(6));
    let data, partial = false;
    try {
      // shortcut: API не умеет фильтровать по наличию фактора — фильтруем первые 200 записей в браузере; нужен ?factor=none на сервере
      if (st.factor === 'none') {
        const all = await get('/users', { q: st.q, source: st.source, page: 1, size: 200 }, signal);
        partial = all.total > 200;
        const items = all.items.filter((u) => !u.devices && !u.totp);
        data = { items, total: items.length };
      } else data = await get('/users', { q: st.q, source: st.source, page: st.page, size: SIZE }, signal);
    } catch (err) {
      if (err.name === 'AbortError') return;
      fill(results, errorState(err, load));
      return;
    }
    countEl.querySelector('.count')?.remove();
    countEl.append(h('span', { class: 'count', text: num(data.total) }));
    if (!data.items.length) {
      const filtered = st.q || st.source || st.factor;
      fill(results, empty(filtered
        ? { iconName: 'search', title: 'Никого не нашли', text: 'Измените запрос или сбросьте фильтр источника.' }
        : { iconName: 'users', title: 'Пользователей пока нет', text: 'Добавьте локального пользователя или заведите доменного — данные подтянутся из каталога.', action: can('users:write') ? button('Добавить пользователя', { kind: 'primary', iconName: 'user-plus', onClick: () => createUser(ctx) }) : null }));
      return;
    }
    fill(results, table({
      caption: 'Пользователи',
      columns: [
        { label: 'Пользователь', primary: true, render: (u) => cellMain(u.display_name || u.login, u.display_name ? u.login : null, { href: `#/users/${u.id}`, mono: true }) },
        { label: 'Источник', render: (u) => SOURCE[u.source] || u.source },
        { label: 'Группы', opt: true, render: (u) => (u.groups.length ? h('div', { class: 'chips' }, u.groups.slice(0, 3).map((g) => chip(g.name)), u.groups.length > 3 ? chip(`+${u.groups.length - 3}`) : null) : null) },
        { label: 'Второй фактор', render: factorsCell },
        { label: 'Статус', render: (u) => h('div', { class: 'chips' }, userStatus(u), u.admin_role ? chip(ROLES[u.admin_role] || u.admin_role, 'role') : null) },
        { label: 'Последний вход', opt: 2, render: (u) => timeEl(u.last_login_at) },
        { cls: 'act', render: (u) => rowMenu(ctx, u, load) },
      ],
      rows: data.items,
    }), partial ? h('div', { class: 'panel-foot' }, notice('warning', 'Фильтр «Нет фактора» применён к первым 200 пользователям: сервер пока не умеет фильтровать по наличию фактора. Уточните поиск, чтобы увидеть остальных.')) : null,
    !st.factor && data.total > SIZE ? pager({ page: st.page, size: SIZE, total: data.total, onPage: (p) => { st.page = p; load(); } }) : null);
  }
  await load();
}

function rowMenu(ctx, u, reload) {
  const { can, navigate } = ctx;
  if (!can('users:write') && !can('devices:write')) return null;   // только «открыть» — это уже делает ссылка в строке
  return actionMenu([
    { label: 'Открыть карточку', icon: 'chevron-right', onSelect: () => navigate(`#/users/${u.id}`) },
    can('devices:write') ? { label: 'Привязать телефон', icon: 'qr-code', onSelect: () => enrollPhone(u) } : null,
    can('users:write') && u.locked_until ? { label: 'Снять блокировку', icon: 'lock-open', onSelect: () => unlock(u, reload) } : null,
    can('users:write') ? (u.status === 'active'
      ? { label: 'Отключить учётную запись', icon: 'ban', danger: true, onSelect: () => setStatus(u, 'disabled', reload) }
      : { label: 'Включить учётную запись', icon: 'circle-check', onSelect: () => setStatus(u, 'active', reload) }) : null,
  ], `Действия: ${u.login}`);
}

// ------------------------------------------------------------------ действия
async function groupOptions(can) {
  if (!can('groups:read')) return null;
  try { return (await get('/groups')).items.map((g) => ({ value: g.id, label: g.name, hint: g.directory_group ? `Каталог: ${g.directory_group}` : '' })); } catch { return null; }
}
const roleOptions = [{ value: '', label: 'Нет — обычный пользователь' }, ...Object.entries(ROLES).map(([value, label]) => ({ value, label }))];

async function createUser(ctx) {
  const { can, navigate } = ctx;
  const groups = await groupOptions(can);
  const fields = [
    { name: 'source', type: 'choices', label: 'Источник учётной записи', default: 'local', options: [
      { value: 'local', label: 'Локальный', icon: 'key', hint: 'Пароль хранится в MFA Gate.' },
      { value: 'ldap', label: 'Из каталога', icon: 'network', hint: 'Имя, почта, телефон и группы подтянутся из LDAP/AD.' },
    ] },
    { name: 'login', label: 'Логин', required: true, mono: true, half: true, hint: 'Без пробелов. Для каталога — как sAMAccountName или UPN.' },
    { name: 'display_name', label: 'Имя', half: true, hint: 'Для каталога можно не заполнять.' },
    { name: 'email', label: 'Почта', type: 'email', half: true },
    { name: 'phone', label: 'Телефон', type: 'tel', half: true },
    { name: 'password', label: 'Пароль', type: 'password', autocomplete: 'new-password', trim: false, hint: 'Не короче 12 символов. Только для локальных пользователей.' },
    groups ? { name: 'group_ids', type: 'checks', label: 'Группы', options: groups, emptyText: 'Групп пока нет — их можно создать в разделе «Группы».' } : null,
    canRoles(can) ? { name: 'admin_role', type: 'select', label: 'Роль в панели управления', options: roleOptions, hint: 'Администратор обязан подключить второй фактор при первом входе.' } : null,
  ].filter(Boolean);
  formDialog({
    title: 'Новый пользователь', size: 'lg', fields, submitLabel: 'Создать',
    onReady: (f) => {
      const sync = () => {
        const ldap = f.controls.source.getValue() === 'ldap';
        f.show('password', !ldap);
      };
      f.controls.source.addEventListener('change', sync);
      sync();
    },
    async onSubmit(v) {
      const body = { login: v.login, source: v.source, display_name: v.display_name || '', email: v.email || null, phone: v.phone || null, group_ids: v.group_ids || [] };
      if (v.source === 'local') body.password = v.password || null;
      if (v.admin_role) body.admin_role = v.admin_role;
      const u = await post('/users', body);
      toast(`Пользователь ${u.login} создан`);
      navigate(`#/users/${u.id}`);
    },
  });
}

async function setStatus(u, status, reload) {
  if (status === 'disabled') {
    const ok = await confirmDialog({
      title: 'Отключить учётную запись?', target: u.display_name ? `${u.login} — ${u.display_name}` : u.login, danger: true, confirmLabel: 'Отключить',
      text: 'Пользователь не сможет войти ни через VPN, ни в панель. Устройства и TOTP сохранятся — учётную запись можно включить обратно.',
      action: () => patch(`/users/${u.id}`, { status }),
    });
    if (!ok) return;
    toast('Учётная запись отключена');
  } else {
    try { await patch(`/users/${u.id}`, { status }); toast('Учётная запись включена'); } catch (err) { return toastError(err); }
  }
  reload();
}
async function unlock(u, reload) {
  try { await post(`/users/${u.id}/unlock`); toast('Блокировка снята'); reload(); } catch (err) { toastError(err); }
}

export async function enrollPhone(u) {
  let data;
  try { data = await post(`/users/${u.id}/enrollment`); } catch (err) { return toastError(err); }
  const done = button('Готово', { kind: 'primary' });
  const m = modal({
    title: 'Привязка телефона', subtitle: u.display_name ? `${u.display_name} (${u.login})` : u.login, size: 'lg',
    body: [
      h('div', { class: 'qr-wrap' }, qrImage(data.qr_svg, 'QR-код привязки телефона'),
        h('div', { class: 'qr-side' },
          h('ol', null,
            h('li', { text: 'Установите приложение MFA Gate на телефон пользователя.' }),
            h('li', { text: 'Нажмите «Привязать» и отсканируйте этот код.' }),
            h('li', { text: 'Телефон появится в карточке пользователя в разделе «Телефоны».' })),
          notice('warning', `Код одноразовый и действует до ${fmtDate(data.expires_at)}. Не пересылайте его по почте и в мессенджерах — по нему можно привязать чужой телефон.`))),
      h('details', null, h('summary', { class: 'hint', text: 'Ссылка привязки для ручного ввода' }), secretBox(data.uri, 'Скопировать ссылку привязки')),
    ],
    actions: [done],
  });
  done.addEventListener('click', () => m.close());
}

export async function issueTotp(u, onDone) {
  let data;
  try { data = await post(`/users/${u.id}/totp`); } catch (err) { return toastError(err); }
  const grouped = data.secret.replace(/(.{4})/g, '$1 ').trim();
  formDialog({
    title: 'Выпуск TOTP', subtitle: u.display_name ? `${u.display_name} (${u.login})` : u.login, size: 'lg', submitLabel: 'Подтвердить',
    fields: [
      { type: 'node', node: h('div', { class: 'qr-wrap' }, qrImage(data.qr_svg, 'QR-код TOTP'),
        h('div', { class: 'qr-side' },
          h('ol', null,
            h('li', { text: 'Отсканируйте код в приложении-аутентификаторе или введите секрет вручную.' }),
            h('li', { text: 'Введите шестизначный код, который покажет приложение.' })),
          secretBox(grouped, 'Скопировать секрет'))) },
      { name: 'code', label: 'Код из приложения', required: true, mono: true, inputmode: 'numeric', autocomplete: 'one-time-code', half: true },
      { name: 'name', label: 'Название', default: 'Аутентификатор', half: true, hint: 'Чтобы отличать несколько TOTP.' },
    ],
    async onSubmit(v) {
      await post(`/users/${u.id}/totp/${data.factor_id}/confirm`, { code: v.code.replace(/\s+/g, ''), name: v.name || 'Аутентификатор' });
      toast('TOTP подключён');
      onDone?.();
    },
  });
}

// ------------------------------------------------------------------ карточка
export async function userCard(ctx) {
  const { main, params, can, signal, navigate } = ctx;
  main.append(h('div', { class: 'panel' }, loadingRegion(4)));
  const u = await get(`/users/${encodeURIComponent(params.id)}`, null, signal);
  const reload = () => ctx.reload();
  const fromList = /^#\/users(\?|$)/.test(ctx.prev || '');
  const name = u.display_name || u.login;

  const actions = [
    can('devices:write') ? button('Привязать телефон', { kind: 'primary', iconName: 'qr-code', onClick: () => enrollPhone(u) }) : null,
    can('users:write') ? button('Изменить', { iconName: 'pencil', onClick: () => editUser(ctx, u) }) : null,
    can('users:write') ? actionMenu([
      u.locked_until ? { label: 'Снять блокировку', icon: 'lock-open', onSelect: () => unlock(u, reload) } : null,
      u.status === 'active'
        ? { label: 'Отключить учётную запись', icon: 'ban', onSelect: () => setStatus(u, 'disabled', reload) }
        : { label: 'Включить учётную запись', icon: 'circle-check', onSelect: () => setStatus(u, 'active', reload) },
      '-',
      { label: 'Удалить пользователя', icon: 'trash-2', danger: true, onSelect: async () => {
        const ok = await confirmDialog({ title: 'Удалить пользователя?', target: u.display_name ? `${u.login} — ${u.display_name}` : u.login, danger: true, confirmLabel: 'Удалить',
          text: 'Будут удалены учётная запись, привязанные телефоны и TOTP. История входов и журнал сохранятся. Действие нельзя отменить.',
          action: () => del(`/users/${u.id}`) });
        if (ok) { toast(`Пользователь ${u.login} удалён`); navigate('#/users'); }
      } },
    ], 'Другие действия') : null,
  ];

  const head = pageHead({
    title: name,
    back: { href: '#/users', label: 'Пользователи', onClick: fromList ? (e) => { e.preventDefault(); history.back(); } : null },
    meta: [h('span', { class: 'chip mono', text: u.login }), userStatus(u), chip(SOURCE[u.source] || u.source), u.admin_role ? chip(u.admin_role_label || ROLES[u.admin_role], 'role') : null],
    actions,
  });

  const activeDevices = u.device_list.filter((d) => d.status === 'active');
  const noFactor = !activeDevices.length && !u.totp_list.some((t) => t.status === 'active');
  const warn = [];
  if (u.status === 'disabled') warn.push(notice('neutral', 'Учётная запись отключена: вход запрещён по всем каналам.'));
  if (u.locked_until) warn.push(notice('warning', `Временно заблокирован после неудачных попыток входа до ${fmtDate(u.locked_until)}.`,
    can('users:write') ? button('Снять блокировку', { size: 'sm', iconName: 'lock-open', onClick: () => unlock(u, reload) }) : null));
  if (noFactor) warn.push(notice('warning', 'Нет второго фактора: пользователь не сможет войти, пока не привяжет телефон или TOTP.'));

  const devices = panel({
    title: 'Телефоны', count: activeDevices.length,
    body: u.device_list.length ? h('ul', { class: 'list-rows' }, u.device_list.map((d) => h('li', null,
      h('div', { class: 'cell-main' }, h('span', { class: 't1', text: d.name }),
        h('span', { class: 't2', text: [d.platform, d.app_version && `версия ${d.app_version}`, d.push_provider === 'none' ? 'без push-канала' : `push: ${d.push_provider}`].filter(Boolean).join(', ') }),
        h('span', { class: 't2' }, d.status === 'active' ? statusBadge('ok', 'Активен', 'plain') : statusBadge('neutral', 'Отозван', 'plain'), ' ', 'на связи ', timeEl(d.last_seen_at))),
      d.status === 'active' && can('devices:write') ? button('Отозвать', { kind: 'danger-ghost', size: 'sm', onClick: () => revokeDevice(d, reload) }) : h('span')))) :
      empty({ iconName: 'smartphone', title: 'Телефон не привязан', text: 'Нажмите «Привязать телефон» и покажите пользователю QR-код — подтверждать входы можно будет одним нажатием.', compact: true }),
  });

  const totps = panel({
    title: 'TOTP', count: u.totp_list.filter((t) => t.status === 'active').length,
    actions: can('devices:write') ? [button('Выпустить', { size: 'sm', iconName: 'plus', onClick: () => issueTotp(u, reload) })] : null,
    body: u.totp_list.length ? h('ul', { class: 'list-rows' }, u.totp_list.map((t) => h('li', null,
      h('div', { class: 'cell-main' }, h('span', { class: 't1', text: t.name }),
        h('span', { class: 't2' }, t.status === 'active' ? statusBadge('ok', 'Подключён', 'plain') : statusBadge('pending', 'Не подтверждён', 'plain'), ' ', 'использован ', timeEl(t.last_used_at))),
      can('devices:write') ? button('Удалить', { kind: 'danger-ghost', size: 'sm', onClick: async () => {
        const ok = await confirmDialog({ title: `Удалить TOTP «${t.name}»?`, danger: true, confirmLabel: 'Удалить', text: 'Коды из этого приложения перестанут приниматься.', action: () => del(`/users/${u.id}/totp/${t.id}`) });
        if (ok) { toast('TOTP удалён'); reload(); }
      } }) : h('span')))) :
      empty({ iconName: 'key', title: 'TOTP не выпущен', text: 'Запасной способ: коды из приложения-аутентификатора работают без интернета.', compact: true }),
  });

  const requests = panel({
    title: 'Последние входы',
    actions: can('requests:read') ? [button('Вся история', { kind: 'ghost', size: 'sm', href: `#/history?login=${encodeURIComponent(u.login)}` })] : null,
    body: u.requests.length ? table({
      caption: 'Последние входы пользователя',
      columns: [
        { label: 'Когда', primary: true, render: (r) => h('div', { class: 'cell-main' }, h('span', { class: 't1', text: fmtDate(r.created_at) }), h('span', { class: 't2', text: r.resource || r.client || '' })) },
        { label: 'Результат', render: (r) => { const [s, t] = REQUEST_STATUS[r.status] || ['neutral', r.status]; return h('div', { class: 'cell-main' }, statusBadge(s, t), r.reason_text ? h('span', { class: 't2', text: r.reason_text }) : null); } },
        { label: 'Канал', opt: true, render: (r) => [CHANNEL[r.channel] || r.channel, r.method ? `, ${METHOD[r.method] || r.method}` : ''].join('') },
        { label: 'Адрес', opt: true, render: (r) => (r.source_ip ? h('span', { class: 'td-mono', text: r.source_ip }) : null) },
      ],
      rows: u.requests,
    }) : empty({ iconName: 'history', title: 'Входов ещё не было', compact: true }),
  });

  const profile = panel({
    title: 'Профиль',
    body: h('div', { class: 'panel-body' }, h('dl', { class: 'kv' },
      h('dt', { text: 'Логин' }), h('dd', null, h('span', { class: 'mono', text: u.login })),
      h('dt', { text: 'Почта' }), h('dd', { text: u.email || '—' }),
      h('dt', { text: 'Телефон' }), h('dd', { text: u.phone || '—' }),
      h('dt', { text: 'Источник' }), h('dd', { text: SOURCE[u.source] || u.source }),
      h('dt', { text: 'Роль' }), h('dd', { text: u.admin_role_label || 'Нет доступа к панели' }),
      h('dt', { text: 'Группы' }), h('dd', null, u.groups.length ? h('div', { class: 'chips' }, u.groups.map((g) => chip(g.name))) : '—'),
      u.source === 'ldap' ? [h('dt', { text: 'Группы каталога' }), h('dd', null, u.directory_groups.length ? h('div', { class: 'chips' }, u.directory_groups.filter((g) => !g.includes('=')).slice(0, 12).map((g) => chip(g))) : '—')] : null,
      h('dt', { text: 'Последний вход' }), h('dd', { text: fmtDate(u.last_login_at) }),
      h('dt', { text: 'Создан' }), h('dd', { text: fmtDate(u.created_at) }))),
  });

  clear(main).append(head, h('div', { class: 'grid' },
    warn.length ? h('div', { class: 'c-12 stack' }, warn) : null,
    h('div', { class: 'c-8 stack' }, h('div', { class: 'grid stretch' }, h('div', { class: 'c-6 stack' }, devices), h('div', { class: 'c-6 stack' }, totps)), requests),
    h('div', { class: 'c-4 stack' }, profile)));
}

async function revokeDevice(d, reload) {
  const ok = await confirmDialog({
    title: `Отозвать «${d.name}»?`, danger: true, confirmLabel: 'Отозвать',
    text: 'Телефон перестанет получать запросы и не сможет подтверждать входы. Чтобы вернуть его, понадобится новая привязка.',
    action: () => post(`/devices/${d.id}/revoke`),
  });
  if (ok) { toast('Устройство отозвано'); reload(); }
}
export { revokeDevice };

async function editUser(ctx, u) {
  const { can } = ctx;
  const groups = await groupOptions(can);
  const local = u.source === 'local';
  const fields = [
    { name: 'display_name', label: 'Имя', half: true },
    { name: 'email', label: 'Почта', type: 'email', half: true },
    { name: 'phone', label: 'Телефон', type: 'tel', half: true },
    { name: 'status', type: 'select', label: 'Состояние', half: true, options: [{ value: 'active', label: 'Активен' }, { value: 'disabled', label: 'Отключён — вход запрещён' }] },
    groups ? { name: 'group_ids', type: 'checks', label: 'Группы', options: groups, emptyText: 'Групп пока нет.' } : null,
    canRoles(can) ? { name: 'admin_role', type: 'select', label: 'Роль в панели управления', options: roleOptions } : null,
    local ? { name: 'password', label: 'Новый пароль', type: 'password', autocomplete: 'new-password', trim: false, hint: 'Оставьте пустым, чтобы не менять. Не короче 12 символов.' } : null,
    !local ? { type: 'node', node: notice('info', 'Пароль доменного пользователя меняется в каталоге. Имя, почта и телефон обновятся из каталога при следующем входе.') } : null,
  ].filter(Boolean);
  formDialog({
    title: 'Изменить пользователя', subtitle: u.login, size: 'lg', fields,
    values: { ...u, group_ids: u.groups.map((g) => g.id), admin_role: u.admin_role || '' },
    async onSubmit(v) {
      const body = { display_name: v.display_name, email: v.email || null, phone: v.phone || null, status: v.status };
      if (v.group_ids) body.group_ids = v.group_ids;
      if (canRoles(can) && (v.admin_role || '') !== (u.admin_role || '')) body.admin_role = v.admin_role;
      if (v.password) body.password = v.password;
      await patch(`/users/${u.id}`, body);
      toast('Изменения сохранены');
      ctx.reload();
    },
  });
}
