/* Verified server-side files: only opaque import IDs are submitted with the form. */
(() => {
    'use strict';
    function init() {
        const field = document.getElementById('id_cloud_imports');
        const list = document.getElementById('cloud-imported-materials');
        if (!field || !list) return;
        // History entries identify drafts. A fresh navigation to /create/ must
        // never inherit files from another draft at that same URL.
        const owner = document.getElementById('cloud-picker-modal')?.dataset.owner || '';
        const navigation = performance.getEntriesByType('navigation')[0]?.type;
        const previous = history.state?.schoolnetCloudDraft;
        const draft = ['reload', 'back_forward'].includes(navigation) && previous?.path === location.pathname && previous?.owner === owner
            ? previous : {id: crypto.randomUUID?.() || Date.now().toString(36) + Math.random().toString(36).slice(2), path: location.pathname, owner};
        try { history.replaceState({...history.state, schoolnetCloudDraft: draft}, ''); } catch (_) {}
        const storageKey = 'schoolnet-cloud-draft:' + owner + ':' + draft.id;
        let state = {ready: [], pending: []};
        try {
            const stored = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
            if (stored && stored.expires > Date.now() && Array.isArray(stored.ready) && Array.isArray(stored.pending)) state = stored;
            // Retire the old shared-page key without restoring its attachments.
            sessionStorage.removeItem('schoolnet-cloud-batch:' + owner + ':' + location.pathname);
        } catch (_) {}
        window.cloudFormState = {
            get: key => state[key] || [],
            set: (key, ids) => {
                state[key] = ids;
                try { sessionStorage.setItem(storageKey, JSON.stringify({...state, expires: Date.now() + 86400000})); } catch (_) {}
            },
        };
        const imports = new Map();
        function render() {
            field.value = JSON.stringify([...imports.keys()]);
            window.cloudFormState.set('ready', [...imports.keys()]);
            const status = document.getElementById('cloud-materials-status');
            if (status) {
                status.hidden = !imports.size;
                status.textContent = '✓ Додано та перевірено файлів: ' + imports.size + '. Збережіть завдання, щоб прикріпити їх.';
            }
            list.replaceChildren();
            for (const item of imports.values()) {
                const row = document.createElement('div');
                row.className = 'cloud-imported-material';
                row.style.cssText = 'display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:10px;margin:5px 0;border:1px solid var(--color-success);border-radius:6px;';
                const label = document.createElement('span');
                label.textContent = item.name;
                label.style.cssText = 'font-weight:700;overflow-wrap:anywhere;flex:1;min-width:140px;';
                const receipt = document.createElement('small');
                const size = new Intl.NumberFormat('uk-UA').format(item.size);
                receipt.textContent = size + ' Б · ✓ Цілісність перевірено' + (item.exported ? ' · експорт Google Документа' : '');
                receipt.title = item.source_verified ? 'Розмір і контрольна сума звірені з диском; збережена копія перевіряється повторно.' : 'Завантаження і формат перевірені; збережена копія звіряється перед прикріпленням.';
                const ai = document.createElement('label');
                const radio = document.createElement('input');
                radio.type = 'radio'; radio.name = 'ai_task_file';
                radio.className = 'ai-task-radio'; radio.value = 'cloud:' + item.id;
                radio.addEventListener('change', () => window.setAiTaskFileSelection?.(radio.value));
                ai.append(radio, document.createTextNode(' Умова для ШІ'));
                const remove = document.createElement('button');
                remove.type = 'button'; remove.className = 'btn btn-secondary btn-sm';
                remove.textContent = 'Видалити';
                remove.addEventListener('click', () => {
                    if (radio.checked) window.setAiTaskFileSelection?.('');
                    imports.delete(item.id); render();
                });
                row.append(label, receipt, ai, remove); list.append(row);
            }
        }
        window.addCloudImportsToStore = items => {
            const checked = document.querySelector('.ai-task-radio:checked')?.value;
            for (const item of items) if (item.status === 'ready' && /^[a-f0-9]{64}$/.test(item.sha256)) imports.set(item.id, item);
            render();
            if (checked) document.querySelectorAll('.ai-task-radio').forEach(input => { input.checked = input.value === checked; });
        };
        // Preserve reload IDs until the picker verifies them with the server.
        const restoreIds = [...window.cloudFormState.get('ready')];
        try {
            const initial = JSON.parse(document.getElementById('cloud-materials-initial')?.textContent || '[]');
            if (initial.length) window.addCloudImportsToStore(initial);
            else { field.value = '[]'; window.cloudFormRestoreIds = restoreIds; }
        } catch (_) { field.value = '[]'; }
        document.getElementById('assignment-form')?.addEventListener('submit', event => {
            if (window.cloudImportBusy) {
                event.preventDefault(); event.stopImmediatePropagation();
                window.openCloudPickerModal?.();
            }
        }, true);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
