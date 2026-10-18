/* Small local UI helpers; submissions and server endpoints are left to existing scripts. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        var taskTools = document.querySelector('.assignment-detail-sticky-tools');
        if (taskTools) {
            var root = document.documentElement;
            root.classList.add('school-has-task-tools');
            var navbar = document.querySelector('.navbar');
            function updateTaskOffsets() {
                var scale = parseFloat(getComputedStyle(root).zoom) || 1;
                var navHeight = navbar ? navbar.getBoundingClientRect().height / scale : 0;
                var navTop = navbar ? parseFloat(getComputedStyle(navbar).top) || 0 : 0;
                root.style.setProperty('--school-sticky-top', Math.ceil(navHeight + navTop) + 'px');
                root.style.setProperty('--school-tools-height', Math.ceil(taskTools.getBoundingClientRect().height / scale) + 'px');
            }
            updateTaskOffsets();
            if (window.ResizeObserver) {
                var taskObserver = new ResizeObserver(updateTaskOffsets);
                taskObserver.observe(taskTools);
                if (navbar) taskObserver.observe(navbar);
            }
            window.addEventListener('resize', updateTaskOffsets);
            new MutationObserver(updateTaskOffsets).observe(root, {attributes:true, attributeFilter:['data-font-scale']});
        }
        function closeMenus(event) {
            document.querySelectorAll('[data-school-menu]').forEach(function (menu) {
                if (!menu.open) return;
                if (event.type === 'keydown') {
                    if (event.key !== 'Escape') return;
                    menu.open = false;
                    menu.querySelector('summary').focus();
                } else if (!menu.contains(event.target) || event.target.closest('.school-card-menu-panel a, .school-card-menu-panel button')) menu.open = false;
            });
        }
        document.addEventListener('click', closeMenus);
        document.addEventListener('keydown', closeMenus);
        // The class filters are inline on small screens. Keep older guide links working.
        var legacyFilter = document.getElementById('mobile-menu-btn');
        if (legacyFilter && document.querySelector('.home-page')) {
            legacyFilter.addEventListener('click', function () {
                var filter = document.getElementById('main-sidebar');
                if (filter) {
                    filter.classList.remove('mobile-open');
                    document.body.classList.remove('sidebar-open');
                    filter.scrollIntoView({block:'start', behavior:'auto'});
                    var first = filter.querySelector('a');
                    if (first) first.focus({preventScroll:true});
                }
            });
        }
        // Bring validation failures into view without hiding server error messages.
        var submitForm = document.getElementById('submit-form');
        if (submitForm) {
            var alert = document.getElementById('form-validation-alert');
            if (alert) alert.setAttribute('role', 'alert');
            var drop = document.getElementById('file-drop-zone');
            if (drop) drop.addEventListener('keydown', function (event) {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    drop.click();
                }
            });
        }
    });
}());
