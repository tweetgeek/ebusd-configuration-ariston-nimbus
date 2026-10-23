'use strict';

// ---------------------------------------------------------------- pomocnicze

const $ = (sel, el = document) => el.querySelector(sel);
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.entries(v).forEach(([p, x]) => el.style.setProperty(p, x));
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}
async function api(path, params = {}) {
  const url = new URL('/api/' + path, location.origin);
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined) url.searchParams.set(k, v);
  const res = await fetch(url);
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}
const nf = new Intl.NumberFormat('pl-PL');
const n = x => nf.format(x);
const fmtT = t => new Date(t * 1000).toLocaleString('pl-PL', { day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit' });
const fmtTs = t => new Date(t * 1000).toLocaleString('pl-PL', { day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
const color = i => `var(--f${i % 8})`;
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter(x => x !== null && x !== undefined && x !== false));
const bytesOf = hex => (hex.match(/../g) || []);

const KIND = { r: 'odczyt', w: 'zapis', b: 'rozgłoszenie', g: 'podsłuchany odczyt', u: 'podsłuchany odczyt', uw: 'podsłuchany zapis' };
const KIND_LONG = {
  r: 'Linia odczytu: ebusd może sam pytać urządzenie o te dane i rozpoznaje też takie same pytania innych urządzeń.',
  w: 'Linia zapisu: ebusd wysyła ją tylko na polecenie, a na magistrali rozpoznaje takie same zapisy innych urządzeń.',
  b: 'Linia rozgłoszenia: ebusd tylko podsłuchuje ramki wysyłane do wszystkich urządzeń.',
  g: 'Linia pasywna: ebusd tylko podsłuchuje odczyty, o które pytają inne urządzenia.',
  uw: 'Linia pasywna: ebusd tylko podsłuchuje zapisy wysyłane przez inne urządzenia.',
};
const NUMERIC = new Set(['UCH', 'SCH', 'BCD', 'D1B', 'D1C', 'UIN', 'SIN', 'D2B', 'D2C', 'ULG', 'SLG', 'MIN']);

const state = {
  tab: null, summary: null, data: {}, range: null, charts: [], pins: [], selected: null,
  filters: {
    lines: { q: '', file: '', only: true, sort: 'count' },
    shapes: { q: '', status: 'none' },
    registers: { q: '', known: 'all' },
  },
};

function addr(a) {
  if (!a) return 'dowolny nadawca';
  const nm = state.summary?.addresses?.[a];
  return nm ? `${a} (${nm})` : `${a}`;
}

// ---------------------------------------------------------------- przekrój ramki

// grupa: { from, len, cls, c, title, label, value, sub }
function renderRow(who, whoSub, bytes, groups) {
  const grid = h('div', { class: 'bytes' });
  const tiles = bytes.map((b, i) => h('div', { class: 'byte', style: { 'grid-column': i + 1 } }, h('span', { class: 'hex' }, b)));
  for (const g of groups) {
    const ts = tiles.slice(g.from, g.from + g.len);
    for (const t of ts) {
      t.classList.add(...(g.cls || '').split(' ').filter(Boolean));
      if (g.c) t.style.setProperty('--c', g.c);
      t.title = g.title || g.label || '';
    }
    const ann = h('div', { class: 'ann ' + (g.cls || ''), style: { 'grid-column': `${g.from + 1} / span ${g.len}`, ...(g.c ? { '--c': g.c } : {}) }, title: g.title || '' },
      h('b', {}, g.label), g.value !== undefined && g.value !== '' ? h('span', { class: 'val' }, g.value) : null, g.sub ? h('span', {}, g.sub) : null);
    ann.addEventListener('mouseenter', () => ts.forEach(t => t.classList.add('hl')));
    ann.addEventListener('mouseleave', () => ts.forEach(t => t.classList.remove('hl')));
    grid.append(ann);
  }
  grid.append(...tiles, ...bytes.map((_, i) => h('div', { class: 'idx', style: { 'grid-column': i + 1 } }, i)));
  return [h('div', { class: 'who' }, h('b', {}, who), whoSub), grid];
}

function headerGroups(q) {
  const b = bytesOf(q);
  return [
    { from: 0, len: 1, cls: 'hdr', label: 'QQ', sub: 'nadawca', title: `adres nadawcy: ${addr(b[0])}` },
    { from: 1, len: 1, cls: 'hdr', label: 'ZZ', sub: b[1] === 'fe' ? 'wszyscy' : 'odbiorca', title: `adres odbiorcy: ${addr(b[1])}` },
    { from: 2, len: 2, cls: 'hdr', label: 'PB SB', sub: 'polecenie', title: 'polecenie: bajt główny i pomocniczy' },
    { from: 4, len: 1, cls: 'hdr', label: 'NN', sub: `${parseInt(b[4], 16)} B`, title: 'liczba bajtów danych nadawcy' },
  ];
}

function fillGaps(groups, total, start) {
  const used = new Array(total).fill(false);
  for (const g of groups) for (let i = g.from; i < g.from + g.len; i++) used[i] = true;
  const out = [...groups];
  let i = start;
  while (i < total) {
    if (used[i]) { i++; continue; }
    let j = i; while (j < total && !used[j]) j++;
    out.push({ from: i, len: j - i, cls: 'unused', label: 'nieczytane', title: 'tych bajtów żadne pole nie odczytuje' });
    i = j;
  }
  return out;
}

function fieldLabel(f, line, fields) {
  if (f.name) return f.name;
  const live = fields.filter(x => !x.ignored);
  return live.length === 1 && !f.ignored ? line.name : f.type;
}

function stripForLine(sample, line, fields) {
  const mb = bytesOf(sample.q), sb = bytesOf(sample.a);
  const idLen = line.idh.length / 2;
  const mg = headerGroups(sample.q);
  if (idLen) mg.push({ from: 5, len: idLen, cls: 'id', label: 'ID', sub: line.idh, title: 'ID z kolumny 8: musi się zgadzać z początkiem danych' });
  const sg = sb.length ? [{ from: 0, len: 1, cls: 'hdr', label: 'NN', sub: `${parseInt(sb[0], 16)} B`, title: 'liczba bajtów odpowiedzi' }] : [];
  (sample.fields || []).forEach(d => {
    const f = fields[d.field];
    const g = {
      from: (d.part === 'm' ? 5 : 1) + d.start, len: d.end - d.start, c: color(d.field),
      cls: f.ignored ? 'ign' : '', label: f.ignored ? 'pominięte' : fit(fieldLabel(f, line, fields), d.end - d.start, d.field),
      value: f.ignored ? '' : `${d.text}${f.unit && d.text !== '' ? ' ' + f.unit : ''}`,
      sub: f.ignored && d.part === 's' && d.start === 0 && line.pbsb === '2000' ? 'maska' : f.template || f.type,
      title: `pole ${d.field + 1}: ${f.name || '(bez nazwy)'}, ${f.template || f.type}, bajty ${d.start}–${d.end - 1} ${d.part === 'm' ? 'danych mastera' : 'odpowiedzi'}, surowo ${d.raw}`,
    };
    (d.part === 'm' ? mg : sg).push(g);
  });
  const wrap = h('div', { class: 'strip' });
  wrap.append(...renderRow('Nadawca', addr(mb[0]), mb, fillGaps(mg, mb.length, 5)));
  if (sb.length) wrap.append(...renderRow('Odpowiedź', addr(mb[1]), sb, fillGaps(sg, sb.length, 1)));
  const legend = (sample.fields || []).filter(d => !fields[d.field].ignored).map(d => {
    const f = fields[d.field];
    return h('span', { class: 'lg', style: { '--c': color(d.field) } }, h('i'), `${d.field + 1}. ${fieldLabel(f, line, fields)} `,
      h('b', {}, `${d.text}${f.unit && d.text !== '' ? ' ' + f.unit : ''}`), h('small', {}, ` (${d.raw})`));
  });
  return h('div', {}, h('div', { class: 'strip-wrap' }, wrap), legend.length ? h('div', { class: 'legend' }, legend) : null);
}

// nazwa mieści się w kolumnach pola (ok. 8 znaków na bajt); inaczej numer pola, a pełna nazwa jest w legendzie
function fit(label, len, i) {
  return label.length <= len * 8 ? label : `pole ${i + 1}`;
}

function regValue(hexs, reg) {
  const v = parseInt(hexs.slice().reverse().join(''), 16);
  const bits = 8 * hexs.length;
  const s = v >= 2 ** (bits - 1) ? v - 2 ** bits : v;
  return s === v ? n(v) : `${n(v)} (ze znakiem ${n(s)})`;
}

function stripForSegments(sample, names = {}, focus = null) {
  const mb = bytesOf(sample.q), sb = bytesOf(sample.a);
  const mg = headerGroups(sample.q), sg = sb.length ? [{ from: 0, len: 1, cls: 'hdr', label: 'NN', sub: `${parseInt(sb[0], 16)} B` }] : [];
  const order = [...new Set((sample.segments || []).filter(s => s.reg).map(s => s.reg))];
  for (const s of sample.segments || []) {
    const off = s.part === 'm' ? 5 : 1;
    const raw = (s.part === 'm' ? mb : sb).slice(off + s.start, off + s.end);
    const nm = s.reg ? (names[s.reg] || '') : '';
    const c = s.reg ? color(order.indexOf(s.reg)) : null;
    let g;
    if (s.role === 'id') g = { cls: 'id', label: `ID ${s.reg}`, sub: nm || 'rejestr', title: `identyfikator rejestru ${s.reg}` };
    else if (s.role === 'value') g = { c, label: nm || `${s.reg} ?`, value: regValue(raw, s.reg), sub: `${raw.length * 8} bitów`, cls: focus && s.reg !== focus ? 'dim' : '' };
    else if (s.role === 'mask') g = { cls: 'ign', c: 'var(--muted)', label: 'maska', value: raw[0] === '00' ? 'dane nieważne' : '', sub: 'ważność' };
    else if (s.role === 'status') g = { cls: 'ign', c, label: 'status', sub: s.reg };
    else g = { cls: 'ign', c, label: s.role, value: regValue(raw, s.reg), sub: s.reg };
    (s.part === 'm' ? mg : sg).push({ from: off + s.start, len: s.end - s.start, ...g });
  }
  const wrap = h('div', { class: 'strip' });
  wrap.append(...renderRow('Nadawca', addr(mb[0]), mb, fillGaps(mg, mb.length, 5)));
  if (sb.length) wrap.append(...renderRow('Odpowiedź', addr(mb[1]), sb, fillGaps(sg, sb.length, 1)));
  return h('div', { class: 'strip-wrap' }, wrap);
}

// ---------------------------------------------------------------- wykresy

function destroyCharts() {
  for (const c of state.charts) { c.u?.destroy(); c.ro?.disconnect(); }
  state.charts = [];
}

function chartCard(title, sub, fetcher, opts = {}) {
  const plot = h('div', { class: 'plot' }, h('span', { class: 'loading' }, 'Wczytuję przebieg…'));
  const info = h('span', {}, sub || '');
  const card = h('div', { class: 'chart' }, h('div', { class: 'head' }, h('b', {}, title), info, opts.actions || null), plot);
  const c = { card, plot, info, fetcher, opts, u: null };
  c.seq = 0;
  c.load = async () => {
    const seq = ++c.seq;          // odpowiedź na starsze żądanie (np. sprzed zmiany zakresu) jest pomijana
    let d;
    try { d = await fetcher(state.range); } catch (e) { if (seq === c.seq) put(plot, h('span', { class: 'errtext' }, e.message)); return; }
    if (seq !== c.seq) return;
    info.textContent = `${sub ? sub + ', ' : ''}${n(d.n)} odczytów w zakresie`;
    if (!d.t.length) { c.u?.destroy(); c.u = null; put(plot, h('span', { class: 'loading' }, 'Brak wartości w tym zakresie.')); return; }
    const data = [d.t, opts.transform ? d.v.map(opts.transform) : d.v];
    if (c.u) { c.u.setData(data); return; }
    put(plot, );
    c.u = new uPlot(chartOpts(plot.clientWidth || 600, opts), data, plot);
    c.ro = new ResizeObserver(() => c.u && c.u.setSize({ width: plot.clientWidth, height: 180 }));
    c.ro.observe(plot);
  };
  state.charts.push(c);
  return c;
}

// podpisy osi czasu po polsku: godziny przy krótkim zakresie, daty przy dłuższym
function timeTicks(u, splits) {
  const span = u.scales.x.max - u.scales.x.min;
  const o = span < 2 * 86400 ? { hour: '2-digit', minute: '2-digit' } : span < 8 * 86400 ? { day: 'numeric', month: 'numeric', hour: '2-digit' } : { day: 'numeric', month: 'numeric' };
  return splits.map(v => new Date(v * 1000).toLocaleString('pl-PL', o));
}

function chartOpts(width, opts) {
  const ink = cssVar('--muted'), grid = cssVar('--line');
  const stroke = cssVar(opts.color || '--f0') || '#3b6fd8';
  const enumMap = opts.values;
  const fmt = v => v === null || v === undefined ? '–' : enumMap && enumMap[v] !== undefined ? `${enumMap[v]} (${v})` : `${+v.toFixed(3)}${opts.unit ? ' ' + opts.unit : ''}`;
  return {
    width, height: 180,
    cursor: { sync: { key: 'bus' }, drag: { x: true, y: false, setScale: false } },
    scales: { x: { time: true } },
    series: [
      { value: (u, v) => v === null ? '–' : fmtTs(v) },
      { label: opts.label || 'wartość', stroke, width: 1.4, value: (u, v) => fmt(v),
        paths: opts.stepped ? uPlot.paths.stepped({ align: 1 }) : undefined, points: { show: false } },
    ],
    axes: [
      { stroke: ink, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, values: timeTicks },
      { stroke: ink, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, size: enumMap ? 110 : 60,
        values: enumMap ? (u, vals) => vals.map(v => enumMap[v] ?? (Number.isInteger(v) ? v : '')) : (u, vals) => vals.map(v => nf.format(v)) },
    ],
    hooks: {
      setSelect: [u => {
        if (u.select.width > 6) {
          const a = u.posToVal(u.select.left, 'x'), b = u.posToVal(u.select.left + u.select.width, 'x');
          setRange({ from: a, to: b });
        }
        u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
      }],
    },
  };
}

function setRange(r) {
  state.range = r;
  $('#range-label').textContent = r ? `Zakres ${fmtT(r.from)} – ${fmtT(r.to)}` : 'Cały okres logów';
  $('#range-reset').hidden = !r;
  for (const c of state.charts) c.load();
}

function chartsSection(title, cards, note) {
  if (!cards.length) return null;
  const sec = h('div', {}, h('h3', {}, title),
    h('p', { class: 'note' }, note || 'Zaznacz fragment wykresu myszą, żeby przybliżyć wszystkie wykresy do tego okresu.'),
    h('div', { class: 'charts' }, cards.map(c => c.card)));
  queueMicrotask(() => cards.forEach(c => c.load()));
  return sec;
}

// ---------------------------------------------------------------- listy

const LISTS = {
  lines: {
    load: () => api('lines'),
    filters(f) {
      const files = state.summary?.csv || [];
      return [
        h('input', { type: 'search', placeholder: 'Szukaj nazwy, ID, polecenia', value: f.q, oninput: e => { f.q = e.target.value; renderRows(); } }),
        h('div', { class: 'row2' },
          h('select', { onchange: e => { f.file = e.target.value; renderRows(); }, 'aria-label': 'Plik' },
            h('option', { value: '' }, 'Oba pliki'), files.map(x => h('option', { value: x, selected: f.file === x }, x))),
          h('select', { onchange: e => { f.sort = e.target.value; renderRows(); }, 'aria-label': 'Kolejność' },
            h('option', { value: 'count', selected: f.sort === 'count' }, 'Najwięcej ramek'),
            h('option', { value: 'file', selected: f.sort === 'file' }, 'Kolejność w pliku'))),
        h('label', {}, h('input', { type: 'checkbox', checked: f.only, onchange: e => { f.only = e.target.checked; renderRows(); } }), 'Tylko linie z ramkami w logu'),
      ];
    },
    rows(list, f) {
      const q = f.q.toLowerCase();
      let out = list.filter(l => (!f.only || l.count) && (!f.file || l.file === f.file) &&
        (!q || `${l.name} ${l.circuit} ${l.idh} ${l.pbsb} ${l.zz}`.toLowerCase().includes(q)));
      out = f.sort === 'count' ? out.sort((a, b) => b.count - a.count) : out.sort((a, b) => a.idx - b.idx);
      const max = Math.max(1, ...list.map(l => l.count));
      return out.map(l => ({
        key: String(l.idx), cls: l.errors ? 'err' : l.count ? 'ok' : 'none',
        title: l.name, sub: `${l.circuit}, ${l.zz} ${l.pbsb} ${l.idh || '(bez ID)'}${l.qq ? ', od ' + l.qq : ''}`,
        num: l.count ? n(l.count) : '0', bar: l.count ? Math.log10(l.count + 1) / Math.log10(max + 1) : 0,
      }));
    },
    show: key => showLine(+key),
  },
  shapes: {
    load: () => api('shapes'),
    filters(f) {
      return [
        h('input', { type: 'search', placeholder: 'Szukaj rejestru, polecenia, adresu', value: f.q, oninput: e => { f.q = e.target.value; renderRows(); } }),
        h('select', { onchange: e => { f.status = e.target.value; renderRows(); }, 'aria-label': 'Które układy' },
          h('option', { value: 'none', selected: f.status === 'none' }, 'Bez pasującej linii'),
          h('option', { value: 'errors', selected: f.status === 'errors' }, 'Z błędami odczytu'),
          h('option', { value: 'all', selected: f.status === 'all' }, 'Wszystkie układy')),
        h('p', { class: 'note', style: { margin: 0 } }, 'Układ to ramki z tym samym nadawcą, odbiorcą, poleceniem i listą rejestrów.'),
      ];
    },
    rows(list, f) {
      const q = f.q.toLowerCase();
      const max = Math.max(1, ...list.map(s => s.count));
      return list.filter(s => (f.status === 'all' || (f.status === 'none' ? !s.matched : s.errors)) &&
        (!q || `${s.qq} ${s.zz} ${s.pbsb} ${s.ids} ${s.line_name || ''}`.toLowerCase().includes(q)))
        .sort((a, b) => b.count - a.count)
        .map(s => ({
          key: String(s.id), cls: !s.matched ? 'none' : s.errors ? 'err' : 'ok',
          title: `${s.qq} → ${s.zz}, ${s.pbsb}`, sub: `${s.ids}${s.line_name ? ', linia ' + s.line_name : ''}`,
          num: n(s.count), bar: Math.log10(s.count + 1) / Math.log10(max + 1),
        }));
    },
    show: key => showShape(+key),
  },
  registers: {
    load: () => api('registers'),
    filters(f) {
      return [
        h('input', { type: 'search', placeholder: 'Szukaj rejestru albo nazwy', value: f.q, oninput: e => { f.q = e.target.value; renderRows(); } }),
        h('select', { onchange: e => { f.known = e.target.value; renderRows(); }, 'aria-label': 'Które rejestry' },
          h('option', { value: 'all', selected: f.known === 'all' }, 'Wszystkie rejestry'),
          h('option', { value: 'unknown', selected: f.known === 'unknown' }, 'Nieodczytywane przez żadną linię'),
          h('option', { value: 'changing', selected: f.known === 'changing' }, 'Nieodczytywane i zmienne'),
          h('option', { value: 'known', selected: f.known === 'known' }, 'Odczytywane przez linie CSV')),
      ];
    },
    rows(list, f) {
      const q = f.q.toLowerCase();
      const max = Math.max(1, ...list.map(r => r.count));
      return list.filter(r => {
        const known = r.decoded > 0;
        if (f.known === 'unknown' && known) return false;
        if (f.known === 'changing' && (known || r.distinct < 2)) return false;
        if (f.known === 'known' && !known) return false;
        return !q || `${r.reg} ${r.src} ${r.names.join(' ')} ${r.extra_name}`.toLowerCase().includes(q);
      }).sort((a, b) => b.count - a.count).map(r => ({
        key: `${r.src}/${r.reg}`, cls: r.decoded ? 'ok' : 'none',
        title: `${r.reg}  ${r.names[0] || r.extra_name || ''}`.trim(),
        sub: `${r.src}, ${r.distinct > 1 ? `${n(r.distinct)} różnych wartości` : 'stała wartość'}${r.decoded ? '' : ', bez linii'}`,
        num: n(r.count), bar: Math.log10(r.count + 1) / Math.log10(max + 1),
      }));
    },
    show: key => { const i = key.lastIndexOf('/'); showRegister(key.slice(0, i), key.slice(i + 1)); },
  },
};

function renderFilters() {
  put($('#filters'), ...LISTS[state.tab].filters(state.filters[state.tab]));
}

function renderRows() {
  const L = LISTS[state.tab], list = state.data[state.tab];
  if (!list) { put($('#rows'), h('p', { class: 'more' }, 'Wczytuję…')); return; }
  const rows = L.rows(list, state.filters[state.tab]);
  const shown = rows.slice(0, 800);
  put($('#rows'), ...shown.map(r => h('button', {
    type: 'button', class: `row ${r.cls}`, 'aria-current': state.selected === r.key ? 'true' : 'false',
    onclick: () => { location.hash = `#${state.tab}/${encodeURIComponent(r.key)}`; },
  }, h('span', {}, h('b', {}, r.title), h('small', {}, r.sub)),
    h('span', { class: 'num' }, r.num, h('i', { class: 'bar', style: { width: `${Math.round(r.bar * 100)}%` } })))),
  rows.length > shown.length ? h('p', { class: 'more' }, `Pokazano 800 z ${n(rows.length)}. Zawęź wyszukiwanie.`) : null,
  rows.length ? null : h('p', { class: 'more' }, 'Nic nie pasuje do filtrów.'));
}

// ---------------------------------------------------------------- widoki szczegółów

// nowy widok: najpierw loading() (usuwa wykresy poprzedniego widoku), potem detail() z treścią
function loading(msg) {
  destroyCharts();
  detail(h('p', { class: 'loading' }, msg));
}

function detail(...kids) {
  const d = $('#detail');
  put(d, ...kids.flat().filter(Boolean));
  d.scrollTop = 0;
}

function showIntro() {
  destroyCharts();
  const tpl = $('#intro').content.cloneNode(true);
  const example = { q: '131e2000027c4f', a: '03011700' };
  const mg = [...headerGroups(example.q), { from: 5, len: 2, cls: 'id', label: 'dane', sub: 'rejestr 7c4f' }];
  const sg = [{ from: 0, len: 1, cls: 'hdr', label: 'NN', sub: '3 B' }, { from: 1, len: 1, cls: 'ign', c: 'var(--muted)', label: 'maska', sub: 'ważność' },
    { from: 2, len: 2, c: color(0), label: 'wartość', value: '23 W', sub: 'bajty od młodszego' }];
  const wrap = h('div', { class: 'strip' });
  wrap.append(...renderRow('Nadawca', 'menedżer energii pyta', bytesOf(example.q), mg), ...renderRow('Odpowiedź', 'jednostka zewnętrzna', bytesOf(example.a), sg));
  tpl.querySelector('#intro-strip').append(wrap);
  detail(tpl);
}

function linkLine(idx, text) {
  return h('button', { type: 'button', class: 'linkish', onclick: () => { location.hash = `#lines/${idx}`; } }, text);
}
function linkReg(src, reg, text) {
  return h('button', { type: 'button', class: 'linkish', onclick: () => { location.hash = `#registers/${encodeURIComponent(`${src}/${reg}`)}`; } }, text);
}

const CSV_COLS = ['typ', 'obwód', 'nazwa', 'opis', 'QQ nadawca', 'ZZ odbiorca', 'PBSB polecenie', 'ID'];
const FIELD_COLS = ['nazwa pola', 'część', 'typ', 'dzielnik / wartości', 'jednostka', 'opis'];
function csvRow(cols, fields) {
  const out = cols.slice(0, 8).map((v, i) => h('div', { class: `col ${i >= 4 ? 'key' : ''}`, title: CSV_COLS[i] }, h('i', {}, CSV_COLS[i]), h('span', {}, v)));
  let fi = 0;
  for (let i = 8; i < cols.length; i += 6) {
    const g = cols.slice(i, i + 6);
    if (!g[2] || !g[2].trim()) continue;
    const f = fields[fi];
    out.push(h('div', { class: `fgroup ${f && f.ignored ? 'ign' : ''}`, style: { '--c': color(fi) }, title: `pole ${fi + 1}` },
      g.map((v, k) => (v || k < 3) ? h('div', { class: 'col' }, h('i', {}, FIELD_COLS[k]), h('span', { title: v }, v.length > 40 ? v.slice(0, 38) + '…' : v)) : null)));
    fi++;
  }
  return h('div', { class: 'csvrow' }, out);
}

async function showLine(idx) {
  loading('Wczytuję linię…');
  let d;
  try { d = await api('line', { idx }); } catch (e) { return detail(h('p', { class: 'errtext' }, e.message)); }
  const ln = d.line;
  const rule = `ebusd przypisuje tej linii ramki, w których odbiorca to ${addr(ln.zz)}, polecenie ${ln.pbsb}` +
    (ln.idh ? `, a dane zaczynają się od ${ln.idh}` : '') + (ln.qq ? `, wysłane przez ${addr(ln.qq)}.` : ', od dowolnego nadawcy.');
  const longer = d.rivals.filter(r => r.idh.length > ln.idh.length && r.idh.startsWith(ln.idh) && r.count);
  const shorter = d.rivals.filter(r => r.idh.length < ln.idh.length);
  const stats = ln.count
    ? `W logach jest ${n(ln.count)} takich ramek, od ${fmtT(ln.first)} do ${fmtT(ln.last)}. Ostatnia odczytana wartość: ${ln.last_text || '(brak)'}.`
    : 'W logach nie ma żadnej ramki dla tej linii.' + (ln.kind === 'r' ? ' Linia odczytu może mimo to działać, jeśli ebusd sam odpytuje urządzenie.' : '');

  const picker = h('div', { class: 'samples', role: 'group', 'aria-label': 'Przykładowe ramki' });
  const stripBox = h('div');
  const pick = (s, btn) => {
    picker.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', b === btn ? 'true' : 'false'));
    put(stripBox, stripForLine(s, ln, d.fields), s.error ? h('p', { class: 'errtext' }, s.error) : null);
  };
  d.samples.forEach((s, i) => {
    const btn = h('button', { type: 'button', 'aria-pressed': 'false' }, h('span', { class: 't' }, fmtT(s.t) + ' '), s.error ? 'błąd' : (s.text || '(bez wartości)'));
    btn.addEventListener('click', () => pick(s, btn));
    picker.append(btn);
  });

  const live = d.fields.map((f, i) => ({ f, i })).filter(({ f }) => !f.ignored && NUMERIC.has(f.type));
  const cards = ln.count ? live.map(({ f, i }) => chartCard(fieldLabel(f, ln, d.fields), `pole ${i + 1}, ${f.template || f.type}${f.unit ? ', ' + f.unit : ''}`,
    r => api('series', { line: idx, field: i, from: r?.from, to: r?.to }),
    { color: `--f${i % 8}`, unit: f.unit, values: f.values, stepped: !!f.values || ['UCH', 'BCD'].includes(f.type) && !f.divisor, label: fieldLabel(f, ln, d.fields) })) : [];

  detail(
    h('h2', {}, ln.name, h('span', { class: 'tag' }, KIND[ln.kind] || ln.kind)),
    h('p', { class: 'meta' }, `obwód ${ln.circuit}, ${ln.file}, wiersz ${ln.lineno}`),
    h('p', { class: 'lead' }, rule, ' ', stats),
    h('p', { class: 'note' }, KIND_LONG[ln.kind] || ''),
    ln.error ? h('p', { class: 'warnbox' }, `Nie da się odczytać definicji: ${ln.error}`) : null,
    ln.errors ? h('p', { class: 'warnbox' }, `${n(ln.errors)} ramek pasuje do tej linii, ale ebusd nie umie ich odczytać i zgłasza błąd zamiast wartości. Przykłady są na dole.`) : null,
    !ln.count && longer.length ? h('p', { class: 'warnbox' }, 'Ramki z tym początkiem danych przejmuje ', longer.map((r, i) => [i ? ', ' : '', linkLine(r.idx, r.name)]), ', bo ma dłuższe ID.') : null,
    h('h3', {}, 'Wiersz w pliku CSV'),
    csvRow(d.columns, d.fields),
    d.samples.length ? [h('h3', {}, 'Jak ebusd czyta ramkę z logu'),
      h('p', { class: 'note' }, 'Każdy kafelek to jeden bajt ramki (szesnastkowo). Kolor pokazuje, które pole z wiersza CSV go odczytuje. Wartości wielobajtowe zapisane są od młodszego bajtu. Najedź na podpis, żeby podświetlić bajty. Poniżej różne wartości z logu.'),
      picker, stripBox] : null,
    h('h3', {}, 'Pola'),
    h('table', {}, h('thead', {}, h('tr', {}, ['', 'Nazwa', 'Część', 'Typ', 'Bajty', 'Dzielnik albo wartości', 'Jednostka'].map(x => h('th', {}, x)))),
      h('tbody', {}, d.fields.map((f, i) => h('tr', {},
        h('td', {}, h('span', { class: 'sw', style: { '--c': f.ignored ? 'var(--muted)' : color(i) } }), i + 1),
        h('td', {}, f.ignored ? 'pominięte' : fieldLabel(f, ln, d.fields), f.comment ? h('div', { class: 'note' }, f.comment) : null),
        h('td', {}, f.part === 'm' ? 'nadawca' : 'odpowiedź'),
        h('td', { class: 'mono' }, f.template ? `${f.template} (${f.type})` : f.type),
        h('td', { class: 'mono' }, f.size),
        h('td', {}, f.values ? Object.entries(f.values).map(([k, v]) => `${k}=${v}`).join(', ') : f.divisor ? `÷ ${f.divisor}` : ''),
        h('td', {}, f.unit))))),
    chartsSection('Wartości w czasie', cards),
    d.regs.length ? [h('h3', {}, 'Rejestry odczytywane przez tę linię'),
      h('p', {}, d.regs.map((x, i) => { const [src, reg] = x.split('|'); return [i ? ', ' : '', linkReg(src, reg, `${reg} w ramkach ${src}`)]; }))] : null,
    d.rivals.length ? [h('h3', {}, 'Inne linie dla tego samego polecenia'),
      h('p', { class: 'note' }, 'Te linie mają ten sam odbiorca i polecenie, a ich ID jest początkiem ID tej linii albo odwrotnie. Do ramki ebusd bierze tę z najdłuższym pasującym ID.'),
      h('table', {}, h('thead', {}, h('tr', {}, ['Linia', 'ID', 'Nadawca', 'Plik', 'Ramek'].map(x => h('th', {}, x)))),
        h('tbody', {}, [{ ...ln, self: true }, ...d.rivals].sort((a, b) => b.idh.length - a.idh.length).map(r => h('tr', { class: r.self ? 'win' : '' },
          h('td', {}, r.self ? `${r.name} (ta linia)` : linkLine(r.idx, r.name)), h('td', { class: 'mono' }, r.idh || '(bez ID)'),
          h('td', { class: 'mono' }, r.qq || 'dowolny'), h('td', {}, `${r.file}:${r.lineno}`), h('td', { class: 'mono' }, n(r.count))))))] : null,
    shorter.length && ln.count ? h('p', { class: 'note' }, 'Linie z krótszym ID dostają tylko te ramki, których ta linia nie przejmuje.') : null,
    d.error_samples.length ? [h('h3', {}, 'Ramki, których nie udało się odczytać'),
      d.error_samples.map(s => [h('p', { class: 'errtext' }, `${fmtTs(s.t)}: ${s.error}`), stripForLine({ ...s, fields: [] }, ln, d.fields)])] : null,
  );
  const first = picker.querySelector('button:last-child');
  if (first) first.click();
}

async function showShape(id) {
  loading('Wczytuję układ…');
  let d;
  try { d = await api('shape', { id }); } catch (e) { return detail(h('p', { class: 'errtext' }, e.message)); }
  const names = {};
  for (const [r, x] of Object.entries(d.names)) names[r] = x.decoded[0] || x.extra || x.defined[0] || '';
  const picker = h('div', { class: 'samples', role: 'group', 'aria-label': 'Przykładowe ramki' });
  const box = h('div');
  d.samples.forEach((s, i) => {
    const btn = h('button', { type: 'button', 'aria-pressed': 'false' }, `przykład ${i + 1}`);
    btn.addEventListener('click', () => {
      picker.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', b === btn ? 'true' : 'false'));
      put(box, s.segments ? stripForSegments(s, names) : stripForSegments({ ...s, segments: [] }),
        s.line >= 0 ? h('p', {}, 'ebusd dopasowuje tę ramkę do linii ', linkLine(s.line, d.lines.find(l => l.idx === s.line)?.name || s.line),
          s.error ? h('span', { class: 'errtext' }, `, ale nie umie jej odczytać: ${s.error}`) : `: ${s.text}`) : null);
    });
    picker.append(btn);
  });
  const regs = d.ids.includes(' B ') ? [] : d.ids.split(' ');
  const src = `${d.qq}>${d.zz} ${d.pbsb}`;
  detail(
    h('h2', {}, `${d.qq} → ${d.zz}, polecenie ${d.pbsb}`),
    h('p', { class: 'meta' }, `nadawca ${addr(d.qq)}, odbiorca ${addr(d.zz)}`),
    h('p', { class: 'lead' }, `${n(d.count)} ramek w logach. `,
      d.lines.length ? ['Pasujące linie: ', d.lines.map((l, i) => [i ? ', ' : '', linkLine(l.idx, l.name), ` (${n(l.count)})`]), '.']
        : 'Żadna linia CSV nie pasuje do tych ramek, więc ebusd zgłasza je jako nieznane.'),
    d.errors ? h('p', { class: 'warnbox' }, `${n(d.errors)} ramek pasuje do linii, ale nie daje się ich odczytać.`) : null,
    h('h3', {}, 'Ramka podzielona na rejestry'),
    h('p', { class: 'note' }, regs.length
      ? 'Ten podział nie korzysta z plików CSV: wynika z budowy ramek Ariston (ID rejestru, wartość, czasem status). Rejestry 4xxx–7xxx mają 2 bajty, pozostałe 1 bajt.'
      : 'Ramka nie ma układu rejestrów znanego z protokołu Ariston (np. zegar, harmonogram albo ogłoszenie adresu).'),
    picker, box,
    regs.length ? [h('h3', {}, 'Rejestry w tym układzie'),
      h('table', {}, h('thead', {}, h('tr', {}, ['Rejestr', 'Odczytywany jako', 'Zdefiniowany w CSV jako', 'extra_registers.csv'].map(x => h('th', {}, x)))),
        h('tbody', {}, regs.map(r => h('tr', {},
          h('td', { class: 'mono' }, linkReg(src, r, r)), h('td', {}, d.names[r]?.decoded.join(', ') || '–'),
          h('td', {}, d.names[r]?.defined.join(', ') || '–'), h('td', {}, d.names[r]?.extra || '–')))))] : null,
    d.candidates.length ? [h('h3', {}, 'Linie z tym samym odbiorcą i poleceniem'),
      h('p', { class: 'note' }, 'Dlaczego każda z nich pasuje albo nie pasuje do pierwszej przykładowej ramki.'),
      h('table', {}, h('thead', {}, h('tr', {}, ['Linia', 'ID', 'Nadawca', 'Wynik'].map(x => h('th', {}, x)))),
        h('tbody', {}, d.candidates.map(c => h('tr', { class: c.why === 'pasuje' ? 'win' : '' },
          h('td', {}, linkLine(c.idx, c.name)), h('td', { class: 'mono' }, c.idh || '(bez ID)'), h('td', { class: 'mono' }, c.qq || 'dowolny'), h('td', {}, c.why)))))]
      : h('p', { class: 'note' }, `W plikach CSV nie ma żadnej linii dla odbiorcy ${d.zz} i polecenia ${d.pbsb}.`),
  );
  picker.querySelector('button')?.click();
}

function regChart(src, reg, opt, title, actions) {
  return chartCard(title, `${src}${opt.signed ? ', ze znakiem' : ''}${opt.div > 1 ? `, ÷ ${opt.div}` : ''}`,
    r => api('regseries', { src, reg, signed: opt.signed ? 1 : 0, from: r?.from, to: r?.to }),
    { color: opt.color || '--f0', transform: opt.div > 1 ? v => v / opt.div : null, label: reg, actions });
}

async function showRegister(src, reg) {
  loading('Wczytuję rejestr…');
  let d;
  try { d = await api('register', { src, reg }); } catch (e) { return detail(h('p', { class: 'errtext' }, e.message)); }
  const opt = { signed: d.width === 2 && /temp|°C/.test(JSON.stringify(d.decoded_by) + d.note), div: 1 };
  const names = [...new Set(d.decoded_by.map(x => x.field_name || x.name))];
  const chartBox = h('div', { class: 'charts' });
  const redraw = () => {
    destroyCharts();
    const cards = [regChart(src, reg, opt, names[0] || `rejestr ${reg}`)];
    state.pins.filter(p => !(p.src === src && p.reg === reg)).forEach((p, i) => cards.push(regChart(p.src, p.reg, { ...p, color: `--f${(i + 1) % 8}` }, `${p.reg} ${p.name || ''}`,
      h('button', { type: 'button', class: 'linkish', onclick: () => { state.pins = state.pins.filter(x => x !== p); redraw(); } }, 'Usuń z porównania'))));
    put(chartBox, ...cards.map(c => c.card));
    cards.forEach(c => c.load());
  };
  const pinned = () => state.pins.some(p => p.src === src && p.reg === reg);
  const pinBtn = h('button', { type: 'button', class: 'btn' }, pinned() ? 'Przypięty do porównania' : 'Przypnij do porównania');
  pinBtn.addEventListener('click', () => {
    if (!pinned()) state.pins.push({ src, reg, signed: opt.signed, div: opt.div, name: names[0] || d.extra_name });
    pinBtn.textContent = 'Przypięty do porównania';
  });
  const sample = d.samples[d.samples.length - 1];
  detail(
    h('h2', {}, reg, names.length ? h('span', { class: 'tag ok' }, names.join(', ')) : d.extra_name ? h('span', { class: 'tag' }, d.extra_name) : h('span', { class: 'tag' }, 'bez linii')),
    h('p', { class: 'meta' }, `rejestr ${d.width * 8}-bitowy w ramkach ${src}: nadawca ${addr(src.slice(0, 2))}, odbiorca ${addr(src.slice(3, 5))}`),
    h('p', { class: 'lead' }, `${n(d.count)} wartości w logach. `, d.decoded_by.length
      ? ['Odczytuje go ', d.decoded_by.map((x, i) => [i ? ', ' : '', linkLine(x.line, x.name), x.field_name && x.field_name !== x.name ? ` (pole ${x.field_name})` : '', ` w ${n(x.count)} ramkach`]), '.']
      : 'Żadna linia CSV nie odczytuje tego rejestru z tych ramek, więc nie trafia on do Home Assistanta.'),
    d.note ? h('p', { class: 'note' }, `Opis w extra_registers.csv: ${d.note}`) : null,
    d.defined.length && !d.decoded_by.length ? h('p', { class: 'note' }, 'Rejestr występuje w ID linii ', d.defined.map((l, i) => [i ? ', ' : '', linkLine(l.idx, l.name), ` (${l.zz} ${l.pbsb} ${l.idh})`]), ', ale te linie dotyczą innych ramek.') : null,
    d.other_sources.length ? h('p', {}, 'Ten sam rejestr w innych ramkach: ', d.other_sources.map((o, i) => [i ? ', ' : '', linkReg(o.src, reg, o.src), ` (${n(o.count)})`])) : null,
    h('h3', {}, 'Wartości w czasie'),
    h('div', { class: 'ctl' },
      h('label', {}, 'Odczyt ', h('select', { onchange: e => { opt.signed = e.target.value === '1'; redraw(); } },
        h('option', { value: '0', selected: !opt.signed }, 'bez znaku'), h('option', { value: '1', selected: opt.signed }, 'ze znakiem'))),
      h('label', {}, 'Dzielnik ', h('select', { onchange: e => { opt.div = +e.target.value; redraw(); } },
        [1, 10, 100, 360, 1000].map(v => h('option', { value: v }, v === 1 ? 'brak' : `÷ ${v}`)))),
      pinBtn),
    h('p', { class: 'note' }, 'Przypięte rejestry pokazują się pod spodem przy każdym otwartym rejestrze, z kursorem zsynchronizowanym między wykresami. Zaznacz fragment, żeby przybliżyć.'),
    chartBox,
    sample ? [h('h3', {}, 'Ostatnia ramka z tym rejestrem'), stripForSegments(sample, Object.fromEntries([[reg, names[0] || d.extra_name || '']]), reg)] : null,
  );
  redraw();
}

// ---------------------------------------------------------------- nawigacja

async function route() {
  const [tab = 'lines', key] = location.hash.slice(1).split('/').map(decodeURIComponent);
  const t = LISTS[tab] ? tab : 'lines';
  if (state.tab !== t) {
    state.tab = t;
    document.querySelectorAll('.tabs button').forEach(b => b.setAttribute('aria-selected', b.dataset.tab === t ? 'true' : 'false'));
    renderFilters();
  }
  state.selected = key || null;
  if (!state.data[t]) {
    renderRows();
    try { state.data[t] = await LISTS[t].load(); } catch (e) { put($('#rows'), h('p', { class: 'more errtext' }, e.message)); return; }
  }
  renderRows();
  if (key) LISTS[t].show(location.hash.slice(1).split('/').slice(1).map(decodeURIComponent).join('/'));
  else showIntro();
}

async function init() {
  document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => { location.hash = `#${b.dataset.tab}`; }));
  $('#range-reset').addEventListener('click', () => setRange(null));
  window.addEventListener('hashchange', route);
  try {
    const s = state.summary = await api('summary');
    const pct = s.frames ? Math.round(100 * s.matched / s.frames) : 0;
    $('#summary').textContent = `${s.logs.length} ${s.logs.length === 1 ? 'log' : 'logi'} z okresu ${fmtT(s.first)} – ${fmtT(s.last)}: ` +
      `${n(s.frames)} ramek, ${pct}% rozpoznanych przez linie CSV, ${n(s.errors)} z błędem odczytu.`;
    $('#n-lines').textContent = n(s.lines);
  } catch (e) {
    $('#summary').textContent = `Serwer panelu nie odpowiada: ${e.message}`;
    return;
  }
  api('shapes').then(x => { state.data.shapes = x; $('#n-shapes').textContent = n(x.filter(s => !s.matched).length) + ' bez linii'; if (state.tab === 'shapes') renderRows(); });
  api('registers').then(x => { state.data.registers = x; $('#n-registers').textContent = n(x.length); if (state.tab === 'registers') renderRows(); });
  route();
}

init();
