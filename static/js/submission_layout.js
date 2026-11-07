(function () {
    document.addEventListener('DOMContentLoaded', function () {
        const form = document.getElementById('submit-form');
        const classSelect = document.getElementById('id_class_group');
        if (!form || !classSelect) return;
        const nameInput = document.getElementById('id_full_name');
        const filesInput = document.getElementById('id_files');
        const linkInput = document.getElementById('id_link');
        const commentInput = document.getElementById('id_comment_student');

        function summary(selector, label, ready) {
            form.querySelectorAll(selector).forEach(function (element) {
                if (element.textContent !== label) element.textContent = label;
                element.dataset.ready = String(ready);
            });
        }
        function syncReadiness() {
            const option = classSelect.options[classSelect.selectedIndex];
            const label = classSelect.value && option ? option.textContent.trim() : 'Клас не обрано';
            summary('[data-submission-class]', label, Boolean(classSelect.value));
            const name = nameInput ? nameInput.value.trim() : '';
            summary('[data-submission-name]', name || 'Не обрано', Boolean(name));
            const count = filesInput && filesInput.files ? filesInput.files.length : 0;
            const hasLink = Boolean(linkInput && linkInput.value.trim());
            const hasText = Boolean(commentInput && commentInput.value.trim());
            const parts = [];
            if (count) parts.push('Файлів: ' + count);
            if (hasLink) parts.push('Посилання');
            if (hasText) parts.push('Текст');
            summary('[data-submission-work]', parts.join(' · ') || 'Не додано', parts.length > 0);
        }
        ['input', 'change', 'click', 'submission:files-updated'].forEach(function (event) {
            form.addEventListener(event, syncReadiness);
        });
        window.addEventListener('pageshow', syncReadiness);
        form.addEventListener('submit', syncReadiness, true);
        syncReadiness();

        const optionalFields = document.getElementById('submission-optional-fields');
        if (optionalFields) {
            optionalFields.addEventListener('toggle', function () {
                if (optionalFields.open && window.autoResizeComment) {
                    window.autoResizeComment(commentInput);
                }
            });
        }
        const dropZone = document.getElementById('file-drop-zone');
        if (dropZone) {
            dropZone.addEventListener('keydown', function (event) {
                // The hidden file input may bubble its synthetic click through the zone.
                if (event.target === dropZone && (event.key === 'Enter' || event.key === ' ')) {
                    event.preventDefault();
                    filesInput.click();
                }
            });
        }
    });
})();
