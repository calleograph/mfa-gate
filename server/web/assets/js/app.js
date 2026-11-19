// Точка входа: сессия, оболочка, хэш-роутинг (#/users/<id>), права разделов.
import { get, post, setCsrf, onSessionLost } from './api.js';
import { h, icon, brandMark, iconButton, button, empty, themeSwitch, errorState, clear, toast, fill, actionMenu, formDialog, PASSWORD_FIELDS, passwordErrors, changePassword } from './ui.js';
import { renderLogin } from './views/login.js';
import { overview } from './views/overview.js';
import { usersList, userCard } from './views/users.js';
import { groupsPage, devicesPage } from './views/access.js';
import { resourcesPage, radiusPage, apiClientsPage, ldapPage } from './views/integrations.js';
import { publicationPage } from './views/publication.js';
import { policiesPage, historyPage, problemsPage, auditPage, diagnosticsPage } from './views/control.js';

const app = document.getElementById('app');
const state = { me: null, perms: new Set() };
export const can = (perm) => state.perms.has('*') || state.perms.has(perm);

const NAV = [
  { items: [{ path: '/', label: 'Обзор', icon: 'layout-dashboard', perm: 'system:read' }] },
  { group: 'Доступ', items: [
    { path: '/users', label: 'Пользователи', icon: 'users', perm: 'users:read' },
    { path: '/groups', label: 'Группы', icon: 'users-round', perm: 'groups:read' },
    { path: '/devices', label: 'Устройства', icon: 'smartphone', perm: 'devices:read' },
    { path: '/policies', label: 'Политики', icon: 'shield', perm: 'policies:read' },
  ] },
  { group: 'Интеграции', items: [
    { path: '/resources', label: 'Ресурсы', icon: 'server', perm: 'resources:read' },
    { path: '/radius', label: 'RADIUS-клиенты', icon: 'router', perm: 'radius:read' },
    { path: '/api-clients', label: 'API-клиенты', icon: 'key-round', perm: 'apiclients:read' },
    { path: '/ldap', label: 'LDAP / AD', icon: 'network', perm: 'ldap:read' },
  ] },
  { group: 'Контроль', items: [
    { path: '/history', label: 'История входов', icon: 'history', perm: 'requests:read' },
    { path: '/audit', label: 'Журнал', icon: 'scroll-text', perm: 'audit:read' },
  ] },
  { group: 'Система', items: [
    { path: '/publication', label: 'Адреса и публикация', icon: 'external-link', perm: 'system:read' },
    { path: '/diagnostics', label: 'Диагностика', icon: 'stethoscope', perm: 'system:read' },
  ] },
];
// разделы без своего пункта меню подсвечивают родительский: «Проблемы входа» — вид «Истории входов»
const NAV_ALIAS = { '/problems': '/history' };

const ROUTES = [
  ['/', overview, 'system:read'],
  ['/users', usersList, 'users:read'],
  ['/users/:id', userCard, 'users:read'],
  ['/groups', groupsPage, 'groups:read'],
  ['/devices', devicesPage, 'devices:read'],
  ['/resources', resourcesPage, 'resources:read'],
  ['/radius', radiusPage, 'radius:read'],
  ['/api-clients', apiClientsPage, 'apiclients:read'],
  ['/ldap', ldapPage, 'ldap:read'],
  ['/publication', publicationPage, 'system:read'],
  ['/policies', policiesPage, 'policies:read'],
  ['/history', historyPage, 'requests:read'],
  ['/problems', problemsPage, 'requests:read'],
  ['/audit', auditPage, 'audit:read'],
  ['/diagnostics', diagnosticsPage, 'system:read'],
];

function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, qs = ''] = raw.split('?');
  return { path: path || '/', query: new URLSearchParams(qs) };
}
function match(path) {
  for (const [pattern, view, perm] of ROUTES) {
    const keys = [];
    const re = new RegExp('^' + pattern.replace(/:(\w+)/g, (_, k) => { keys.push(k); return '([^/]+)'; }) + '/?$');
    const m = path.match(re);
    if (m) return { view, perm, pattern, params: Object.fromEntries(keys.map((k, i) => [k, decodeURIComponent(m[i + 1])])) };
  }
  return null;
}

// ------------------------------------------------------------------ оболочка
let shell, mainEl, navLinks = [], menuBtn, firstRender = true, routeAbort = null, prevHash = '', curHash = '';

