/* Submission notices use small JSON polls; hidden/offline pages do no work. */
(() => {
    'use strict';
    const root = document.getElementById('submission-notifications');
    if (!root) return;
    const list = root.querySelector('.submission-notifications-list');
    const clear = root.querySelector('.submission-notifications-clear');
    const teacher = root.dataset.viewer.startsWith('teacher-');
    const classSelect = teacher ? null : document.getElementById('id_class_group');
    const feedClassSelect = teacher ? null : document.getElementById('school-class-select');
    let classId = '';
    let storageKey = '';
    let cursor = null;
    let pending = [];
    let policies = {};
    let timer = null;
    let controller = null;
    let generation = 0;
    let failures = 0;
    let audio = null;
    let stopped = false;
    const visible = new Map();

    const originalTitle = document.title;

    function save() {
        try {
            sessionStorage.setItem(storageKey, JSON.stringify({cursor, pending: pending.filter(item => !item.preview)}));
        } catch (_) { /* Notifications also work with storage disabled. */ }
    }

    function unlockAudio() {
        if (!Object.values(policies).some(policy => policy.sound) &&
            !document.querySelector('#submission-notification-form input[name$="_sound"]:checked')) return;
        try {
            const Audio = window.AudioContext || window.webkitAudioContext;
            if (!Audio) return;
            if (!audio) audio = new Audio();
            if (audio.state === 'suspended') audio.resume().catch(() => {});
        } catch (_) { /* Text remains available if audio is unavailable. */ }
    }
    document.addEventListener('pointerdown', unlockAudio, {passive: true});
    document.addEventListener('keydown', unlockAudio);

    const SOUND_PRESETS = {
        chime: {
            type: 'sine',
            normal: [523, 784],
            repeat: [660, 523, 440],
            step: 0.12,
        },
        marimba: {
            type: 'triangle',
            normal: [440, 554, 659],
            repeat: [659, 554, 440],
            step: 0.10,
        },
        ping: {
            type: 'sine',
            normal: [880, 1318],
            repeat: [1318, 988, 880],
            step: 0.08,
        },
        bell: {
            type: 'sine',
            normal: [587, 880, 1174],
            repeat: [1174, 880, 587],
            step: 0.12,
        },
        subtle: {
            type: 'sine',
            normal: [392, 523],
            repeat: [523, 392],
            step: 0.14,
        },
        fanfare: {
            type: 'triangle',
            normal: [523, 659, 784, 1046],
            repeat: [1046, 784, 659, 523],
            step: 0.09,
        },
    };

    const TEMPLATES = {
        work_submitted: {
            normal: 'Роботу здано',
            repeat: attempt => `Роботу здано повторно · спроба ${attempt}`,
        },
        new_work: {
            normal: 'Нова здана робота',
            repeat: attempt => `Повторна здача роботи · спроба ${attempt}`,
        },
        task_completed: {
            normal: 'Завдання виконано',
            repeat: attempt => `Завдання виконано повторно · спроба ${attempt}`,
        },
        submission_received: {
            normal: 'Отримано відповідь',
            repeat: attempt => `Отримано оновлену відповідь · спроба ${attempt}`,
        },
        submission_success: {
            normal: 'Успішна здача роботи',
            repeat: attempt => `Повторна здача · спроба ${attempt}`,
        },
    };

    function formatStatus(item) {
        const key = item.template || (item.policy && item.policy.template) || 'work_submitted';
        const tpl = TEMPLATES[key] || TEMPLATES.work_submitted;
        return item.resubmission ? tpl.repeat(item.attempt || 2) : tpl.normal;
    }

    function sound(repeat, allowBackground = false, presetKey = null) {
        if (!audio || audio.state !== 'running') return;
        if (document.hidden && !allowBackground) return;
        const config = SOUND_PRESETS[presetKey] || SOUND_PRESETS.chime;
        const notes = repeat ? config.repeat : config.normal;
        const start = audio.currentTime;
        notes.forEach((frequency, index) => {
            const oscillator = audio.createOscillator();
            const gain = audio.createGain();
            const at = start + index * config.step;
            oscillator.type = config.type || 'sine';
            oscillator.frequency.value = frequency;
            gain.gain.setValueAtTime(0, at);
            gain.gain.linearRampToValueAtTime(.08, at + .015);
            gain.gain.exponentialRampToValueAtTime(.001, at + (config.step - .01));
            oscillator.connect(gain);
            gain.connect(audio.destination);
            oscillator.start(at);
            oscillator.stop(at + config.step);
            oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
        });
    }

    function dismiss(id) {
        pending = pending.filter(item => item.id !== id);
        save();
        render();
    }

    function createCard(item) {
        const card = document.createElement('article');
        card.className = 'submission-notice' + (item.resubmission ? ' is-resubmission' : '');
        card.dataset.eventId = item.id;
        const canOpen = (teacher && /^\/teacher\/view-file\/\d+\/$/.test(item.url || '')) ||
                        (!teacher && /^\/assignment\/\d+\/$/.test(item.url || ''));
        const main = document.createElement(canOpen ? 'a' : 'button');
        main.className = 'submission-notice-main';
        if (canOpen) main.href = item.url;
        else main.type = 'button';
        main.addEventListener('click', () => dismiss(item.id));
        const name = document.createElement('span');
        name.className = 'submission-notice-name';
        name.textContent = item.name;
        if (item.show_class && item.class_name) {
            const group = document.createElement('span');
            group.className = 'submission-notice-class';
            group.textContent = item.class_name;
            name.append(group);
        }
        const status = document.createElement('span');
        status.className = 'submission-notice-status';
        status.textContent = formatStatus(item);
        const assignment = document.createElement('span');
        assignment.className = 'submission-notice-assignment';
        assignment.textContent = item.assignment;
        assignment.title = item.assignment;
        main.append(name, status, assignment);
        if (item.authors) {
            const authors = document.createElement('span');
            authors.className = 'submission-notice-assignment';
            authors.textContent = 'Співавтори: ' + item.authors;
            authors.title = authors.textContent;
            main.append(authors);
        }
        const hint = document.createElement('span');
        hint.className = 'submission-notice-hint';
        if (item.preview) {
            hint.textContent = 'Приклад сповіщення';
        } else if (teacher) {
            hint.textContent = 'Відкрити для перевірки →';
        } else if (canOpen) {
            hint.textContent = 'Відкрити завдання →';
        } else {
            hint.textContent = 'Натисніть, щоб закрити';
        }
        main.append(hint);
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'submission-notice-close';
        close.textContent = '×';
        close.setAttribute('aria-label', `Закрити сповіщення: ${item.name}`);
        close.addEventListener('click', () => dismiss(item.id));
        card.append(main, close);
        return card;
    }

    function hasBackgroundPolicy() {
        return Object.values(policies).some(p => p.background_active !== false);
    }

    function render() {
        const now = Date.now();
        pending = pending.filter(item => {
            const policy = item.preview ? item.policy : policies[item.teacher_id];
            const focused = visible.get(item.id)?.card.contains(document.activeElement);
            return policy && (focused || policy.sticky || !item.shownAt || now - item.shownAt < policy.duration * 1000);
        });
        const active = new Set(pending.slice(0, 3).map(item => item.id));
        visible.forEach((entry, id) => {
            clearTimeout(entry.timer);
            if (!active.has(id)) { entry.card.remove(); visible.delete(id); }
        });
        let play = null;
        let playBackground = false;
        let playPreset = null;
        pending.slice(0, 3).forEach(item => {
            const policy = item.preview ? item.policy : policies[item.teacher_id];
            if (!item.shownAt) item.shownAt = now;
            let entry = visible.get(item.id);
            if (!entry) {
                entry = {card: createCard(item)};
                visible.set(item.id, entry);
                list.append(entry.card);
                // One sound per batch; repeated submissions take precedence.
                if (!item.sounded && policy && policy.sound) {
                    play = Boolean(play || item.resubmission);
                    if (policy.background_active !== false) playBackground = true;
                    playPreset = item.sound_preset || (policy && policy.sound_preset) || playPreset;
                }
                item.sounded = true;
            }
            if (policy && !policy.sticky) {
                entry.timer = setTimeout(() => {
                    // Do not remove the control while someone uses the keyboard on it.
                    if (entry.card.contains(document.activeElement)) {
                        entry.card.addEventListener('focusout', () => dismiss(item.id), {once: true});
                    } else dismiss(item.id);
                }, Math.max(1, policy.duration * 1000 - (now - item.shownAt)));
            }
        });
        if (play !== null) sound(play, playBackground, playPreset);
        clear.hidden = pending.length < 2;
        clear.textContent = pending.length > 3 ? `Ще ${pending.length - 3} у черзі · Закрити всі` : 'Закрити всі';
        save();
    }

    clear.addEventListener('click', () => { pending = []; render(); schedule(0); });

    function hasAllClassesPolicy() {
        const list = Object.values(policies);
        return list.length > 0 && list.some(p => p.all_classes);
    }

    function schedule(delay) {
        clearTimeout(timer);
        if (stopped || navigator.onLine === false) return;
        if (!teacher && !classId && Object.keys(policies).length > 0 && !hasAllClassesPolicy()) return;
        if (document.hidden && !hasBackgroundPolicy()) return;
        timer = setTimeout(poll, delay);
    }

    async function poll() {
        if (controller || stopped || navigator.onLine === false) return;
        if (!teacher && !classId && Object.keys(policies).length > 0 && !hasAllClassesPolicy()) return;
        if (document.hidden && !hasBackgroundPolicy()) return;
        const currentGeneration = generation;
        controller = new AbortController();
        const currentController = controller;
        const timeout = setTimeout(() => currentController.abort(), 10000);
        let delay = 5000;
        try {
            const url = new URL(root.dataset.endpoint, location.origin);
            if (!teacher && classId) url.searchParams.set('class', classId);
            if (cursor !== null) url.searchParams.set('since', cursor);
            const response = await fetch(url, {signal: currentController.signal, cache: 'no-store',
                headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
            if (!response.ok) throw new Error('Notification request failed');
            const data = await response.json();
            if (currentGeneration !== generation || stopped) return;
            policies = data.policies || {};
            if (cursor !== null && data.cursor < cursor) pending = []; // Database restore.
            pending = pending.filter(item => item.preview || policies[item.teacher_id]);
            const known = new Set(pending.map(item => item.id));
            let accepted = cursor;
            let full = false;
            let newBatch = 0;
            for (const item of data.events) {
                if (!known.has(item.id)) {
                    // Keep DOM small and retain cursor backpressure for a long sticky queue.
                    if (pending.length >= 100) { full = true; break; }
                    pending.push(item);
                    known.add(item.id);
                    newBatch += 1;
                }
                accepted = item.id;
            }
            cursor = full ? accepted : data.cursor;
            failures = 0;
            delay = data.has_more && !full ? 1000 : (document.hidden ? 8000 : data.poll_after * 1000);
            if (newBatch > 0 && document.hidden) {
                document.title = '🔔 Роботу здано!';
            }
            render();
            if (!teacher && !classId && !hasAllClassesPolicy()) return;
        } catch (_) {
            failures += 1;
            delay = Math.min(60000, 5000 * (2 ** Math.min(failures, 4)));
        } finally {
            clearTimeout(timeout);
            if (controller === currentController) controller = null;
            schedule(currentGeneration === generation ? delay : 0);
        }
    }

    function selectClass() {
        generation += 1;
        if (controller) controller.abort();
        classId = teacher ? '' : String(classSelect ? classSelect.value : feedClassSelect ? feedClassSelect.value : root.dataset.classId || '');
        if (!/^\d+$/.test(classId)) classId = '';
        storageKey = 'schoolnet-submission-notices:' + root.dataset.viewer + (classId ? ':' + classId : '');
        cursor = null;
        pending = [];
        visible.forEach(entry => { clearTimeout(entry.timer); entry.card.remove(); });
        visible.clear();
        clear.hidden = true;
        try {
            const state = JSON.parse(sessionStorage.getItem(storageKey));
            if (state && Number.isSafeInteger(state.cursor) && state.cursor >= 0) {
                cursor = state.cursor;
                pending = Array.isArray(state.pending) ? state.pending.slice(0, 100) : [];
            }
        } catch (_) { /* Fresh baseline when storage is unavailable. */ }
        schedule(0);
    }
    if (classSelect) classSelect.addEventListener('change', selectClass);
    if (!teacher) document.addEventListener('schoolnet:class-change', selectClass);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            document.title = originalTitle;
            schedule(0);
        } else if (!hasBackgroundPolicy()) {
            clearTimeout(timer);
            if (controller) controller.abort();
        }
    });
    window.addEventListener('offline', () => { clearTimeout(timer); if (controller) controller.abort(); });
    window.addEventListener('online', () => schedule(0));
    window.addEventListener('pagehide', () => {
        stopped = true;
        clearTimeout(timer);
        visible.forEach(entry => clearTimeout(entry.timer));
        if (controller) controller.abort();
        save();
    });
    window.addEventListener('pageshow', () => { stopped = false; schedule(0); });

    document.querySelectorAll('[data-submission-preview]').forEach(button => {
        button.addEventListener('click', async () => {
            unlockAudio();
            if (audio && audio.state === 'suspended') await audio.resume().catch(() => {});
            const form = document.getElementById('submission-notification-form');
            const repeat = button.dataset.submissionPreview === 'repeat';
            const policy = {
                sound: form.elements.teacher_sound.checked || form.elements.students_sound.checked,
                duration: Math.max(1, Math.min(60, Number(form.elements.duration_seconds.value) || 5)),
                sticky: form.elements.until_dismissed.checked,
                background_active: form.elements.students_background_active ? form.elements.students_background_active.checked : true,
                show_class: form.elements.show_class_badge ? form.elements.show_class_badge.checked : false,
                click_opens_task: form.elements.students_click_opens_task ? form.elements.students_click_opens_task.checked : true,
                template: form.elements.text_template ? form.elements.text_template.value : 'work_submitted',
                sound_preset: form.elements.sound_preset ? form.elements.sound_preset.value : 'chime',
            };
            const previewUrl = teacher ? '/teacher/view-file/1/' : (policy.click_opens_task ? '/assignment/34/' : '');
            pending.unshift({id: 'preview-' + Date.now(), preview: true, policy,
                name: 'Шевченко Олександра', class_name: '7-А', show_class: policy.show_class,
                template: policy.template, sound_preset: policy.sound_preset,
                assignment: 'Практична робота: створення презентації',
                resubmission: repeat, attempt: repeat ? 2 : 1, url: previewUrl});
            render();
        });
    });

    const soundPreviewBtn = document.getElementById('btn-preview-sound');
    if (soundPreviewBtn) {
        soundPreviewBtn.addEventListener('click', async () => {
            unlockAudio();
            if (audio && audio.state === 'suspended') await audio.resume().catch(() => {});
            const presetSelect = document.getElementById('id_sound_preset');
            const preset = presetSelect ? presetSelect.value : 'chime';
            sound(false, true, preset);
        });
    }

    const templateSelect = document.getElementById('id_text_template');
    const templateHint = document.getElementById('template-description-hint');
    const templateDescriptions = {
        work_submitted: 'Нейтральний і стильний стиль («Роботу здано») без поділу на стать.',
        new_work: 'Акцент на новому надходженні («Нова здана робота»).',
        task_completed: 'Акцент на успішному виконанні («Завдання виконано»).',
        submission_received: 'Діловий стиль для вчителя та учнів («Отримано відповідь»).',
        submission_success: 'Позитивний мотивуючий статус («Успішна здача роботи»).',
    };
    if (templateSelect && templateHint) {
        templateSelect.addEventListener('change', () => {
            templateHint.textContent = templateDescriptions[templateSelect.value] || '';
        });
    }

    window.playNotificationSound = (preset, repeat) => sound(repeat, true, preset);
    selectClass();
})();
