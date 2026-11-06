var _duplicateOrigClassIds = [];
var _dupTeacherSchedules = {};
var _dupSchedulesLoaded = false;

function loadDupTeacherSchedules(callback) {
    if (_dupSchedulesLoaded) { if (callback) callback(); return; }
    fetch('/api/teacher-schedules/')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data && data.schedules_by_class) {
                Object.keys(data.schedules_by_class).forEach(function(k) {
                    _dupTeacherSchedules[parseInt(k, 10)] = data.schedules_by_class[k];
                });
            }
            _dupSchedulesLoaded = true;
            if (callback) callback();
        })
        .catch(function(err) {
            console.warn('Не вдалося завантажити розклад:', err);
            _dupSchedulesLoaded = true;
            if (callback) callback();
        });
}

function dupRebuildScheduleGrid() {
    var grid = document.getElementById('dup-schedule-targets-grid');
    var wrap = document.getElementById('dup-schedule-wrap');
    if (!grid || !wrap) return;

    var checkedCbs = document.querySelectorAll('.duplicate-class-cb:checked');
    if (checkedCbs.length === 0) {
        wrap.style.display = 'none';
        grid.innerHTML = '';
        return;
    }
    wrap.style.display = 'block';

    // Зберігаємо попередні значення
    var existingValues = {};
    var existingDates = {};
    grid.querySelectorAll('.dup-schedule-row').forEach(function(row) {
        var cid = parseInt(row.dataset.classId, 10);
        var sel = row.querySelector('select');
        var dateInp = row.querySelector('input[type="date"]');
        if (sel) existingValues[cid] = sel.value;
        if (dateInp) existingDates[cid] = dateInp.value;
    });

    grid.innerHTML = '';

    checkedCbs.forEach(function(cb) {
        var classId = parseInt(cb.dataset.classId, 10);
        var className = cb.dataset.className || 'Клас';
        var schedules = _dupTeacherSchedules[classId] || [];
        var prevVal = existingValues[classId] || 'auto';
        var prevDate = existingDates[classId] || '';

        var row = document.createElement('div');
        row.className = 'dup-schedule-row';
        row.dataset.classId = classId;

        var nameSpan = document.createElement('span');
        nameSpan.className = 'dup-schedule-class-name';
        nameSpan.textContent = '🏫 ' + className + ':';
        row.appendChild(nameSpan);

        var selectWrap = document.createElement('div');
        selectWrap.style.cssText = 'flex:1;min-width:180px;';
        var select = document.createElement('select');
        select.name = 'class_schedule_target_' + classId;
        select.className = 'form-select';
        select.style.cssText = 'font-size:12px;padding:5px 8px;width:100%;';

        var autoOpt = document.createElement('option');
        autoOpt.value = 'auto';
        autoOpt.textContent = schedules.length > 0 ? '⚡ Автоматично (найближчий урок за розкладом)' : '(Розклад для цього класу не задано)';
        select.appendChild(autoOpt);

        schedules.forEach(function(sch) {
            var opt = document.createElement('option');
            opt.value = sch.value;
            opt.textContent = sch.label;
            select.appendChild(opt);
        });

        var noneOpt = document.createElement('option');
        noneOpt.value = 'none';
        noneOpt.textContent = '🌐 Без прив\'язки до дня розкладу';
        select.appendChild(noneOpt);

        if (prevVal) select.value = prevVal;
        selectWrap.appendChild(select);
        row.appendChild(selectWrap);

        var dateWrap = document.createElement('div');
        dateWrap.style.cssText = 'display:flex;align-items:center;gap:5px;flex-shrink:0;';

        var dateLabel = document.createElement('span');
        dateLabel.style.cssText = 'font-size:11.5px;color:var(--color-text-muted);white-space:nowrap;';
        dateLabel.textContent = '📅 Дата:';
        dateWrap.appendChild(dateLabel);

        var dateInput = document.createElement('input');
        dateInput.type = 'date';
        dateInput.name = 'class_schedule_target_date_' + classId;
        dateInput.className = 'form-input';
        dateInput.style.cssText = 'font-size:12px;padding:4px 8px;width:130px;';
        dateInput.title = 'Конкретна дата уроку для ' + className;
        if (prevDate) dateInput.value = prevDate;
        dateWrap.appendChild(dateInput);

        row.appendChild(dateWrap);
        grid.appendChild(row);
    });
}

function duplicateOnClassChange(cb) {
    duplicateUpdateLabelStyle(cb);
    loadDupTeacherSchedules(dupRebuildScheduleGrid);
}

function duplicateUpdateLabelStyle(cb) {
    var label = cb.closest('.duplicate-class-label');
    if (label) {
        if (cb.checked) label.classList.add('is-checked');
        else label.classList.remove('is-checked');
    }
    var errEl = document.getElementById('duplicate-classes-error');
    if (errEl) errEl.style.display = 'none';
    var box = document.getElementById('duplicate-classes-container');
    if (box) box.style.borderColor = 'var(--color-border)';
}

