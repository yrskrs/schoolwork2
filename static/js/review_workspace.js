/* Panel changes and width controls preserve the file DOM and unfinished work. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        const container = document.querySelector('.fv-container');
        const workbench = document.getElementById('fv-workbench');
        const documentPane = document.getElementById('fv-document-card');
        const scroller = document.getElementById('fv-panel-scroll');
        const expand = document.getElementById('fv-expand-tools');
        const collapse = document.getElementById('fv-collapse-tools');
        const expandDocument = document.getElementById('fv-expand-document');
        if (!container || !workbench || !documentPane || !scroller || !expand) return;
        const tabs = Array.from(workbench.querySelectorAll('[data-review-tab]'));
        const panels = Array.from(workbench.querySelectorAll('[data-review-panel]'));
        const mobile = matchMedia('(max-width:1100px)');
        const positions = Object.create(null);
        const valid = new Set(panels.map(panel => panel.dataset.reviewPanel));
        let current = 'task', documentOnly = false, layout = 'split', mobilePanel = 'document';
        try {
            const saved = sessionStorage.getItem('schoolnet.review.panel');
            if (valid.has(saved)) current = saved;
            const savedLayout = sessionStorage.getItem('schoolnet.review.layout');
            if (['split','tools','document'].includes(savedLayout)) layout = savedLayout;
            else if (sessionStorage.getItem('schoolnet.review.expanded') === 'true') layout = 'tools';
            const savedMobile = sessionStorage.getItem('schoolnet.review.mobilePanel');
            if (savedMobile === 'document' || valid.has(savedMobile)) mobilePanel = savedMobile;
        } catch (error) { /* Storage may be unavailable. */ }
        function render() {
            const showDocumentOnly = mobile.matches ? documentOnly : layout === 'document';
            const showToolsOnly = !mobile.matches && layout === 'tools';
            documentPane.hidden = mobile.matches ? !showDocumentOnly : showToolsOnly;
            container.classList.toggle('fv-tools-expanded', showToolsOnly);
            container.classList.toggle('fv-document-expanded', !mobile.matches && showDocumentOnly);
            container.classList.toggle('fv-mobile-document', mobile.matches && showDocumentOnly);
            scroller.hidden = showDocumentOnly;
            workbench.querySelector('.fv-workbench-heading').hidden = showDocumentOnly;
            panels.forEach(panel => { panel.hidden = panel.dataset.reviewPanel !== current; });
            const selected = showDocumentOnly ? 'document' : current;
            tabs.forEach(tab => {
                const active = tab.dataset.reviewTab === selected;
                tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
            });
            documentPane.setAttribute('role', mobile.matches ? 'tabpanel' : 'region');
            documentPane.setAttribute('aria-labelledby', mobile.matches ? 'fv-tab-document' : 'fv-file-title');
            expand.setAttribute('aria-pressed', String(showToolsOnly));
            expand.textContent = showToolsOnly ? '↔ Робота поруч' : '⤢ Розгорнути';
            expandDocument.setAttribute('aria-pressed', String(mobile.matches ? document.fullscreenElement === documentPane : showDocumentOnly));
            expandDocument.textContent = mobile.matches ? (document.fullscreenElement === documentPane ? '↙ Вийти' : '⛶ На весь екран') : (showDocumentOnly ? '↔ Поруч' : '⤢ Розгорнути');
            expandDocument.hidden = mobile.matches && !documentPane.requestFullscreen;
        }
        function setLayout(next) {
            if (!scroller.hidden) positions[current] = scroller.scrollTop;
            layout = next;
            try { sessionStorage.setItem('schoolnet.review.layout', layout); } catch (error) {}
            render();
            if (!scroller.hidden) scroller.scrollTop = positions[current] || 0;
        }
        function select(name, focusPanel) {
            if (name === 'document') {
                positions[current] = scroller.scrollTop;
                documentOnly = true;
                if (!mobile.matches) setLayout('document');
            } else if (valid.has(name)) {
                if (!scroller.hidden) positions[current] = scroller.scrollTop;
                current = name; documentOnly = false;
                if (layout === 'document') setLayout('split');
                try { sessionStorage.setItem('schoolnet.review.panel', current); } catch (error) {}
            } else return;
            if (mobile.matches) {
                mobilePanel = name;
                try { sessionStorage.setItem('schoolnet.review.mobilePanel', name); } catch (error) {}
            }
            render(); scroller.scrollTop = positions[current] || 0;
            if (focusPanel) (name === 'document' ? documentPane : panels.find(panel => panel.dataset.reviewPanel === current)).focus({preventScroll:true});
        }
        tabs.forEach(tab => {
            tab.addEventListener('click', () => select(tab.dataset.reviewTab));
            tab.addEventListener('keydown', event => {
                if (event.altKey || event.ctrlKey || event.metaKey) return;
                const visible = tabs.filter(item => getComputedStyle(item).display !== 'none');
                const index = visible.indexOf(tab);
                let next;
                if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % visible.length;
                else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index + visible.length - 1) % visible.length;
                else if (event.key === 'Home') next = 0;
                else if (event.key === 'End') next = visible.length - 1;
                else return;
                event.preventDefault(); select(visible[next].dataset.reviewTab); visible[next].focus();
            });
        });
        expand.addEventListener('click', () => setLayout(layout === 'tools' ? 'split' : 'tools'));
        collapse.addEventListener('click', () => { positions[current] = scroller.scrollTop; setLayout('document'); expandDocument.focus({preventScroll:true}); });
        expandDocument.addEventListener('click', () => {
            if (mobile.matches) {
                if (document.fullscreenElement === documentPane) document.exitFullscreen();
                else documentPane.requestFullscreen().catch(() => { expandDocument.textContent = 'Повний екран недоступний'; });
            } else setLayout(layout === 'document' ? 'split' : 'document');
        });
        mobile.addEventListener('change', () => { documentOnly = mobilePanel === 'document'; render(); });
        document.addEventListener('fullscreenchange', render);
        window.selectReviewPanel = select;
        window.updateReviewCommentCount = function () {
            const count = document.querySelectorAll('#fv-comments-container .fv-comment-card').length;
            const badge = document.getElementById('fv-comment-count');
            badge.textContent = count; badge.hidden = !count;
            document.getElementById('fv-tab-comments').setAttribute('aria-label', 'Відгук' + (count ? '. Відгуків: ' + count : '. Відгуків ще немає'));
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
            badge.title = data.ai_generated_details || (data.ai_generated_detected ? 'ШІ-перевірка виявила ознаки генерації. Подробиці у рекомендаціях.' : 'Виразних ознак ШІ не знайдено. Це не підтверджує самостійність роботи.');
        };
        window.updateReviewCommentCount();
        container.classList.add('fv-workspace-ready');
        documentOnly = mobile.matches && mobilePanel === 'document'; render();
    });
}());
