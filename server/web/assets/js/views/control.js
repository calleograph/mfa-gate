// Раздел «Контроль»: политики, история входов, проблемы входа, журнал, диагностика.
import { get } from '../api.js';
import {
  h, icon, pageHead, panel, button, busy, statusBadge, statusIcon, chip, risk, table, cellMain, timeEl, empty, errorState, loadingRegion,
  pager, segmented, notice, num, fmtDate, plural, fill } from '../ui.js';
import { crudPage } from './crud.js';
import { REQUEST_STATUS, CHANNEL, METHOD, REASONS, POLICY_ACTION, SEVERITY, DIAG, AUDIT_EVENT, AUDIT_GROUPS } from '../labels.js';

// ------------------------------------------------------------------ политики
export function policiesPage(ctx) {
  return crudPage(ctx, {
    area: 'policies', endpoint: '/policies', title: 'Политики',
    desc: 'Правила проверяются по возрастанию приоритета, применяется первое совпавшее. Если ни одно не подошло, второй фактор обязателен.',
    createLabel: 'Создать политику', createTitle: 'Новая политика', createdText: 'Политика создана', formSize: 'lg',
    name: (p) => p.name,
    extras: async (c) => ({ resources: c.can('resources:read') ? (await get('/resources')).items : [] }),
    empty: { iconName: 'shield', title: 'Политик пока нет', text: 'Сейчас второй фактор требуется всем — это безопасно. Политики нужны для исключений: запретить вход группе, ограничить способы, разрешить вход без MFA для служебных сценариев.' },
    columns: ({ resources }) => [
      { label: 'Приоритет', cls: 'num', render: (p) => h('span', { class: 'td-mono', text: String(p.priority) }) },
      { label: 'Политика', primary: true, render: (p) => h('div', { class: 'cell-main' }, h('span', { class: 't1', text: p.name }), p.enabled ? null : h('span', { class: 't2', text: 'выключена — не применяется' })) },
      { label: 'Кому', render: (p) => h('div', { class: 'cell-main' },
        h('span', { text: p.resource_ids.length ? p.resource_ids.map((id) => resources.find((r) => r.id === id)?.name || 'ресурс').join(', ') : 'Все ресурсы' }),
        h('span', { class: 't2', text: [p.groups.length ? `группы: ${p.groups.join(', ')}` : 'все пользователи', p.exclude_groups.length ? `кроме: ${p.exclude_groups.join(', ')}` : ''].filter(Boolean).join('; ') })) },
      { label: 'Действие', render: (p) => { const [s, t] = POLICY_ACTION[p.action]; return h('div', { class: 'cell-main' }, statusBadge(p.enabled ? s : 'neutral', t), p.action === 'mfa' ? h('span', { class: 't2', text: p.methods.map((m) => METHOD[m] || m).join(', ') }) : null); } },
      { label: 'Если фактор недоступен', opt: true, render: (p) => (p.action !== 'mfa' ? h('span', { class: 'faint', text: 'не применяется' }) : p.on_unavailable === 'allow' ? risk('Пропустить по паролю') : 'Запретить вход') },
    ],
    fields: ({ resources }) => [
      { name: 'name', label: 'Название', required: true, half: true, placeholder: 'Подрядчики — только TOTP' },
      { name: 'priority', type: 'number', label: 'Приоритет', half: true, min: 0, max: 100000, default: 100, hint: 'Меньше — проверяется раньше.' },
      { name: 'resource_ids', type: 'checks', label: 'Ресурсы', options: resources.map((r) => ({ value: r.id, label: r.name })), hint: 'Ничего не выбрано — политика действует для всех ресурсов.', emptyText: 'Ресурсов пока нет — политика будет действовать для всех.' },
      { name: 'groups', type: 'tags', label: 'Только для групп', half: true, placeholder: 'VPN Users', hint: 'Пусто — для всех. Группы MFA Gate или каталога (CN либо DN).' },
      { name: 'exclude_groups', type: 'tags', label: 'Кроме групп', half: true, placeholder: 'Contractors', hint: 'Участники этих групп пропускают политику.' },
      { name: 'action', type: 'choices', label: 'Действие', default: 'mfa', options: [
        { value: 'mfa', label: 'Второй фактор', icon: 'shield-check', hint: 'Пароль и подтверждение на телефоне или TOTP-код.' },
        { value: 'deny', label: 'Запретить', icon: 'ban', hint: 'Вход отклоняется даже с верным паролем.' },
        { value: 'allow', label: 'Без MFA', icon: 'shield-alert', risk: true, hint: 'Риск: достаточно одного пароля. Только для служебных сценариев.' },
      ] },
      { name: 'methods', type: 'checks', label: 'Разрешённые способы', default: ['push', 'totp'], options: [
        { value: 'push', label: 'Push на телефон', hint: 'Подтверждение одним нажатием в приложении MFA Gate.' },
        { value: 'totp', label: 'TOTP-код', hint: 'Шесть цифр из приложения-аутентификатора, работает без интернета.' },
      ] },
      { name: 'on_unavailable', type: 'choices', label: 'Если у пользователя нет подходящего фактора', default: 'deny', options: [
        { value: 'deny', label: 'Запретить вход', icon: 'lock', hint: 'Рекомендуется.' },
        { value: 'allow', label: 'Пропустить по паролю', icon: 'shield-alert', risk: true, hint: 'Риск: пользователь без привязанного телефона или кода входит только по паролю. Неотвеченный запрос на телефон всё равно отклоняется.' },
      ] },
      { name: 'enabled', type: 'switch', label: 'Политика включена', default: true },
    ],
    onFormReady(f) {
      const sync = () => { const mfa = f.controls.action.getValue() === 'mfa'; f.show('methods', mfa); f.show('on_unavailable', mfa); };
      f.controls.action.addEventListener('change', sync);
      sync();
    },
    toBody(v, row, f) {
      if (v.action === 'mfa' && !v.methods?.length) { f.setErrors([{ field: 'methods', message: 'Выберите хотя бы один способ' }]); return false; }
      return { name: v.name, priority: v.priority ?? 100, enabled: v.enabled, resource_ids: v.resource_ids, groups: v.groups, exclude_groups: v.exclude_groups, action: v.action,
        methods: v.methods || row?.methods || ['push', 'totp'], on_unavailable: v.on_unavailable || row?.on_unavailable || 'deny' };
    },
  });
}

