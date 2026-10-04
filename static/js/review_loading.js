/* Load expensive review content after the controls appear. All requests are read-only. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        const container = document.querySelector('[data-review-async]');
        if (!container) return;
        let controller = new AbortController();
        window.addEventListener('pagehide', () => controller.abort());

        async function get(url) {
            const response = await fetch(url, {credentials: 'same-origin', signal: controller.signal,
                headers: {'Accept': 'application/json'}});
            if (!response.ok || response.redirected) throw new Error('Request failed');
            return response.json();
        }
        function failure(target, retry, message) {
            target.setAttribute('aria-busy', 'false');
            target.replaceChildren();
            const text = document.createElement('p'); text.setAttribute('role', 'status');
            text.textContent = message;
            const button = document.createElement('button'); button.type = 'button';
            button.className = 'btn btn-secondary btn-sm'; button.textContent = 'Спробувати ще раз';
            button.addEventListener('click', retry);
            target.append(text, button);
            if (container.dataset.reviewFallback) {
                const link = document.createElement('a');
                link.href = container.dataset.reviewFallback;
                link.className = 'btn btn-secondary btn-sm'; link.textContent = 'Відкрити повний перегляд';
                target.append(link);
            }
        }
        const preview = container.querySelector('[data-review-preview]');
        const duplicates = container.querySelector('[data-review-duplicates]');
        let previewReady = !preview;
        let duplicatesReady = !duplicates;
        let warmed = false;
        async function warmNext() {
            // Only one document, after the current one; respect constrained clients.
            if (warmed || !previewReady || document.hidden || !container.dataset.nextPreview ||
                navigator.connection?.saveData || navigator.connection?.effectiveType === '2g' ||
                document.documentElement.classList.contains('sn-lite')) return;
            warmed = true;
            try { await get(container.dataset.nextPreview); } catch (error) { /* Normal opening retries. */ }
        }
        async function loadPreview() {
            preview.setAttribute('aria-busy', 'true');
            try {
                const data = await get(preview.dataset.reviewPreview);
                if (typeof data.html !== 'string') throw new Error('Invalid preview');
                preview.innerHTML = data.html;
                preview.setAttribute('aria-busy', 'false'); previewReady = true;
            } catch (error) {
                if (error.name !== 'AbortError') failure(preview, loadPreview,
                    'Не вдалося завантажити перегляд. Можна повторити спробу або завантажити оригінальний файл.');
            }
        }
        async function loadDuplicates() {
            duplicates.setAttribute('aria-busy', 'true');
            try {
                const data = await get(duplicates.dataset.reviewDuplicates);
                if (typeof data.html !== 'string' || typeof data.chip !== 'string' ||
                    typeof data.group_chip !== 'string') throw new Error('Invalid result');
                duplicates.innerHTML = data.html;
                duplicates.setAttribute('aria-busy', 'false');
                duplicatesReady = true;
                document.getElementById('fv-quick-duplicate').outerHTML = data.chip;
                document.getElementById('fv-quick-group').outerHTML = data.group_chip;
            } catch (error) {
                if (error.name === 'AbortError') return;
                const chip = document.getElementById('fv-quick-duplicate');
                chip.textContent = '🔎 Збіг: не перевірено';
                chip.title = 'Перевірка збігів не завершилася. Повторіть спробу у вкладці «Про роботу».';
                failure(duplicates, loadDuplicates, 'Не вдалося перевірити збіги файлів.');
            }
        }
        if (preview) loadPreview();
        if (duplicates) loadDuplicates();
        window.addEventListener('pageshow', event => {
            if (!event.persisted) return;
            controller = new AbortController(); warmed = false;
            if (!previewReady) loadPreview();
            if (!duplicatesReady) loadDuplicates();
        });
        const next = document.getElementById('next-sub-btn');
        if (next) {
            next.addEventListener('pointerenter', warmNext);
            next.addEventListener('focus', warmNext);
        }
    });
}());
