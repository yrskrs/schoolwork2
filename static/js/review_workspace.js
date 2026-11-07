/* Moving between panels never replaces the file DOM or unfinished editor state. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        const container = document.querySelector('.fv-container');
        const workbench = document.getElementById('fv-workbench');
        const documentPane = document.getElementById('fv-document-card');
        const scroller = document.getElementById('fv-panel-scroll');
        if (!container || !workbench || !documentPane || !scroller) return;
        const tabs = Array.from(workbench.querySelectorAll('[data-review-tab]'));
        const panels = Array.from(workbench.querySelectorAll('[data-review-panel]'));
        const mobile = matchMedia('(max-width:800px)');
        const positions = Object.create(null), valid = new Set(panels.map(p => p.dataset.reviewPanel));
        const expand = document.getElementById('fv-expand-tools'), collapse = document.getElementById('fv-collapse-tools');
        const expandDocument = document.getElementById('fv-expand-document'), restore = document.getElementById('fv-restore-tools');
        const mobileDocument = document.getElementById('fv-mobile-document'), mobileTools = document.getElementById('fv-mobile-tools');
        let current = 'comments', layout = 'split', mobilePanel = 'document';
        function remember(key, value) { try { sessionStorage.setItem('schoolnet.review.v2.' + key, value); } catch (error) {} }
        try {
            const saved = sessionStorage.getItem('schoolnet.review.v2.panel');
            if (valid.has(saved)) current = saved;
            const savedLayout = sessionStorage.getItem('schoolnet.review.v2.layout');
            if (['split', 'tools', 'document'].includes(savedLayout)) layout = savedLayout;
            const savedMobile = sessionStorage.getItem('schoolnet.review.v2.mobile');
            if (savedMobile === 'document' || savedMobile === 'tools') mobilePanel = savedMobile;
        } catch (error) {}
        function fitViewport() {
            const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
            const top = Math.max(0, container.getBoundingClientRect().top) / zoom;
            container.style.setProperty('--fv-top', top + 'px');
            container.style.setProperty('--fv-viewport-height', (window.innerHeight / zoom) + 'px');
        }
        function render() {
            const documentOnly = mobile.matches ? mobilePanel === 'document' : layout === 'document';
            const toolsOnly = mobile.matches ? !documentOnly : layout === 'tools';
            documentPane.hidden = toolsOnly;
            workbench.hidden = documentOnly;
            restore.hidden = mobile.matches || !documentOnly;
            container.classList.toggle('fv-tools-expanded', !mobile.matches && toolsOnly);
            container.classList.toggle('fv-document-expanded', !mobile.matches && documentOnly);
            panels.forEach(panel => {panel.hidden = panel.dataset.reviewPanel !== current;});
            tabs.forEach(tab => {
                const active = tab.dataset.reviewTab === current;
                tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
            });
            mobileDocument.setAttribute('aria-pressed', String(documentOnly));
            mobileTools.setAttribute('aria-pressed', String(!documentOnly));
            expand.setAttribute('aria-pressed', String(toolsOnly));
            expand.textContent = toolsOnly ? '↔ Робота поруч' : '⤢ Розгорнути';
            expandDocument.setAttribute('aria-pressed', String(documentOnly));
            expandDocument.textContent = documentOnly && !mobile.matches ? '↔ Поруч' : '⤢ Розгорнути';
            expandDocument.setAttribute('aria-label', mobile.matches ? 'Робота на весь екран' : documentOnly ? 'Повернути оцінювання поруч' : 'Розгорнути роботу учня');
            ['btn-web-toggle-width', 'btn-html-toggle-width'].forEach(id => {
                const toggle = document.getElementById(id);
                if (!toggle) return;
                toggle.setAttribute('aria-pressed', String(documentOnly));
                toggle.textContent = documentOnly && !mobile.matches ? '↔ Поруч' : '⤢ На всю ширину';
            });
            window.reviewCurrentPanel = current;
            fitViewport();
        }
        function setLayout(next) { layout = next; remember('layout', layout); render(); }
        function select(name, focusPanel) {
            if (name !== 'document' && !valid.has(name)) return;
            positions[current] = scroller.scrollTop;
            if (name === 'document') {
                if (mobile.matches) { mobilePanel = 'document'; remember('mobile', mobilePanel); }
                else setLayout('document');
            } else {
                current = name; remember('panel', current);
                mobilePanel = 'tools'; remember('mobile', mobilePanel);
                if (layout === 'document') setLayout('split');
            }
            render(); scroller.scrollTop = positions[current] || 0;
            if (focusPanel) (name === 'document' ? documentPane : panels.find(p => p.dataset.reviewPanel === current)).focus({preventScroll:true});
        }
        tabs.forEach(tab => {
            tab.addEventListener('click', () => select(tab.dataset.reviewTab));
            tab.addEventListener('keydown', event => {
                if (event.altKey || event.ctrlKey || event.metaKey) return;
                const visible = tabs.filter(t => getComputedStyle(t).display !== 'none');
                const index = visible.indexOf(tab); let next;
                if (['ArrowRight', 'ArrowDown'].includes(event.key)) next = (index + 1) % visible.length;
                else if (['ArrowLeft', 'ArrowUp'].includes(event.key)) next = (index + visible.length - 1) % visible.length;
                else if (event.key === 'Home') next = 0;
                else if (event.key === 'End') next = visible.length - 1;
                else return;
                event.preventDefault(); select(visible[next].dataset.reviewTab); visible[next].focus();
            });
        });
        expand.addEventListener('click', () => setLayout(layout === 'tools' ? 'split' : 'tools'));
        collapse.addEventListener('click', () => {setLayout('document'); restore.focus({preventScroll:true});});
        expandDocument.addEventListener('click', () => {
            if (mobile.matches) {
                if (document.fullscreenElement) document.exitFullscreen();
                else documentPane.requestFullscreen?.().catch(() => {});
            } else setLayout(layout === 'document' ? 'split' : 'document');
        });
        mobile.addEventListener('change', render);
        window.addEventListener('resize', fitViewport);
        if (window.ResizeObserver) {
            const observer = new ResizeObserver(fitViewport);
            observer.observe(document.querySelector('.fv-workspace-toolbar'));
            const navbar = document.querySelector('.navbar'); if (navbar) observer.observe(navbar);
            const mobileNav = document.querySelector('.platform-teacher-mobile-nav'); if (mobileNav) observer.observe(mobileNav);
        }
        if (window.MutationObserver) new MutationObserver(fitViewport).observe(document.documentElement, {attributes:true,attributeFilter:['style','data-font-scale']});
        // Sanitized and asynchronously inserted spreadsheets use trusted delegated events.
        // Formula strings are displayed as text; they are never evaluated.
        documentPane.addEventListener('click', event => {
            const cell = event.target.closest('.excel-table td');
            if (cell && !cell.classList.contains('row-header') && window.showFormula) window.showFormula(cell);
        });
        window.selectReviewPanel = select;
        window.updateReviewCommentCount = function () {
            const count = document.querySelectorAll('#fv-comments-container .fv-comment-card').length;
            const badge = document.getElementById('fv-comment-count');
            badge.textContent = count; badge.hidden = !count;
            badge.parentElement.setAttribute('aria-label', 'Історія відгуків. Відгуків: ' + count);
            if (!count && !document.getElementById('no-comments-msg')) {
                const empty = document.createElement('p'); empty.id = 'no-comments-msg'; empty.className = 'text-muted';
                empty.textContent = 'Коментарів до роботи ще немає.';
                document.getElementById('fv-comments-container').append(empty);
            }
        };
        window.updateReviewAIStatus = function (data) {
            const badge = document.getElementById('fv-quick-ai');
            badge.classList.toggle('fv-status-warning', !!data.ai_generated_detected);
            badge.textContent = data.ai_generated_detected ? '🤖 Ознаки ШІ' : '🤖 Авторство: невідоме';
            badge.title = data.ai_generated_details || 'Результат ШІ не підтверджує самостійність роботи.';
        };
        window.updateReviewCommentCount();
        container.classList.add('fv-workspace-ready'); render();
    });
}());
