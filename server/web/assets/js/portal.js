// Портал сотрудника: вход доменной учётной записью, первичная настройка второго фактора, свои способы входа, история и сеансы.
import { api, get, post, del, setCsrf, onSessionLost } from './api.js';
import { h, icon, brandMark, button, busy, notice, qrImage, secretBox, clear, fill, toast, toastError, confirmDialog, modal, panel, table,
  cellMain, timeEl, fmtDate, statusBadge, empty, errorState, loadingRegion, themeSwitch, actionMenu, copyText, plural, num, pushWait } from './ui.js';
import { REQUEST_STATUS, CHANNEL, METHOD } from './labels.js';

// старые закладки панели (/#/users) — теперь панель живёт по адресу /admin/
if (/^#\/(users|groups|devices|resources|radius|api-clients|ldap|policies|history|problems|audit|diagnostics|publication)\b/.test(location.hash)) {
  location.replace('/admin/' + location.hash);
}

const root = document.getElementById('app');
// ссылки на приложение той же версии, что и сервер (без входа); загружаются один раз
const appInfo = get('/portal/app').catch(() => null);
let app = null;
appInfo.then((d) => { app = d; });
const DENIED = {
  reenroll: 'Второй фактор для вашей учётной записи уже настраивался. Если телефон потерян или сброшен, обратитесь к администратору — он разрешит повторную настройку на сутки.',
  network: 'Первую настройку можно пройти только из сети компании. Подключитесь к офисной сети и войдите снова.',
  group: 'Самостоятельная настройка второго фактора для вашей учётной записи не разрешена. Обратитесь к администратору.',
};
let me = null;
let stop = () => {};

onSessionLost(() => authScreen('Сессия истекла — войдите снова'));

function apply(state) {
  me = state;
  setCsrf(state.csrf);
  if (state.stage === 'ready') cabinet(); else authScreen(null, state);
}

const factorCount = (f) => (f ? f.totp.length + f.devices.length : 0);
const head = (title, lead) => [h('h1', { text: title }), lead ? h('p', { class: 'lead', text: lead }) : null];

