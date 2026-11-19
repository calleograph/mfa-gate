// Вход администратора: пароль → второй фактор (push с живым ожиданием, TOTP-код) или первичное подключение TOTP.
import { api, post, setCsrf } from '../api.js';
import { h, brandMark, button, busy, notice, qrImage, secretBox, clear, fill, form, pushWait, PASSWORD_FIELDS, passwordErrors, changePassword } from '../ui.js';

export function renderLogin(root, { onDone, message, themeSwitch }) {
  let stop = () => {};
  const foldA = h('div', { class: 'fold is-current', text: 'Пароль' });
  const foldB = h('div', { class: 'fold', text: 'Второй фактор' });
  const stage = h('div');
  const card = h('div', { class: 'auth-card' },
    h('div', { class: 'brand' }, brandMark(), 'MFA Gate'),
    h('div', { class: 'folds', role: 'img', 'aria-label': 'Вход в два шага' }, foldA, foldB),
    stage);
  fill(root, h('div', { class: 'auth' },
    h('main', { class: 'auth-main' }, card),
    h('footer', { class: 'auth-foot' }, themeSwitch())));

  const setStep = (n) => {
    foldA.className = 'fold ' + (n === 1 ? 'is-current' : 'is-done');
    foldB.className = 'fold ' + (n === 2 ? 'is-current' : n > 2 ? 'is-done' : '');
  };
  const finish = (me) => { stop(); setStep(3); setCsrf(me.csrf); onDone(me); };
  const sessionLost = (err) => { stop(); passwordStep(err.message); };
  const head = (title, lead) => [h('h1', { text: title }), lead ? h('p', { class: 'lead', text: lead }) : null];

  // -------------------------------------------------------------- шаг 1: пароль
  function passwordStep(msg) {
    setStep(1);
    document.title = 'Вход — MFA Gate';
    const errBox = h('div', { 'aria-live': 'polite' }, msg ? notice('warning', msg) : null);
    const login = h('input', { class: 'input', id: 'lg-login', name: 'username', autocomplete: 'username', required: true, autocapitalize: 'none', spellcheck: 'false' });
    const pass = h('input', { class: 'input', id: 'lg-pass', name: 'password', type: 'password', autocomplete: 'current-password', required: true });
    const submit = button('Продолжить', { kind: 'primary', type: 'submit' });
    const frm = h('form', { class: 'auth-panel', novalidate: true },
      h('div', { class: 'field' }, h('label', { for: 'lg-login', text: 'Логин' }), login),
      h('div', { class: 'field' }, h('label', { for: 'lg-pass', text: 'Пароль' }), pass),
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
        try {
          const res = await post('/auth/login', { login: login.value.trim(), password: pass.value });
          setCsrf(res.csrf);
          if (res.next === 'password_change') passwordChangeStep(login.value.trim(), pass.value);
          else if (res.next === 'totp_setup') setupStep(); else mfaStep(res.methods || []);
        } catch (err) {
          errBox.append(notice('error', err.message));
          pass.value = '';
          pass.focus();
        }
      });
    });
    fill(stage, ...head('Вход в панель управления', 'Доступ только для администраторов MFA Gate.'), frm);
    login.focus();
  }

  // -------------------------------------------------------------- шаг 1б: смена временного пароля (первый администратор от установщика)
  function passwordChangeStep(userLogin = '', current = '') {
    stop();
    setStep(1);
    document.title = 'Смена пароля — MFA Gate';
    const f = form(PASSWORD_FIELDS, { current_password: current });
    const errBox = h('div', { 'aria-live': 'polite' });
    const submit = button('Сменить пароль и продолжить', { kind: 'primary', type: 'submit' });
    // логин рядом с новым паролем — чтобы менеджер паролей сохранил пару
    const frm = h('form', { class: 'auth-panel', novalidate: true },
      h('input', { class: 'sr-only', type: 'text', name: 'username', autocomplete: 'username', value: userLogin, readonly: true, tabindex: '-1', 'aria-hidden': 'true' }),
      f.el, errBox, submit);
    frm.addEventListener('submit', (e) => {
      e.preventDefault();
      clear(errBox);
      f.clearErrors();
      const v = f.values();
      const missing = PASSWORD_FIELDS.filter((x) => !v[x.name]).map((x) => ({ field: x.name, message: 'Обязательное поле' }));
      const bad = missing.length ? missing : passwordErrors(v);
      if (bad.length) { f.setErrors(bad); return; }
      busy(submit, async () => {
        try {
          const res = await changePassword(v);
          setCsrf(res.csrf);
          if (res.next === 'totp_setup') setupStep(); else mfaStep(res.methods || []);
        } catch (err) {
          if (err.status === 401) return sessionLost(err);
          const rest = err.fields?.length ? f.setErrors(err.fields) : [];
          if (!err.fields?.length || rest.length) errBox.append(notice('error', [err.message, ...rest].join('. ')));
        }
      });
    });
    fill(stage, ...head('Смените временный пароль', 'Этот пароль выдан при установке и годится только для первого входа. Придумайте свой — затем подключите второй фактор.'), frm);
    (current ? f.controls.new_password : f.controls.current_password).focus();
  }

  // -------------------------------------------------------------- шаг 2: второй фактор
  function mfaStep(methods, prefer) {
    setStep(2);
    const usePush = methods.includes('push') && prefer !== 'totp';
    if (usePush) pushStep(methods); else codeStep(methods);
  }

  function codeStep(methods) {
    stop();
    const errBox = h('div', { 'aria-live': 'polite' });
    const code = h('input', { class: 'input otp-input', id: 'lg-code', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 64, placeholder: '000000', 'aria-describedby': 'lg-code-h' });
    const submit = button('Войти', { kind: 'primary', type: 'submit' });
    const frm = h('form', { class: 'auth-panel', novalidate: true },
      h('div', { class: 'field' }, h('label', { for: 'lg-code', text: 'Код из приложения' }), code,
        h('p', { class: 'hint', id: 'lg-code-h', text: 'Шесть цифр из приложения-аутентификатора. Аварийный код, выданный на сервере, тоже подходит.' })),
      errBox, submit,
      methods.includes('push') ? h('div', { class: 'auth-alt' }, button('Отправить запрос на телефон', { kind: 'ghost', iconName: 'smartphone', onClick: () => pushStep(methods) })) : null);
    frm.addEventListener('submit', (e) => {
      e.preventDefault();
      clear(errBox);
      const value = code.value.replace(/\s+/g, '');
      if (value.length < 6) { errBox.append(notice('error', 'Введите код полностью')); code.focus(); return; }
      busy(submit, async () => {
        try { finish(await post('/auth/mfa', { code: value })); } catch (err) {
          if (err.code === 'password_change_required') return passwordChangeStep();
          if (err.status === 401 && err.code !== 'invalid_otp') return sessionLost(err);
          errBox.append(notice('error', err.message));
          code.select();
        }
      });
    });
    fill(stage, ...head('Подтвердите вход', 'Пароль принят. Остался второй фактор.'), frm);
    code.focus();
  }

  function pushStep(methods) {
    stop();
    const w = pushWait({
      start: () => post('/auth/mfa/push'),
      poll: (id, signal) => api(`/auth/mfa/push/${encodeURIComponent(id)}`, { query: { wait: 25 }, signal }),
      text: 'Откройте MFA Gate на привязанном телефоне и подтвердите вход в панель.',
      doneText: 'Открываем панель…',
      onApproved: finish,
      retry: () => pushStep(methods),
      onError: (err) => {
        if (err.code === 'password_change_required') { passwordChangeStep(); return true; }
        if (err.status === 401) { sessionLost(err); return true; }
        return false;
      },
      alt: methods.includes('totp') ? button('Ввести код вместо этого', { kind: 'ghost', iconName: 'key', onClick: () => codeStep(methods) }) : null,
    });
    stop = w.stop;
    fill(stage, ...head('Подтвердите вход', 'Пароль принят. Остался второй фактор.'), w.el);
  }

  // -------------------------------------------------------------- первичная настройка TOTP
  async function setupStep() {
    setStep(2);
    fill(stage, ...head('Подключите второй фактор', 'Вход в панель всегда требует второй фактор. Настройте приложение-аутентификатор — это займёт минуту.'),
      h('div', { class: 'push-wait', role: 'status' }, h('p', { class: 'pw-text', text: 'Готовим секрет…' })));
    let data;
    try { data = await post('/auth/totp-setup'); } catch (err) {
      if (err.code === 'already_enrolled') return codeStep(['totp']);
      if (err.code === 'password_change_required') return passwordChangeStep();
      if (err.status === 401) return sessionLost(err);
      fill(stage, ...head('Подключите второй фактор'), notice('error', err.message), button('Начать заново', { kind: 'primary', onClick: () => passwordStep() }));
      return;
    }
    const grouped = data.secret.replace(/(.{4})/g, '$1 ').trim();
    const errBox = h('div', { 'aria-live': 'polite' });
    const code = h('input', { class: 'input otp-input', id: 'lg-setup', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 8, placeholder: '000000' });
    const submit = button('Подтвердить и войти', { kind: 'primary', type: 'submit' });
    const frm = h('form', { class: 'auth-panel', novalidate: true },
      qrImage(data.qr_svg, 'QR-код для приложения-аутентификатора'),
      h('div', { class: 'field' }, h('span', { class: 'label', text: 'Или введите секрет вручную' }), secretBox(grouped, 'Скопировать секрет')),
      h('div', { class: 'field' }, h('label', { for: 'lg-setup', text: 'Код из приложения' }), code),
      errBox, submit);
    frm.addEventListener('submit', (e) => {
      e.preventDefault();
      clear(errBox);
      const value = code.value.replace(/\s+/g, '');
      if (value.length < 6) { errBox.append(notice('error', 'Введите шесть цифр из приложения')); code.focus(); return; }
      busy(submit, async () => {
        try { finish(await post(`/auth/totp-setup/${encodeURIComponent(data.factor_id)}`, { code: value })); } catch (err) {
          if (err.code === 'password_change_required') return passwordChangeStep();
          if (err.status === 401) return sessionLost(err);
          errBox.append(notice('error', err.message));
          code.select();
        }
      });
    });
    fill(stage, ...head('Подключите второй фактор', 'Вход в панель всегда требует второй фактор. Отсканируйте QR-код в приложении-аутентификаторе и введите код, который оно покажет.'), frm);
    code.focus();
  }

  passwordStep(message);
}
