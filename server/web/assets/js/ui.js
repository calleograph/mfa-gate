// Компоненты интерфейса без фреймворка. Текст пользователя всегда идёт через textContent, innerHTML не используется.
import { ApiError, post } from './api.js';

const SPRITE = '/assets/icons/sprite.svg';
const SVG = 'http://www.w3.org/2000/svg';

// ------------------------------------------------------------------ DOM
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'on') for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k in el && typeof v !== 'string' && k !== 'list') el[k] = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
  }
  append(el, kids);
  return el;
}
export function append(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
export const clear = (el) => { el.replaceChildren(); return el; };
// replaceChildren без «null» в тексте: пустые значения пропускаются
export const fill = (el, ...kids) => append(clear(el), kids);

export function icon(name, cls = '') {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('class', 'ic' + (cls ? ' ' + cls : ''));
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const use = document.createElementNS(SVG, 'use');
  use.setAttribute('href', `${SPRITE}#${name}`);
  svg.append(use);
  return svg;
}

// Знак MFA Gate: плитка с градиентом темы (CSS .mark), внутри — проём ворот с замочной скважиной. Тот же рисунок — в favicon.svg.
export function brandMark(cls = 'mark') {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const gate = document.createElementNS(SVG, 'path');
  gate.setAttribute('d', 'M6 20V10a6 6 0 0 1 12 0v10M3.5 20h17M12 12.5V16');
  const key = document.createElementNS(SVG, 'circle');
  Object.entries({ cx: 12, cy: 11, r: 2, class: 'm-k' }).forEach(([k, v]) => key.setAttribute(k, v));
  svg.append(gate, key);
  return h('span', { class: cls, 'aria-hidden': 'true' }, svg);
}

// ------------------------------------------------------------------ форматирование
const dtf = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
const rtf = new Intl.RelativeTimeFormat('ru', { numeric: 'auto' });
export const fmtDate = (iso) => (iso ? dtf.format(new Date(iso)) : '—');
export function fmtAgo(iso) {
  if (!iso) return '—';
  const s = (new Date(iso) - Date.now()) / 1000;
  const a = Math.abs(s);
  if (a < 45) return 'только что';
  if (a < 3600) return rtf.format(Math.round(s / 60), 'minute');
  if (a < 86400) return rtf.format(Math.round(s / 3600), 'hour');
  if (a < 86400 * 7) return rtf.format(Math.round(s / 86400), 'day');
  return dtf.format(new Date(iso));
}
export const timeEl = (iso) => (iso ? h('time', { datetime: iso, title: fmtDate(iso), text: fmtAgo(iso) }) : h('span', { class: 'faint', text: '—' }));
export function plural(n, [one, few, many]) {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
}
export const num = (n) => new Intl.NumberFormat('ru-RU').format(n ?? 0);

// ------------------------------------------------------------------ статусы: цвет + значок + текст
const STATUS_ICON = { ok: 'circle-check', warning: 'triangle-alert', error: 'circle-x', pending: 'clock', neutral: 'ban' };
export const statusBadge = (s, text, cls = '') => h('span', { class: 'status ' + cls, 'data-s': s }, icon(STATUS_ICON[s] || 'circle-dashed'), text);
export const statusIcon = (s) => icon(STATUS_ICON[s] || 'circle-dashed');
export const chip = (text, cls = '') => h('span', { class: 'chip ' + cls, text });
export const risk = (text) => h('span', { class: 'risk-line' }, icon('shield-alert'), text);

// ------------------------------------------------------------------ кнопки
export function button(label, { kind = 'secondary', size = '', iconName, type = 'button', onClick, disabled, title, ariaLabel, href, target } = {}) {
  const cls = `btn ${kind} ${size}`.trim();
  const kids = [iconName ? icon(iconName) : null, label];
  if (href) return h('a', { class: cls, href, target, rel: target ? 'noopener noreferrer' : null, title, 'aria-label': ariaLabel }, kids);
  return h('button', { class: cls, type, disabled, title, 'aria-label': ariaLabel, on: onClick ? { click: onClick } : null }, kids);
}
export const iconButton = (name, label, onClick, extra = {}) =>
  h('button', { class: 'icon-btn', type: 'button', 'aria-label': label, title: label, on: onClick ? { click: onClick } : null, ...extra }, icon(name));

// Кнопка с состоянием загрузки на время асинхронного действия
export async function busy(btn, fn) {
  if (!btn) return fn();
  btn.classList.add('is-loading');
  btn.setAttribute('aria-busy', 'true');
  btn.disabled = true;
  try { return await fn(); } finally {
    btn.classList.remove('is-loading');
    btn.removeAttribute('aria-busy');
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------ тосты
export function toast(message, s = 'ok', timeout = s === 'error' ? 8000 : 4000) {
  const box = document.getElementById('toasts');
  const close = () => { el.classList.add('out'); setTimeout(() => el.remove(), 220); };
  const el = h('div', { class: 'toast', 'data-s': s, role: s === 'error' ? 'alert' : null },
    icon(s === 'error' ? 'circle-x' : s === 'warning' ? 'triangle-alert' : 'circle-check'),
    h('div', { class: 't-body', text: message }),
    iconButton('x', 'Закрыть уведомление', close));
  box.append(el);
  setTimeout(close, timeout);
}
export const toastError = (err) => toast(err?.message || 'Не удалось выполнить действие', 'error');

// ------------------------------------------------------------------ модальные окна (native <dialog>: inert-фон, Escape, возврат фокуса)
export function modal({ title, subtitle, size = '', body, actions = [], dismissible = true, onClose, labelledBy } = {}) {
  const opener = document.activeElement;
  const id = 'm' + Math.random().toString(36).slice(2, 8);
  const head = h('div', { class: 'modal-head' },
    h('div', null, h('h2', { id: id + '-t', text: title }), subtitle ? h('p', { class: 'm-sub', text: subtitle }) : null),
    dismissible ? iconButton('x', 'Закрыть', () => close()) : null);
  const bodyEl = h('div', { class: 'modal-body' }, body);
  const foot = actions.length ? h('div', { class: 'modal-foot' }, actions) : null;
  const dlg = h('dialog', { class: `modal ${size}`.trim(), 'aria-labelledby': labelledBy || id + '-t' }, head, bodyEl, foot);
  let closed = false;
  function close(result) {
    if (closed) return;
    closed = true;
    dlg.close();
    dlg.remove();
    if (opener && document.contains(opener)) opener.focus();
    onClose?.(result);
  }
  dlg.addEventListener('cancel', (e) => { e.preventDefault(); if (dismissible) close(); });
  // клик по подложке: цель — сам dialog, а не содержимое
  dlg.addEventListener('mousedown', (e) => { dlg._downOnBackdrop = e.target === dlg; });
  dlg.addEventListener('click', (e) => { if (dismissible && e.target === dlg && dlg._downOnBackdrop) close(); });
  // явная ловушка фокуса (Tab не уходит в адресную строку браузера)
  dlg.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const f = focusables(dlg);
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
  });
  document.body.append(dlg);
  dlg.showModal();
  const first = dlg.querySelector('[autofocus]') || bodyEl.querySelector('input:not([type=hidden]):not([type=radio]):not([type=checkbox]),select,textarea') || foot?.querySelector('.btn.primary,.btn.danger') || head.querySelector('button');
  first?.focus();
  return { close, dialog: dlg, body: bodyEl, foot };
}
const focusables = (root) => [...root.querySelectorAll('a[href],button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])')]
  .filter((el) => el.offsetParent !== null || el === document.activeElement);

export function confirmDialog({ title, text, target, confirmLabel = 'Подтвердить', danger = false, action }) {
  return new Promise((resolve) => {
    const errBox = h('div');
    const ok = button(confirmLabel, { kind: danger ? 'danger' : 'primary' });
    const cancel = button('Отмена', { kind: 'ghost' });
    // для опасных действий первый фокус — на «Отмена»: случайный Enter ничего не ломает
    if (danger) cancel.setAttribute('autofocus', '');
    const m = modal({ title, size: 'sm', body: [h('p', { class: 'muted', text }), target ? h('p', null, h('span', { class: 'chip mono', text: target })) : null, errBox], actions: [cancel, ok], onClose: (r) => resolve(r || false) });
    cancel.addEventListener('click', () => m.close(false));
    ok.addEventListener('click', () => busy(ok, async () => {
      clear(errBox);
      try { const r = action ? await action() : true; m.close(r ?? true); } catch (err) { errBox.append(notice('error', err.message)); }
    }));
  });
}

export function notice(s, text, extra) {
  return h('div', { class: 'notice', 'data-s': s, role: s === 'error' ? 'alert' : null },
    icon(s === 'error' ? 'circle-x' : s === 'warning' ? 'triangle-alert' : s === 'ok' ? 'circle-check' : 'info'),
    h('div', { class: 'n-body' }, typeof text === 'string' ? h('span', { text }) : text, extra));
}

// ------------------------------------------------------------------ копирование
// Панель открывают и по HTTP (первичная установка по IP): там navigator.clipboard нет — он требует secure context.
// Запасной путь — выделение и execCommand('copy'); если и он не сработал, видимый текст остаётся выделенным с подсказкой.
export async function copyText(text, source) {
  // у поля секрета — подсказка рядом (тост не виден поверх модального окна), иначе тост
  const boxEl = source?.closest('.secret-box');
  const say = (msg, s = 'ok') => {
    if (!boxEl) return toast(msg, s);
    let note = boxEl.nextElementSibling?.classList.contains('copy-note') ? boxEl.nextElementSibling : null;
    if (!note) { note = h('p', { class: 'copy-note', role: 'status' }); boxEl.after(note); }
    note.dataset.s = s;
    note.textContent = msg;
  };
  if (window.isSecureContext && navigator.clipboard) {
    try { await navigator.clipboard.writeText(text); say('Скопировано в буфер обмена'); return true; } catch { /* нет разрешения — запасной путь */ }
  }
  // при открытом <dialog> всё снаружи инертно: временное поле кладём в само окно, иначе выделение не сработает
  const host = source?.closest('dialog[open]') || document.querySelector('dialog[open]') || document.body;
  const ta = h('textarea', { class: 'sr-only', readonly: true, tabindex: '-1', 'aria-hidden': 'true' });
  ta.value = text;
  host.append(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { ok = false; } finally { ta.remove(); }
  source?.focus();
  if (ok) { say('Скопировано в буфер обмена'); return true; }
  const code = boxEl?.querySelector('code');
  if (code) { const r = document.createRange(); r.selectNodeContents(code); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); }
  say('Скопируйте вручную: браузер не дал доступа к буферу обмена. ' + (code ? 'Текст выделен — нажмите Ctrl+C (⌘C).' : ''), 'warning');
  return false;
}
export const secretBox = (value, label = 'Скопировать') =>
  h('div', { class: 'secret-box' }, h('code', { text: value }), iconButton('copy', label, (e) => copyText(value, e.currentTarget)));

// Секрет показывается один раз: закрыть можно только явной кнопкой
export function secretDialog({ title, intro, items, warning = 'Секрет показывается один раз. После закрытия окна его нельзя будет посмотреть снова — только выпустить новый.' }) {
  const done = button('Секрет сохранён, закрыть', { kind: 'primary' });
  const m = modal({
    title, dismissible: false,
    body: [notice('warning', warning), intro ? h('p', { class: 'muted', text: intro }) : null,
      items.map(({ label, value }) => h('div', { class: 'field' }, h('span', { class: 'label', text: label }), secretBox(value, `Скопировать: ${label}`)))],
    actions: [done],
  });
  done.addEventListener('click', () => m.close());
  return m;
}

export function qrImage(svg, alt) {
  const bytes = new TextEncoder().encode(svg);
  let bin = '';
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return h('div', { class: 'qr' }, h('img', { src: 'data:image/svg+xml;base64,' + btoa(bin), alt, width: 176, height: 176 }));
}

// ------------------------------------------------------------------ меню действий (клавиатура, Escape, клик вне)
let openMenu = null;
document.addEventListener('mousedown', (e) => { if (openMenu && !openMenu.wrap.contains(e.target)) openMenu.close(false); });
// trigger — своя кнопка вместо «⋯» (меню профиля); подпись у неё своя, aria-атрибуты меню добавляются здесь
export function actionMenu(items, label = 'Действия', trigger = null) {
  const id = 'mn' + Math.random().toString(36).slice(2, 8);
  if (trigger) { trigger.type = 'button'; trigger.setAttribute('aria-haspopup', 'menu'); trigger.setAttribute('aria-expanded', 'false'); trigger.setAttribute('aria-controls', id); }
  else trigger = h('button', { class: 'icon-btn', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false', 'aria-controls': id, 'aria-label': label, title: label }, icon('ellipsis'));
  const wrap = h('div', { class: 'menu-wrap' }, trigger);
  let menu = null;
  const api = {
    wrap,
    close(focus = true) {
      if (!menu) return;
      menu.remove(); menu = null; openMenu = null;
      trigger.setAttribute('aria-expanded', 'false');
      if (focus) trigger.focus();
    },
  };
  const visible = items.filter(Boolean);
  function open(focusLast = false) {
    if (openMenu) openMenu.close(false);
    menu = h('div', { class: 'menu', role: 'menu', id, 'aria-label': label });
    for (const it of visible) {
      if (it === '-') { menu.append(h('hr', { role: 'separator' })); continue; }
      const mi = h('button', { type: 'button', role: 'menuitem', tabindex: '-1', class: it.danger ? 'danger' : '', 'aria-disabled': it.disabled ? 'true' : null, title: it.disabled && it.reason ? it.reason : null },
        icon(it.icon || 'chevron-right'), it.label);
      mi.addEventListener('click', () => { if (it.disabled) return; api.close(true); it.onSelect(); });
      menu.append(mi);
    }
    wrap.append(menu);
    trigger.setAttribute('aria-expanded', 'true');
    openMenu = api;
    const r = menu.getBoundingClientRect();
    if (r.bottom > window.innerHeight - 8 && trigger.getBoundingClientRect().top > r.height + 16) menu.classList.add('up');
    const mis = [...menu.querySelectorAll('[role=menuitem]')];
    (focusLast ? mis[mis.length - 1] : mis[0])?.focus();
    menu.addEventListener('keydown', (e) => {
      const i = mis.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); mis[(i + 1) % mis.length].focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); mis[(i - 1 + mis.length) % mis.length].focus(); }
      else if (e.key === 'Home') { e.preventDefault(); mis[0].focus(); }
      else if (e.key === 'End') { e.preventDefault(); mis[mis.length - 1].focus(); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); api.close(true); }
      else if (e.key === 'Tab') api.close(false);
    });
  }
  trigger.addEventListener('click', () => (menu ? api.close(true) : open()));
  trigger.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); open(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); open(true); }
  });
  return wrap;
}

