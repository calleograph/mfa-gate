// Адреса и публикация: что куда сопоставить на reverse proxy и межсетевом экране (docs/network.md).
import { get } from '../api.js';
import { h, icon, pageHead, panel, table, cellMain, chip, notice, button, copyText, loadingRegion } from '../ui.js';

export async function publicationPage(ctx) {
  const { main, signal } = ctx;
  main.append(h('div', { class: 'panel' }, loadingRegion(5)));
  const d = await get('/system/publication', null, signal);
  const head = pageHead({ title: 'Адреса и публикация', desc: 'Каждый компонент MFA Gate: адрес, протокол, направление и кому разрешён доступ. Передайте эту страницу сетевым администраторам.' });

  const components = panel({
    title: 'Компоненты', count: d.components.length,
    body: table({
      caption: 'Адреса компонентов MFA Gate',
      columns: [
        { label: 'Компонент', primary: true, render: (c) => h('div', { class: 'cell-main' }, h('span', { class: 't1' }, c.name, c.planned ? ' ' : null, c.planned ? chip('планируется') : null), h('span', { class: 't2', text: c.purpose })) },
        { label: 'Адрес', render: (c) => h('div', { class: 'cell-main' }, h('span', { class: 't1 mono break', text: c.address }), h('span', { class: 't2', text: c.protocol })) },
        { label: 'Доступ', render: (c) => cellMain(c.access, c.direction) },
        { label: 'Прокси и межсетевой экран', opt: 1, render: (c) => h('div', { class: 'cell-main' }, h('span', { class: 't2', text: c.proxy }), c.upstream ? h('span', { class: 't2 mono', text: `→ ${c.upstream}` }) : null) },
      ],
      rows: d.components,
    }),
  });

  const timeouts = panel({
    title: 'Таймауты VPN-шлюза',
    body: h('div', { class: 'panel-body stack-s' },
      h('p', null, `Ответ на push ждём ${d.push_timeout} с. Таймаут RADIUS на шлюзе ставьте не меньше `, h('strong', { text: `${d.gateway_timeout} с` }),
        ', иначе шлюз оборвёт попытку раньше, чем сотрудник успеет подтвердить вход. Повторы шлюза не создают второй запрос на телефон.'),
      h('p', { class: 'muted', text: 'Ideco NGFW: «Время ожидания, с» по умолчанию 4 — увеличьте до 60–120. Режим запроса «Логин» — для push и TOTP через запрос кода, «Логин и OTP-код» — только для TOTP. Для такого шлюза в RADIUS-клиенте в поле «Кто проверяет доменный пароль» выберите «Сам шлюз».' })),
  });

  const snippet = (title, text, lang) => panel({
    title, actions: [button('Скопировать', { kind: 'secondary', size: 'sm', iconName: 'copy', onClick: (e) => copyText(text, e.currentTarget) })],
    body: h('pre', { class: 'code-block', tabindex: '0', 'aria-label': `Пример конфигурации ${lang}` }, h('code', { text })),
  });

  main.replaceChildren(head, h('div', { class: 'grid' },
    d.warnings.length ? h('div', { class: 'c-12 stack' }, d.warnings.map((w) => notice('warning', w))) : null,
    h('div', { class: 'c-12 stack' }, components),
    h('div', { class: 'c-12 stack' }, timeouts),
    h('div', { class: 'c-6 stack' }, snippet('nginx', d.nginx, 'nginx')),
    h('div', { class: 'c-6 stack' }, snippet('Caddy', d.caddy, 'Caddy')),
    h('p', { class: 'c-12 muted' }, icon('info'), ' Подробная схема, правила межсетевого экрана и профили шлюзов — docs/network.md и docs/integrations.md в репозитории проекта.')));
}
