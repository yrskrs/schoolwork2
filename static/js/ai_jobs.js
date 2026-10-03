/* Keep AI requests short while the durable worker performs the evaluation. */
(function () {
    'use strict';
    async function fetchResult(url, options) {
        let response = await fetch(url, options);
        while (response.status === 202) {
            const job = await response.json();
            if (!job.status_url) throw new Error('Не вдалося отримати статус перевірки.');
            const statusUrl = new URL(job.status_url, window.location.origin);
            if (statusUrl.origin !== window.location.origin) throw new Error('Некоректна адреса статусу.');
            await new Promise(resolve => setTimeout(resolve, document.hidden ? 5000 : 1500));
            response = await fetch(statusUrl.href, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
        }
        return response;
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
                const response = await fetchResult(element.dataset.aiJobStatus);
                const result = await response.json();
                if (result.ok || result.status === 'success' || result.status === 'ok') {
                    window.location.reload();
                } else {
                    element.textContent = result.error || 'Перевірку перервано. Можна спробувати ще раз.';
                }
            } catch (error) {
                element.textContent = 'З’єднання перервано. Перевірка триває; оновіть сторінку для перегляду статусу.';
            }
        });
    });
})();