// ------------------------------------------------------------------ состояния
export const empty = ({ iconName = 'circle-dashed', title, text, action, compact } = {}) =>
  h('div', { class: 'empty' + (compact ? ' compact' : '') }, h('div', { class: 'e-icon' }, icon(iconName, 'lg')), h('h3', { text: title }), text ? h('p', { text }) : null, action);

export function errorState(err, retry) {
  const offline = err?.code === 'network';
  const forbidden = err?.status === 403;
  return empty({
    iconName: offline ? 'wifi-off' : forbidden ? 'lock' : 'circle-alert',
    title: offline ? 'Нет связи с сервером' : forbidden ? 'Недостаточно прав' : 'Не удалось загрузить данные',
    text: err?.message,
    action: retry && !forbidden ? button('Повторить', { iconName: 'refresh-cw', onClick: retry }) : null,
  });
}

export function skeletonRows(n = 5) {
  return h('div', { class: 'sk-rows', 'aria-hidden': 'true' },
    Array.from({ length: n }, () => h('div', { class: 'sk-row' }, h('span', { class: 'sk w80' }), h('span', { class: 'sk w60' }), h('span', { class: 'sk w40' }), h('span', { class: 'sk w30' }))));
}
export const loadingRegion = (n) => h('div', { role: 'status', 'aria-label': 'Загрузка' }, skeletonRows(n));

