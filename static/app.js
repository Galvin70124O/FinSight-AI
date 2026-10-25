document.addEventListener('DOMContentLoaded', () => {
  const root = document.documentElement;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch (e) { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* private mode */ } },
  };
  const cssVar = (name) => getComputedStyle(root).getPropertyValue(name).trim();

  // ---------- Theme ----------
  const themeToggle = document.getElementById('themeToggle');
  const syncThemeButton = () => {
    const dark = root.getAttribute('data-theme') !== 'light';
    themeToggle?.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', cssVar('--bg'));
  };
  const toggleTheme = () => {
    const next = root.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    root.setAttribute('data-theme', next);
    store.set('finsight-theme', next);
    syncThemeButton();
    themeCharts();
  };
  themeToggle?.addEventListener('click', toggleTheme);

  // ---------- Sidebar: icon rail on desktop, drawer on small screens ----------
  const sidebar = document.getElementById('sidebar');
  const scrim = document.getElementById('sidebarScrim');
  const openButton = document.getElementById('sidebarOpen');
  const collapseButton = document.getElementById('sidebarCollapse');
  const resizeCharts = () => window.setTimeout(() => window.dispatchEvent(new Event('resize')), 240);

  const toggleCollapsed = () => {
    const collapsed = root.classList.toggle('sidebar-collapsed');
    store.set('finsight-sidebar', collapsed ? 'collapsed' : 'open');
    collapseButton?.setAttribute('aria-pressed', String(collapsed));
    collapseButton?.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
    resizeCharts();
  };
  collapseButton?.addEventListener('click', toggleCollapsed);
  if (root.classList.contains('sidebar-collapsed')) {
    collapseButton?.setAttribute('aria-pressed', 'true');
    collapseButton?.setAttribute('aria-label', 'Expand sidebar');
  }

  const setDrawer = (open) => {
    sidebar?.classList.toggle('open', open);
    if (scrim) scrim.hidden = !open;
    openButton?.setAttribute('aria-expanded', String(open));
    if (open) sidebar?.querySelector('.nav-link')?.focus();
  };
  openButton?.addEventListener('click', () => setDrawer(true));
  scrim?.addEventListener('click', () => setDrawer(false));
  sidebar?.querySelectorAll('.nav-link').forEach((link) => link.addEventListener('click', () => setDrawer(false)));

  // Highlight the nav link for the dashboard section in view.
  const spyLinks = document.querySelectorAll('.nav-link[data-spy]');
  if (spyLinks.length && 'IntersectionObserver' in window) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const id = entry.target.id === 'cashflow' ? 'analytics' : entry.target.id;
        spyLinks.forEach((link) => link.classList.toggle('active', link.dataset.spy === id));
      });
    }, { rootMargin: '-30% 0px -60% 0px' });
    ['top', 'insights', 'analytics', 'cashflow', 'categories', 'transactions'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
  }

  // ---------- Spotlight cards: two CSS variables per frame, nothing else ----------
  document.querySelectorAll('[data-spotlight]').forEach((card) => {
    let frame = 0;
    card.addEventListener('pointermove', (event) => {
      if (event.pointerType === 'touch') return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const box = card.getBoundingClientRect();
        card.style.setProperty('--mx', `${event.clientX - box.left}px`);
        card.style.setProperty('--my', `${event.clientY - box.top}px`);
      });
    });
  });

  // ---------- Count-up figures ----------
  // Rupees with Indian digit grouping, e.g. ₹1,25,000.
  const inr = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 });
  document.querySelectorAll('[data-counter]').forEach((element) => {
    const target = parseFloat(element.getAttribute('data-counter') || '0');
    const isCurrency = element.getAttribute('data-currency') === 'INR';
    const prefix = element.getAttribute('data-prefix') || '';
    const suffix = element.getAttribute('data-suffix') || '';
    const decimals = parseInt(element.getAttribute('data-decimals') || '0', 10);
    const render = (value) => {
      element.textContent = isCurrency ? inr.format(value) : `${prefix}${value.toFixed(decimals)}${suffix}`;
    };
    if (reducedMotion) { render(target); return; }
    const duration = 900;
    const start = performance.now();
    const step = (now) => {
      const progress = Math.min((now - start) / duration, 1);
      render(target * (1 - Math.pow(1 - progress, 4)));
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });

  // ---------- Net balance strip readout ----------
  document.querySelectorAll('[data-net-strip]').forEach((strip) => {
    const readout = strip.querySelector('.net-readout');
    if (!readout) return;
    const reset = () => { readout.textContent = readout.dataset.default; };
    strip.querySelectorAll('.net-bar').forEach((bar) => {
      const show = () => { readout.textContent = bar.dataset.readout; };
      bar.addEventListener('pointerenter', show);
      bar.addEventListener('focus', show);
      bar.addEventListener('blur', reset);
    });
    strip.addEventListener('pointerleave', reset);
  });

  // ---------- Charts ----------
  // The server renders the Plotly figures; this restyles them to match the
  // current theme (colours, fonts, grid) without changing any data.
  const alpha = (hex, a) => {
    const h = hex.replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  };

  function themeCharts() {
    if (!window.Plotly) return;
    const font = cssVar('--font');
    const muted = cssVar('--muted');
    const text = cssVar('--text');
    const accent = cssVar('--accent');
    const neg = cssVar('--neg');
    const grid = cssVar('--border-2');
    const tickfont = { color: muted, size: 11, family: font };
    const base = {
      font: { family: font, color: muted, size: 12 },
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      hoverlabel: { bgcolor: cssVar('--card-2'), bordercolor: cssVar('--border-3'), font: { family: font, color: text, size: 12 } },
      showlegend: false,
    };

    document.querySelectorAll('.js-plotly-plot').forEach((chart) => {
      const traces = chart.data || [];
      if (!traces.length) return;

      if (traces[0].type === 'pie') {
        const colors = Array.from({ length: 8 }, (_, i) => cssVar(`--c${i}`));
        const total = chart.closest('[data-total]')?.dataset.total || '';
        window.Plotly.restyle(chart, {
          hole: 0.68, sort: false, textinfo: 'none',
          'marker.colors': [traces[0].labels.map((_, i) => colors[i % colors.length])],
          'marker.line.color': cssVar('--card'), 'marker.line.width': 2,
        });
        window.Plotly.relayout(chart, {
          ...base, title: { text: '' }, margin: { l: 0, r: 0, t: 0, b: 0 },
          annotations: [
            { text: 'Spent', showarrow: false, x: 0.5, y: 0.58, font: { size: 12, color: muted, family: font } },
            { text: total, showarrow: false, x: 0.5, y: 0.44, font: { size: 17, color: text, family: font } },
          ],
        });
        return;
      }

      const layout = {
        ...base,
        margin: { l: 64, r: 12, t: 8, b: 32 },
        // Invisible outside ticks push the month labels clear of the "₹0" label.
        'xaxis.showgrid': false, 'xaxis.zeroline': false, 'xaxis.showline': false,
        'xaxis.ticks': 'outside', 'xaxis.ticklen': 8, 'xaxis.tickcolor': 'rgba(0,0,0,0)',
        'xaxis.tickfont': tickfont, 'xaxis.title.text': '',
        'yaxis.gridcolor': grid, 'yaxis.zeroline': false, 'yaxis.showline': false, 'yaxis.ticks': '',
        'yaxis.tickfont': tickfont, 'yaxis.title.text': '',
      };

      if (chart.id === 'spendingChart') {
        window.Plotly.restyle(chart, {
          mode: 'lines+markers', 'line.color': accent, 'line.width': 2.25, 'line.shape': 'linear',
          fillcolor: alpha(accent, 0.12), 'marker.size': 6, 'marker.color': accent,
          'marker.line.color': cssVar('--card'), 'marker.line.width': 2,
        });
      } else if (traces.some((t) => t.name === 'Income')) {
        // Income vs expense as two overlapping areas.
        const colorFor = (t) => (t.name === 'Income' ? accent : neg);
        window.Plotly.restyle(chart, {
          type: 'scatter', mode: 'lines', fill: 'tozeroy',
          'line.color': traces.map(colorFor), 'line.width': 2, 'line.shape': 'spline', 'line.smoothing': 0.6,
          fillcolor: traces.map((t) => alpha(colorFor(t), 0.1)),
        });
        layout.hovermode = 'x unified';
      }
      window.Plotly.relayout(chart, layout);
    });
  }
  themeCharts();
  syncThemeButton();

  // Month / Quarter / Year switch for the spending chart. The server sends
  // each view (labels, totals, ₹ hover text and axis ticks) as JSON.
  const viewsElement = document.getElementById('spendingViews');
  const spendingViews = viewsElement ? JSON.parse(viewsElement.textContent) : {};
  const showSpendingView = (range) => {
    const view = spendingViews[range];
    const chart = document.getElementById('spendingChart');
    if (!view || !chart || !window.Plotly) return;
    window.Plotly.update(
      chart,
      { x: [view.x], y: [view.y], customdata: [view.customdata] },
      { 'yaxis.tickvals': view.tickvals, 'yaxis.ticktext': view.ticktext },
    );
  };

  document.querySelectorAll('.seg-btn').forEach((button) => {
    button.addEventListener('click', () => {
      const container = button.closest('.segmented');
      container?.querySelectorAll('.seg-btn').forEach((chip) => {
        chip.classList.remove('active');
        chip.setAttribute('aria-pressed', 'false');
      });
      button.classList.add('active');
      button.setAttribute('aria-pressed', 'true');
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

    const visibleRows = rows.filter((row) => activeFilter === 'all' || row.dataset.type === activeFilter);
    visibleRows.sort((left, right) => {
      if (sortValue === 'date-desc') return right.dataset.date.localeCompare(left.dataset.date);
      if (sortValue === 'date-asc') return left.dataset.date.localeCompare(right.dataset.date);
      if (sortValue === 'amount-desc') return parseFloat(right.dataset.amount) - parseFloat(left.dataset.amount);
      if (sortValue === 'amount-asc') return parseFloat(left.dataset.amount) - parseFloat(right.dataset.amount);
      return 0;
    });

    const tbody = document.querySelector('#transactionsTable tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    visibleRows.forEach((row) => tbody.appendChild(row));
    rows.forEach((row) => { row.style.display = visibleRows.includes(row) ? '' : 'none'; });
  };

  filterButtons.forEach((button) => {
    button.addEventListener('click', () => {
      filterButtons.forEach((chip) => { chip.classList.remove('active'); chip.setAttribute('aria-pressed', 'false'); });
      button.classList.add('active');
      button.setAttribute('aria-pressed', 'true');
      applyFilters();
    });
  });
  sortSelect?.addEventListener('change', applyFilters);

  // ---------- Import dropzone ----------
  document.querySelectorAll('[data-dropzone]').forEach((zone) => {
    const input = zone.querySelector('input[type="file"]');
    const label = zone.querySelector('[data-dropzone-label]');
    const original = label?.innerHTML;
    const showFile = () => {
      const file = input?.files?.[0];
      zone.classList.toggle('has-file', Boolean(file));
      if (label) label.innerHTML = original;
      if (file && label) label.textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
    };
    input?.addEventListener('change', showFile);
    ['dragenter', 'dragover'].forEach((type) => zone.addEventListener(type, () => zone.classList.add('is-over')));
    ['dragleave', 'drop'].forEach((type) => zone.addEventListener(type, () => zone.classList.remove('is-over')));
  });

  // ---------- Command palette (Ctrl/Cmd+K) ----------
  const palette = document.getElementById('palette');
  const paletteInput = document.getElementById('paletteInput');
  const paletteList = document.getElementById('paletteList');
  const paletteOpen = document.getElementById('paletteOpen');
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
  if (document.getElementById('demoForm')) commands.push({ group: 'Actions', label: 'Load demo data', action: 'demo' });
  const txItems = rows.map((row) => ({
    group: 'Transactions',
    label: row.querySelector('.desc')?.textContent.trim() || row.dataset.category,
    meta: `${row.dataset.category} · ${row.dataset.date} · ${row.querySelector('.amount')?.textContent.trim() || ''}`,
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
      if (item.row.style.display === 'none') document.querySelector('.chip[data-filter="all"]')?.click();
      item.row.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center' });
      item.row.classList.add('flash-row');
      window.setTimeout(() => item.row.classList.remove('flash-row'), 1600);
      return;
    }
    if (item.action === 'theme') toggleTheme();
    if (item.action === 'sidebar') toggleCollapsed();
    if (item.action === 'logout') document.getElementById('logoutForm')?.requestSubmit();
    if (item.action === 'demo') document.getElementById('demoForm')?.requestSubmit();
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
      empty.textContent = 'No results';
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
      li.setAttribute('aria-selected', String(index === active));
      const kind = item.href ? 'go' : item.target ? 'section' : 'action';
      li.innerHTML = item.iconHtml || `<span class="palette-dot"><svg viewBox="0 0 24 24">${ICONS[kind]}</svg></span>`;
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

  const highlight = () => {
    paletteList.querySelectorAll('.palette-item').forEach((li) => {
      const selected = li.id === `palette-item-${active}`;
      li.setAttribute('aria-selected', String(selected));
      if (selected) li.scrollIntoView({ block: 'nearest' });
    });
    paletteInput.setAttribute('aria-activedescendant', `palette-item-${active}`);
  };

  const openPalette = () => {
    if (!palette || !palette.hidden) return;
    lastFocus = document.activeElement;
    palette.hidden = false;
    document.body.classList.add('palette-open');
    paletteInput.value = '';
    active = 0;
    render();
    paletteInput.focus();
  };
  function closePalette() {
    if (!palette || palette.hidden) return;
    palette.hidden = true;
    document.body.classList.remove('palette-open');
    lastFocus?.focus?.();
  }

  paletteOpen?.addEventListener('click', openPalette);
  palette?.querySelectorAll('[data-palette-close]').forEach((el) => el.addEventListener('click', closePalette));
  paletteInput?.addEventListener('input', () => { active = 0; render(); });
  paletteInput?.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); active = (active + 1) % Math.max(results.length, 1); highlight(); }
    if (event.key === 'ArrowUp') { event.preventDefault(); active = (active - 1 + results.length) % Math.max(results.length, 1); highlight(); }
    if (event.key === 'Enter' && results[active]) { event.preventDefault(); run(results[active]); }
    if (event.key === 'Tab') event.preventDefault(); // keep focus in the dialog
  });

  document.addEventListener('keydown', (event) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '');
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (palette?.hidden) openPalette(); else closePalette();
    } else if (event.key === '/' && !typing && palette?.hidden) {
      event.preventDefault();
      openPalette();
    } else if (event.key === 'Escape') {
      if (palette && !palette.hidden) closePalette();
      else if (sidebar?.classList.contains('open')) { setDrawer(false); openButton?.focus(); }
    }
  });
});