// ================================================================== вход и первичная настройка
function authScreen(message, state) {
  stop();
  const foldA = h('div', { class: 'fold is-current', text: 'Учётная запись' });
  const foldB = h('div', { class: 'fold', text: 'Второй фактор' });
  const stage = h('div');
  const appSlot = h('div', { class: 'app-slot' });
  fill(root, h('div', { class: 'auth' },
    h('main', { class: 'auth-main' }, h('div', { class: 'auth-card' },
      h('div', { class: 'brand' }, brandMark(), 'MFA Gate'),
      h('div', { class: 'folds', role: 'img', 'aria-label': 'Вход в два шага' }, foldA, foldB),
      stage),
      appSlot),
    h('footer', { class: 'auth-foot' }, themeSwitch())));
  appInfo.then((d) => d && fill(appSlot, h('section', { class: 'auth-card app-card', 'aria-labelledby': 'app-h' },
    h('h2', { id: 'app-h', text: 'Приложение для телефона' }), appBlock(d))));
  const setStep = (n) => {
    foldA.className = 'fold ' + (n === 1 ? 'is-current' : 'is-done');
    foldB.className = 'fold ' + (n === 2 ? 'is-current' : n > 2 ? 'is-done' : '');
  };
  const sessionLost = (err) => { stop(); passwordStep(err.message); };
  const handled = (err) => { if (err.status === 401 && !['invalid_otp', 'locked'].includes(err.code)) { sessionLost(err); return true; } return false; };

  function next(st) {
    me = st;
    setCsrf(st.csrf);
    if (st.stage === 'ready') { stop(); setStep(3); cabinet(); return; }
    setStep(2);
    if (st.stage === 'mfa') mfaStep(st);
    else if (st.stage === 'enroll') enrollStep(st);
    else deniedStep(st);
  }

  function passwordStep(msg) {
    stop();
    setStep(1);
    document.title = 'Вход — MFA Gate';
    const errBox = h('div', { 'aria-live': 'polite' }, msg ? notice('warning', msg) : null);
    const login = h('input', { class: 'input', id: 'pl-login', name: 'username', autocomplete: 'username', required: true, autocapitalize: 'none', spellcheck: 'false', 'aria-describedby': 'pl-login-h' });
    const pass = h('input', { class: 'input', id: 'pl-pass', name: 'password', type: 'password', autocomplete: 'current-password', required: true });
    const submit = button('Продолжить', { kind: 'primary', type: 'submit' });
    const frm = h('form', { class: 'auth-panel', novalidate: true },
      h('div', { class: 'field' }, h('label', { for: 'pl-login', text: 'Логин' }), login,
        h('p', { class: 'hint', id: 'pl-login-h', text: 'Как при входе в рабочий компьютер: ivanov, DOMAIN\\ivanov или ivanov@example.org' })),
      h('div', { class: 'field' }, h('label', { for: 'pl-pass', text: 'Пароль' }), pass),
      errBox, submit);
    frm.addEventListener('submit', (e) => {
      e.preventDefault();
      clear(errBox);
      if (!login.value.trim() || !pass.value) {
        errBox.append(notice('error', 'Введите логин и пароль'));
        (login.value.trim() ? pass : login).focus();
        return;
      }
      busy(submit, async () => {
        try { next(await post('/portal/login', { login: login.value.trim(), password: pass.value })); } catch (err) {
          errBox.append(notice('error', err.message));
          pass.value = '';
          pass.focus();
        }
      });
    });
    fill(stage, ...head('Личный кабинет', 'Войдите доменной учётной записью, чтобы настроить второй фактор для VPN и корпоративных сервисов.'), frm);
    login.focus();
  }

  function mfaStep(st, prefer) {
    if (st.login_methods.includes('push') && prefer !== 'code') pushStep(st); else codeStep(st);
  }

  function codeStep(st) {
    stop();
    const backupOnly = !st.login_methods.includes('totp');
    const errBox = h('div', { 'aria-live': 'polite' });
    const code = h('input', { class: 'input otp-input', id: 'pl-code', inputmode: backupOnly ? 'text' : 'numeric', autocomplete: 'one-time-code', maxlength: 64,
      placeholder: backupOnly ? 'xxxxx-xxxxx' : '000000', 'aria-describedby': 'pl-code-h', autocapitalize: 'none', spellcheck: 'false' });
    const hint = [st.login_methods.includes('totp') ? 'шесть цифр из приложения-аутентификатора' : null, st.login_methods.includes('backup') ? 'резервный код из сохранённого списка' : null]
      .filter(Boolean).join(' или ');
    const submit = button('Войти', { kind: 'primary', type: 'submit' });
    const frm = h('form', { class: 'auth-panel', novalidate: true },
      h('div', { class: 'field' }, h('label', { for: 'pl-code', text: backupOnly ? 'Резервный код' : 'Код подтверждения' }), code,
        h('p', { class: 'hint', id: 'pl-code-h', text: hint.charAt(0).toUpperCase() + hint.slice(1) + '.' })),
      errBox, submit,
      st.login_methods.includes('push') ? h('div', { class: 'auth-alt' }, button('Отправить запрос на телефон', { kind: 'ghost', iconName: 'smartphone', onClick: () => pushStep(st) })) : null);
    frm.addEventListener('submit', (e) => {
      e.preventDefault();
      clear(errBox);
      const value = code.value.trim();
      if (value.replace(/[\s-]/g, '').length < 6) { errBox.append(notice('error', 'Введите код полностью')); code.focus(); return; }
      busy(submit, async () => {
        try { next(await post('/portal/mfa', { code: value })); } catch (err) {
          if (handled(err)) return;
          errBox.append(notice('error', err.message));
          code.select();
        }
      });
    });
    fill(stage, ...head('Подтвердите вход', 'Пароль принят. Остался второй фактор.'), frm);
    code.focus();
  }

  function pushStep(st) {
    stop();
    const hasCode = st.login_methods.some((m) => m !== 'push');
    const w = pushWait({
      start: () => post('/portal/mfa/push'),
      poll: (id, signal) => api(`/portal/mfa/push/${encodeURIComponent(id)}`, { query: { wait: 25 }, signal }),
      text: 'Откройте уведомление MFA Gate на телефоне и подтвердите вход в личный кабинет.',
      doneText: 'Открываем личный кабинет…',
      onApproved: next,
      retry: () => pushStep(st),
      onError: handled,
      alt: hasCode ? button('Ввести код вместо этого', { kind: 'ghost', iconName: 'key', onClick: () => codeStep(st) }) : null,
    });
    stop = w.stop;
    fill(stage, ...head('Подтвердите вход', 'Пароль принят. Остался второй фактор.'), w.el);
  }

  function enrollStep(st) {
    stop();
    document.title = 'Настройка второго фактора — MFA Gate';
    const opt = (iconName, title, text, badge, onClick) => h('button', { type: 'button', class: 'choice method', on: { click: onClick } },
      h('span', { class: 'c-title' }, icon(iconName), title, badge ? h('span', { class: 'chip accent', text: badge }) : null),
      h('span', { class: 'c-text', text }));
    const list = h('div', { class: 'methods' },
      st.methods.includes('totp') ? opt('key', 'Приложение-аутентификатор', 'Код из шести цифр в любом приложении TOTP: Яндекс Ключ, Google или Microsoft Authenticator, Aegis.', 'Рекомендуется', () => totpSetup(st)) : null,
      st.methods.includes('push') ? opt('smartphone', 'Телефон с MFA Gate', 'Уведомление на Android-телефон: подтверждение одним касанием и отпечатком пальца.', null, () => phoneSetup(st)) : null);
    fill(stage, ...head('Настройте второй фактор', 'Он защищает вход в VPN и личный кабинет: кроме пароля понадобится телефон. Выберите удобный способ — позже можно добавить второй.'),
      h('div', { class: 'auth-panel' }, list, notice('info', 'Настройка доступна 15 минут после входа. Не закрывайте страницу, пока не закончите.')));
  }

  async function totpSetup(st) {
    stop();
    fill(stage, ...head('Приложение-аутентификатор'), h('div', { class: 'push-wait', role: 'status' }, h('p', { class: 'pw-text', text: 'Готовим секрет…' })));
    let data;
    try { data = await post('/portal/totp'); } catch (err) {
      if (handled(err)) return;
      fill(stage, ...head('Приложение-аутентификатор'), notice('error', err.message), button('Назад', { kind: 'secondary', iconName: 'arrow-left', onClick: () => enrollStep(st) }));
      return;
    }
    const f = totpForm(data, async (code) => next(await post(`/portal/totp/${encodeURIComponent(data.factor_id)}/confirm`, { code })), handled);
    fill(stage, ...head('Приложение-аутентификатор', 'Отсканируйте QR-код в приложении и введите код, который оно покажет.'), f,
      h('div', { class: 'auth-alt' }, button('Выбрать другой способ', { kind: 'ghost', iconName: 'arrow-left', onClick: () => enrollStep(st) })));
    f.querySelector('input.otp-input').focus();
  }

  async function phoneSetup(st) {
    stop();
    fill(stage, ...head('Телефон с MFA Gate'), h('div', { class: 'push-wait', role: 'status' }, h('p', { class: 'pw-text', text: 'Готовим ссылку привязки…' })));
    let data;
    try { data = await post('/portal/devices/enrollment'); } catch (err) {
      if (handled(err)) return;
      fill(stage, ...head('Телефон с MFA Gate'), notice('error', err.message), button('Назад', { kind: 'secondary', iconName: 'arrow-left', onClick: () => enrollStep(st) }));
      return;
    }
    const live = h('p', { class: 'hint', role: 'status', 'aria-live': 'polite', text: 'Ждём, когда телефон привяжется…' });
    fill(stage, ...head('Телефон с MFA Gate', 'Привяжите телефон — затем подтвердите на нём первый вход.'), phoneSteps(data), live,
      h('div', { class: 'auth-alt' }, button('Выбрать другой способ', { kind: 'ghost', iconName: 'arrow-left', onClick: () => enrollStep(st) })));
    // телефон привязывается через Device API — узнаём об этом по смене стадии сессии
    const timer = setInterval(async () => {
      try {
        const s = await get('/portal/me');
        if (s.stage !== 'enroll') { clearInterval(timer); toast('Телефон привязан'); next(s); }
      } catch (err) { if (handled(err)) clearInterval(timer); }
    }, 3000);
    stop = () => clearInterval(timer);
  }

  function deniedStep(st) {
    stop();
    fill(stage, ...head('Настройка недоступна'), h('div', { class: 'auth-panel' }, notice('warning', DENIED[st.enroll_denied] || DENIED.group),
      button('Выйти', { kind: 'secondary', iconName: 'log-out', onClick: logout })));
  }

  if (state) next(state); else passwordStep(message);
}