// ------------------------------------------------------------------ общий список с фильтрами и пагинацией
// переключатель «Все входы / Проблемы входа»: один раздел меню, два вида
const historyViews = (current) => segmented([{ value: '/history', label: 'Все входы' }, { value: '/problems', label: 'Проблемы входа' }],
  current, (v) => { location.hash = '#' + v; }, 'Вид истории входов');

async function filteredList(ctx, { title, desc, endpoint, filters, columns, emptyState, headActions, caption }) {
  const { main, query, setQuery, signal } = ctx;
  const SIZE = 50;
  const st = { page: Number(query.get('page')) || 1 };
  for (const f of filters) st[f.name] = query.get(f.name) || '';
  const head = pageHead({ title, desc, actions: headActions?.(st) });
  const results = h('div', { 'aria-live': 'polite' });
  const bar = h('form', { class: 'toolbar', role: 'search', novalidate: true });
  for (const f of filters) {
    const id = 'flt-' + f.name;
    let c;
    if (f.options) {
      c = h('select', { class: 'select', id }, f.options.map(([v, l]) => h('option', { value: v, text: l })));
      c.value = st[f.name];
      c.addEventListener('change', () => { st[f.name] = c.value; st.page = 1; load(); });
      bar.append(h('div', { class: `field ${f.span}` }, h('label', { for: id, text: f.label }), h('div', { class: 'select-wrap' }, c, icon('chevron-down'))));
    } else {
      c = h('input', { class: 'input' + (f.mono ? ' mono' : ''), id, type: 'search', value: st[f.name], placeholder: f.placeholder || '', autocomplete: 'off' });
      let t = 0;
      c.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { st[f.name] = c.value.trim(); st.page = 1; load(); }, 300); });
      bar.append(h('div', { class: `field ${f.span}` }, h('label', { for: id, text: f.label }), h('div', { class: 'search' }, icon('search'), c)));
    }
  }
  bar.addEventListener('submit', (e) => e.preventDefault());
  // на телефоне фильтры свёрнуты, чтобы первый экран занимали записи
  const activeCount = () => filters.filter((f) => st[f.name]).length;
  const toggle = button('Фильтры', { kind: 'secondary', size: 'sm', iconName: 'search' });
  toggle.classList.add('filters-toggle');
  toggle.setAttribute('aria-expanded', 'false');
  bar.id = 'flt-' + Math.random().toString(36).slice(2, 7);
  toggle.setAttribute('aria-controls', bar.id);
  const syncToggle = () => { const n = activeCount(); toggle.lastChild.textContent = n ? `Фильтры: ${n}` : 'Фильтры'; };
  bar.classList.add('is-collapsed');
  toggle.addEventListener('click', () => { const open = bar.classList.toggle('is-collapsed') === false; toggle.setAttribute('aria-expanded', String(open)); });
  syncToggle();
  head.addEventListener('filters', syncToggle);
  main.append(head, h('section', { class: 'panel' }, toggle, bar, results));

  async function load() {
    setQuery(st);
    head.dispatchEvent(new CustomEvent('filters', { detail: st }));
    fill(results, loadingRegion(6));
    const q = { page: st.page, size: SIZE };
    for (const f of filters) q[f.name] = st[f.name];
    let data;
    try { data = await get(endpoint, q, signal); } catch (err) {
      if (err.name === 'AbortError') return;
      fill(results, errorState(err, load));
      return;
    }
    const h1 = head.querySelector('h1');
    h1.querySelector('.count')?.remove();
    h1.append(h('span', { class: 'count', text: num(data.total) }));
    if (!data.items.length) {
      const filtered = filters.some((f) => st[f.name]);
      fill(results, empty(filtered ? { iconName: 'search', title: 'Ничего не нашлось', text: 'Измените или сбросьте фильтры.' } : emptyState));
      return;
    }
    fill(results, table({ caption, columns, rows: data.items }),
      data.total > SIZE ? pager({ page: st.page, size: SIZE, total: data.total, onPage: (p) => { st.page = p; load(); window.scrollTo(0, 0); } }) : null);
  }
  await load();
  return head;
}

