document.addEventListener('DOMContentLoaded', () => {
  const root = document.documentElement;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch (e) { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* private mode: the theme lasts for this page only */ } },
  };
  const cssVar = (name) => getComputedStyle(root).getPropertyValue(name).trim();

  // ---------- Theme ----------
  const themeButtons = document.querySelectorAll('[data-theme-toggle]');
  const syncTheme = () => {
    const dark = root.getAttribute('data-theme') === 'dark';
    themeButtons.forEach((button) => button.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme'));
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', cssVar('--bg'));
  };
  const toggleTheme = () => {
    const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    store.set('finsight-theme', next);
    syncTheme();
    themeCharts();
  };
  themeButtons.forEach((button) => button.addEventListener('click', toggleTheme));

  // ---------- Landing: each section rises in once as it enters the screen ----------
  const reveals = document.querySelectorAll('[data-reveal]');
  if (reveals.length && 'IntersectionObserver' in window) {
    root.classList.add('reveal-ready');
    const revealer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-in');
        revealer.unobserve(entry.target);
      });
    }, { rootMargin: '0px 0px -10% 0px' });
    reveals.forEach((el) => revealer.observe(el));
  }

  // ---------- Highlight the nav item for the dashboard section in view ----------
  const spyLinks = document.querySelectorAll('[data-spy]');
  if (spyLinks.length && document.getElementById('spending') && 'IntersectionObserver' in window) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const id = entry.target.dataset.spyGroup || entry.target.id;
        spyLinks.forEach((link) => link.classList.toggle('active', link.dataset.spy === id));
      });
    }, { rootMargin: '-35% 0px -55% 0px' });
    // Sections without their own nav item light up the nearest one.
    const groups = { top: 'top', brief: 'spending', cashflow: 'spending', categories: 'spending', spending: 'spending', health: 'health', unusual: 'transactions', transactions: 'transactions' };
    Object.entries(groups).forEach(([id, group]) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.dataset.spyGroup = document.querySelector(`[data-spy="${group}"]`) ? group : 'top';
      observer.observe(el);
    });
  }

  // ---------- Charts ----------
  // The server sends each figure as JSON; this draws it in the current theme.
  // Each frame keeps its final height and a placeholder until the chart is drawn.
  const alpha = (hex, a) => {
    const h = hex.replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  };
  const charts = Array.from(document.querySelectorAll('.chart[data-figure]'));
  const figures = new Map(charts.map((el) => [el, JSON.parse(document.getElementById(el.dataset.figure).textContent)]));
  const narrowQuery = window.matchMedia('(max-width: 640px)');
  const narrow = () => narrowQuery.matches;
  // Month names need room: on a phone label about four of them, always including the latest.
  const xTicks = (labels) => {
    if (!narrow() || labels.length <= 4) return { tickmode: 'auto' };
    const step = Math.ceil(labels.length / 4);
    return { tickmode: 'array', tickvals: labels.filter((_, i) => (labels.length - 1 - i) % step === 0) };
  };

  function styleTrace(trace) {
    const accent = cssVar('--accent');
    const surface = cssVar('--surface');
    const quiet = cssVar('--chart-muted');
    const roles = {
      actual: { line: { color: accent, width: 2 }, marker: { size: 8, color: accent, line: { color: surface, width: 2 } } },
      // The band's fill is per theme (--band-alpha); its 1px edges keep the shape visible at 3:1 or more.
      'band-low': { line: { width: 1, color: accent } },
      band: { line: { width: 1, color: accent }, fillcolor: alpha(accent, parseFloat(cssVar('--band-alpha')) || 0.24) },
      forecast: { line: { color: accent, width: 2, dash: 'dot' }, marker: { size: [0, 8], color: surface, line: { color: accent, width: 2 } } },
      income: { marker: { color: accent } },
      spending: { marker: { color: quiet } },
    };
    return { ...trace, ...(roles[trace.meta] || {}) };
  }

  function layoutFor(figure, labels = figure.data[0].x) {
    const font = cssVar('--font');
    const muted = cssVar('--muted');
    const tickfont = { color: muted, size: 12, family: font };
    return {
      ...figure.layout,
      font: { family: font, color: muted, size: 12 },
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      margin: { l: 8, r: 8, t: 8, b: 32 },
      showlegend: false,
      bargap: 0.35, bargroupgap: 0.12,
      hovermode: 'x unified',
      hoverlabel: { bgcolor: cssVar('--surface'), bordercolor: cssVar('--line-strong'), font: { family: font, color: cssVar('--ink'), size: 13 } },
      xaxis: { showgrid: false, zeroline: false, showline: false, fixedrange: true, tickfont, tickangle: 0, automargin: true, ...xTicks(labels) },
      // Money axes start at ₹0 (so a small change never looks big), drawn as a baseline under its label.
      yaxis: { ...(figure.layout.yaxis || {}), rangemode: 'tozero', automargin: true, gridcolor: cssVar('--line'), zeroline: true, zerolinecolor: cssVar('--line-strong'), zerolinewidth: 1, showline: false, fixedrange: true, tickfont, ticks: '' },
    };
  }

  function drawCharts() {
    if (!window.Plotly) {
      charts.forEach((el) => el.classList.add('has-error'));
      return;
    }
    charts.forEach((el) => {
      const figure = figures.get(el);
      window.Plotly.newPlot(el, figure.data.map(styleTrace), layoutFor(figure), { displayModeBar: false, responsive: true })
        .then(() => el.classList.add('is-ready'))
        .catch(() => el.classList.add('has-error'));
    });
  }

  function themeCharts() {
    if (!window.Plotly) return;
    charts.forEach((el) => {
      if (!el.classList.contains('is-ready')) return;
      const figure = figures.get(el);
      // Keep whatever the Month/Quarter/Year switch put in the chart; only restyle it.
      const data = el.data.map((trace) => styleTrace(trace));
      const layout = layoutFor(figure, el.data[0].x);
      window.Plotly.react(el, data, { ...layout, yaxis: { ...layout.yaxis, tickvals: el.layout.yaxis.tickvals, ticktext: el.layout.yaxis.ticktext } });
    });
  }
  if (charts.length) {
    drawCharts();
    narrowQuery.addEventListener('change', themeCharts);
  }
  syncTheme();

  // Month / Quarter / Year: the server sends each view (labels, totals, ₹ hover text, axis ticks).
  // The forecast band only belongs to the monthly view.
  const viewsElement = document.getElementById('spendingViews');
  const spendingViews = viewsElement ? JSON.parse(viewsElement.textContent) : {};
  const showSpendingView = (range) => {
    const view = spendingViews[range];
    const chart = document.getElementById('spendingChart');
    if (!view || !chart?.classList.contains('is-ready')) return;
    const monthly = range === 'month';
    const ticks = xTicks(view.x);
    window.Plotly.update(chart, { x: [view.x], y: [view.y], customdata: [view.customdata] }, {
      'yaxis.tickvals': view.tickvals, 'yaxis.ticktext': view.ticktext, 'xaxis.tickmode': ticks.tickmode, 'xaxis.tickvals': ticks.tickvals,
    }, [0]);
    const extra = chart.data.map((_, i) => i).slice(1);
    if (extra.length) window.Plotly.restyle(chart, { visible: monthly }, extra);
    document.querySelectorAll('[data-forecast-only]').forEach((el) => { el.hidden = !monthly; });
  };

  document.querySelectorAll('.seg-btn').forEach((button) => {
    button.addEventListener('click', () => {
      button.closest('.segmented')?.querySelectorAll('.seg-btn').forEach((other) => {
        other.classList.toggle('active', other === button);
        other.setAttribute('aria-pressed', String(other === button));
      });
      showSpendingView(button.dataset.range);
    });
  });

  // ---------- Transactions: filter chips + sort ----------
  const filterButtons = document.querySelectorAll('.chip[data-filter]');
  const sortSelect = document.getElementById('sortSelect');
  const rows = Array.from(document.querySelectorAll('#transactionsTable tbody tr'));

  const applyFilters = () => {
    const activeFilter = document.querySelector('.chip[data-filter].active')?.dataset.filter || 'all';
    const sortValue = sortSelect?.value || 'date-desc';
    const sorted = [...rows].sort((left, right) => {
      if (sortValue === 'date-desc') return right.dataset.date.localeCompare(left.dataset.date);
      if (sortValue === 'date-asc') return left.dataset.date.localeCompare(right.dataset.date);
      if (sortValue === 'amount-desc') return parseFloat(right.dataset.amount) - parseFloat(left.dataset.amount);
      return parseFloat(left.dataset.amount) - parseFloat(right.dataset.amount);
    });
    const tbody = document.querySelector('#transactionsTable tbody');
    sorted.forEach((row) => {
      row.hidden = activeFilter !== 'all' && row.dataset.type !== activeFilter;
      tbody?.appendChild(row);
    });
  };

  filterButtons.forEach((button) => {
    button.addEventListener('click', () => {
      filterButtons.forEach((chip) => {
        chip.classList.toggle('active', chip === button);
        chip.setAttribute('aria-pressed', String(chip === button));
      });
      applyFilters();
    });
  });
  sortSelect?.addEventListener('change', applyFilters);

  // ---------- Import dropzone ----------
  document.querySelectorAll('[data-dropzone]').forEach((zone) => {
    const input = zone.querySelector('input[type="file"]');
    const label = zone.querySelector('[data-dropzone-label]');
    const original = label?.textContent;
    const showFile = () => {
      const file = input?.files?.[0];
      zone.classList.toggle('has-file', Boolean(file));
      if (label) label.textContent = file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : original;
    };
    input?.addEventListener('change', showFile);
    ['dragenter', 'dragover'].forEach((type) => zone.addEventListener(type, () => zone.classList.add('is-over')));
    ['dragleave', 'drop'].forEach((type) => zone.addEventListener(type, () => zone.classList.remove('is-over')));
  });

  // ---------- Forms sent once: a second tap does nothing (the server also refuses a repeat) ----------
  document.querySelectorAll('form[data-once]').forEach((form) => form.addEventListener('submit', (event) => {
    if (form.dataset.sent) { event.preventDefault(); return; }
    form.dataset.sent = 'true';
    const button = form.querySelector('[type="submit"]');
    if (button) { button.setAttribute('aria-disabled', 'true'); button.textContent = 'Saving…'; }
  }));

  // ---------- Categories: filter merchants as you type (the Search button does the same on the server) ----------
  const merchantFilter = document.querySelector('[data-merchant-filter]');
  if (merchantFilter) {
    const merchants = [...document.querySelectorAll('[data-search]')];
    const none = document.querySelector('[data-merchant-filter-empty]');
    merchantFilter.addEventListener('input', () => {
      const query = merchantFilter.value.trim().toLowerCase();
      let shown = 0;
      merchants.forEach((item) => { item.hidden = !item.dataset.search.includes(query); shown += item.hidden ? 0 : 1; });
      if (none) none.hidden = shown > 0;
    });
  }

  // ---------- Command palette (Ctrl/Cmd+K, or "/") ----------
  const palette = document.getElementById('palette');
  if (!palette) return;
  const paletteInput = document.getElementById('paletteInput');
  const paletteList = document.getElementById('paletteList');
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  document.querySelectorAll('[data-mod-key]').forEach((kbd) => { kbd.textContent = isMac ? '⌘ K' : 'Ctrl K'; });

  const ICONS = {
    go: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    section: '<path d="M4 6h16M4 12h10M4 18h7"/>',
    action: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  };
  let commands = [];
  try { commands = JSON.parse(document.getElementById('paletteCommands')?.textContent || '[]'); } catch (e) { commands = []; }
  document.querySelectorAll('[data-section]').forEach((section) => {
    if (section.id) commands.push({ group: 'Sections', label: section.dataset.section, target: section.id });
  });
  if (document.getElementById('demoForm')) commands.push({ group: 'Actions', label: 'Load sample data', action: 'demo' });
  const txItems = rows.map((row) => ({
    group: 'Transactions',
    label: row.querySelector('.desc')?.textContent.trim() || row.dataset.category,
    meta: `${row.dataset.category} · ${row.querySelector('.tx-date')?.textContent.trim() || row.dataset.date} · ${row.querySelector('.amount')?.textContent.trim() || ''}`,
    row,
    iconHtml: row.querySelector('.cat-icon')?.outerHTML,
  }));

  let results = [];
  let active = 0;
  let lastFocus = null;

  const run = (item) => {
    closePalette();
    if (item.href) { window.location.href = item.href; return; }
    if (item.target) {
      document.getElementById(item.target)?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
      return;
    }
    if (item.row) {
      if (item.row.hidden) document.querySelector('.chip[data-filter="all"]')?.click();
      item.row.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center' });
      item.row.classList.add('flash-row');
      window.setTimeout(() => item.row.classList.remove('flash-row'), 1600);
      return;
    }
    if (item.action === 'theme') toggleTheme();
    if (item.action === 'logout') document.getElementById('logoutForm')?.requestSubmit();
    if (item.action === 'demo') document.getElementById('demoForm')?.requestSubmit();
  };

  const highlight = () => {
    paletteList.querySelectorAll('.palette-item').forEach((li) => {
      const selected = li.id === `palette-item-${active}`;
      li.setAttribute('aria-selected', String(selected));
      if (selected) li.scrollIntoView({ block: 'nearest' });
    });
    paletteInput.setAttribute('aria-activedescendant', `palette-item-${active}`);
  };

  const render = () => {
    const query = paletteInput.value.trim().toLowerCase();
    const match = (item) => !query || `${item.label} ${item.meta || ''}`.toLowerCase().includes(query);
    results = commands.filter(match);
    if (query) results = results.concat(txItems.filter(match).slice(0, 8));
    active = Math.min(active, Math.max(results.length - 1, 0));

    paletteList.innerHTML = '';
    if (!results.length) {
      const empty = document.createElement('li');
      empty.className = 'palette-empty';
      empty.textContent = `Nothing matches “${paletteInput.value.trim()}”. Try a category, an amount or a section name.`;
      paletteList.appendChild(empty);
      paletteInput.removeAttribute('aria-activedescendant');
      return;
    }
    let group = '';
    results.forEach((item, index) => {
      if (item.group !== group) {
        group = item.group;
        const heading = document.createElement('li');
        heading.className = 'palette-group';
        heading.setAttribute('role', 'presentation');
        heading.textContent = group;
        paletteList.appendChild(heading);
      }
      const li = document.createElement('li');
      li.className = 'palette-item';
      li.id = `palette-item-${index}`;
      li.setAttribute('role', 'option');
      const kind = item.href ? 'go' : item.target ? 'section' : 'action';
      li.innerHTML = item.iconHtml || `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[kind]}</svg>`;
      const label = document.createElement('span');
      label.className = 'p-label';
      label.textContent = item.label;
      li.appendChild(label);
      if (item.meta) {
        const meta = document.createElement('span');
        meta.className = 'p-meta';
        meta.textContent = item.meta;
        li.appendChild(meta);
      }
      li.addEventListener('mousemove', () => { if (active !== index) { active = index; highlight(); } });
      li.addEventListener('click', () => run(item));
      paletteList.appendChild(li);
    });
    highlight();
  };

  const openPalette = () => {
    if (!palette.hidden) return;
    lastFocus = document.activeElement;
    palette.hidden = false;
    document.body.classList.add('palette-open');
    paletteInput.value = '';
    active = 0;
    render();
    paletteInput.focus();
  };
  function closePalette() {
    if (palette.hidden) return;
    palette.hidden = true;
    document.body.classList.remove('palette-open');
    lastFocus?.focus?.();
  }

  document.querySelectorAll('[data-palette-open]').forEach((button) => button.addEventListener('click', openPalette));
  palette.querySelectorAll('[data-palette-close]').forEach((el) => el.addEventListener('click', closePalette));
  paletteInput.addEventListener('input', () => { active = 0; render(); });
  paletteInput.addEventListener('keydown', (event) => {
    const count = Math.max(results.length, 1);
    if (event.key === 'ArrowDown') { event.preventDefault(); active = (active + 1) % count; highlight(); }
    if (event.key === 'ArrowUp') { event.preventDefault(); active = (active - 1 + count) % count; highlight(); }
    if (event.key === 'Enter' && results[active]) { event.preventDefault(); run(results[active]); }
    if (event.key === 'Tab') event.preventDefault(); // keep focus in the dialog
  });

  document.addEventListener('keydown', (event) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '');
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (palette.hidden) openPalette(); else closePalette();
    } else if (event.key === '/' && !typing && palette.hidden) {
      event.preventDefault();
      openPalette();
    } else if (event.key === 'Escape' && !palette.hidden) {
      closePalette();
    }
  });
});
