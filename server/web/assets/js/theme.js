// Классический (не модульный) скрипт: применяет сохранённую тему до первой отрисовки, чтобы не было вспышки.
try {
  var t = localStorage.getItem('mfa_gate.theme');
  if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
} catch (e) { /* хранилище недоступно — тема по системе */ }
