window.__currentAiUndAssignmentId = null;

function openAiUnderstandingModal(assignmentId) {
    window.__currentAiUndAssignmentId = assignmentId;
    const modal = document.getElementById('ai-understanding-modal');
    if (!modal) return;

    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';

    // Слухач Esc
    window.addEventListener('keydown', handleAiUndEsc);

    // Завантажуємо аналіз
    fetchAiUnderstanding(assignmentId, false);
}

function closeAiUnderstandingModal() {
    const modal = document.getElementById('ai-understanding-modal');
    if (modal) modal.style.display = 'none';
    document.body.style.overflow = '';
    window.removeEventListener('keydown', handleAiUndEsc);
}

function handleAiUndEsc(e) {
    if (e.key === 'Escape') closeAiUnderstandingModal();
}

// Закриття по кліку на бекдроп
document.getElementById('ai-understanding-modal')?.addEventListener('click', function(e) {
    if (e.target === this) closeAiUnderstandingModal();
});

function getAiUndCsrfToken() {
    const inputToken = document.querySelector('#ai-understanding-modal [name=csrfmiddlewaretoken]')?.value
        || document.querySelector('[name=csrfmiddlewaretoken]')?.value;
    if (inputToken) return inputToken;
    if (typeof CSRF_TOKEN !== 'undefined' && CSRF_TOKEN) return CSRF_TOKEN;
    const match = document.cookie.match(/(?:^|;\s*)schoolwork2_csrftoken=([^;]+)/);
    if (match) return decodeURIComponent(match[1]);
    return '';
}

function fetchAiUnderstanding(assignmentId, forceRefresh) {
    if (!assignmentId) return;

    const loadingEl = document.getElementById('ai-und-loading');
    const contentEl = document.getElementById('ai-und-content');
    const errorEl = document.getElementById('ai-und-error');
    const refreshBtn = document.getElementById('btn-ai-und-refresh');

    if (loadingEl) loadingEl.style.display = 'block';
    if (contentEl) contentEl.style.display = 'none';
    if (errorEl) errorEl.style.display = 'none';
    if (refreshBtn) refreshBtn.disabled = true;

    try {
        const token = getAiUndCsrfToken();
        const url = `/assignment/${assignmentId}/ai-understanding/${forceRefresh ? '?refresh=1' : ''}`;
        const headers = {
            'X-Requested-With': 'XMLHttpRequest'
        };
        if (token) {
            headers['X-CSRFToken'] = token;
        }

        const opts = forceRefresh ? {
            method: 'POST',
            headers: headers
        } : {
            method: 'GET',
            headers: headers
        };

        SchoolNetAI.fetch(url, opts)
            .then(async res => {
                let data = null;
                try {
                    data = await res.json();
                } catch (parseErr) {
                    throw new Error(res.ok ? 'Некоректний формат відповіді сервера' : ('Помилка сервера HTTP ' + res.status));
                }
                if (!res.ok) {
                    throw new Error(data && data.message ? data.message : ('Помилка сервера HTTP ' + res.status));
                }
                return data;
            })
            .then(res => {
                if (loadingEl) loadingEl.style.display = 'none';
                if (refreshBtn) refreshBtn.disabled = false;

                if (res.status === 'success' && res.data) {
                    renderAiUnderstandingData(res.data, res.updated_at, res.cached, res.is_teacher);
                    if (contentEl) contentEl.style.display = 'flex';
                } else {
                    showAiUnderstandingError(res.message || 'Не вдалося завантажити аналіз завдання.');
                }
            })
            .catch(err => {
                if (loadingEl) loadingEl.style.display = 'none';
                if (refreshBtn) refreshBtn.disabled = false;
                showAiUnderstandingError('Помилка аналізу ШІ: ' + err.message);
            });
    } catch (err) {
        if (loadingEl) loadingEl.style.display = 'none';
        if (refreshBtn) refreshBtn.disabled = false;
        showAiUnderstandingError('Помилка: ' + err.message);
    }
}

