(function () {
    document.addEventListener('DOMContentLoaded', function () {
        const form = document.getElementById('submit-form');
        const classSelect = document.getElementById('id_class_group');
        if (!form || !classSelect) return;

        function syncSelectedClass() {
            const option = classSelect.options[classSelect.selectedIndex];
            const label = classSelect.value && option ? option.textContent.trim() : 'Клас не обрано';
            document.querySelectorAll('[data-submission-class]').forEach(function (element) {
                element.textContent = label;
            });
        }
        classSelect.addEventListener('change', syncSelectedClass);
        classSelect.addEventListener('input', syncSelectedClass);
        window.addEventListener('pageshow', syncSelectedClass);
        form.addEventListener('submit', syncSelectedClass, true);
        syncSelectedClass();

        const moreOptions = form.querySelector('.submission-more-options');
        if (moreOptions && window.matchMedia('(max-width: 960px)').matches) moreOptions.open = false;

        const optionalFields = document.getElementById('submission-optional-fields');
        if (optionalFields) {
            optionalFields.addEventListener('toggle', function () {
                if (optionalFields.open && window.autoResizeComment) {
                    window.autoResizeComment(document.getElementById('id_comment_student'));
                }
            });
        }

        const dropZone = document.getElementById('file-drop-zone');
        if (dropZone) {
            dropZone.addEventListener('keydown', function (event) {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    document.getElementById('id_files').click();
                }
            });
        }
    });
})();
