/* Keep AI requests short while the durable worker performs the evaluation. */
(function () {
    'use strict';
    const panels = new WeakMap();
    function makePanel(anchor, existing) {
        if (!anchor && !existing) return null;
        const root = existing || panels.get(anchor) || document.createElement('section');
        if (!existing && !root.parentNode) {
            anchor.before(root);
            panels.set(anchor, root);
        }
        root.className = 'ai-event-panel';
        root.replaceChildren();
        const title = document.createElement('strong');
        title.textContent = 'Події перевірки ШІ';
        const status = document.createElement('p');
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        status.textContent = 'Очікую запуску перевірки…';
        const bar = document.createElement('div');
        bar.className = 'ai-event-bar';
        bar.setAttribute('aria-hidden', 'true');
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = 'Перебіг перевірки';
        const list = document.createElement('ol');
        details.append(summary, list);
        root.append(title, status, bar, details);
        root.dataset.state = 'running';
        let lastSequence = 0;
        return {
            update(job) {
                (job.events || []).forEach(event => {
                    if (event.sequence <= lastSequence) return;
                    const row = document.createElement('li');
                    row.textContent = event.message;
                    row.dataset.kind = event.kind;
                    list.append(row);
                    lastSequence = event.sequence;
                    status.textContent = event.message;
                    if (event.kind === 'model_error') details.open = true;
                    while (list.children.length > 200) list.firstElementChild.remove();
                });
                if (!lastSequence && job.status === 'queued') status.textContent = 'Перевірку додано до черги. Очікую вільного працівника…';
                if (!lastSequence && job.status === 'running') status.textContent = 'Читаю матеріали та готую перевірку…';
            },
            finish(job) {
                this.update(job);
                const success = job.ok || job.status === 'success' || job.status === 'ok';
                root.dataset.state = success ? 'success' : 'failed';
                if (!lastSequence) status.textContent = success ? 'Перевірку завершено.' : (job.error || 'Перевірку завершено без оцінки.');
            },
            disconnected() {
                root.dataset.state = 'disconnected';
                status.textContent = 'З’єднання перервано. Перевірка може тривати; оновіть сторінку для перегляду статусу.';
            }
        };
    }
    async function fetchResult(url, options, progressOptions) {
        const panel = makePanel(progressOptions && progressOptions.anchor, progressOptions && progressOptions.existing);
        try {
            let response = await fetch(url, options);
            while (response.status === 202) {
                const job = await response.json();
                if (panel) panel.update(job);
                if (!job.status_url) throw new Error('Не вдалося отримати статус перевірки.');
                const statusUrl = new URL(job.status_url, window.location.origin);
                if (statusUrl.origin !== window.location.origin) throw new Error('Некоректна адреса статусу.');
                await new Promise(resolve => setTimeout(resolve, document.hidden ? 5000 : 1500));
                response = await fetch(statusUrl.href, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
            }
            if (panel) {
                const result = await response.clone().json();
                panel.finish(result);
            }
            return response;
        } catch (error) {
            if (panel) panel.disconnected();
            throw error;
        }
    }
    function escapeResult(value) {
        if (typeof value === 'string') return value.replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
        if (Array.isArray(value)) return value.map(escapeResult);
        if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, escapeResult(item)]));
        return value;
    }
    window.SchoolNetAI = {fetch: fetchResult, escapeResult: escapeResult};
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-ai-job-status]').forEach(async function (element) {
            try {
                const response = await fetchResult(element.dataset.aiJobStatus, undefined, {existing: element});
                const result = await response.json();
                if (result.ok || result.status === 'success' || result.status === 'ok') {
                    window.location.reload();
                } else {
                    element.dataset.state = 'failed';
                }
            } catch (error) {
                element.dataset.state = 'disconnected';
            }
        });
    });
})();
