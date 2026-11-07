(() => {
  // ─── 1. Керування полями форми створення посилання за типом ────────────────
  const kind = document.getElementById('id_kind');
  const targets = { class: 'class_group', student: 'student', assignment: 'assignment' };
  const descriptions = {
    site: 'Доступ до поточного сайту у звичному режимі. Учні самі вибирають клас та ім’я.',
    class: 'Лише завдання та роботи вибраного класу. Учень вибирає своє ім’я.',
    student: 'Завдання класу та власні роботи учня. Ім’я й клас підставляються автоматично.',
    assignment: 'Лише вибране завдання, його матеріали, здача та роботи до нього.'
  };

  function updateKind() {
    ['class_group', 'student', 'assignment'].forEach(name => {
      const row = document.querySelector(`[data-link-field="${name}"]`);
      if (row) row.hidden = targets[kind.value] !== name;
      const field = document.getElementById(`id_${name}`);
      if (field) field.disabled = targets[kind.value] !== name;
    });
    const help = document.getElementById('access-kind-help');
    if (help) help.textContent = descriptions[kind.value] || '';
  }

  if (kind) {
    kind.addEventListener('change', updateKind);
    updateKind();
  }

  // ─── 2. Копіювання посилань у буфер обміну ─────────────────────────────────
  const copyStatus = document.getElementById('access-copy-status');

  async function copyText(text) {
    let success = false;
    try {
      if (window.isSecureContext && navigator.clipboard) {
        await navigator.clipboard.writeText(text);
        success = true;
      } else {
        const temp = document.createElement('textarea');
        temp.value = text;
        temp.style.position = 'fixed';
        temp.style.opacity = '0';
        document.body.appendChild(temp);
        temp.focus();
        temp.select();
        success = document.execCommand('copy');
        document.body.removeChild(temp);
      }
    } catch (_) {
      success = false;
    }
    return success;
  }

  // Копіювання з поля введення (у картці)
  document.querySelectorAll('[data-copy]').forEach(button => {
    button.addEventListener('click', async () => {
      const input = document.getElementById(button.dataset.copy);
      if (!input) return;
      const ok = await copyText(input.value);
      if (!ok) {
        input.focus();
        input.select();
      }
      if (copyStatus) {
        copyStatus.textContent = ok ? 'Посилання скопійовано.' : 'Адресу виділено. Натисніть Ctrl+C, щоб скопіювати.';
      }
    });
  });

  // Копіювання прямо зі значення (у таблиці)
  document.querySelectorAll('[data-copy-val]').forEach(button => {
    button.addEventListener('click', async () => {
      const text = button.dataset.copyVal;
      if (!text) return;
      const ok = await copyText(text);
      if (copyStatus) {
        copyStatus.textContent = ok ? 'Посилання скопійовано.' : 'Не вдалося скопіювати посилання.';
      }
    });
  });

  // ─── 3. Фільтрація за статусом та швидкий пошук ───────────────────────────
  const searchInput = document.getElementById('access-search-input');
  const searchClear = document.getElementById('access-search-clear');
  const filterTabs = document.querySelectorAll('.access-tab-btn');
  const cards = document.querySelectorAll('.access-link-card');
  const tableRows = document.querySelectorAll('.access-table-row');
  const noResults = document.getElementById('access-no-results');
  const countAll = document.getElementById('count-all');
  const countActive = document.getElementById('count-active');
  const countInactive = document.getElementById('count-inactive');

  let currentFilter = 'all'; // 'all', 'active', 'inactive'
  let currentSearch = '';

  function applyFilter() {
    let visibleCount = 0;
    const query = currentSearch.trim().toLowerCase();

    if (searchClear) {
      searchClear.hidden = query.length === 0;
    }

    // Фільтрація карток
    cards.forEach(card => {
      const status = card.dataset.status;
      const searchText = (card.dataset.search || '').toLowerCase();
      const matchesFilter = (currentFilter === 'all') || (status === currentFilter);
      const matchesSearch = !query || searchText.includes(query);

      if (matchesFilter && matchesSearch) {
        card.style.display = '';
        visibleCount++;
      } else {
        card.style.display = 'none';
      }
    });

    // Фільтрація рядків таблиці
    tableRows.forEach(row => {
      const status = row.dataset.status;
      const searchText = (row.dataset.search || '').toLowerCase();
      const matchesFilter = (currentFilter === 'all') || (status === currentFilter);
      const matchesSearch = !query || searchText.includes(query);

      if (matchesFilter && matchesSearch) {
        row.style.display = '';
      } else {
        row.style.display = 'none';
      }
    });

    if (noResults) {
      noResults.hidden = (visibleCount > 0 || cards.length === 0);
    }
  }

  // Оновлення лічильників
  if (cards.length > 0) {
    let active = 0;
    let inactive = 0;
    cards.forEach(card => {
      if (card.dataset.status === 'active') active++;
      else inactive++;
    });
    if (countAll) countAll.textContent = cards.length;
    if (countActive) countActive.textContent = active;
    if (countInactive) countInactive.textContent = inactive;
  }

  filterTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      filterTabs.forEach(t => {
        t.classList.remove('is-active');
        t.setAttribute('aria-selected', 'false');
      });
      tab.classList.add('is-active');
      tab.setAttribute('aria-selected', 'true');
      currentFilter = tab.dataset.filter || 'all';
      applyFilter();
    });
  });

  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      currentSearch = e.target.value;
      applyFilter();
    });
  }

  if (searchClear) {
    searchClear.addEventListener('click', () => {
      searchInput.value = '';
      currentSearch = '';
      applyFilter();
      searchInput.focus();
    });
  }

  // ─── 4. Плавне анімоване перемикання (Картки ↔ Таблиця) ───────────────────
  const btnCards = document.getElementById('view-mode-cards');
  const btnTable = document.getElementById('view-mode-table');
  const cardsList = document.getElementById('access-cards-list');
  const tableWrapper = document.getElementById('access-table-wrapper');
  const collapseAllBtn = document.getElementById('toggle-collapse-all');
  const gridContainer = document.getElementById('access-grid-container');
  const formDetails = document.getElementById('access-form-details');
  const toolbar = document.querySelector('.access-toolbar');

  const isEditing = formDetails && formDetails.dataset.isEditing === 'true';
  let isSwitching = false;

  function syncFormAndGrid(targetMode) {
    if (gridContainer) {
      gridContainer.classList.toggle('is-table-mode', targetMode === 'table');
    }
    if (formDetails) {
      if (targetMode === 'cards') {
        formDetails.open = true;
      } else if (!isEditing) {
        formDetails.open = false;
      }
    }
  }

  function setViewMode(mode, immediate = false) {
    if (isSwitching && !immediate) return;
    const targetMode = (mode === 'table') ? 'table' : 'cards';

    // Збереження вертикального якоря панелі, щоб уникнути будь-яких стрибків
    const prevToolbarTop = toolbar ? toolbar.getBoundingClientRect().top : null;

    if (btnCards) {
      btnCards.classList.toggle('is-active', targetMode === 'cards');
      btnCards.setAttribute('aria-pressed', targetMode === 'cards' ? 'true' : 'false');
    }
    if (btnTable) {
      btnTable.classList.toggle('is-active', targetMode === 'table');
      btnTable.setAttribute('aria-pressed', targetMode === 'table' ? 'true' : 'false');
    }
    if (collapseAllBtn) {
      collapseAllBtn.style.display = (targetMode === 'cards') ? '' : 'none';
    }

    try { localStorage.setItem('access_links_view_mode', targetMode); } catch (_) {}

    const oldPane = (targetMode === 'table') ? cardsList : tableWrapper;
    const newPane = (targetMode === 'table') ? tableWrapper : cardsList;

    if (immediate) {
      syncFormAndGrid(targetMode);
      if (oldPane) {
        oldPane.classList.add('is-hidden');
        oldPane.classList.remove('is-anim-in', 'is-anim-out');
        oldPane.hidden = true;
      }
      if (newPane) {
        newPane.classList.remove('is-hidden', 'is-anim-out');
        newPane.classList.add('is-anim-in');
        newPane.hidden = false;
      }
      return;
    }

    if (!oldPane || !newPane) return;

    // Плавний кросфейд без стрибків висоти чи зміщення позиції
    isSwitching = true;
    oldPane.classList.remove('is-anim-in');
    oldPane.classList.add('is-anim-out');

    setTimeout(() => {
      oldPane.classList.add('is-hidden');
      oldPane.classList.remove('is-anim-out');
      oldPane.hidden = true;

      // Оновлюємо сітку та форму під вибраний режим
      syncFormAndGrid(targetMode);

      newPane.hidden = false;
      newPane.classList.remove('is-hidden');
      newPane.classList.add('is-anim-out');

      // Примусовий reflow перед анімацією появи
      void newPane.offsetHeight;

      newPane.classList.remove('is-anim-out');
      newPane.classList.add('is-anim-in');

      // Якщо положення панелі змістилося, миттєво стабілізуємо скрол
      if (toolbar && prevToolbarTop !== null) {
        const currentToolbarTop = toolbar.getBoundingClientRect().top;
        const diff = currentToolbarTop - prevToolbarTop;
        if (Math.abs(diff) > 2) {
          window.scrollBy({ top: diff, behavior: 'instant' });
        }
      }

      setTimeout(() => {
        isSwitching = false;
      }, 240);
    }, 130);
  }

  if (btnCards) btnCards.addEventListener('click', () => setViewMode('cards'));
  if (btnTable) btnTable.addEventListener('click', () => setViewMode('table'));

  // Відновлення збереженого режиму без анімації при завантаженні
  try {
    const savedMode = localStorage.getItem('access_links_view_mode');
    if (savedMode === 'table') {
      setViewMode('table', true);
    }
  } catch (_) {}

  // ─── 5. Масове згортання / розгортання карток ─────────────────────────────
  if (collapseAllBtn) {
    let allExpanded = false;
    collapseAllBtn.addEventListener('click', () => {
      const visibleCards = Array.from(cards).filter(c => c.style.display !== 'none');
      if (allExpanded) {
        // Згортаємо неактивні, повертаючи охайний вигляд
        visibleCards.forEach(c => {
          if (c.dataset.status === 'inactive') {
            c.removeAttribute('open');
          }
        });
        collapseAllBtn.textContent = '↕️ Згорнути всі';
        allExpanded = false;
      } else {
        visibleCards.forEach(c => c.setAttribute('open', ''));
        collapseAllBtn.textContent = '↕️ Згорнути неактивні';
        allExpanded = true;
      }
    });
  }
})();