// ------------------------------------------------------------------ таблица (на узких экранах строки становятся карточками)
export function table({ columns, rows, caption }) {
  const thead = h('thead', null, h('tr', null, columns.map((c) => h('th', { scope: 'col', class: [c.cls, c.opt === 2 ? 'opt opt2' : c.opt ? 'opt' : ''].filter(Boolean).join(' ') || null }, c.label ? c.label : h('span', { class: 'sr-only', text: c.srLabel || 'Действия' })))));
  const tbody = h('tbody', null, rows.map((row) => h('tr', null, columns.map((c) => {
    const v = c.render ? c.render(row) : row[c.key];
    const isEmpty = v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length);
    return h('td', { class: [c.cls, c.opt === 2 ? 'opt opt2' : c.opt ? 'opt' : '', c.primary ? 'primary' : '', isEmpty && !c.primary && c.cls !== 'act' ? 'empty-cell' : ''].filter(Boolean).join(' ') || null, 'data-label': c.label || null },
      isEmpty ? h('span', { class: 'faint', text: '—' }) : v);
  }))));
  return h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, caption ? h('caption', { class: 'sr-only', text: caption }) : null, thead, tbody));
}
export const cellMain = (title, sub, { href, mono } = {}) =>
  h('div', { class: 'cell-main' }, href ? h('span', null, h('a', { class: 't1', href, text: title, 'aria-label': sub && sub !== title ? `${title} (${sub})` : null })) : h('span', { class: 't1', text: title }), sub ? h('span', { class: 't2' + (mono ? ' mono' : ''), text: sub }) : null);

