/* AI sections work with ordinary links; enhance them without replacing the forms. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        const root = document.querySelector('[data-ai-settings]');
        if (!root) return;
        const tabs = Array.from(root.querySelectorAll('[data-ai-tab]'));
        const panels = Array.from(root.querySelectorAll('[data-ai-panel]'));
        const sections = tabs.map(tab => tab.dataset.aiTab);
        let active = root.dataset.aiSection;

        function select(section, options) {
            options = options || {};
            if (!sections.includes(section)) section = 'connection';
            active = section;
            root.dataset.aiSection = section;
            tabs.forEach(tab => {
                const selected = tab.dataset.aiTab === section;
                tab.setAttribute('aria-selected', String(selected));
                tab.tabIndex = selected ? 0 : -1;
            });
            panels.forEach(panel => { panel.hidden = panel.dataset.aiPanel !== section; });
            if (options.updateUrl !== false) {
                const url = new URL(window.location.href);
                url.searchParams.set('tab', 'ai');
                url.searchParams.set('ai_section', section);
                url.hash = '';
                if (url.href !== window.location.href) history.pushState(null, '', url);
            }
            if (options.focus) tabs.find(tab => tab.dataset.aiTab === section).focus();
        }

        tabs.forEach((tab, index) => {
            tab.addEventListener('click', event => {
                if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                event.preventDefault();
                select(tab.dataset.aiTab);
            });
            tab.addEventListener('keydown', event => {
                let next;
                if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
                else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
                else if (event.key === 'Home') next = 0;
                else if (event.key === 'End') next = tabs.length - 1;
                else return;
                event.preventDefault();
                select(tabs[next].dataset.aiTab, {focus: true});
            });
        });

        root.querySelectorAll('form').forEach(form => {
            const panel = form.closest('[data-ai-panel]');
            if (!panel || form.querySelector('[name="ai_section"]')) return;
            const field = document.createElement('input');
            field.type = 'hidden';
            field.name = 'ai_section';
            field.value = panel.dataset.aiPanel;
            form.appendChild(field);
        });

        // A collapsed detail must open before the browser focuses an invalid field.
        root.addEventListener('invalid', event => {
            const panel = event.target.closest('[data-ai-panel]');
            if (panel) select(panel.dataset.aiPanel);
            for (let parent = event.target.parentElement; parent && parent !== root; parent = parent.parentElement) {
                if (parent.tagName === 'DETAILS') parent.open = true;
            }
        }, true);

        function sectionFromLocation() {
            const hash = window.location.hash;
            if (hash === '#used-models-stats-section') return 'statistics';
            if (hash === '#ai-error-logs-section') return 'errors';
            return new URL(window.location.href).searchParams.get('ai_section') || 'connection';
        }
        window.addEventListener('popstate', function () {
            const url = new URL(window.location.href);
            const requestedTab = url.searchParams.get('tab');
            const mainTab = ['profile', 'environment', 'ai'].includes(requestedTab) ? requestedTab : 'profile';
            if (window.switchSettingsTab) window.switchSettingsTab(mainTab, null, false);
            select(sectionFromLocation(), {updateUrl: false});
        });
        window.addEventListener('hashchange', function () {
            select(sectionFromLocation(), {updateUrl: false});
        });
        window.SchoolNetAISettings = {
            select: select,
            getActive: function () { return active; },
            reveal: function (section, element) {
                select(section);
                for (let parent = element && element.parentElement; parent && parent !== root; parent = parent.parentElement) {
                    if (parent.tagName === 'DETAILS') parent.open = true;
                }
            }
        };
        select(sectionFromLocation(), {updateUrl: false});
    });
})();
