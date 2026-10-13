document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('assignment-export-form');
    if (!form) return;
    const assignments = Array.from(form.querySelectorAll('input[name="assignments"]'));
    const days = Array.from(form.querySelectorAll('.export-day'));
    function updateSelection() {
        const count = new Set(assignments.filter(input => input.checked).map(input => input.value)).size;
        document.getElementById('export-selected-count').textContent = 'Обрано: ' + count;
        document.getElementById('export-download').disabled = count === 0;
        days.forEach(day => {
            const inputs = Array.from(day.querySelectorAll('input[name="assignments"]'));
            const selected = inputs.filter(input => input.checked).length;
            const checkbox = day.querySelector('.export-day-checkbox');
            checkbox.checked = selected === inputs.length;
            checkbox.indeterminate = selected > 0 && selected < inputs.length;
        });
    }
    days.forEach(day => day.querySelector('.export-day-checkbox').addEventListener('change', function (event) {
        const ids = new Set(Array.from(day.querySelectorAll('input[name="assignments"]')).map(input => input.value));
        assignments.forEach(input => { if (ids.has(input.value)) input.checked = event.target.checked; });
        updateSelection();
    }));
    assignments.forEach(input => input.addEventListener('change', function () {
        assignments.forEach(other => { if (other.value === input.value) other.checked = input.checked; });
        updateSelection();
    }));
    document.getElementById('export-select-all').addEventListener('click', () => {
        assignments.forEach(input => { input.checked = true; }); updateSelection();
    });
    document.getElementById('export-clear-all').addEventListener('click', () => {
        assignments.forEach(input => { input.checked = false; }); updateSelection();
    });
    form.addEventListener('submit', function (event) {
        if (!assignments.some(input => input.checked)) event.preventDefault();
    });
    updateSelection();
});