export function pager({ page, size, total, onPage }) {
  const pages = Math.max(1, Math.ceil(total / size));
  const from = total ? (page - 1) * size + 1 : 0, to = Math.min(total, page * size);
  return h('nav', { class: 'pager', 'aria-label': 'Страницы' },
    h('span', { text: `${num(from)}–${num(to)} из ${num(total)}` }),
    h('div', { class: 'pg-btns' },
      button('Назад', { size: 'sm', iconName: 'chevron-left', disabled: page <= 1, onClick: () => onPage(page - 1) }),
      button('Вперёд', { size: 'sm', disabled: page >= pages, onClick: () => onPage(page + 1) })));
}

export function segmented(options, value, onChange, label) {
  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': label });
  for (const o of options) {
    const b = h('button', { type: 'button', 'aria-pressed': String(o.value === value), title: o.title || null, 'aria-label': o.ariaLabel || null }, o.icon ? icon(o.icon) : null, o.label || null);
    b.addEventListener('click', () => {
      seg.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      onChange(o.value);
    });
    seg.append(b);
  }
  return seg;
}

export function panel({ title, count, actions, body, foot, cls = '' }) {
  return h('section', { class: 'panel ' + cls },
    title ? h('div', { class: 'panel-head' }, h('h2', null, title, count !== undefined && count !== null ? h('span', { class: 'count', text: num(count) }) : null), actions ? h('div', { class: 'page-actions' }, actions) : null) : null,
    body, foot ? h('div', { class: 'panel-foot' }, foot) : null);
}

