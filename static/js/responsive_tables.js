/* Record tables may become cards; schedules and journals retain their matrix. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        const page = document.querySelector('.page-wrapper');
        if (!page) return;
        page.querySelectorAll('.feed-layout').forEach(layout => {
            if (layout.querySelector('.teacher-work-sidebar, .settings-tools-sidebar')) layout.classList.add('has-work-sidebar');
        });
        const regions = new WeakMap();
        const observer = window.ResizeObserver ? new ResizeObserver(entries => entries.forEach(entry => update(entry.target))) : null;
        function update(region) {
            const table = regions.get(region);
            if (!table || !table.isConnected) return;
            const width = region.getBoundingClientRect().width;
            if (!width) return;
            const record = width < 680;
            table.classList.toggle('is-record-layout', record);
            region.classList.toggle('is-record-region', record);
        }
        function enhance(tables) {
            tables.forEach(table => {
                if (!table.isConnected || !page.contains(table)) return;
                if (table.matches('[data-table-layout="matrix"],.journal-table,.printable-schedule-table')) return;
                if (!table.tHead || !table.tHead.rows.length || table.closest('.assignment-full-description,.rich-text-content,.fv-viewer-body,pre')) return;
                let region = table.parentElement;
                if (!table.dataset.platformTableReady) {
                    // Preserve forms and existing nodes; no cloning or moving controls.
                    table.dataset.platformTableReady = 'true';
                    table.classList.add('platform-record-table');
                    region.classList.add('platform-table-region');
                    regions.set(region, table);
                    if (observer) observer.observe(region);
                }
                const labels = Array.from(table.tHead.rows[0].cells).flatMap(cell => Array(cell.colSpan).fill((cell.title || cell.textContent).replace(/\s+/g, ' ').trim()));
                const compact = table.dataset.recordCompact === 'true';
                if (compact) table.classList.add('record-compact');
                Array.from(table.tBodies).forEach(body => Array.from(body.rows).forEach(row => {
                    let index = 0;
                    Array.from(row.cells).forEach(cell => {
                        if (cell.classList.contains('record-toggle-cell')) return;
                        cell.dataset.columnLabel = cell.colSpan > 1 ? '' : labels[index] || '';
                        cell.classList.toggle('platform-full-row', cell.colSpan > 1 || (index === 0 && table.classList.contains('journal-table')));
                        index += cell.colSpan;
                    });
                    if (compact) addRecordToggle(row);
                }));
                update(region);
            });
        }
        // Secondary columns stay behind a "Деталі" toggle on narrow screens.
        function addRecordToggle(row) {
            if (row.querySelector('.record-toggle-cell')) return;
            if (!row.querySelector('td[data-record~="secondary"]')) return;
            const cell = document.createElement('td');
            cell.className = 'record-toggle-cell platform-full-row';
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'record-toggle';
            button.setAttribute('aria-expanded', 'false');
            button.innerHTML = 'Деталі <span aria-hidden="true">▾</span>';
            button.addEventListener('click', () => {
                const open = row.classList.toggle('is-record-expanded');
                button.setAttribute('aria-expanded', String(open));
                button.innerHTML = open
                    ? 'Згорнути <span aria-hidden="true">▴</span>'
                    : 'Деталі <span aria-hidden="true">▾</span>';
            });
            cell.appendChild(button);
            row.appendChild(cell);
        }
        enhance(page.querySelectorAll('table'));
        const dirtyTables = new Set();
        let queued = false;
        new MutationObserver(mutations => {
            mutations.forEach(mutation => {
                const target = mutation.target;
                // Status text, timers and previews outside tables require no table work.
                if (target.matches('table,thead,tbody,tfoot,tr') || target.closest('thead')) {
                    const table = target.closest('table');
                    if (table) dirtyTables.add(table);
                }
                mutation.addedNodes.forEach(node => {
                    if (node.nodeType !== 1) return;
                    if (node.matches('table')) dirtyTables.add(node);
                    node.querySelectorAll('table').forEach(table => dirtyTables.add(table));
                });
            });
            if (queued || !dirtyTables.size) return;
            queued = true;
            requestAnimationFrame(() => {
                queued = false;
                enhance(dirtyTables);
                dirtyTables.clear();
            });
        }).observe(page, {childList: true, subtree: true});
        if (!observer) window.addEventListener('resize', () => enhance(page.querySelectorAll('table')));
    });
})();
