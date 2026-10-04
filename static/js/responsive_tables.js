/* Keep UI tables usable without a horizontal scroll; document previews keep their own layout. */
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
            if (!table) return;
            const heads = Array.from(table.tHead ? table.tHead.rows[0].cells : []);
            const width = region.getBoundingClientRect().width;
            if (!width) return;
            const matrix = table.classList.contains('journal-table') || table.classList.contains('printable-schedule-table');
            const needed = matrix ? 160 + Math.max(0, heads.length - 1) * 90 : 0;
            const record = width < 680 || (matrix && width < needed);
            table.classList.toggle('is-record-layout', record);
            region.classList.toggle('is-record-region', record);
        }
        function enhance() {
            page.querySelectorAll('table').forEach(table => {
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
                Array.from(table.tBodies).forEach(body => Array.from(body.rows).forEach(row => {
                    let index = 0;
                    Array.from(row.cells).forEach(cell => {
                        cell.dataset.columnLabel = cell.colSpan > 1 ? '' : labels[index] || '';
                        cell.classList.toggle('platform-full-row', cell.colSpan > 1 || (index === 0 && table.classList.contains('journal-table')));
                        index += cell.colSpan;
                    });
                }));
                update(region);
            });
        }
        enhance();
        let queued = false;
        new MutationObserver(() => {
            if (queued) return;
            queued = true;
            requestAnimationFrame(() => {queued = false; enhance();});
        }).observe(page, {childList: true, subtree: true});
        if (!observer) window.addEventListener('resize', enhance);
    });
})();