// ------------------------------------------------------------------ формы
let fid = 0;
export function form(fields, values = {}) {
  const el = h('div', { class: 'form' });
  const controls = {};
  const wraps = {};
  const errors = {};
  for (const f of fields) {
    if (f.type === 'section') { el.append(h('div', { class: 'form-section' }, h('h3', { text: f.label }), f.hint ? h('p', { text: f.hint }) : null)); continue; }
    if (f.type === 'node') { el.append(f.node); continue; }
    const id = `f${++fid}`;
    const hintId = f.hint ? id + '-h' : null;
    const errId = id + '-e';
    const err = h('div', { class: 'field-error', id: errId, hidden: true });
    errors[f.name] = err;
    const describedBy = [hintId, errId].filter(Boolean).join(' ');
    let control, labelEl;
    const v = values[f.name] ?? f.default;
    const labelText = [f.label, f.required ? h('span', { class: 'req', text: ' обязательно' }) : null];
    switch (f.type) {
      case 'switch': {
        control = h('input', { type: 'checkbox', role: 'switch', id, checked: Boolean(v), 'aria-describedby': describedBy });
        const w = h('label', { class: 'switch', for: id }, h('span', { class: 'sw-text' }, f.label, f.hint ? h('span', { class: 'sub', id: hintId, text: f.hint }) : null), control);
        wraps[f.name] = h('div', { class: 'field' + (f.half ? ' half' : '') }, w, err);
        break;
      }
      case 'checks': {
        const set = new Set(v || []);
        const boxes = (f.options || []).map((o) => {
          const cb = h('input', { type: 'checkbox', value: o.value, checked: set.has(o.value) });
          return h('label', { class: 'check' + (o.risk ? ' risk' : '') }, cb, h('span', null, o.label, o.hint ? h('span', { class: 'sub', text: o.hint }) : null));
        });
        control = h('div', { class: 'checks' + (f.oneCol ? ' one' : ''), role: 'group', 'aria-labelledby': id + '-l', 'aria-describedby': describedBy }, boxes.length ? boxes : h('p', { class: 'hint check', text: f.emptyText || 'Нет вариантов' }));
        control.getValue = () => [...control.querySelectorAll('input:checked')].map((x) => x.value);
        labelEl = h('span', { class: 'label', id: id + '-l' }, labelText);
        break;
      }
      case 'choices': {
        control = h('div', { class: 'choices', role: 'radiogroup', 'aria-labelledby': id + '-l', dataset: { n: String(f.options.length) } },
          f.options.map((o) => h('label', { class: 'choice' + (o.risk ? ' risk' : '') },
            h('input', { type: 'radio', name: id, value: o.value, checked: o.value === v }),
            h('span', { class: 'c-title' }, o.icon ? icon(o.icon) : null, o.label), h('span', { class: 'c-text', text: o.hint || '' }))));
        control.getValue = () => control.querySelector('input:checked')?.value;
        labelEl = h('span', { class: 'label', id: id + '-l' }, labelText);
        break;
      }
      case 'tags':
        control = tagsInput({ id, values: v || [], placeholder: f.placeholder, mono: f.mono, describedBy, validate: f.validate });
        labelEl = h('label', { for: id }, labelText);
        break;
      case 'select': {
        const sel = h('select', { class: 'select', id, 'aria-describedby': describedBy }, (f.options || []).map((o) => h('option', { value: o.value, text: o.label })));
        sel.value = v ?? (f.options?.[0]?.value ?? '');
        control = sel;
        labelEl = h('label', { for: id }, labelText);
        wraps[f.name] = h('div', { class: 'field' + (f.half ? ' half' : '') }, labelEl, h('div', { class: 'select-wrap' }, sel, icon('chevron-down')), f.hint ? h('p', { class: 'hint', id: hintId, text: f.hint }) : null, err);
        break;
      }
      case 'textarea':
        control = h('textarea', { class: 'textarea' + (f.mono ? ' mono' : ''), id, rows: f.rows || 4, placeholder: f.placeholder || null, 'aria-describedby': describedBy, spellcheck: f.mono ? 'false' : null });
        control.value = v ?? '';
        labelEl = h('label', { for: id }, labelText);
        break;
      default:
        control = h('input', {
          class: 'input' + (f.mono ? ' mono' : ''), id, type: f.type || 'text', placeholder: f.placeholder || null, autocomplete: f.autocomplete || 'off',
          inputmode: f.inputmode || null, min: f.min ?? null, max: f.max ?? null, 'aria-describedby': describedBy, spellcheck: f.mono ? 'false' : null, required: f.required || null,
        });
        control.value = v ?? '';
        labelEl = h('label', { for: id }, labelText);
    }
    controls[f.name] = control;
    if (!wraps[f.name]) wraps[f.name] = h('div', { class: 'field' + (f.half ? ' half' : '') }, labelEl, control, f.hint ? h('p', { class: 'hint', id: hintId, text: f.hint }) : null, err);
    el.append(wraps[f.name]);
  }
  // у составных полей (теги, флажки) ошибка и фокус — на внутреннем элементе
  const target = (name) => { const c = controls[name]; return c?.focusTarget || c?.querySelector?.('input') || c; };
  const read = (f) => {
    const c = controls[f.name];
    if (!c) return undefined;
    if (f.type === 'switch') return c.checked;
    if (c.getValue) return c.getValue();
    if (f.type === 'number') return c.value === '' ? null : Number(c.value);
    return f.trim === false ? c.value : c.value.trim();
  };
  const api = {
    el, controls, wraps,
    values() {
      const out = {};
      for (const f of fields) if (f.name && !wraps[f.name].hidden) out[f.name] = read(f);
      return out;
    },
    clearErrors() {
      for (const [name, e] of Object.entries(errors)) { e.hidden = true; clear(e); target(name)?.removeAttribute('aria-invalid'); }
    },
    setErrors(list) {
      api.clearErrors();
      const unknown = [];
      let first = null;
      for (const { field, message } of list) {
        const name = field.split('.')[0];
        const e = errors[name];
        if (!e || wraps[name].hidden) { unknown.push(message); continue; }
        e.hidden = false;
        e.append(icon('circle-alert'), h('span', { text: message }));
        target(name)?.setAttribute('aria-invalid', 'true');
        first ||= target(name);
      }
      first?.focus?.();
      return unknown;
    },
    show(name, visible) { if (wraps[name]) wraps[name].hidden = !visible; },
  };
  return api;
}