function buildShell() {
  const me = state.me;
  const initials = (me.display_name || me.login).split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase();
  navLinks = [];
  const nav = h('nav', { class: 'nav', 'aria-label': 'Разделы' });
  for (const g of NAV) {
    const items = g.items.filter((it) => can(it.perm));
    if (!items.length) continue;
    const gid = g.group ? 'ng-' + g.group : null;
    nav.append(h('div', { class: 'nav-group', role: g.group ? 'group' : null, 'aria-labelledby': gid },
      g.group ? h('div', { class: 'nav-label', id: gid, text: g.group }) : null,
      h('ul', null, items.map((it) => {
        const a = h('a', { href: '#' + it.path, dataset: { path: it.path } }, icon(it.icon), it.label);
        navLinks.push(a);
        return h('li', null, a);
      }))));
  }
  const closeBtn = iconButton('x', 'Закрыть меню', () => toggleNav(false));
  const sidebar = h('aside', { class: 'sidebar', id: 'sidebar', 'aria-label': 'Навигация' },
    h('div', { class: 'side-head' }, h('a', { class: 'brand', href: '#/' }, brandMark(), 'MFA Gate'), closeBtn),
    nav,
    h('div', { class: 'side-foot' },
      insecureHttp() ? h('a', { class: 'http-note', href: HTTPS_DOCS, target: '_blank', rel: 'noopener noreferrer' }, icon('lock-open'),
        h('span', { text: 'Соединение без HTTPS — только для изолированной сети' }), h('span', { class: 'hn-more', text: 'Как включить HTTPS' })) : null,
      actionMenu([
        me.source === 'local' ? { label: 'Сменить пароль', icon: 'key-round', onSelect: passwordDialog } : null,
        me.source === 'local' ? '-' : null,
        { label: 'Выйти', icon: 'log-out', onSelect: logout },
      ], 'Меню профиля', h('button', { class: 'who who-btn', title: `${me.display_name || me.login} (${me.login}) — меню профиля` },
        h('span', { class: 'avatar', 'aria-hidden': 'true', text: initials }),
        h('span', { class: 'who-text' }, h('span', { class: 'who-name', text: me.display_name || me.login }), h('span', { class: 'who-role', text: me.role_label || me.role })),
        icon('chevron-down'))),
      h('div', { class: 'side-row' }, themeSwitch(),
        h('a', { class: 'icon-btn', href: '/api/docs', target: '_blank', rel: 'noopener noreferrer', 'aria-label': 'Документация API для интеграторов (откроется в новой вкладке)', title: 'Документация API (Swagger)' }, icon('book-open')))));
  menuBtn = iconButton('menu', 'Открыть меню', () => toggleNav(true), { 'aria-controls': 'sidebar', 'aria-expanded': 'false' });
  mainEl = h('main', { class: 'content', id: 'main', tabindex: '-1' });
  const skip = h('a', { class: 'skip', href: '#main', text: 'Перейти к содержимому', on: { click: (e) => { e.preventDefault(); mainEl.focus(); } } });
  shell = h('div', { class: 'shell' }, skip, sidebar,
    h('div', { class: 'scrim', on: { click: () => toggleNav(false) } }),
    h('div', { class: 'main' }, h('header', { class: 'topbar' }, menuBtn, h('a', { class: 'brand', href: '#/' }, brandMark(), 'MFA Gate')), mainEl));
  shell.addEventListener('keydown', (e) => { if (e.key === 'Escape' && shell.classList.contains('nav-open')) { toggleNav(false); } });
  fill(app, shell);
  app.removeAttribute('aria-busy');
}

// Первичная установка открывается по http://IP:порт. Петлевой адрес (SSH-туннель) не предупреждаем: трафик не выходит в сеть.
const HTTPS_DOCS = 'https://github.com/calleograph/mfa-gate#https';
const insecureHttp = () => location.protocol === 'http:' && !/^(localhost|127(\.\d{1,3}){3}|\[::1\])$/.test(location.hostname);

function passwordDialog() {
  formDialog({
    title: 'Смена пароля', subtitle: 'Остальные сеансы этой учётной записи завершатся. Текущий останется открытым.',
    fields: PASSWORD_FIELDS, submitLabel: 'Сменить пароль', size: 'sm',
    async onSubmit(v, f) {
      const bad = passwordErrors(v);
      if (bad.length) { f.setErrors(bad); return false; }
      await changePassword(v);
      toast('Пароль изменён. Другие сеансы этой учётной записи завершены.');
    },
  });
}