const reqStatus = (r) => { const [s, t] = REQUEST_STATUS[r.status] || ['neutral', r.status]; return h('div', { class: 'cell-main' }, statusBadge(s, t), r.reason_text ? h('span', { class: 't2', text: r.reason_text }) : null); };

export function historyPage(ctx) {
  return filteredList(ctx, {
    title: 'История входов', caption: 'История входов', endpoint: '/auth-requests',
    headActions: () => [historyViews('/history')],
    desc: 'Каждая проверка входа через RADIUS, API и в эту панель: кто, куда, чем закончилось и почему.',
    filters: [
      { name: 'login', label: 'Пользователь', placeholder: 'Логин', span: 'tb-3', mono: true },
      { name: 'status', label: 'Результат', span: 'tb-3', options: [['', 'Любой'], ...Object.entries(REQUEST_STATUS).map(([k, [, l]]) => [k, l])] },
      { name: 'channel', label: 'Канал', span: 'tb-3', options: [['', 'Любой'], ...Object.entries(CHANNEL)] },
      { name: 'reason', label: 'Причина отказа', span: 'tb-3', options: [['', 'Любая'], ...Object.entries(REASONS)] },
    ],
    emptyState: { iconName: 'history', title: 'Входов пока не было', text: 'Здесь появятся запросы от VPN-шлюзов, интеграций и входы в панель.' },
    columns: [
      { label: 'Пользователь', primary: true, render: (r) => cellMain(r.login, r.resource || r.client || '') },
      { label: 'Результат', render: reqStatus },
      { label: 'Канал', render: (r) => [CHANNEL[r.channel] || r.channel, r.method ? METHOD[r.method] || r.method : null].filter(Boolean).join(', ') },
      { label: 'Адрес', opt: true, render: (r) => (r.source_ip ? h('span', { class: 'td-mono', text: r.source_ip }) : null) },
      { label: 'Время', render: (r) => h('div', { class: 'cell-main' }, h('span', { class: 't1 nowrap', text: fmtDate(r.created_at) }), r.latency_ms !== null ? h('span', { class: 't2', text: `ответ за ${num(r.latency_ms)} мс` }) : null) },
    ],
  });
}