// QR + секрет + поле кода: общий для первичной настройки и для добавления в кабинете
function totpForm(data, confirm, onErr = () => false) {
  const errBox = h('div', { 'aria-live': 'polite' });
  const id = 'tc' + Math.random().toString(36).slice(2, 7);
  const code = h('input', { class: 'input otp-input', id, inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 8, placeholder: '000000' });
  const submit = button('Подтвердить', { kind: 'primary', type: 'submit' });
  const frm = h('form', { class: 'auth-panel', novalidate: true },
    h('div', { class: 'qr-wrap' }, qrImage(data.qr_svg, 'QR-код для приложения-аутентификатора'),
      h('div', { class: 'qr-side' }, h('ol', null,
        h('li', { text: 'Откройте приложение-аутентификатор и нажмите «Добавить».' }),
        h('li', { text: 'Отсканируйте QR-код или введите секрет вручную.' }),
        h('li', { text: 'Введите шесть цифр, которые покажет приложение.' })))),
    h('div', { class: 'field' }, h('span', { class: 'label', text: 'Секрет для ручного ввода' }), secretBox(data.secret.replace(/(.{4})/g, '$1 ').trim(), 'Скопировать секрет')),
    h('div', { class: 'field' }, h('label', { for: id, text: 'Код из приложения' }), code),
    errBox, submit);
  frm.addEventListener('submit', (e) => {
    e.preventDefault();
    clear(errBox);
    const value = code.value.replace(/\s+/g, '');
    if (value.length < 6) { errBox.append(notice('error', 'Введите шесть цифр из приложения')); code.focus(); return; }
    busy(submit, async () => {
      try { await confirm(value); } catch (err) {
        if (onErr(err)) return;
        errBox.append(notice('error', err.message));
        code.select();
      }
    });
  });
  return frm;
}