function toggleNav(open) {
  const wasOpen = shell.classList.contains('nav-open');
  if (open === wasOpen) return;
  shell.classList.toggle('nav-open', open);
  menuBtn.setAttribute('aria-expanded', String(open));
  if (open) shell.querySelector('#sidebar .icon-btn').focus();
  else if (window.matchMedia('(max-width: 1023px)').matches) menuBtn.focus();
}

async function render() {
  if (!state.me) return;
  const { path, query } = parseHash();
  if (location.hash !== curHash) { prevHash = curHash; curHash = location.hash; }
  routeAbort?.abort();
  routeAbort = new AbortController();
  const raw = '/' + (path.split('/')[1] || '');
  const section = NAV_ALIAS[raw] || raw;
  navLinks.forEach((a) => (a.dataset.path === section ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')));
  if (shell.classList.contains('nav-open')) { shell.classList.remove('nav-open'); menuBtn.setAttribute('aria-expanded', 'false'); }
  clear(mainEl);
  const m = match(path);
  const ctx = {
    main: mainEl, query, params: m?.params || {}, signal: routeAbort.signal, can, me: state.me, prev: prevHash,
    navigate: (hash) => { location.hash = hash; },
    // фильтры и страница — в адресе без новой записи истории; «назад» ведёт на предыдущий раздел
    setQuery(obj) {
      const qs = new URLSearchParams(Object.entries(obj).filter(([, v]) => v !== '' && v !== null && v !== undefined && v !== 1 && v !== '1'));
      history.replaceState(null, '', '#' + path + ([...qs].length ? '?' + qs : ''));
      curHash = location.hash;
    },
    reload: render,
  };
  if (!m) {
    setTitle('Не найдено');
    mainEl.append(empty({ iconName: 'circle-alert', title: 'Такой страницы нет', text: 'Проверьте адрес или вернитесь на обзор.', action: button('На обзор', { href: '#/' }) }));
  } else if (m.perm && !can(m.perm)) {
    setTitle('Нет доступа');
    const first = NAV.flatMap((g) => g.items).find((it) => can(it.perm));
    mainEl.append(empty({ iconName: 'lock', title: 'Раздел недоступен для вашей роли', text: `Нужно право ${m.perm}. Его выдаёт главный администратор.`, action: first ? button(first.label, { href: '#' + first.path }) : null }));
  } else {
    try { await m.view(ctx); } catch (err) {
      if (err?.name === 'AbortError') return;
      console.error(err);
      if (err?.status === 401) return;
      clear(mainEl).append(errorState(err, render));
    }
  }
  if (!firstRender) { const h1 = mainEl.querySelector('h1'); if (h1) { h1.tabIndex = -1; h1.focus({ preventScroll: true }); } window.scrollTo(0, 0); }
  firstRender = false;
}
export const setTitle = (t) => { document.title = `${t} — MFA Gate`; };

async function logout() {
  try { await post('/auth/logout'); } catch { /* сессия уже закрыта */ }
  state.me = null;
  setCsrf('');
  showLogin();
}

function showLogin(message) {
  state.me = null;
  setTitle('Вход');
  renderLogin(app, { onDone: start, message, themeSwitch });
  app.removeAttribute('aria-busy');
}

function start(me) {
  state.me = me;
  state.perms = new Set(me.permissions || []);
  setCsrf(me.csrf);
  buildShell();
  firstRender = true;
  // администратор без права на обзор попадает в первый доступный раздел
  if (!location.hash || location.hash === '#/' || location.hash === '#') {
    if (!can('system:read')) { const first = NAV.flatMap((g) => g.items).find((it) => can(it.perm)); if (first) { location.replace('#' + first.path); return; } }
  }
  render();
}

onSessionLost(() => {
  if (!state.me) return;
  toast('Сессия завершилась — войдите снова', 'warning');
  showLogin();
});
window.addEventListener('hashchange', render);

(async function boot() {
  try {
    const me = await get('/auth/me');
    if (me.mfa_passed) start(me); else showLogin();
  } catch (err) {
    if (err.status === 401) showLogin();
    else { app.removeAttribute('aria-busy'); fill(app, h('main', { class: 'content' }, errorState(err, () => location.reload()))); }
  }
})();