function duplicateSelectAllClasses(checked) {
    document.querySelectorAll('.duplicate-class-cb').forEach(function(cb) {
        cb.checked = checked;
        duplicateUpdateLabelStyle(cb);
    });
    loadDupTeacherSchedules(dupRebuildScheduleGrid);
}

function duplicateResetOrigClasses() {
    var classSet = new Set(_duplicateOrigClassIds || []);
    document.querySelectorAll('.duplicate-class-cb').forEach(function(cb) {
        cb.checked = classSet.has(parseInt(cb.value, 10));
        duplicateUpdateLabelStyle(cb);
    });
    loadDupTeacherSchedules(dupRebuildScheduleGrid);
}

function renderDuplicateClasses(classesList, selectedIds) {
    var container = document.getElementById('duplicate-classes-container');
    if (!container) return;
    container.innerHTML = '';
    var selectedSet = new Set(selectedIds || []);

    classesList.forEach(function(c) {
        var isChecked = selectedSet.has(c.id);
        var label = document.createElement('label');
        label.className = 'duplicate-class-label' + (isChecked ? ' is-checked' : '');
        label.style.cssText = 'display:inline-flex;align-items:center;gap:5px;background:var(--color-surface);border:1px solid var(--color-border);border-radius:6px;padding:4px 9px;font-size:12px;font-weight:700;cursor:pointer;user-select:none;transition:all 0.15s ease;';

        var input = document.createElement('input');
        input.type = 'checkbox';
        input.name = 'duplicate_classes[]';
        input.value = c.id;
        input.className = 'duplicate-class-cb';
        input.dataset.classId = c.id;
        input.dataset.className = c.name;
        input.checked = isChecked;
        input.style.cssText = 'cursor:pointer;accent-color:var(--color-primary);';
        input.onchange = function() { duplicateOnClassChange(this); };

        var span = document.createElement('span');
        span.textContent = c.name;

        label.appendChild(input);
        label.appendChild(span);
        container.appendChild(label);
    });
}

function openDuplicateModal(assignmentId, title, classIds, subjectName) {
    var modal = document.getElementById('duplicate-modal');
    var form = document.getElementById('duplicate-assignment-form');
    var titleEl = document.getElementById('duplicate-modal-orig-title');
    var titleInput = document.getElementById('duplicate-title');
    var subjectEl = document.getElementById('duplicate-modal-orig-subject');
    var errEl = document.getElementById('duplicate-classes-error');

    if (!modal || !form) return;

    form.action = '/teacher/assignment/' + assignmentId + '/duplicate/';
    if (titleEl) titleEl.textContent = title || '—';
    if (titleInput) titleInput.value = title || '';

    if (subjectEl) {
        if (subjectName) {
            subjectEl.textContent = subjectName;
            subjectEl.style.display = 'inline-block';
        } else {
            subjectEl.style.display = 'none';
        }
    }

    if (errEl) errEl.style.display = 'none';
    var box = document.getElementById('duplicate-classes-container');
    if (box) box.style.borderColor = 'var(--color-border)';

    _duplicateOrigClassIds = classIds || [];

    var existingCbs = document.querySelectorAll('.duplicate-class-cb');
    if (existingCbs.length > 0) {
        duplicateResetOrigClasses();
    } else {
        fetch('/api/classes/')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data && data.classes) {
                    renderDuplicateClasses(data.classes, _duplicateOrigClassIds);
                    loadDupTeacherSchedules(dupRebuildScheduleGrid);
                }
            })
            .catch(function(err) { console.error('Помилка завантаження класів:', err); });
    }

    modal.style.display = 'flex';
}

function closeDuplicateModal() {
    var modal = document.getElementById('duplicate-modal');
    if (modal) modal.style.display = 'none';
}

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeDuplicateModal();
});

function initializeDuplicateDialog() {
    var form = document.getElementById('duplicate-assignment-form');
    if (form) {
        form.addEventListener('submit', function(e) {
            var checked = form.querySelectorAll('.duplicate-class-cb:checked');
            var errEl = document.getElementById('duplicate-classes-error');
            var box = document.getElementById('duplicate-classes-container');
            if (checked.length === 0) {
                e.preventDefault();
                if (errEl) errEl.style.display = 'block';
                if (box) {
                    box.style.borderColor = '#ef4444';
                    box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
                return false;
            }
            if (errEl) errEl.style.display = 'none';
            var btn = document.getElementById('btn-submit-duplicate');
            var icon = document.getElementById('duplicate-submit-icon');
            var text = document.getElementById('duplicate-submit-text');
            if (btn) {
                btn.disabled = true;
                if (icon) icon.textContent = '⏳';
                if (text) text.textContent = 'Створюємо...';
            }
        });
    }
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initializeDuplicateDialog);
else initializeDuplicateDialog();
