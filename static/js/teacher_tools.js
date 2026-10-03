(function () {
    document.addEventListener('DOMContentLoaded', function () {
        const tools = document.getElementById('teacher-page-tools');
        if (tools && window.matchMedia('(max-width: 960px)').matches) tools.open = false;
        if (tools) {
            tools.addEventListener('invalid', function () { tools.open = true; }, true);
        }
    });
})();