function phoneSteps(data) {
  return h('div', { class: 'qr-wrap' }, qrImage(data.qr_svg, 'QR-код привязки телефона'),
    h('div', { class: 'qr-side' }, h('ol', null,
      h('li', null, 'Установите на Android-телефон приложение MFA Gate Authenticator: ', appLinks(app), '.'),
      h('li', { text: 'В приложении нажмите «Сканировать QR-код» (если телефон уже привязан к другому серверу — «Серверы» → «Привязать ещё сервер») и наведите камеру на этот код.' }),
      h('li', { text: 'Разрешите уведомления: по ним вы будете подтверждать вход.' })),
      h('p', { class: 'faint', text: `Код действует до ${fmtDate(data.expires_at)} и подходит для одного телефона.` })));
}

// Блок скачивания: на компьютере — QR-код (навести камеру телефона), на телефоне — кнопка
function appBlock(d) {
  return h('div', { class: 'app-block' },
    h('p', { class: 'muted', text: `MFA Gate Authenticator подтверждает вход в VPN одним касанием. Android 8.0 и новее, версия ${d.version}.` }),
    h('ul', { class: 'app-list' }, d.android.map((a) => h('li', { class: 'app-item' },
      h('div', { class: 'app-qr' }, qrImage(a.qr_svg, `QR-код для скачивания: ${a.title}`)),
      h('div', { class: 'app-text' }, h('strong', { text: a.title }), h('span', { class: 'muted', text: a.text }),
        button('Скачать APK', { kind: a.flavor === 'gms' ? 'primary' : 'secondary', size: 'sm', iconName: 'download', href: a.url, ariaLabel: `Скачать APK: ${a.title}` }))))),
    h('p', { class: 'faint', text: 'iPhone: подключите любое приложение-аутентификатор с кодами — Яндекс Ключ, Google Authenticator или Microsoft Authenticator.' }));
}

// короткие ссылки для шага «установите приложение»
const appLinks = (d) => (d ? h('span', null, h('a', { href: d.android[0].url, text: 'для телефонов с Google' }), ' или ', h('a', { href: d.android[1].url, text: 'без Google (Huawei и др.)' }))
  : h('span', { text: 'файл выдаёт администратор' }));

async function logout() {
  stop();
  try { await post('/portal/logout'); } catch { /* сессия уже закрыта */ }
  me = null;
  authScreen();
}

