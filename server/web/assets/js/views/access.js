// Раздел «Доступ»: группы и устройства.
import { get } from '../api.js';
import { h, pageHead, statusBadge, table, cellMain, timeEl, empty, errorState, loadingRegion, pager, button, num, fill } from '../ui.js';
import { crudPage } from './crud.js';
import { revokeDevice } from './users.js';

export function groupsPage(ctx) {
  return crudPage(ctx, {
    area: 'groups', endpoint: '/groups', title: 'Группы',
    desc: 'Группы используются в политиках. Группу MFA Gate можно сопоставить группе каталога — тогда доменные пользователи попадают в неё автоматически.',
    createLabel: 'Создать группу', createTitle: 'Новая группа', createdText: 'Группа создана',
    name: (g) => g.name,
    empty: { iconName: 'users-round', title: 'Групп пока нет', text: 'Создайте группу, чтобы назначать политики не каждому пользователю, а сразу отделу или роли.' },
    columns: () => [
      { label: 'Группа', primary: true, render: (g) => cellMain(g.name, g.description) },
      { label: 'Группа каталога', render: (g) => (g.directory_group ? h('span', { class: 'td-mono', text: g.directory_group }) : null) },
      // сопоставленная группа пополняется из каталога при входе — число добавленных вручную её не описывает
      { label: 'Участников', cls: 'num', render: (g) => (g.directory_group ? h('span', { class: 't2', text: g.members ? `${num(g.members)} вручную + из каталога` : 'из каталога' }) : num(g.members)) },
    ],
    fields: () => [
      { name: 'name', label: 'Название', required: true },
      { name: 'description', label: 'Описание', type: 'textarea', rows: 2 },
      { name: 'directory_group', label: 'Группа каталога', mono: true, placeholder: 'CN=VPN Users,OU=Groups,DC=corp,DC=example,DC=org', hint: 'DN или CN группы AD/LDAP. Пусто — группа только локальная, участников добавляют в карточке пользователя.' },
    ],
    toBody: (v) => ({ name: v.name, description: v.description || '', directory_group: v.directory_group || null }),
    deleteText: (g) => `Группа исчезнет у ${g.directory_group ? 'всех её участников, в том числе пришедших из каталога' : `${num(g.members)} участников`}, а политики, которые на неё ссылаются по имени, перестанут срабатывать для них.`,
  });
}

export async function devicesPage(ctx) {
  const { main, query, can, setQuery, signal } = ctx;
  const SIZE = 50;
  let page = Number(query.get('page')) || 1;
  const head = pageHead({ title: 'Устройства', desc: 'Телефоны, привязанные к пользователям. Привязать новый телефон можно в карточке пользователя.' });
  const results = h('section', { class: 'panel', 'aria-live': 'polite' });
  main.append(head, results);
  async function load() {
    setQuery({ page });
    fill(results, loadingRegion(5));
    let data;
    try { data = await get('/devices', { page, size: SIZE }, signal); } catch (err) {
      if (err.name === 'AbortError') return;
      fill(results, errorState(err, load));
      return;
    }
    const h1 = head.querySelector('h1');
    h1.querySelector('.count')?.remove();
    h1.append(h('span', { class: 'count', text: num(data.total) }));
    if (!data.items.length) {
      fill(results, empty({ iconName: 'smartphone', title: 'Телефонов пока нет', text: 'Откройте карточку пользователя и нажмите «Привязать телефон» — появится QR-код для приложения MFA Gate.', action: can('users:read') ? button('К пользователям', { href: '#/users' }) : null }));
      return;
    }
    fill(results, table({
      caption: 'Устройства',
      columns: [
        { label: 'Устройство', primary: true, render: (d) => cellMain(d.name, [d.platform, d.app_version].filter(Boolean).join(' ')) },
        { label: 'Пользователь', render: (d) => (can('users:read') ? h('span', null, h('a', { href: `#/users/${d.user_id}`, text: d.login })) : d.login) },
        { label: 'Push', opt: true, render: (d) => (d.push_provider === 'none' ? statusBadge('warning', 'Нет канала', 'plain') : d.push_provider.toUpperCase()) },
        { label: 'Статус', render: (d) => (d.status === 'active' ? statusBadge('ok', 'Активен') : statusBadge('neutral', 'Отозван')) },
        { label: 'На связи', render: (d) => timeEl(d.last_seen_at) },
        { cls: 'act', render: (d) => (d.status === 'active' && can('devices:write') ? button('Отозвать', { kind: 'danger-ghost', size: 'sm', onClick: () => revokeDevice(d, load), ariaLabel: `Отозвать ${d.name} (${d.login})` }) : null) },
      ],
      rows: data.items,
    }), data.total > SIZE ? pager({ page, size: SIZE, total: data.total, onPage: (p) => { page = p; load(); } }) : null);
  }
  await load();
}