export function tagsInput({ id, values = [], placeholder = 'Введите и нажмите Enter', mono = false, describedBy, validate }) {
  const list = [...values];
  const input = h('input', { id, type: 'text', placeholder, autocomplete: 'off', spellcheck: 'false', 'aria-describedby': describedBy || null });
  const box = h('div', { class: 'tags' + (mono ? ' mono' : '') });
  const render = () => {
    fill(box, ...list.map((t, i) => h('span', { class: 'tag' }, h('span', { class: 't-text', text: t, title: t }),
      iconButton('x', `Убрать ${t}`, () => { list.splice(i, 1); render(); input.focus(); }))), input);
  };
  const commit = () => {
    const parts = input.value.split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
    for (const p of parts) {
      const bad = validate?.(p);
      if (bad) { toast(bad, 'warning'); continue; }
      if (!list.some((x) => x.toLowerCase() === p.toLowerCase())) list.push(p);
    }
    input.value = '';
    render();
    input.focus();
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); if (input.value.trim()) commit(); }
    else if (e.key === 'Backspace' && !input.value && list.length) { list.pop(); render(); input.focus(); }
  });
  input.addEventListener('blur', () => { if (input.value.trim()) commit(); });
  input.addEventListener('paste', () => setTimeout(() => { if (/[,;\n]/.test(input.value)) commit(); }));
  box.addEventListener('click', (e) => { if (e.target === box) input.focus(); });
  render();
  box.getValue = () => { if (input.value.trim()) commit(); return [...list]; };
  box.focusTarget = input;
  return box;
}

