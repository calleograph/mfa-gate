// Обзор: первичная настройка, состояние системы, счётчики, входы за сутки, события, требующие внимания.
import { get } from '../api.js';
import { h, icon, pageHead, panel, button, statusBadge, statusIcon, table, cellMain, timeEl, empty, errorState, loadingRegion, num, plural, fmtAgo, fmtDate, fill } from '../ui.js';
import { REQUEST_STATUS, CHANNEL, DIAG, AUDIT_EVENT } from '../labels.js';

export async function overview(ctx) {
  const { main, can, signal } = ctx;
  main.append(pageHead({ title: 'Обзор' }));
  const body = h('div', { class: 'grid' }, h('div', { class: 'c-12 panel' }, loadingRegion(4)));
  main.append(body);

  const opt = (perm, path) => (can(perm) ? get(path, null, signal).catch(() => null) : Promise.resolve(undefined));
  const [d, ldap, policies] = await Promise.all([get('/dashboard', null, signal), opt('ldap:read', '/ldap'), opt('policies:read', '/policies')]);

  const steps = [
    { title: 'Подключите каталог LDAP / AD', text: 'Чтобы сотрудники входили доменным паролем. Можно пропустить, если все учётные записи локальные.', href: '#/ldap', done: ldap === undefined ? null : (ldap?.total || 0) > 0, optional: true },
    { title: 'Добавьте защищаемый ресурс', text: 'VPN или приложение, доступ к которому требует второго фактора.', href: '#/resources', done: d.resources > 0 },
    { title: 'Подключите RADIUS-клиент', text: 'VPN-шлюз, который отправляет MFA Gate запросы на проверку.', href: '#/radius', done: d.radius_clients > 0 },
    { title: 'Настройте политику доступа', text: 'Без политик второй фактор нужен всем — это безопасное значение по умолчанию.', href: '#/policies', done: policies === undefined ? null : (policies?.total || 0) > 0 },
    { title: 'Привяжите телефон пользователю', text: 'Откройте карточку пользователя и покажите ему QR-код привязки.', href: '#/users', done: d.devices > 0 },
  ];
  const doneCount = steps.filter((s) => s.done).length;
  const nextIdx = steps.findIndex((s) => s.done === false);
  const setupDone = nextIdx === -1;

  const progress = h('div', { class: 'progress' }, h('span', { class: 'bar', 'aria-hidden': 'true' }, h('i')), h('span', { text: `${doneCount} из ${steps.length}` }));
  progress.querySelector('i').style.setProperty('--p', String(doneCount / steps.length));
  const setup = panel({
    title: 'Первичная настройка', actions: [progress],
    body: h('ol', { class: 'setup' }, steps.map((s, i) => h('li', { class: 'step' + (s.done ? ' is-done' : '') + (i === nextIdx ? ' is-next' : '') },
      h('span', { class: 'step-n', 'aria-hidden': 'true' }, s.done ? icon('check') : String(i + 1)),
      h('div', null,
        h('p', { class: 's-title' }, s.title, h('span', { class: 'sr-only', text: s.done ? ' — выполнено' : s.done === null ? ' — нет доступа к разделу' : ' — не выполнено' })),
        h('p', { class: 's-text', text: s.done === null ? 'Состояние неизвестно: у вашей роли нет доступа к этому разделу.' : s.text })),
      s.done || s.done === null ? h('span') : button(i === nextIdx ? 'Перейти' : 'Открыть', { kind: i === nextIdx ? 'primary' : 'secondary', size: 'sm', href: s.href })))),
  });

  // что сделать сейчас: из данных обзора, диагностика добавляет пункты по мере загрузки
  const todo = h('ul', { class: 'feed' });
  const todoItem = (s, text, href, label) => h('li', null, h('span', { 'data-s': s }, s === 'risk' ? icon('shield-alert') : statusIcon(s)),
    h('div', { class: 'todo' }, h('p', { text }), href ? button(label, { kind: 'secondary', size: 'sm', href }) : null));
  if (d.last24h?.denied) todo.append(todoItem('error', `${num(d.last24h.denied)} ${plural(d.last24h.denied, ['запрос отклонён', 'запроса отклонено', 'запросов отклонено'])} на телефоне за сутки. Если пользователь не входил сам, его пароль, вероятно, известен постороннему.`, can('requests:read') ? '#/history?status=denied' : null, 'Показать запросы'));
  if (d.users_without_factor) todo.append(todoItem('warning', `${num(d.users_without_factor)} ${plural(d.users_without_factor, ['пользователь', 'пользователя', 'пользователей'])} без второго фактора — не смогут войти.`, can('users:read') ? '#/users?factor=none' : null, 'Показать пользователей'));
  if (d.locked_users) todo.append(todoItem('warning', `${num(d.locked_users)} ${plural(d.locked_users, ['учётная запись временно заблокирована', 'учётные записи временно заблокированы', 'учётных записей временно заблокировано'])} после неудачных входов.`, can('requests:read') ? '#/problems' : null, 'Разобрать проблемы входа'));
  const todoEmpty = h('div', { class: 'panel-body' }, h('p', { class: 'muted', text: 'Срочных действий нет: отклонённых push, пользователей без фактора и блокировок нет.' }));
  const actions = panel({ title: 'Что требует действий', body: h('div', null, todo, todoEmpty) });
  const syncTodo = () => { todoEmpty.hidden = todo.children.length > 0; };
  syncTodo();

  const diag = panel({ title: 'Состояние системы', body: loadingRegion(3), foot: h('a', { href: '#/diagnostics', text: 'Открыть диагностику' }) });
  loadDiag(diag, signal, (bad) => {
    bad.forEach((c) => todo.append(todoItem(c.status, `${c.name}: ${c.message}`, c.name.startsWith('Каталог') && can('ldap:read') ? '#/ldap' : '#/diagnostics', c.name.startsWith('Каталог') ? 'Открыть каталог' : 'Открыть диагностику')));
    syncTodo();
  });

  // счётчики: одна линейка с общими разделителями
  const cell = (v, k, d2, href, warn) => h(href ? 'a' : 'div', { class: 'cell', href }, h('span', { class: 'v', text: num(v) }), h('span', { class: 'k', text: k }), d2 ? h('span', { class: 'd' + (warn ? ' warn' : ''), text: d2 }) : null);
  const ledger = h('section', { class: 'c-12 panel', 'aria-label': 'Ключевые показатели' }, h('div', { class: 'ledger' },
    cell(d.users, plural(d.users, ['пользователь', 'пользователя', 'пользователей']), 'всего в системе', can('users:read') ? '#/users' : null),
    cell(d.users_without_factor, 'без второго фактора', d.users_without_factor ? 'не смогут войти, пока не привяжут телефон или TOTP' : 'у всех есть второй фактор', can('users:read') && d.users_without_factor ? '#/users?factor=none' : null, d.users_without_factor > 0),
    cell(d.devices + d.totp, 'активных факторов', `телефонов ${num(d.devices)}, TOTP ${num(d.totp)}`, can('devices:read') ? '#/devices' : null),
    cell(d.locked_users, 'временно заблокированы', d.locked_users ? 'после серии неудачных входов' : 'блокировок нет', null, d.locked_users > 0)));

  // входы за 24 часа
  const total24 = Object.values(d.last24h || {}).reduce((a, b) => a + b, 0);
  const groups = [['ok', 'Успешно', ['approved', 'allowed', 'consumed']], ['error', 'Отказ', ['rejected']], ['error', 'Отклонено на телефоне', ['denied']], ['warning', 'Истекло', ['expired']], ['pending', 'Ожидают', ['pending']]]
    .map(([s, label, keys]) => ({ s, label, keys, n: keys.reduce((a, k) => a + (d.last24h?.[k] || 0), 0) }));
  const bar = h('div', { class: 'spread-bar', role: 'img', 'aria-label': groups.map((g) => `${g.label}: ${g.n}`).join(', ') },
    groups.filter((g) => g.n).map((g) => { const i = h('i', { 'data-s': g.s }); i.style.setProperty('--w', String(g.n)); return i; }));
  const day = panel({
    title: 'Входы за 24 часа', count: total24,
    body: h('div', { class: 'panel-body' }, total24 ? h('div', { class: 'spread' }, bar,
      h('ul', { class: 'legend' }, groups.map((g) => h('li', null, statusBadge(g.s, g.label, 'plain'), h('span', { class: 'n', text: num(g.n) }))))) :
      h('p', { class: 'muted', text: 'За сутки запросов не было.' })),
    foot: can('requests:read') ? h('a', { href: '#/problems', text: 'Проблемы входа' }) : null,
  });

  const recent = panel({
    title: 'Последние запросы',
    actions: can('requests:read') ? [button('Вся история', { kind: 'ghost', size: 'sm', href: '#/history' })] : null,
    body: d.recent?.length ? table({
      caption: 'Последние запросы входа',
      columns: [
        { label: 'Пользователь', primary: true, render: (r) => cellMain(r.login, r.resource || r.client || '', { href: can('requests:read') ? `#/history?login=${encodeURIComponent(r.login)}` : null }) },
        { label: 'Результат', render: (r) => { const [s, t] = REQUEST_STATUS[r.status] || ['neutral', r.status]; return h('div', { class: 'cell-main' }, statusBadge(s, t), r.reason_text ? h('span', { class: 't2', text: r.reason_text }) : null); } },
        { label: 'Канал', opt: true, render: (r) => CHANNEL[r.channel] || r.channel },
        { label: 'Когда', render: (r) => timeEl(r.created_at) },
      ],
      rows: d.recent,
    }) : empty({ iconName: 'history', title: 'Запросов пока нет', text: 'Здесь появятся входы через VPN, API и в эту панель.', compact: true }),
  });

  const attention = panel({
    title: 'Требует внимания',
    body: d.attention?.length ? h('ul', { class: 'feed' }, d.attention.slice(0, 5).map((a) => h('li', null,
      h('span', { 'data-s': a.severity === 'critical' ? 'error' : 'warning' }, statusIcon(a.severity === 'critical' ? 'error' : 'warning')),
      h('div', null, h('p', { text: (AUDIT_EVENT[a.event] || a.event) + (a.result === 'fail' ? ' — неудачно' : '') + (a.target ? `: ${a.target}` : '') }),
        h('p', { class: 'f-meta' }, h('span', { text: `${a.actor || 'система'}, ` }), h('time', { datetime: a.at, title: fmtDate(a.at), text: fmtAgo(a.at) })))))) :
      empty({ iconName: 'shield-check', title: 'Всё спокойно', text: 'Предупреждений и критичных событий нет.', compact: true }),
    foot: can('audit:read') ? h('a', { href: '#/audit?severity=warning', text: 'Открыть журнал' }) : null,
  });

  // пока настройка не завершена, чек-лист — главный блок; после — его место занимает история
  fill(body, h('div', { class: 'c-8 stack' }, setupDone ? null : setup, actions), h('div', { class: 'c-4 stack' }, diag, day), ledger,
    h('div', { class: 'c-8 stack' }, recent), h('div', { class: 'c-4 stack' }, attention));
}

async function loadDiag(p, signal, onBad) {
  const target = p.children[1];
  try {
    const r = await get('/system/diagnostics', null, signal);
    const bad = r.checks.filter((c) => c.status !== 'ok');
    const node = h('div', null,
      h('div', { class: 'panel-body' }, statusBadge(r.status, r.status === 'ok' ? 'Все проверки пройдены' : DIAG[r.status]),
        h('p', { class: 'hint', text: `${r.checks.length} ${plural(r.checks.length, ['проверка', 'проверки', 'проверок'])}: в порядке ${r.checks.length - bad.length}, с замечаниями ${bad.length}. Версия ${r.version}.` })));
    target.replaceWith(node);
    onBad?.(bad);
  } catch (err) {
    if (err.name === 'AbortError') return;
    target.replaceWith(errorState(err, () => { p.children[1].replaceWith(loadingRegion(3)); loadDiag(p, signal, onBad); }));
  }
}