// ================================================================== кабинет
function cabinet() {
  stop();
  document.title = 'Личный кабинет — MFA Gate';
  const name = me.display_name || me.login;
  const initials = name.split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase();
  const menu = actionMenu([{ label: 'Выйти', icon: 'log-out', onSelect: logout }], 'Меню профиля',
    h('button', { type: 'button', class: 'who-btn' }, h('span', { class: 'avatar', 'aria-hidden': 'true', text: initials }),
      h('span', { class: 'who-text' }, h('span', { class: 'who-name', text: name }), h('span', { class: 'who-role', text: me.login })), icon('chevron-down')));
  const content = h('main', { class: 'content', id: 'main', tabindex: '-1' });
  fill(root, h('div', { class: 'portal' },
    h('a', { class: 'skip', href: '#main', text: 'К содержимому' }),
    h('header', { class: 'portal-top' }, h('div', { class: 'portal-bar' },
      h('div', { class: 'brand' }, brandMark(), h('span', { class: 'brand-text' }, 'MFA Gate', h('span', { class: 'brand-sub', text: 'Личный кабинет' }))),
      h('div', { class: 'portal-tools' }, themeSwitch(), menu))),
    content));
  render(content);
}

// «Фамилия Имя Отчество» (обычный displayName в AD) → «Имя Отчество»; иначе имя как есть
const greeting = (u) => { const w = (u.display_name || '').trim().split(/\s+/); return w.length === 3 ? `${w[1]} ${w[2]}` : (u.display_name || u.login); };