// ------------------------------------------------------------------ проблемы входа
// [что сделать, куда перейти, подпись кнопки]; 'user' — поиск пользователя по логину
const FIX = {
  no_factor: ['Привяжите телефон или выпустите TOTP в карточке пользователя.', 'user', 'Найти пользователя'],
  locked: ['Снимите блокировку в карточке пользователя, если попытки были его.', 'user', 'Найти пользователя'],
  directory_unavailable: ['Запустите диагностику каталога: DNS, порт, TLS.', '#/ldap', 'К каталогам'],
  directory_denied: ['Учётная запись заблокирована в каталоге, пароль истёк или нет нужной группы.', '#/ldap', 'К каталогам'],
  ldap_password: ['Пароль в каталоге неверен или истёк — пусть пользователь проверит его в домене.', null],
  local_password: ['Неверный пароль. Задайте новый в карточке, если пользователь его забыл.', 'user', 'Найти пользователя'],
  unknown_user: ['Проверьте написание логина или заведите пользователя.', '#/users', 'К пользователям'],
  user_disabled: ['Учётная запись отключена. Включите её в карточке, если это ошибка.', 'user', 'Найти пользователя'],
  policy_deny: ['Вход запрещён политикой — проверьте, какая политика сработала.', '#/policies', 'К политикам'],
  invalid_otp: ['Проверьте время на телефоне пользователя; при повторах выпустите TOTP заново.', 'user', 'Найти пользователя'],
  push_denied: ['Пользователь отклонил запрос. Если он не входил сам — его пароль, вероятно, известен постороннему: смените пароль.', 'user', 'Найти пользователя'],
  push_expired: ['Телефон не ответил: проверьте интернет на нём и доставку push в диагностике.', '#/diagnostics', 'К диагностике'],
  resource_disabled: ['Ресурс выключен — включите его, если доступ нужен.', '#/resources', 'К ресурсам'],
};