// Модальная форма: ошибки полей — у полей, прочие — над кнопками
export function formDialog({ title, subtitle, fields, values, submitLabel = 'Сохранить', size = '', onSubmit, onReady }) {
  const f = form(fields, values);
  const errBox = h('div', { 'aria-live': 'polite' });
  const submit = button(submitLabel, { kind: 'primary', type: 'submit' });
  const cancel = button('Отмена', { kind: 'ghost' });
  const formEl = h('form', { novalidate: true, id: 'frm' + (++fid) }, f.el, errBox);
  submit.setAttribute('form', formEl.id);
  const m = modal({ title, subtitle, size, body: formEl, actions: [cancel, submit] });
  cancel.addEventListener('click', () => m.close());
  formEl.addEventListener('submit', (e) => {
    e.preventDefault();
    busy(submit, async () => {
      clear(errBox);
      f.clearErrors();
      const values = f.values();
      // обязательные поля проверяем до запроса, остальное — сервер
      const missing = fields.filter((x) => x.required && x.name && !f.wraps[x.name].hidden && (values[x.name] === '' || values[x.name] === null || (Array.isArray(values[x.name]) && !values[x.name].length)))
        .map((x) => ({ field: x.name, message: 'Обязательное поле' }));
      if (missing.length) { f.setErrors(missing); return; }
      try {
        const res = await onSubmit(values, f);
        if (res !== false) m.close(res);
      } catch (err) {
        if (!(err instanceof ApiError)) { errBox.append(notice('error', 'Непредвиденная ошибка интерфейса')); console.error(err); return; }
        const rest = err.fields?.length ? f.setErrors(err.fields) : [];
        if (!err.fields?.length || rest.length) errBox.append(notice('error', [err.message, ...rest].join('. ')));
      }
    });
  });
  onReady?.(f, m);
  return { ...m, form: f };
}

// ------------------------------------------------------------------ смена своего пароля (экран временного пароля и окно из меню профиля)
export const PASSWORD_FIELDS = [
  { name: 'current_password', label: 'Текущий пароль', type: 'password', autocomplete: 'current-password', required: true, trim: false },
  { name: 'new_password', label: 'Новый пароль', type: 'password', autocomplete: 'new-password', required: true, trim: false, hint: 'Не короче 12 символов и не совпадает с текущим.' },
  { name: 'new_password_repeat', label: 'Повторите новый пароль', type: 'password', autocomplete: 'new-password', required: true, trim: false },
];
export function passwordErrors(v) {
  if (v.new_password.length < 12) return [{ field: 'new_password', message: 'Минимум 12 символов' }];
  if (v.new_password_repeat !== v.new_password) return [{ field: 'new_password_repeat', message: 'Пароли не совпадают' }];
  return [];
}
// bad_password и same_password приходят без fields — показываем их у своих полей
export async function changePassword(v) {
  try { return await post('/auth/password', { current_password: v.current_password, new_password: v.new_password }); } catch (err) {
    if (err.code === 'bad_password') err.fields = [{ field: 'current_password', message: err.message }];
    else if (err.code === 'same_password') err.fields = [{ field: 'new_password', message: err.message }];
    throw err;
  }
}

// ------------------------------------------------------------------ заголовок страницы
export function pageHead({ title, count, back, desc, actions, meta }) {
  document.title = `${title} — MFA Gate`;
  return h('header', { class: 'page-head' },
    h('div', { class: 'titles' },
      back ? h('a', { class: 'back', href: back.href, on: back.onClick ? { click: back.onClick } : null }, icon('arrow-left'), back.label) : null,
      h('h1', null, title, count !== undefined && count !== null ? h('span', { class: 'count', text: num(count) }) : null),
      desc ? h('p', { class: 'page-desc', text: desc }) : null,
      meta ? h('div', { class: 'head-meta' }, meta) : null),
    actions && actions.filter(Boolean).length ? h('div', { class: 'page-actions' }, actions) : null);
}

