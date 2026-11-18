// Общая страница «список + создать/изменить/удалить» для групп, ресурсов, RADIUS- и API-клиентов, политик.
import { get, post, put, del } from '../api.js';
import { h, pageHead, button, table, empty, errorState, loadingRegion, actionMenu, toast, formDialog, confirmDialog, num, fill } from '../ui.js';

export async function crudPage(ctx, cfg) {
  const { main, can, signal } = ctx;
  const writable = can(cfg.area + ':write');
  const results = h('section', { class: 'panel', 'aria-live': 'polite' }, loadingRegion(4));
  const head = pageHead({
    title: cfg.title, desc: cfg.desc,
    actions: [...(cfg.headActions?.(ctx) || []), writable ? button(cfg.createLabel, { kind: 'primary', iconName: 'plus', onClick: () => openForm() }) : null],
  });
  main.append(head);
  if (cfg.intro) main.append(cfg.intro);
  main.append(results);
  let extras = {};
  let data = null;

  async function load() {
    fill(results, loadingRegion(4));
    try {
      [data, extras] = await Promise.all([get(cfg.endpoint, null, signal), cfg.extras ? cfg.extras(ctx) : {}]);
    } catch (err) {
      if (err.name === 'AbortError') return;
      fill(results, errorState(err, load));
      return;
    }
    const h1 = head.querySelector('h1');
    h1.querySelector('.count')?.remove();
    h1.append(h('span', { class: 'count', text: num(data.total) }));
    if (!data.items.length) {
      fill(results, empty({ ...cfg.empty, action: writable ? button(cfg.createLabel, { kind: 'primary', iconName: 'plus', onClick: () => openForm() }) : null }));
      return;
    }
    const cols = cfg.columns(extras, data);
    if (writable || cfg.rowMenu) {
      cols.push({ cls: 'act', render: (row) => actionMenu([
        writable ? { label: 'Изменить', icon: 'pencil', onSelect: () => openForm(row) } : null,
        ...(cfg.rowMenu?.(row, { ctx, reload: load, extras, writable }) || []),
        writable ? '-' : null,
        writable ? { label: 'Удалить', icon: 'trash-2', danger: true, onSelect: () => remove(row) } : null,
      ].filter((x, i, a) => x && !(x === '-' && (i === a.length - 1))), `Действия: ${cfg.name(row)}`) });
    }
    fill(results, table({ caption: cfg.title, columns: cols, rows: data.items }));
  }

  function openForm(row) {
    const isNew = !row;
    formDialog({
      title: isNew ? cfg.createTitle : `Изменить: ${cfg.name(row)}`,
      size: cfg.formSize || '',
      fields: cfg.fields(extras, row, data),
      values: row ? (cfg.toForm ? cfg.toForm(row) : row) : {},
      submitLabel: isNew ? 'Создать' : 'Сохранить',
      onReady: cfg.onFormReady ? (f) => cfg.onFormReady(f, row) : null,
      async onSubmit(values, f) {
        const body = cfg.toBody(values, row, f);
        if (body === false) return false;
        const res = isNew ? await post(cfg.endpoint, body) : await put(`${cfg.endpoint}/${row.id}`, body);
        toast(isNew ? cfg.createdText : 'Изменения сохранены');
        cfg.afterSave?.(res, isNew);
        load();
      },
    });
  }

  async function remove(row) {
    const ok = await confirmDialog({
      title: `Удалить «${cfg.name(row)}»?`, danger: true, confirmLabel: 'Удалить',
      text: cfg.deleteText?.(row) || 'Действие нельзя отменить.',
      action: () => del(`${cfg.endpoint}/${row.id}`),
    });
    if (ok) { toast('Удалено'); load(); }
  }

  await load();
}