function render(content) {
  const f = me.factors;
  const total = factorCount(f);
  const cell = (v, k, d, warn) => h('div', { class: 'cell' }, h('span', { class: 'v', text: num(v) }), h('span', { class: 'k', text: k }), h('span', { class: 'd' + (warn ? ' warn' : ''), text: d }));
  const refresh = async (state) => { me = state || await get('/portal/me'); render(content); };

  const add = [
    me.methods.includes('totp') ? button('Приложение', { kind: 'secondary', size: 'sm', iconName: 'plus', ariaLabel: 'Добавить приложение-аутентификатор', onClick: () => addTotp(refresh) }) : null,
    me.methods.includes('push') ? button('Телефон', { kind: 'secondary', size: 'sm', iconName: 'plus', ariaLabel: 'Привязать телефон с MFA Gate', onClick: () => addPhone(refresh) }) : null,
  ].filter(Boolean);
  const rows = [
    ...f.devices.map((d) => ({ kind: 'push', id: d.id, title: d.name, sub: `Телефон · ${d.push === 'none' ? 'уведомления через постоянное соединение' : 'push-уведомления'}`, added: d.created_at, used: d.last_seen_at, usedLabel: 'на связи' })),
    ...f.totp.map((t) => ({ kind: 'totp', id: t.id, title: t.name, sub: 'Приложение-аутентификатор · код TOTP', added: t.created_at, used: t.last_used_at, usedLabel: 'использован' })),
  ];
  const removeFactor = (r) => confirmDialog({
    title: r.kind === 'push' ? 'Отвязать телефон?' : 'Удалить приложение?', target: r.title, danger: true, confirmLabel: r.kind === 'push' ? 'Отвязать' : 'Удалить',
    text: 'Этим способом больше нельзя будет подтвердить вход. Если телефон потерян — правильное решение.',
    action: async () => { refresh(await (r.kind === 'push' ? post(`/portal/devices/${r.id}/revoke`) : del(`/portal/totp/${r.id}`))); toast(r.kind === 'push' ? 'Телефон отвязан' : 'Приложение удалено'); },
  });
  const factorsPanel = panel({
    title: 'Способы входа', count: total, actions: add,
    body: rows.length ? table({
      caption: 'Подключённые способы подтверждения входа',
      columns: [
        { label: 'Способ', primary: true, render: (r) => h('div', { class: 'factor' }, h('span', { class: 'f-ic', 'aria-hidden': 'true' }, icon(r.kind === 'push' ? 'smartphone' : 'key')), cellMain(r.title, r.sub)) },
        { label: 'Добавлен', opt: 1, render: (r) => timeEl(r.added) },
        { label: 'Активность', render: (r) => (r.used ? h('span', null, r.usedLabel + ' ', timeEl(r.used)) : h('span', { class: 'faint', text: 'ещё не использовался' })) },
        { cls: 'act', srLabel: 'Действия', render: (r) => (total > 1 ? actionMenu([{ label: r.kind === 'push' ? 'Отвязать телефон' : 'Удалить', icon: 'trash-2', danger: true, onSelect: () => removeFactor(r) }], `Действия: ${r.title}`) : h('span')) },
      ],
      rows,
    }) : h('div', { class: 'panel-body' }, empty({ iconName: 'shield-alert', title: 'Нет ни одного способа входа', text: 'Вы вошли по резервному коду. Подключите приложение или телефон, иначе не сможете подключиться к VPN.', compact: true })),
    foot: total === 1 ? h('span', { class: 'muted', text: 'Единственный способ удалить нельзя — сначала подключите второй.' }) : null,
  });

  const codesLeft = f.backup_codes_left;
  const backupPanel = panel({
    title: 'Резервные коды',
    body: h('div', { class: 'panel-body stack-s' },
      h('p', { class: 'muted', text: 'Одноразовые коды для входа в личный кабинет, если телефона нет под рукой. Войдя по коду, вы сможете привязать новый телефон.' }),
      codesLeft ? h('p', null, h('strong', { text: `${num(codesLeft)} ${plural(codesLeft, ['код остался', 'кода осталось', 'кодов осталось'])}` })) : notice('warning', 'Кодов нет. Создайте их и сохраните в надёжном месте.'),
      button(codesLeft ? 'Создать новые' : 'Создать коды', { kind: codesLeft ? 'secondary' : 'primary', iconName: 'list-checks', onClick: () => newBackupCodes(refresh, codesLeft) })),
  });

  const appBody = h('div', { class: 'panel-body' }, loadingRegion(2));
  const appPanel = panel({ title: 'Приложение для телефона', body: appBody });
  const historyBody = h('div', null, loadingRegion(4));
  const sessionsBody = h('div', null, loadingRegion(2));
  const help = panel({
    title: 'Если телефон потерян',
    body: h('div', { class: 'panel-body stack-s' },
      h('p', { class: 'muted', text: 'Отвяжите его здесь, если можете войти другим способом или резервным кодом. Если войти нечем — обратитесь к администратору: он отзовёт телефон и разрешит настроить новый.' })),
  });

  fill(content, h('div', { class: 'grid' },
    h('header', { class: 'c-12 page-head' }, h('div', { class: 'titles' }, h('h1', { text: `Здравствуйте, ${greeting(me)}` }),
      h('p', { class: 'page-desc', text: 'Здесь настраивается второй фактор для VPN и корпоративных сервисов: что подключено, где использовалось и как восстановить доступ.' }))),
    h('section', { class: 'c-12 panel', 'aria-label': 'Состояние защиты' }, h('div', { class: 'ledger', 'data-n': '3' },
      cell(total, plural(total, ['способ входа', 'способа входа', 'способов входа']), total > 1 ? 'есть запасной способ' : total ? 'добавьте запасной способ' : 'вход в VPN невозможен', total < 2),
      cell(f.devices.length, plural(f.devices.length, ['телефон', 'телефона', 'телефонов']), f.devices.length ? 'подтверждение одним касанием' : 'push не подключён'),
      cell(codesLeft, 'резервных кодов', codesLeft ? 'на случай потери телефона' : 'не созданы', !codesLeft))),
    h('div', { class: 'c-8 stack' }, factorsPanel, panel({ title: 'История входов', body: historyBody })),
    h('div', { class: 'c-4 stack' }, backupPanel, appPanel, panel({ title: 'Сеансы личного кабинета', body: sessionsBody }), help)));
  appInfo.then((d) => (d ? fill(appBody, appBlock(d)) : appPanel.remove()));

  get('/portal/history').then((r) => fill(historyBody, r.items.length ? table({
    caption: 'Последние входы',
    columns: [
      { label: 'Когда', primary: true, render: (x) => cellMain(fmtDate(x.at), x.source_ip || '') },
      { label: 'Куда', render: (x) => cellMain(x.resource || CHANNEL[x.channel] || x.channel, CHANNEL[x.channel] || '') },
      { label: 'Способ', opt: 1, render: (x) => METHOD[x.method] || x.method },
      { label: 'Результат', render: (x) => { const [s, t] = REQUEST_STATUS[x.status] || ['neutral', x.status]; return h('div', { class: 'cell-main' }, statusBadge(s, t), x.reason ? h('span', { class: 't2', text: x.reason }) : null); } },
    ],
    rows: r.items,
  }) : h('div', { class: 'panel-body' }, empty({ iconName: 'history', title: 'Входов пока не было', compact: true }))))
    .catch((err) => fill(historyBody, h('div', { class: 'panel-body' }, errorState(err))));

  const loadSessions = () => get('/portal/sessions').then((r) => fill(sessionsBody, h('ul', { class: 'sessions' }, r.items.map((s) => h('li', null,
    h('div', { class: 'cell-main' }, h('span', { class: 't1', text: s.current ? 'Этот сеанс' : (s.ip || 'Другой сеанс') }), h('span', { class: 't2' }, 'вход ', timeEl(s.created_at), s.current && s.ip ? ` · ${s.ip}` : '')),
    s.current ? h('span', { class: 'chip accent', text: 'сейчас' }) : button('Завершить', { kind: 'ghost', size: 'sm', onClick: async () => {
      try { await del(`/portal/sessions/${s.id}`); toast('Сеанс завершён'); loadSessions(); } catch (err) { toastError(err); }
    } }))))))
    .catch((err) => fill(sessionsBody, h('div', { class: 'panel-body' }, errorState(err))));
  loadSessions();
}