// ------------------------------------------------------------------ тема
const THEME_KEY = 'mfa_gate.theme';
function getTheme() { try { return localStorage.getItem(THEME_KEY) || 'auto'; } catch { return 'auto'; } }
function setTheme(t) {
  if (t === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  try { if (t === 'auto') localStorage.removeItem(THEME_KEY); else localStorage.setItem(THEME_KEY, t); } catch { /* приватный режим — тема до перезагрузки */ }
}
export const themeSwitch = () => segmented([
  { value: 'auto', icon: 'monitor', ariaLabel: 'Тема как в системе', title: 'Как в системе' },
  { value: 'light', icon: 'sun', ariaLabel: 'Светлая тема', title: 'Светлая' },
  { value: 'dark', icon: 'moon', ariaLabel: 'Тёмная тема', title: 'Тёмная' },
], getTheme(), setTheme, 'Тема оформления');

// ------------------------------------------------------------------ ожидание push (вход в панель и в портал)
// start() открывает запрос, poll(id, signal) ждёт решения; onError(err) возвращает true, если ошибку обработал вызывающий.
export function pushWait({ start, poll, text, doneText, onApproved, onError, retry, alt }) {
  const ac = new AbortController();
  let timer = 0;
  const stop = () => { ac.abort(); clearInterval(timer); };
  const box = h('div', { class: 'push-wait' });
  // живая область — только смена состояния и рубежи 1:00 / 0:10, а не каждая секунда таймера
  const live = h('p', { class: 'sr-only', role: 'status', 'aria-live': 'polite' });
  const mark = h('div', { class: 'pw-mark', 'aria-hidden': 'true' }, h('span', { class: 'a' }), h('span', { class: 'b' }));
  const title = h('p', { class: 'pw-title', text: 'Отправляем запрос на телефон…' });
  const textEl = h('p', { class: 'pw-text', text });
  const bar = h('i');
  const time = h('p', { class: 'pw-time', 'aria-hidden': 'true' });
  const actions = h('div', { class: 'auth-panel' });
  box.append(mark, title, textEl, h('div', { class: 'pw-bar', 'aria-hidden': 'true' }, bar), time, live);
  const say = (t) => { live.textContent = t; };
  if (alt) actions.append(h('div', { class: 'auth-alt' }, alt));
  const fail = (t, d) => {
    stop();
    box.classList.add('is-fail');
    title.textContent = t; textEl.textContent = d; time.textContent = '';
    say(`${t}. ${d}`);
    const again = button('Отправить снова', { kind: 'primary', iconName: 'refresh-cw', onClick: retry });
    actions.prepend(again);
    again.focus();
  };
  (async () => {
    try {
      const r = await start();
      const end = new Date(r.expires_at).getTime();
      const total = Math.max(1000, end - Date.now());
      title.textContent = 'Ждём подтверждения на телефоне';
      say('Запрос отправлен на телефон. Подтвердите вход в приложении MFA Gate.');
      const tick = () => {
        const left = Math.max(0, end - Date.now());
        bar.style.setProperty('--p', String(left / total));
        const s = Math.ceil(left / 1000);
        time.textContent = `Осталось ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
        if (s === 60 || s === 10) say(`Осталось ${s} секунд`);
      };
      tick();
      timer = setInterval(tick, 1000);
      while (!ac.signal.aborted) {
        const s = await poll(r.request_id, ac.signal);
        if (s.status === 'approved') {
          clearInterval(timer);
          box.classList.add('is-ok');
          title.textContent = 'Вход подтверждён';
          say('Вход подтверждён');
          textEl.textContent = doneText;
          time.textContent = '';
          setTimeout(() => onApproved(s.me), 600);
          return;
        }
        if (s.status === 'consumed') return fail('Подтверждение уже использовано', 'Этот запрос подтвердили в другой вкладке или сессии. Отправьте новый.');
        if (s.status === 'denied') return fail('Запрос отклонён на телефоне', 'Если вход пытались выполнить не вы — смените пароль и сообщите ответственному за безопасность.');
        if (s.status !== 'pending' || Date.now() > end + 3000) return fail('Время ожидания вышло', 'Телефон не ответил. Проверьте, что на нём есть интернет, и отправьте запрос снова.');
      }
    } catch (err) {
      if (err.name === 'AbortError' || onError(err)) return;
      fail('Не удалось отправить запрос', err.message);
    }
  })();
  return { el: h('div', { class: 'auth-panel' }, box, actions), stop };
}
