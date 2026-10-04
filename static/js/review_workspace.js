/* The review workbench keeps one scroll position per panel and preserves file DOM. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        const container = document.querySelector('.fv-container');
        const workbench = document.getElementById('fv-workbench');
        const documentPane = document.getElementById('fv-document-card');
        const scroller = document.getElementById('fv-panel-scroll');
        const expand = document.getElementById('fv-expand-tools');
        if (!container || !workbench || !documentPane || !scroller || !expand) return;
        const tabs = Array.from(workbench.querySelectorAll('[data-review-tab]'));
        const panels = Array.from(workbench.querySelectorAll('[data-review-panel]'));
        const mobile = matchMedia('(max-width:1100px)');
        const positions = Object.create(null);
        const valid = new Set(panels.map(panel => panel.dataset.reviewPanel));
        let current = 'task', documentOnly = false, expanded = false, mobilePanel = 'document';
        try {
            const saved = sessionStorage.getItem('schoolnet.review.panel');
            if (valid.has(saved)) current = saved;
            expanded = sessionStorage.getItem('schoolnet.review.expanded') === 'true';
            const savedMobile = sessionStorage.getItem('schoolnet.review.mobilePanel');
            if (savedMobile === 'document' || valid.has(savedMobile)) mobilePanel = savedMobile;
        } catch (error) { /* Private browsing can disable storage. */ }

        function render() {
            const showDocumentOnly = mobile.matches && documentOnly;
            const showToolsOnly = !mobile.matches && expanded;
            documentPane.hidden = mobile.matches ? !showDocumentOnly : showToolsOnly;
            container.classList.toggle('fv-tools-expanded', showToolsOnly);
            container.classList.toggle('fv-mobile-document', showDocumentOnly);
            scroller.hidden = showDocumentOnly;
            workbench.querySelector('.fv-workbench-heading').hidden = showDocumentOnly;
            panels.forEach(panel => { panel.hidden = panel.dataset.reviewPanel !== current; });
            const selected = showDocumentOnly ? 'document' : current;
            tabs.forEach(tab => {
                const active = tab.dataset.reviewTab === selected;
                tab.setAttribute('aria-selected', String(active));
                tab.tabIndex = active ? 0 : -1;
            });
            documentPane.setAttribute('role', mobile.matches ? 'tabpanel' : 'region');
            documentPane.setAttribute('aria-labelledby', mobile.matches ? 'fv-tab-document' : 'fv-file-title');
            expand.setAttribute('aria-pressed', String(expanded));
            expand.textContent = expanded ? '↔ Робота поруч' : '⤢ Розгорнути';
        }
        function select(name, focusPanel) {
            if (name === 'document') {
                if (!mobile.matches) return;
                positions[current] = scroller.scrollTop;
                documentOnly = true;
            } else if (valid.has(name)) {
                if (!documentOnly) positions[current] = scroller.scrollTop;
                current = name;
                documentOnly = false;
                try { sessionStorage.setItem('schoolnet.review.panel', current); } catch (error) {}
            } else return;
            render();
            if (mobile.matches) {
                mobilePanel = name;
                try { sessionStorage.setItem('schoolnet.review.mobilePanel', name); } catch (error) {}
            }
            scroller.scrollTop = positions[current] || 0;
            if (focusPanel) (name === 'document' ? documentPane : panels.find(panel => panel.dataset.reviewPanel === current)).focus({preventScroll:true});
        }
        tabs.forEach(tab => {
            tab.addEventListener('click', () => select(tab.dataset.reviewTab));
            tab.addEventListener('keydown', event => {
                if (event.altKey || event.ctrlKey || event.metaKey) return;
                const visible = tabs.filter(item => getComputedStyle(item).display !== 'none');
                const index = visible.indexOf(tab);
                let next;
                if (event.key === 'ArrowRight') next = (index + 1) % visible.length;
                else if (event.key === 'ArrowLeft') next = (index + visible.length - 1) % visible.length;
                else if (event.key === 'Home') next = 0;
                else if (event.key === 'End') next = visible.length - 1;
                else return;
                event.preventDefault();
                select(visible[next].dataset.reviewTab);
                visible[next].focus();
            });
        });
        expand.addEventListener('click', () => {
            expanded = !expanded;
            try { sessionStorage.setItem('schoolnet.review.expanded', String(expanded)); } catch (error) {}
            render();
        });
        mobile.addEventListener('change', () => { documentOnly = mobilePanel === 'document'; render(); });
        window.selectReviewPanel = select;
        container.classList.add('fv-workspace-ready');
        documentOnly = mobile.matches && mobilePanel === 'document';
        render();
    });
}());