async function addTotp(refresh) {
  let data;
  try { data = await post('/portal/totp'); } catch (err) { toastError(err); return; }
  const m = modal({ title: 'Приложение-аутентификатор', subtitle: 'Новый способ входа', size: 'lg',
    body: totpForm(data, async (code) => { const st = await post(`/portal/totp/${encodeURIComponent(data.factor_id)}/confirm`, { code }); m.close(); toast('Приложение подключено'); refresh(st); }) });
}

async function addPhone(refresh) {
  let data;
  try { data = await post('/portal/devices/enrollment'); } catch (err) { toastError(err); return; }
  const before = me.factors.devices.length;
  const live = h('p', { class: 'hint', role: 'status', 'aria-live': 'polite', text: 'Ждём, когда телефон привяжется…' });
  let timer = 0;
  const m = modal({ title: 'Привязать телефон', subtitle: 'MFA Gate Authenticator для Android', size: 'lg', body: h('div', { class: 'stack-s' }, phoneSteps(data), live),
    actions: [button('Готово', { kind: 'secondary', onClick: () => m.close() })], onClose: () => { clearInterval(timer); refresh(); } });
  timer = setInterval(async () => {
    try {
      const st = await get('/portal/me');
      if (st.factors && st.factors.devices.length > before) { clearInterval(timer); me = st; toast('Телефон привязан'); m.close(); }
    } catch { clearInterval(timer); }
  }, 3000);
}

async function newBackupCodes(refresh, hadCodes) {
  if (hadCodes && !await confirmDialog({ title: 'Создать новые резервные коды?', text: 'Прежние неиспользованные коды перестанут действовать.', confirmLabel: 'Создать', action: async () => {} })) return;
  let codes;
  try { ({ codes } = await post('/portal/backup-codes')); } catch (err) { toastError(err); return; }
  const text = codes.join('\n');
  const download = () => {
    const a = h('a', { href: URL.createObjectURL(new Blob([`Резервные коды MFA Gate (${me.login})\n\n${text}\n`], { type: 'text/plain' })), download: 'mfa-gate-backup-codes.txt' });
    document.body.append(a); a.click(); a.remove();
  };
  modal({ title: 'Резервные коды', subtitle: 'Каждый код срабатывает один раз', dismissible: true,
    body: h('div', { class: 'stack-s' }, notice('warning', 'Сохраните коды сейчас: после закрытия окна их нельзя будет посмотреть снова.'),
      h('ol', { class: 'codes' }, codes.map((c) => h('li', null, h('code', { text: c }))))),
    actions: [button('Скачать', { kind: 'secondary', iconName: 'download', onClick: download }),
      button('Скопировать', { kind: 'primary', iconName: 'copy', onClick: (e) => copyText(text, e.currentTarget) })],
    onClose: () => refresh() });
}

root.removeAttribute('aria-busy');
get('/portal/me').then(apply).catch(() => authScreen());