function stripHtmlTags(text) {
    if (!text) return '';
    if (typeof text !== 'string') text = String(text);
    var s = text.replace(/<(?:br\s*\/|\/p|\/div|\/li|\/tr|\/h[1-6])>/gi, '\n');
    s = s.replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '');
    s = s.replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '');
    s = s.replace(/<[^>]+>/g, '');
    var doc = new DOMParser().parseFromString(s, 'text/html');
    return (doc.body.textContent || '').replace(/[ \t]+/g, ' ').replace(/\n\s*\n\s*\n+/g, '\n\n').trim();
}

function renderStudentExplanationHtml(rawText) {
    if (!rawText) return '';
    var cleanText = stripHtmlTags(rawText);

    // Розбиваємо кроки на окремі рядки, якщо вони йдуть поспіль в одному абзаці (наприклад "Крок 1. ... Крок 2. ...")
    cleanText = cleanText.replace(/([.!?;\n]|\A)\s*(?=(?:\*{0,2}(?:Крок|Етап)\s*\d+|\d+\s*[\.\)]\s*(?:\*{0,2}[A-ZА-ЯІЇЄ]|\*{2})))/gi, '$1\n');

    var rawLines = cleanText.split('\n');
    var items = [];

    rawLines.forEach(function(line) {
        line = line.trim();
        if (!line) return;
        // Прибираємо заголовкові решітки markdown
        line = line.replace(/^#{1,6}\s*/, '');

        // 1. Step matcher: "Крок 1: ...", "**Крок 1:** ...", "1. ...", "* **Крок 1**"
        var stepMatch = line.match(/^(?:(?:\*{0,2}[•\-\*]?\s*)?(?:\*{0,2}(Крок\s*\d+|Етап\s*\d+)\*{0,2}|(?:\*{0,2}(\d+)\s*[\.\)]\*{0,2})))\s*[\:\–\—\-\.\*]*\s*(.*)$/i);
        if (stepMatch) {
            var stepLabel = (stepMatch[1] || ('Крок ' + stepMatch[2])).replace(/[\.\)\*]+$/, '').trim();
            var stepBody = (stepMatch[3] || '').replace(/^\*+/, '').trim();
            items.push({ type: 'step', label: stepLabel, body: stepBody });
            return;
        }

        // 2. Section Headers
        var isSectionHeader = false;
        var headerIcon = '';
        var headerColor = '#059669';
        var cleanHeaderLine = line.replace(/^\*+|\*+$/g, '').trim();

        if (/^(?:[📌🎯📋✅🎒]\s*)?коротко\s*[:\–\—\-]/i.test(cleanHeaderLine)) {
            isSectionHeader = true; headerIcon = '📌'; headerColor = '#4f46e5';
        } else if (/^(?:[📌🎯📋✅🎒]\s*)?покроковий план/i.test(cleanHeaderLine)) {
            isSectionHeader = true; headerIcon = '📋'; headerColor = '#059669';
        } else if (/^(?:[📌🎯📋✅🎒]\s*)?що має бути в результаті/i.test(cleanHeaderLine)) {
            isSectionHeader = true; headerIcon = '🎯'; headerColor = '#2563eb';
        } else if (/^(?:[📌🎯📋✅🎒]\s*)?перед здачею перевір/i.test(cleanHeaderLine)) {
            isSectionHeader = true; headerIcon = '✅'; headerColor = '#d97706';
        } else if (/^🎒\s*як (?:підготувати|виконати)/i.test(cleanHeaderLine)) {
            isSectionHeader = true; headerIcon = '🎒'; headerColor = '#059669';
        }

        if (isSectionHeader) {
            items.push({ type: 'header', icon: headerIcon, color: headerColor, text: cleanHeaderLine.replace(/^[📌🎯📋✅🎒]\s*/, '') });
            return;
        }

        // 3. Bullet or checklist item: starts with •, -, *, [ ], [x], ✔, ✓
        var bulletMatch = line.match(/^(?:[•\-\*]|\[[\s x]\]|[✔✓])\s*(.*)$/i);
        if (bulletMatch) {
            var itemBody = bulletMatch[1].replace(/^\*+|\*+$/g, '').trim();
            items.push({ type: 'bullet', body: itemBody });
            return;
        }

        // 4. Regular line
        items.push({ type: 'text', body: line });
    });

    function formatInlineMarkdown(text) {
        if (!text) return '';
        var escaped = escapeHtml(text);
        // Форматування **жирного тексту** у виразний <strong>
        return escaped.replace(/\*\*([^*]+)\*\*/g, '<strong style="color:var(--color-text-primary);font-weight:750;">$1</strong>');
    }

    var html = '';
    var inStepList = false;
    var inBulletList = false;

    function closeLists() {
        if (inStepList) {
            html += '</ol>';
            inStepList = false;
        }
        if (inBulletList) {
            html += '</ul>';
            inBulletList = false;
        }
    }

    items.forEach(function(item) {
        if (item.type === 'step') {
            if (inBulletList) { html += '</ul>'; inBulletList = false; }
            if (!inStepList) {
                html += '<ol class="ai-steps-list" style="list-style:none;padding:0;margin:10px 0;display:flex;flex-direction:column;gap:9px;">';
                inStepList = true;
            }
            html += '<li class="ai-step-item" style="display:flex;gap:11px;align-items:flex-start;background:var(--color-surface);border:1.5px solid rgba(16,185,129,0.3);border-radius:9px;padding:10px 13px;box-shadow:0 1px 3px rgba(0,0,0,0.03);">';
            html += '<span class="ai-step-badge" style="background:linear-gradient(135deg,#059669,#10b981);color:#fff;font-size:11.5px;font-weight:800;padding:2.5px 9px;border-radius:7px;white-space:nowrap;margin-top:1px;letter-spacing:0.02em;box-shadow:0 1px 3px rgba(5,150,105,0.25);">' + escapeHtml(item.label) + '</span>';
            html += '<span class="ai-step-text" style="font-size:13.5px;line-height:1.6;color:var(--color-text-primary);flex:1;">' + formatInlineMarkdown(item.body) + '</span>';
            html += '</li>';
            return;
        }

        if (item.type === 'bullet') {
            if (inStepList) { html += '</ol>'; inStepList = false; }
            if (!inBulletList) {
                html += '<ul class="ai-bullets-list" style="list-style:none;padding:0;margin:8px 0;display:flex;flex-direction:column;gap:6px;">';
                inBulletList = true;
            }
            html += '<li style="display:flex;gap:9px;align-items:flex-start;font-size:13px;line-height:1.55;color:var(--color-text-primary);">';
            html += '<span style="color:var(--color-success);font-weight:bold;margin-top:1px;">✔</span>';
            html += '<span style="flex:1;">' + formatInlineMarkdown(item.body) + '</span>';
            html += '</li>';
            return;
        }

        closeLists();

        if (item.type === 'header') {
            html += '<div style="margin-top:14px;margin-bottom:6px;font-weight:800;font-size:13px;color:' + item.color + ';display:flex;align-items:center;gap:6px;letter-spacing:0.02em;">';
            html += '<span>' + item.icon + '</span>';
            html += '<span>' + escapeHtml(item.text) + '</span>';
            html += '</div>';
            return;
        }

        if (item.type === 'text') {
            html += '<div style="font-size:13px;line-height:1.6;color:var(--color-text-primary);margin-bottom:6px;font-weight:500;">' + formatInlineMarkdown(item.body) + '</div>';
        }
    });

    closeLists();
    return html || ('<div style="font-size:13px;line-height:1.6;color:var(--color-text-primary);">' + escapeHtml(cleanText) + '</div>');
}