export async function problemsPage(ctx) {
  const { main, query, setQuery, signal, can } = ctx;
  let days = Number(query.get('days')) || 7;
  const head = pageHead({ title: 'Проблемы входа', desc: 'Отказы и истёкшие запросы, сгруппированные по пользователю и причине. С этого списка удобно начинать разбор обращений «не могу войти».',
    actions: [historyViews('/problems')] });
  const results = h('div', { 'aria-live': 'polite' });
  const bar = h('div', { class: 'panel-head' }, h('h2', { text: 'За период' }),
    segmented([1, 7, 30, 90].map((d) => ({ value: d, label: d === 1 ? 'Сутки' : `${d} ${plural(d, ['день', 'дня', 'дней'])}` })), days, (v) => { days = v; load(); }, 'Период'));
  main.append(head, h('section', { class: 'panel' }, bar, results));
  async function load() {
    setQuery({ days: days === 7 ? '' : days });
    fill(results, loadingRegion(5));
    let data;
    try { data = await get('/auth-requests/problems', { days }, signal); } catch (err) {
      if (err.name === 'AbortError') return;
      fill(results, errorState(err, load));
      return;
    }
    // отклонённый на телефоне push — признак утечки пароля: такие строки первыми
    data.items.sort((x, y) => (y.reason === 'push_denied') - (x.reason === 'push_denied'));
    if (!data.items.length) { fill(results, empty({ iconName: 'shield-check', title: 'Проблем не найдено', text: 'За выбранный период не было отказов и истёкших запросов.' })); return; }
    fill(results, table({
      caption: 'Проблемы входа',
      columns: [
        { label: 'Пользователь', primary: true, render: (r) => h('div', { class: 'cell-main' }, h('span', null, h('a', { class: 't1', href: `#/history?login=${encodeURIComponent(r.login)}${r.reason ? '&reason=' + encodeURIComponent(r.reason) : ''}`, text: r.login }))) },
        { label: 'Причина', render: (r) => h('div', { class: 'cell-main' }, r.reason === 'push_denied' ? risk('Возможна утечка пароля') : null, h('span', { class: 't1', text: r.reason_text || REASONS[r.reason] || 'Без причины' }), FIX[r.reason] ? h('span', { class: 'fix', text: FIX[r.reason][0] }) : null) },
        { label: 'Попыток', cls: 'num', render: (r) => num(r.count) },
        { label: 'Последняя', render: (r) => timeEl(r.last) },
        { cls: 'act', render: (r) => { const [, href, label] = FIX[r.reason] || []; if (!href) return null; if (href === 'user') return can('users:read') ? button(label, { size: 'sm', kind: 'secondary', href: `#/users?q=${encodeURIComponent(r.login)}`, ariaLabel: `${label}: ${r.login}` }) : null; return button(label, { size: 'sm', kind: 'secondary', href }); } },
      ],
      rows: data.items,
    }));
  }
  await load();
}

// ------------------------------------------------------------------ журнал
export async function auditPage(ctx) {
  const exportBtn = button('Скачать CSV', { iconName: 'download', href: '/api/v1/audit/export.csv' });
  exportBtn.setAttribute('download', 'mfa-gate-audit.csv');
  const head = await filteredList(ctx, {
    title: 'Журнал', caption: 'Журнал событий', endpoint: '/audit',
    desc: 'Все действия администраторов и важные события системы. Записи не редактируются и не удаляются из панели; по истечении срока хранения (по умолчанию 365 дней) удаляются автоматически.',
    headActions: () => [exportBtn],
    filters: [
      { name: 'event', label: 'Событие', span: 'tb-3', options: AUDIT_GROUPS },
      { name: 'actor', label: 'Кто', placeholder: 'Логин или ключ', span: 'tb-3', mono: true },
      { name: 'severity', label: 'Важность', span: 'tb-3', options: [['', 'Любая'], ...Object.entries(SEVERITY).map(([k, [, l]]) => [k, l])] },
      { name: 'result', label: 'Результат', span: 'tb-3', options: [['', 'Любой'], ['ok', 'Успешно'], ['fail', 'Ошибка']] },
    ],
    emptyState: { iconName: 'scroll-text', title: 'Записей пока нет', text: 'Журнал заполняется при входах в панель и изменениях настроек.' },
    columns: [
      { label: 'Событие', primary: true, render: (a) => cellMain(AUDIT_EVENT[a.event] || a.event, a.event, { mono: true }) },
      { label: 'Важность', render: (a) => { const [s, t] = SEVERITY[a.severity] || ['neutral', a.severity]; return statusBadge(s, t); } },
      { label: 'Кто', render: (a) => h('div', { class: 'cell-main' }, h('span', { class: 't1', text: a.actor || 'система' }), a.ip ? h('span', { class: 't2 mono', text: a.ip }) : null) },
      { label: 'Объект', render: (a) => (a.target ? h('span', { class: 'break', text: a.target }) : null) },
      { label: 'Итог', opt: true, render: (a) => (a.result === 'ok' ? statusBadge('ok', 'Успешно', 'plain') : statusBadge('error', 'Ошибка', 'plain')) },
      { label: 'Подробности', opt: true, render: (a) => details(a.details) },
      { label: 'Время', render: (a) => { const [d, t] = fmtDate(a.at).split(', '); return h('div', { class: 'cell-main' }, h('span', { class: 'nowrap', text: d }), h('span', { class: 't2', text: t })); } },
    ],
  });
  // ссылка выгрузки повторяет текущие фильтры
  head.addEventListener('filters', (e) => {
    const qs = new URLSearchParams(Object.entries(e.detail).filter(([k, v]) => v && k !== 'page'));
    exportBtn.href = '/api/v1/audit/export.csv' + ([...qs].length ? '?' + qs : '');
  });
  const qs = new URLSearchParams(Object.entries(Object.fromEntries(ctx.query)).filter(([k, v]) => v && k !== 'page'));
  exportBtn.href = '/api/v1/audit/export.csv' + ([...qs].length ? '?' + qs : '');
}
function details(d) {
  if (!d || typeof d !== 'object') return null;
  const entries = Object.entries(d).filter(([, v]) => v !== null && v !== '' && !(Array.isArray(v) && !v.length));
  if (!entries.length) return null;
  return h('div', { class: 'details' }, entries.slice(0, 4).map(([k, v]) => h('div', { text: `${k}: ${Array.isArray(v) ? v.join(', ') : typeof v === 'object' ? JSON.stringify(v) : v}` })));
}

