var _rescheduleCurrentAssignmentId = null;
var _rescheduleCurrentClassId = null;

function openRescheduleModalDirect(assignmentId, title, classId, defaultDate, defaultSlotId) {
    _rescheduleCurrentAssignmentId = assignmentId;
    _rescheduleCurrentClassId = classId || '';

    var modal = document.getElementById('reschedule-modal');
    var form = document.getElementById('reschedule-assignment-form');
    var titleEl = document.getElementById('reschedule-modal-title');
    var classInput = document.getElementById('reschedule-class-group-input');
    var dateInput = document.getElementById('reschedule-new-date');
    var slotSelect = document.getElementById('reschedule-new-bell-slot');
    var hintEl = document.getElementById('auto-next-lesson-hint');

    form.action = '/teacher/assignment/' + assignmentId + '/reschedule/';
    titleEl.textContent = title || 'Завдання #' + assignmentId;
    classInput.value = classId || '';

    // Встановлюємо дату
    if (defaultDate) {
        dateInput.value = defaultDate;
    } else {
        // За замовчуванням завтра
        var tomorrow = new Date();
        tomorrow.setDate(tomorrow.getDate() + 1);
        var yyyy = tomorrow.getFullYear();
        var mm = String(tomorrow.getMonth() + 1).padStart(2, '0');
        var dd = String(tomorrow.getDate()).padStart(2, '0');
        dateInput.value = yyyy + '-' + mm + '-' + dd;
    }

    if (defaultSlotId && slotSelect) {
        slotSelect.value = defaultSlotId;
    }

    if (hintEl) {
        hintEl.style.display = 'none';
        hintEl.textContent = '';
    }

    modal.style.display = 'flex';
}

function closeRescheduleModal() {
    var modal = document.getElementById('reschedule-modal');
    if (modal) modal.style.display = 'none';
}

function fetchAndFillNextLesson() {
    if (!_rescheduleCurrentClassId) {
        // Якщо клас не закріплений, ставимо наступний робочий день
        var d = new Date();
        d.setDate(d.getDate() + 1);
        if (d.getDay() === 0) d.setDate(d.getDate() + 1);
        if (d.getDay() === 6) d.setDate(d.getDate() + 2);
        var yyyy = d.getFullYear();
        var mm = String(d.getMonth() + 1).padStart(2, '0');
        var dd = String(d.getDate()).padStart(2, '0');
        document.getElementById('reschedule-new-date').value = yyyy + '-' + mm + '-' + dd;
        var hintEl = document.getElementById('auto-next-lesson-hint');
        hintEl.style.display = 'block';
        hintEl.textContent = 'Встановлено наступний робочий день: ' + dd + '.' + mm + '.' + yyyy;
        return;
    }

    var btn = document.getElementById('btn-auto-next-lesson');
    var origText = btn.innerHTML;
    btn.innerHTML = '<span>⏳</span> <span>Пошук у розкладі...</span>';

    fetch('/api/next-lesson-slot/?class_id=' + encodeURIComponent(_rescheduleCurrentClassId))
        .then(function(res) { return res.json(); })
        .then(function(data) {
            btn.innerHTML = origText;
            if (data.success && data.next_date) {
                document.getElementById('reschedule-new-date').value = data.next_date;
                if (data.bell_slot_id) {
                    document.getElementById('reschedule-new-bell-slot').value = data.bell_slot_id;
                }
                var hintEl = document.getElementById('auto-next-lesson-hint');
                hintEl.style.display = 'block';
                hintEl.style.color = '#10b981';
                hintEl.textContent = '✅ ' + (data.info || ('Наступний урок: ' + data.next_date_display));
            }
        })
        .catch(function(err) {
            btn.innerHTML = origText;
            console.error('Error fetching next lesson slot:', err);
        });
}

// Закриття по кліку за межі вікна або по Esc
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeRescheduleModal();
});
document.addEventListener('click', function(e) {
    var modal = document.getElementById('reschedule-modal');
    if (modal && e.target === modal) closeRescheduleModal();
});
