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

  // Search and filters cover all pages, including with JavaScript disabled.
  const searchForm = document.getElementById('access-filter-form');
  const searchInput = document.getElementById('access-search-input');
  const searchClear = document.getElementById('access-search-clear');
  const cards = document.querySelectorAll('.access-link-card');
  if (searchInput) searchInput.addEventListener('input', () => { searchClear.hidden = !searchInput.value; });
  if (searchClear) searchClear.addEventListener('click', () => { searchInput.value = ''; searchForm.requestSubmit(); });
  document.getElementById('access-per-page')?.addEventListener('change', () => searchForm.requestSubmit());
  // A closed QR section never starts a QR request, even on long lists.
  document.querySelectorAll('.access-qr').forEach(section => {
    section.addEventListener('toggle', () => {
      const image = section.querySelector('img[data-src]');
      if (section.open && image && !image.hasAttribute('src')) image.src = image.dataset.src;
    });
  });

  // One large QR preview in the native top layer; no image request before opening.
  const qrDialog = document.getElementById('access-qr-dialog');
  const qrImage = document.getElementById('access-qr-image');
  const qrStatus = document.getElementById('access-qr-load-status');
  if (qrDialog && typeof qrDialog.showModal === 'function') {
    document.querySelectorAll('[data-qr-url]').forEach(opener => {
      opener.addEventListener('click', event => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        const name = opener.dataset.qrName || 'Посилання';
        document.getElementById('access-qr-name').textContent = name;
        document.getElementById('access-qr-download').href = opener.dataset.qrUrl;
        qrImage.alt = `QR-код: ${name}`;
        qrImage.hidden = true;
        qrStatus.hidden = false;
        qrStatus.textContent = 'Завантаження QR-коду…';
        qrImage.onload = () => { qrImage.hidden = false; qrStatus.hidden = true; };
        qrImage.onerror = () => { qrImage.hidden = true; qrStatus.hidden = false; qrStatus.textContent = 'Не вдалося завантажити QR-код. Закрийте вікно та спробуйте ще раз.'; };
        qrDialog.showModal();
        qrImage.src = opener.dataset.qrUrl;
      });
    });
    qrDialog.addEventListener('click', event => {
      const box = qrDialog.getBoundingClientRect();
      if (event.target === qrDialog && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) qrDialog.close();
    });
    qrDialog.addEventListener('close', () => {
      qrImage.onload = null;
      qrImage.onerror = null;
      qrImage.removeAttribute('src');
      qrImage.hidden = true;
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

  const isEditing = formDetails && (formDetails.dataset.isEditing === 'true' || formDetails.dataset.keepOpen === 'true');
  let isSwitching = false;
  let currentViewMode = 'cards';
  let pendingViewMode = null;

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
    const targetMode = (mode === 'table') ? 'table' : 'cards';
    if (isSwitching && !immediate) { pendingViewMode = targetMode; return; }
    if (targetMode === currentViewMode && !immediate) return;
    currentViewMode = targetMode;
    immediate = immediate || window.matchMedia('(prefers-reduced-motion: reduce)').matches;

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
        if (pendingViewMode) { const next = pendingViewMode; pendingViewMode = null; setViewMode(next); }
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

  // Collapse and expand all visible cards; the label describes the next action.
  function updateCollapseLabel() {
    const visible = Array.from(cards).filter(card => !card.hidden && card.style.display !== 'none');
    const expanded = visible.length > 0 && visible.every(card => card.open);
    if (collapseAllBtn) collapseAllBtn.textContent = expanded ? '↕️ Згорнути всі' : '↕️ Розгорнути всі';
  }
  if (collapseAllBtn) collapseAllBtn.addEventListener('click', () => {
    const visible = Array.from(cards).filter(card => !card.hidden && card.style.display !== 'none');
    const collapse = visible.length > 0 && visible.every(card => card.open);
    visible.forEach(card => { card.open = !collapse; });
    updateCollapseLabel();
  });
  cards.forEach(card => card.addEventListener('toggle', updateCollapseLabel));
  updateCollapseLabel();
})();