function renderAiUnderstandingData(data, updatedAt, isCached, isTeacher) {
    // Дата та статус
    const badge = document.getElementById('ai-und-status-badge');
    const updatedEl = document.getElementById('ai-und-updated-at');
    const refreshBtn = document.getElementById('btn-ai-und-refresh');
    const tipsCard = document.getElementById('ai-und-tips-card');
    const modalTitle = document.querySelector('#ai-understanding-modal h3');
    const studentGuideTitle = document.getElementById('ai-und-student-guide-title');

    if (isTeacher === false) {
        if (refreshBtn) refreshBtn.style.display = 'none';
        if (tipsCard) tipsCard.style.display = 'none';
        if (modalTitle) modalTitle.textContent = 'Як ШІ розуміє це завдання (підказка до виконання)';
        if (studentGuideTitle) studentGuideTitle.textContent = '🎒 Покрокове пояснення для тебе (відповідно до твого класу)';
    } else {
        if (refreshBtn) refreshBtn.style.display = 'inline-flex';
        if (modalTitle) modalTitle.textContent = 'Як ШІ розуміє це завдання';
        if (studentGuideTitle) studentGuideTitle.textContent = '💡 Пояснення для учнів відповідно до віку (покроково)';
    }

    if (updatedAt) {
        updatedEl.textContent = `Аналіз ШІ: ${updatedAt}${isCached ? ' (зі збереженої копії)' : ' (щойно сформовано)'}`;
    }

    // 0. Пояснення для учнів за віком та класом
    const studentGuideCard = document.getElementById('ai-und-student-guide-card');
    const studentExplEl = document.getElementById('ai-und-student-explanation');
    const targetAudEl = document.getElementById('ai-und-target-audience-badge');

    if (data.student_explanation) {
        studentExplEl.innerHTML = renderStudentExplanationHtml(data.student_explanation);
        if (data.analysis_mode === 'local') {
            const note = document.createElement('p');
            note.className = 'form-hint';
            note.textContent = 'Попереднє пояснення: ШІ зараз недоступний. Перевір умову в матеріалах або уточни її у вчителя.';
            studentExplEl.prepend(note);
        }
        if (data.target_audience) {
            targetAudEl.textContent = stripHtmlTags(data.target_audience);
            targetAudEl.style.display = 'inline-block';
        } else {
            targetAudEl.style.display = 'none';
        }
        studentGuideCard.style.display = 'block';
    } else {
        studentGuideCard.style.display = 'none';
    }

    // 1. Тема та мета
    const topicGoalEl = document.getElementById('ai-und-topic-goal');
    topicGoalEl.textContent = stripHtmlTags(data.topic_and_goal || 'Навчальне практичне завдання');

    // Відображення типу завдання та очікуваного результату (Deliverable)
    const delivWrap = document.getElementById('ai-und-deliverable-wrap');
    const typeBadge = document.getElementById('ai-und-task-type-badge');
    const delivDesc = document.getElementById('ai-und-deliverable-desc');
    if (delivWrap && (data.task_type || data.deliverable)) {
        delivWrap.style.display = 'flex';
        if (typeBadge) {
            var typeLabels = {
                'bulletin': '📰 Бюлетень',
                'presentation': '📊 Презентація',
                'programming': '💻 Програмування',
                'code': '💻 Код програми',
                'scratch': '🐱 Проєкт Scratch',
                'spreadsheet': '📈 Електронна таблиця',
                'table': '📈 Таблиця',
                'diagram': '📊 Діаграма / Схема',
                'database': '🗄️ База даних',
                'creative': '🎨 Творче завдання',
                'project': '🚀 Проєкт',
                'research': '🔍 Дослідження',
                'document': '📄 Текстовий документ',
                'calculation': '🔢 Обчислення',
                'practical': '⚙️ Практична робота',
                'question_answer': '❓ Відповіді на запитання',
                'other': '📌 Практичне завдання'
            };
            typeBadge.textContent = typeLabels[data.task_type] || ('📌 ' + (data.task_type || 'Завдання'));
        }
        if (delivDesc) {
            var d = data.deliverable || {};
            var descText = typeof d === 'string' ? d : (d.description || d.type || '');
            delivDesc.textContent = descText ? ('Очікуваний результат: ' + descText) : '';
        }
    } else if (delivWrap) {
        delivWrap.style.display = 'none';
    }

    const sourceInfoEl = document.getElementById('ai-und-source-info');
    sourceInfoEl.innerHTML = `<span>📍</span> <span>Джерело завдань: <strong>${escapeHtml(stripHtmlTags(data.tasks_source_info || 'Умова та презентація'))}</strong></span>`;

    // 2. Кількість завдань
    const countBadge = document.getElementById('ai-und-task-count-badge');
    const totalCount = data.tasks_total_count || (data.tasks ? data.tasks.length : 1);
    countBadge.textContent = `Виявлено обов'язкових завдань: ${totalCount}`;

    // Перелік завдань
    const tasksListEl = document.getElementById('ai-und-tasks-list');
    tasksListEl.innerHTML = '';
    const tasks = data.tasks || [];
    if (tasks.length > 0) {
        tasks.forEach(t => {
            const card = document.createElement('div');
            card.className = 'ai-task-card';
            var cleanTitle = stripHtmlTags(t.title || '');
            var cleanSource = stripHtmlTags(t.source || '');
            var cleanActions = stripHtmlTags(t.expected_actions || '');
            var cleanSubmission = stripHtmlTags(t.expected_submission || '');

            card.innerHTML = `
                <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">
                    <div style="font-weight:750;font-size:13px;color:var(--color-primary);display:flex;align-items:center;gap:6px;">
                        <span>📌</span>
                        <span>Завдання ${escapeHtml(String(t.num || ''))}: ${escapeHtml(cleanTitle)}</span>
                    </div>
                    ${cleanSource ? `<span style="font-size:11px;background:rgba(99,102,241,0.08);color:var(--color-text-secondary);padding:2px 7px;border-radius:4px;border:1px solid var(--color-border);">${escapeHtml(cleanSource)}</span>` : ''}
                </div>
                <div style="font-size:12.5px;color:var(--color-text-primary);margin-top:4px;line-height:1.5;">
                    <strong>Що потрібно зробити:</strong> ${escapeHtml(cleanActions)}
                </div>
                ${cleanSubmission ? `
                <div style="font-size:12px;color:var(--color-text-muted);margin-top:3px;line-height:1.45;">
                    <strong>Очікувана відповідь:</strong> ${escapeHtml(cleanSubmission)}
                </div>` : ''}
            `;
            tasksListEl.appendChild(card);
        });
    } else {
        tasksListEl.innerHTML = `<div style="font-size:12.5px;color:var(--color-text-muted);">Завдання визначаються загальною умовою уроку.</div>`;
    }

    // 3. Формат здачі
    const formatEl = document.getElementById('ai-und-submission-format');
    formatEl.textContent = stripHtmlTags(data.submission_format_expected || 'Один документ або файл відповідно до інструкцій.');

    // 4. Шкала оцінювання
    const gb = data.grading_breakdown || {};
    document.getElementById('ai-und-grade-full').textContent = gb.full_completion || `Якісне виконання всіх ${totalCount} завдань у повному обсязі.`;
    document.getElementById('ai-und-grade-partial-2').textContent = gb.partial_two_tasks || 'Зараховуються виконані вимоги за критеріями вчителя.';
    document.getElementById('ai-und-grade-partial-1').textContent = gb.partial_one_task || 'Оцінюються підтверджені результати за критеріями вчителя.';

    // Правила ШІ
    const rulesListEl = document.getElementById('ai-und-rules-list');
    rulesListEl.innerHTML = '';
    const rules = gb.rules || [
        'Кожне практичне завдання вимагає окремої відповіді учня.',
        'Одне речення або фраза не може зараховуватися одночасно за два різні завдання.',
        'Розбаловка вчителя має пріоритет; технічно неперевірене не вважається невиконаним.'
    ];
    rules.forEach(r => {
        const li = document.createElement('li');
        li.textContent = r;
        rulesListEl.appendChild(li);
    });

    // 5. Поради вчителю
    const tipsListEl = document.getElementById('ai-und-tips-list');
    tipsListEl.innerHTML = '';
    const tips = data.teacher_recommendations || [];
    if (isTeacher !== false && tips.length > 0) {
        document.getElementById('ai-und-tips-card').style.display = 'block';
        tips.forEach(tp => {
            const li = document.createElement('li');
            li.textContent = tp;
            tipsListEl.appendChild(li);
        });
    } else {
        document.getElementById('ai-und-tips-card').style.display = 'none';
    }
}

function showAiUnderstandingError(msg) {
    const errorEl = document.getElementById('ai-und-error');
    const errText = document.getElementById('ai-und-error-text');
    errText.textContent = msg;
    errorEl.style.display = 'block';
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
