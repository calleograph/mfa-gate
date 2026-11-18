// Вход администратора: пароль → второй фактор (push с живым ожиданием, TOTP-код) или первичное подключение TOTP.
import { api, post, setCsrf } from '../api.js';
import { h, icon, brandMark, button, busy, notice, qrImage, secretBox, clear, fill } from '../ui.js';

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
          if (res.next === 'totp_setup') setupStep(); else mfaStep(res.methods || []);
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
    const ac = new AbortController();
    let timer = 0;
    stop = () => { ac.abort(); clearInterval(timer); };
    const box = h('div', { class: 'push-wait' });
    // живая область — только смена состояния и рубежи 1:00 / 0:10, а не каждая секунда таймера
    const live = h('p', { class: 'sr-only', role: 'status', 'aria-live': 'polite' });
    const mark = h('div', { class: 'pw-mark', 'aria-hidden': 'true' }, h('span', { class: 'a' }), h('span', { class: 'b' }));
    const title = h('p', { class: 'pw-title', text: 'Отправляем запрос на телефон…' });
    const text = h('p', { class: 'pw-text', text: 'Откройте MFA Gate на привязанном телефоне и подтвердите вход в панель.' });
    const bar = h('i');
    const time = h('p', { class: 'pw-time', 'aria-hidden': 'true' });
    const actions = h('div', { class: 'auth-panel' });
    box.append(mark, title, text, h('div', { class: 'pw-bar', 'aria-hidden': 'true' }, bar), time, live);
    const say = (t) => { live.textContent = t; };
    if (methods.includes('totp')) actions.append(h('div', { class: 'auth-alt' }, button('Ввести код вместо этого', { kind: 'ghost', iconName: 'key', onClick: () => codeStep(methods) })));
    fill(stage, ...head('Подтвердите вход', 'Пароль принят. Остался второй фактор.'), h('div', { class: 'auth-panel' }, box, actions));

    const fail = (t, d) => {
      stop();
      box.classList.add('is-fail');
      title.textContent = t; text.textContent = d; time.textContent = '';
      say(`${t}. ${d}`);
      const again = button('Отправить снова', { kind: 'primary', iconName: 'refresh-cw', onClick: () => pushStep(methods) });
      actions.prepend(again);
      again.focus();
    };
    (async () => {
      try {
        const r = await post('/auth/mfa/push');
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
          const s = await api(`/auth/mfa/push/${encodeURIComponent(r.request_id)}`, { query: { wait: 25 }, signal: ac.signal });
          if (s.status === 'approved') {
            clearInterval(timer);
            box.classList.add('is-ok');
            title.textContent = 'Вход подтверждён';
            say('Вход подтверждён');
            text.textContent = 'Открываем панель…';
            time.textContent = '';
            setTimeout(() => finish(s.me), 600);
            return;
          }
          if (s.status === 'consumed') return fail('Подтверждение уже использовано', 'Этот запрос подтвердили в другой вкладке или сессии. Отправьте новый.');
          if (s.status === 'denied') return fail('Запрос отклонён на телефоне', 'Если вход пытались выполнить не вы — смените пароль и сообщите ответственному за безопасность.');
          if (s.status !== 'pending' || Date.now() > end + 3000) return fail('Время ожидания вышло', 'Телефон не ответил. Проверьте, что на нём есть интернет, и отправьте запрос снова.');
        }
      } catch (err) {
        if (err.name === 'AbortError') return;
        if (err.status === 401) return sessionLost(err);
        fail('Не удалось отправить запрос', err.message);
      }
    })();
  }

  // -------------------------------------------------------------- первичная настройка TOTP
  async function setupStep() {
    setStep(2);
    fill(stage, ...head('Подключите второй фактор', 'Вход в панель всегда требует второй фактор. Настройте приложение-аутентификатор — это займёт минуту.'),
      h('div', { class: 'push-wait', role: 'status' }, h('p', { class: 'pw-text', text: 'Готовим секрет…' })));
    let data;
    try { data = await post('/auth/totp-setup'); } catch (err) {
      if (err.code === 'already_enrolled') return codeStep(['totp']);
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
