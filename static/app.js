document.addEventListener('DOMContentLoaded', () => {
  const body = document.body;
  const loader = document.getElementById('page-loader');
  const sidebar = document.getElementById('sidebar');
  const sidebarToggle = document.getElementById('sidebarToggle');
  const themeToggle = document.getElementById('themeToggle');

  const applyTheme = (theme) => {
    body.setAttribute('data-theme', theme);
    localStorage.setItem('finsight-theme', theme);
  };

  const savedTheme = localStorage.getItem('finsight-theme');
  const preferredTheme = savedTheme || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  applyTheme(preferredTheme);

  if (sidebarToggle && sidebar) {
    sidebarToggle.addEventListener('click', () => {
      sidebar.classList.toggle('is-collapsed');
    });
  }

  document.querySelectorAll('[data-counter]').forEach((element) => {
    const target = parseFloat(element.getAttribute('data-counter') || '0');
    const prefix = element.getAttribute('data-prefix') || '';
    const suffix = element.getAttribute('data-suffix') || '';
    const decimals = parseInt(element.getAttribute('data-decimals') || '0', 10);
    const duration = 900;
    const start = performance.now();

    const step = (now) => {
      const progress = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = target * eased;
      element.textContent = `${prefix}${current.toFixed(decimals)}${suffix}`;
      if (progress < 1) {
        requestAnimationFrame(step);
      }
    };

    requestAnimationFrame(step);
  });

  document.querySelectorAll('.seg-btn').forEach((button) => {
    button.addEventListener('click', () => {
      const container = button.closest('.segmented');
      container?.querySelectorAll('.seg-btn').forEach((chip) => chip.classList.remove('active'));
      button.classList.add('active');
    });
  });

  const filterButtons = document.querySelectorAll('.filter-chip');
  const sortSelect = document.getElementById('sortSelect');
  const rows = Array.from(document.querySelectorAll('#transactionsTable tbody tr'));

  const applyFilters = () => {
    const activeFilter = document.querySelector('.filter-chip.active')?.dataset.filter || 'all';
    const sortValue = sortSelect?.value || 'date-desc';

    let visibleRows = rows.filter((row) => activeFilter === 'all' || row.dataset.type === activeFilter);

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
    rows.forEach((row) => {
      row.style.display = visibleRows.includes(row) ? '' : 'none';
    });
  };

  filterButtons.forEach((button) => {
    button.addEventListener('click', () => {
      filterButtons.forEach((chip) => chip.classList.remove('active'));
      button.classList.add('active');
      applyFilters();
    });
  });

  sortSelect?.addEventListener('change', applyFilters);

  setTimeout(() => {
    loader?.classList.add('hidden');
  }, 350);
});
