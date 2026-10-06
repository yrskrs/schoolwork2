/* Keep AI requests short while the durable worker performs the evaluation. */
(function () {
    'use strict';
    const panels = new WeakMap();

    function getFriendlyStudentMessage(event, defaultMsg) {
        if (!event) return defaultMsg;
        const msg = (event.message || '').toLowerCase();
        const kind = event.kind;
        if (kind === 'model_start' || msg.includes('очікую відповідь')) {
            return '🧐 Розумний робот уважно читає завдання рядок за рядком…';
        }
        if (kind === 'context_prepared' || msg.includes('підготовлено')) {
            return '📖 Розгортаю зошит та вивчаю виконані вправи…';
        }
        if (kind === 'switching' || msg.includes('перемикаюсь')) {
            return '🔍 Перевіряю ще уважніше іншим розумним способом…';
        }
        if (kind === 'model_success' || msg.includes('відповідь отримано')) {
            return '🎉 Майже все! Записую оцінку та корисну пораду…';
        }
        if (kind === 'model_error') {
            return '🤔 Зачекай ще хвилинку, перевіряю за правилами уроку…';
        }
        return defaultMsg || '🤖 Розумний помічник старанно перевіряє твою роботу…';
    }

    function makePanel(anchor, existing, isStudent) {
        if (!anchor && !existing) return null;
        const root = existing || panels.get(anchor) || document.createElement('section');
        if (!existing && !root.parentNode) {
            anchor.before(root);
            panels.set(anchor, root);
        }
        root.className = isStudent ? 'ai-event-panel ai-kid-panel' : 'ai-event-panel';
        root.replaceChildren();

        if (isStudent) {
            const animBox = document.createElement('div');
            animBox.className = 'ai-kid-robot-anim';
            animBox.innerHTML = '<svg class="ai-kid-robot-svg" viewBox="0 0 160 140" width="128" height="112" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
                '<line x1="80" y1="36" x2="80" y2="18" stroke="#4f46e5" stroke-width="4" stroke-linecap="round"/>' +
                '<circle cx="80" cy="14" r="7" class="ai-robot-antenna-bulb" fill="#f59e0b"/>' +
                '<rect x="42" y="36" width="76" height="54" rx="16" fill="#6366f1" stroke="#4338ca" stroke-width="3"/>' +
                '<rect x="50" y="44" width="60" height="38" rx="10" fill="#1e1b4b"/>' +
                '<g class="ai-robot-eyes">' +
                    '<ellipse cx="64" cy="62" rx="6" ry="7" fill="#38bdf8"/>' +
                    '<circle cx="66" cy="60" r="2.5" fill="#ffffff"/>' +
                    '<ellipse cx="96" cy="62" rx="6" ry="7" fill="#38bdf8"/>' +
                    '<circle cx="98" cy="60" r="2.5" fill="#ffffff"/>' +
                '</g>' +
                '<circle cx="56" cy="72" r="3.5" fill="#f43f5e" opacity="0.8"/>' +
                '<circle cx="104" cy="72" r="3.5" fill="#f43f5e" opacity="0.8"/>' +
                '<path class="ai-robot-mouth-smile" d="M 74 72 Q 80 77 86 72" stroke="#38bdf8" stroke-width="2.5" fill="none" stroke-linecap="round"/>' +
                '<path class="ai-robot-mouth-sad" d="M 74 76 Q 80 70 86 76" stroke="#f43f5e" stroke-width="2.5" fill="none" stroke-linecap="round"/>' +
                '<g class="ai-robot-sad-brows">' +
                    '<path d="M 58 53 Q 64 56 69 52" stroke="#818cf8" stroke-width="2" fill="none" stroke-linecap="round"/>' +
                    '<path d="M 91 52 Q 96 56 102 53" stroke="#818cf8" stroke-width="2" fill="none" stroke-linecap="round"/>' +
                '</g>' +
                '<g class="ai-robot-tears">' +
                    '<path class="ai-tear-left" d="M 64 69 C 61 74, 59 78, 64 83 C 68 78, 66 74, 64 69 Z" fill="#38bdf8"/>' +
                    '<circle class="ai-tear-drop-left" cx="64" cy="87" r="2.5" fill="#38bdf8"/>' +
                    '<path class="ai-tear-right" d="M 96 69 C 93 74, 91 78, 96 83 C 100 78, 98 74, 96 69 Z" fill="#38bdf8"/>' +
                    '<circle class="ai-tear-drop-right" cx="96" cy="87" r="2.5" fill="#38bdf8"/>' +
                '</g>' +
                '<rect x="52" y="94" width="56" height="36" rx="10" fill="#4f46e5" stroke="#4338ca" stroke-width="3"/>' +
                '<circle cx="80" cy="106" r="4" fill="#fbbf24"/>' +
                '<circle cx="80" cy="118" r="4" fill="#34d399"/>' +
                '<g class="ai-robot-book">' +
                    '<path d="M 46 116 L 80 124 L 114 116 L 110 134 L 80 138 L 50 134 Z" fill="#ec4899" stroke="#be185d" stroke-width="2"/>' +
                    '<path d="M 48 114 L 80 121 L 112 114 L 108 131 L 80 135 L 52 131 Z" fill="#fdf2f8"/>' +
                    '<line x1="80" y1="121" x2="80" y2="135" stroke="#be185d" stroke-width="1.5"/>' +
                    '<line x1="56" y1="120" x2="74" y2="123" stroke="#94a3b8" stroke-width="1.5" stroke-linecap="round"/>' +
                    '<line x1="56" y1="125" x2="72" y2="127" stroke="#94a3b8" stroke-width="1.5" stroke-linecap="round"/>' +
                    '<line x1="86" y1="123" x2="104" y2="120" stroke="#94a3b8" stroke-width="1.5" stroke-linecap="round"/>' +
                    '<line x1="88" y1="127" x2="104" y2="125" stroke="#94a3b8" stroke-width="1.5" stroke-linecap="round"/>' +
                    '<path d="M 72 88 L 68 112 L 92 112 L 88 88 Z" class="ai-scanner-beam" fill="rgba(56, 189, 248, 0.25)"/>' +
                '</g>' +
                '<circle cx="48" cy="122" r="5" fill="#818cf8"/>' +
                '<circle cx="112" cy="122" r="5" fill="#818cf8"/>' +
            '</svg>';
            root.append(animBox);
        }

        const title = document.createElement('strong');
        title.className = isStudent ? 'ai-kid-title' : '';
        title.textContent = isStudent ? '🤖 Розумний робот читає та перевіряє твою роботу!' : 'Події перевірки ШІ';

        const status = document.createElement('p');
        status.className = isStudent ? 'ai-kid-status' : '';
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        status.textContent = isStudent ? '📖 Розгортаю зошит та шукаю завдання…' : 'Очікую запуску перевірки…';

        const bar = document.createElement('div');
        bar.className = isStudent ? 'ai-kid-progress' : 'ai-event-bar';
        bar.setAttribute('aria-hidden', 'true');
        if (isStudent) {
            const barInner = document.createElement('div');
            barInner.className = 'ai-kid-progress-bar';
            bar.append(barInner);
        }

        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = isStudent ? '⚙️ Деталі перевірки (для допитливих)' : 'Перебіг перевірки';
        if (isStudent) {
            summary.style.fontSize = '11px';
            summary.style.opacity = '0.7';
            summary.style.cursor = 'pointer';
        }
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
                    status.textContent = isStudent ? getFriendlyStudentMessage(event, status.textContent) : event.message;
                    if (!isStudent && event.kind === 'model_error') details.open = true;
                    while (list.children.length > 200) list.firstElementChild.remove();
                });
                if (!lastSequence && job.status === 'queued') {
                    status.textContent = isStudent ? '⏳ Займаю чергу до розумного робота…' : 'Перевірку додано до черги. Очікую вільного працівника…';
                }
                if (!lastSequence && job.status === 'running') {
                    status.textContent = isStudent ? '📖 Читаю написане та готую оцінку…' : 'Читаю матеріали та готую перевірку…';
                }
            },
            finish(job) {
                this.update(job);
                const success = job.ok || job.status === 'success' || job.status === 'ok';
                root.dataset.state = success ? 'success' : 'failed';
                if (!lastSequence) {
                    if (isStudent) {
                        status.textContent = success ? '🎉 Ура! Твою роботу успішно перевірено!' : (job.error || 'Не вдалося перевірити роботу.');
                    } else {
                        status.textContent = success ? 'Перевірку завершено.' : (job.error || 'Перевірку завершено без оцінки.');
                    }
                }
                if (!success && isStudent) {
                    title.textContent = '😢 Розумний робот не зміг завершити перевірку...';
                    if (bar) bar.style.display = 'none';
                }
            },
            disconnected() {
                root.dataset.state = 'disconnected';
                status.textContent = isStudent ? '📡 Ой, з’єднання перервано. Спробуй оновити сторінку.' : 'З’єднання перервано. Перевірка може тривати; оновіть сторінку для перегляду статусу.';
            }
        };
    }
    async function fetchResult(url, options, progressOptions) {
        const isStudent = Boolean(
            (progressOptions && progressOptions.isStudent) ||
            url.includes('/student-ai-check/') ||
            (progressOptions && progressOptions.existing && progressOptions.existing.dataset.student) ||
            (!window.location.pathname.startsWith('/teacher/') && (window.location.pathname.includes('/submission/') || window.location.pathname.includes('/submit/')))
        );
        const panel = makePanel(progressOptions && progressOptions.anchor, progressOptions && progressOptions.existing, isStudent);
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
    function getSadRobotHtml(errorMessage, subId) {
        var msg = errorMessage || 'ШІ не зміг завершити перевірку. Спробуйте ще раз; спробу не використано.';
        var retryCall = subId ? 'runStudentAICheck(' + subId + ')' : 'window.location.reload()';
        return '<div class="ai-kid-panel ai-sad-robot-card" data-state="failed">' +
            '<div class="ai-kid-robot-anim">' +
                '<svg class="ai-kid-robot-svg" viewBox="0 0 160 140" width="128" height="112" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
                    '<line x1="80" y1="36" x2="80" y2="18" stroke="#4f46e5" stroke-width="4" stroke-linecap="round"/>' +
                    '<circle cx="80" cy="14" r="7" class="ai-robot-antenna-bulb" fill="#60a5fa"/>' +
                    '<rect x="42" y="36" width="76" height="54" rx="16" fill="#6366f1" stroke="#4338ca" stroke-width="3"/>' +
                    '<rect x="50" y="44" width="60" height="38" rx="10" fill="#1e1b4b"/>' +
                    '<g class="ai-robot-eyes">' +
                        '<ellipse cx="64" cy="62" rx="6" ry="7" fill="#38bdf8"/>' +
                        '<circle cx="66" cy="60" r="2.5" fill="#ffffff"/>' +
                        '<ellipse cx="96" cy="62" rx="6" ry="7" fill="#38bdf8"/>' +
                        '<circle cx="98" cy="60" r="2.5" fill="#ffffff"/>' +
                    '</g>' +
                    '<circle cx="56" cy="72" r="3.5" fill="#f43f5e" opacity="0.8"/>' +
                    '<circle cx="104" cy="72" r="3.5" fill="#f43f5e" opacity="0.8"/>' +
                    '<path class="ai-robot-mouth-sad" d="M 74 76 Q 80 70 86 76" stroke="#f43f5e" stroke-width="2.5" fill="none" stroke-linecap="round"/>' +
                    '<g class="ai-robot-sad-brows">' +
                        '<path d="M 58 53 Q 64 56 69 52" stroke="#818cf8" stroke-width="2" fill="none" stroke-linecap="round"/>' +
                        '<path d="M 91 52 Q 96 56 102 53" stroke="#818cf8" stroke-width="2" fill="none" stroke-linecap="round"/>' +
                    '</g>' +
                    '<g class="ai-robot-tears">' +
                        '<path class="ai-tear-left" d="M 64 69 C 61 74, 59 78, 64 83 C 68 78, 66 74, 64 69 Z" fill="#38bdf8"/>' +
                        '<circle class="ai-tear-drop-left" cx="64" cy="87" r="2.5" fill="#38bdf8"/>' +
                        '<path class="ai-tear-right" d="M 96 69 C 93 74, 91 78, 96 83 C 100 78, 98 74, 96 69 Z" fill="#38bdf8"/>' +
                        '<circle class="ai-tear-drop-right" cx="96" cy="87" r="2.5" fill="#38bdf8"/>' +
                    '</g>' +
                    '<rect x="52" y="94" width="56" height="36" rx="10" fill="#4f46e5" stroke="#4338ca" stroke-width="3"/>' +
                    '<circle cx="80" cy="106" r="4" fill="#fbbf24"/>' +
                    '<circle cx="80" cy="118" r="4" fill="#34d399"/>' +
                    '<g class="ai-robot-book">' +
                        '<path d="M 46 116 L 80 124 L 114 116 L 110 134 L 80 138 L 50 134 Z" fill="#ec4899" stroke="#be185d" stroke-width="2"/>' +
                        '<path d="M 48 114 L 80 121 L 112 114 L 108 131 L 80 135 L 52 131 Z" fill="#fdf2f8"/>' +
                        '<line x1="80" y1="121" x2="80" y2="135" stroke="#be185d" stroke-width="1.5"/>' +
                        '<line x1="56" y1="120" x2="74" y2="123" stroke="#94a3b8" stroke-width="1.5" stroke-linecap="round"/>' +
                        '<line x1="56" y1="125" x2="72" y2="127" stroke="#94a3b8" stroke-width="1.5" stroke-linecap="round"/>' +
                        '<line x1="86" y1="123" x2="104" y2="120" stroke="#94a3b8" stroke-width="1.5" stroke-linecap="round"/>' +
                        '<line x1="88" y1="127" x2="104" y2="125" stroke="#94a3b8" stroke-width="1.5" stroke-linecap="round"/>' +
                    '</g>' +
                    '<circle cx="48" cy="122" r="5" fill="#818cf8"/>' +
                    '<circle cx="112" cy="122" r="5" fill="#818cf8"/>' +
                '</svg>' +
            '</div>' +
            '<strong class="ai-kid-title" style="color:var(--color-danger);font-size:16px;">' +
                '😢 Ой, вибач... Розумний робот не зміг завершити перевірку' +
            '</strong>' +
            '<div style="font-weight:700;color:var(--color-danger);font-size:13.5px;margin:8px auto;padding:8px 14px;background:rgba(239,68,68,0.08);border-radius:var(--radius-sm);max-width:560px;">' +
                '❌ ' + escapeResult(msg) +
            '</div>' +
            '<p class="ai-kid-status" style="color:var(--color-text-secondary);max-width:520px;margin:6px auto 14px;font-size:12.5px;line-height:1.4;">' +
                'Не хвилюйся: <strong>твою спробу не використано</strong>! Спробуй натиснути кнопку нижче, щоб запустити перевірку знову.' +
            '</p>' +
            '<div style="display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;margin-top:10px;">' +
                '<button type="button" class="btn btn-primary" onclick="' + retryCall + '" style="background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;border:none;padding:10px 22px;border-radius:var(--radius-md);font-weight:800;font-size:13.5px;cursor:pointer;display:inline-flex;align-items:center;gap:8px;box-shadow:0 3px 12px rgba(99,102,241,0.35);">' +
                    '<span>🔄</span> <span>Спробувати ще раз</span>' +
                '</button>' +
            '</div>' +
        '</div>';
    }
    window.SchoolNetAI = {fetch: fetchResult, escapeResult: escapeResult, getSadRobotHtml: getSadRobotHtml};
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