// ------------------------------------------------------------------ диагностика
export async function diagnosticsPage(ctx) {
  const { main, signal } = ctx;
  const again = button('Проверить снова', { iconName: 'refresh-cw' });
  const head = pageHead({ title: 'Диагностика', desc: 'Состояние компонентов MFA Gate: база данных, миграции, ключ шифрования, RADIUS, доставка push, каталоги.', actions: [again] });
  const out = h('div', { class: 'stack', 'aria-live': 'polite' }, h('div', { class: 'panel' }, loadingRegion(6)));
  main.append(head, out);
  async function load() {
    let r;
    try { r = await get('/system/diagnostics', null, signal); } catch (err) {
      if (err.name === 'AbortError') return;
      fill(out, h('div', { class: 'panel' }, errorState(err, () => busy(again, load))));
      return;
    }
    const counts = { ok: 0, warning: 0, error: 0 };
    r.checks.forEach((c) => { counts[c.status] = (counts[c.status] || 0) + 1; });
    const order = { error: 0, warning: 1, ok: 2 };
    const checks = [...r.checks].sort((a, b) => order[a.status] - order[b.status]);
    fill(out, 
      notice(r.status, h('span', null, h('strong', { text: r.status === 'ok' ? 'Все проверки пройдены. ' : r.status === 'warning' ? 'Есть предупреждения. ' : 'Есть ошибки. ' }),
        `В порядке: ${counts.ok}, предупреждений: ${counts.warning}, ошибок: ${counts.error}. Версия ${r.version}, проверено ${fmtDate(r.checked_at)}.`)),
      panel({
        title: 'Проверки', count: r.checks.length,
        body: h('ul', { class: 'checklist' }, checks.map((c) => h('li', null,
          h('span', { class: 'ck-ic', 'data-s': c.status }, statusIcon(c.status), h('span', { class: 'sr-only', text: DIAG[c.status] || c.status })),
          h('span', { class: 'ck-title', text: c.name }),
          c.status !== 'ok' && c.name.startsWith('Каталог') && ctx.can('ldap:read') ? button('Открыть каталог', { size: 'sm', kind: 'secondary', href: '#/ldap' }) : h('span'),
          h('p', { class: 'ck-msg', text: c.message }),
          c.action ? h('p', { class: 'ck-act' }, icon('chevron-right'), h('span', { text: c.action })) : null))),
      }));
  }
  again.addEventListener('click', () => busy(again, load));
  await load();
}
