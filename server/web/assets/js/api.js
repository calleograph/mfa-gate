// Обёртка над REST API: CSRF-заголовок для изменяющих запросов, единый формат ошибок {code, message, fields}.
let csrf = '';
let onUnauthorized = null;

export const setCsrf = (value) => { csrf = value || ''; };
export const onSessionLost = (fn) => { onUnauthorized = fn; };

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || `Ошибка сервера (${status})`);
    this.status = status;
    this.code = body?.code || 'error';
    this.fields = (body?.fields || []).map((f) => ({ field: f.field, message: translate(f.message) }));
  }
}

// Pydantic отвечает по-английски — переводим частые сообщения валидации, остальное показываем как есть.
const RULES = [
  [/^Value error, (.*)$/s, (m) => m[1].charAt(0).toUpperCase() + m[1].slice(1)],
  [/^Field required$/, () => 'Обязательное поле'],
  [/^String should have at least 1 character$/, () => 'Обязательное поле'],
  [/^String should have at least (\d+) characters$/, (m) => `Минимум ${m[1]} символов`],
  [/^String should have at most (\d+) characters?$/, (m) => `Не больше ${m[1]} символов`],
  [/^String should match pattern/, () => 'Недопустимые символы (пробелы и слэши запрещены)'],
  [/^List should have at least 1 item/, () => 'Добавьте хотя бы одно значение'],
  [/^Input should be a valid UUID/, () => 'Выберите значение из списка'],
  [/^Input should be a valid integer/, () => 'Нужно целое число'],
  [/^Input should be greater than or equal to (\S+)$/, (m) => `Не меньше ${m[1]}`],
  [/^Input should be less than or equal to (\S+)$/, (m) => `Не больше ${m[1]}`],
  [/^Input should be /, () => 'Недопустимое значение'],
];
function translate(msg = '') {
  for (const [re, fn] of RULES) { const m = msg.match(re); if (m) return fn(m); }
  return msg;
}

export async function api(path, { method = 'GET', body, query, signal } = {}) {
  let url = '/api/v1' + path;
  if (query) {
    const qs = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== '' && v !== null && v !== undefined));
    if ([...qs].length) url += '?' + qs;
  }
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-CSRF-Token'] = csrf;
  let res;
  try {
    res = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin', signal });
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new ApiError(0, { code: 'network', message: 'Нет связи с сервером. Проверьте сеть и повторите.' });
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* не JSON — ниже сообщение по коду */ }
  if (!res.ok) {
    const err = new ApiError(res.status, data && data.message ? data : { code: 'http', message: `Сервер ответил ошибкой ${res.status}` });
    if (res.status === 401 && !path.startsWith('/auth/') && onUnauthorized) onUnauthorized(err);
    throw err;
  }
  return data;
}

export const get = (path, query, signal) => api(path, { query, signal });
export const post = (path, body = {}) => api(path, { method: 'POST', body });
export const put = (path, body) => api(path, { method: 'PUT', body });
export const patch = (path, body) => api(path, { method: 'PATCH', body });
export const del = (path) => api(path, { method: 'DELETE' });
