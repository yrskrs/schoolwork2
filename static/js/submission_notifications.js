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

    function sound(repeat) {
        if (!audio || audio.state !== 'running' || document.hidden) return;
        // Short ascending pair for a new submission, descending three notes for a repeat.
        const notes = repeat ? [660, 523, 440] : [523, 784];
        const start = audio.currentTime;
        notes.forEach((frequency, index) => {
            const oscillator = audio.createOscillator();
            const gain = audio.createGain();
            const at = start + index * .12;
            oscillator.type = 'sine';
            oscillator.frequency.value = frequency;
            gain.gain.setValueAtTime(0, at);
            gain.gain.linearRampToValueAtTime(.08, at + .015);
            gain.gain.exponentialRampToValueAtTime(.001, at + .11);
            oscillator.connect(gain);
            gain.connect(audio.destination);
            oscillator.start(at);
            oscillator.stop(at + .12);
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
        const canOpen = teacher && /^\/teacher\/view-file\/\d+\/$/.test(item.url || '');
        const main = document.createElement(canOpen ? 'a' : 'button');
        main.className = 'submission-notice-main';
        if (canOpen) main.href = item.url;
        else main.type = 'button';
        main.addEventListener('click', () => dismiss(item.id));
        const name = document.createElement('span');
        name.className = 'submission-notice-name';
        name.textContent = item.name;
        const group = document.createElement('span');
        group.className = 'submission-notice-class';
        group.textContent = item.class_name;
        name.append(group);
        const status = document.createElement('span');
        status.className = 'submission-notice-status';
        status.textContent = item.resubmission ? `Повторно здав/ла роботу · спроба ${item.attempt}` : 'Здав/ла роботу';
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
        hint.textContent = item.preview ? 'Приклад сповіщення' : canOpen ? 'Відкрити для перевірки →' : 'Натисніть, щоб закрити';
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
        pending.slice(0, 3).forEach(item => {
            const policy = item.preview ? item.policy : policies[item.teacher_id];
            if (!item.shownAt) item.shownAt = now;
            let entry = visible.get(item.id);
            if (!entry) {
                entry = {card: createCard(item)};
                visible.set(item.id, entry);
                list.append(entry.card);
                // One sound per batch; repeated submissions take precedence.
                if (!item.sounded && policy.sound) play = Boolean(play || item.resubmission);
                item.sounded = true;
            }
            if (!policy.sticky) {
                entry.timer = setTimeout(() => {
                    // Do not remove the control while someone uses the keyboard on it.
                    if (entry.card.contains(document.activeElement)) {
                        entry.card.addEventListener('focusout', () => dismiss(item.id), {once: true});
                    } else dismiss(item.id);
                }, Math.max(1, policy.duration * 1000 - (now - item.shownAt)));
            }
        });
        if (play !== null) sound(play);
        clear.hidden = pending.length < 2;
        clear.textContent = pending.length > 3 ? `Ще ${pending.length - 3} у черзі · Закрити всі` : 'Закрити всі';
        save();
    }

    clear.addEventListener('click', () => { pending = []; render(); schedule(0); });

    function schedule(delay) {
        clearTimeout(timer);
        if (stopped || document.hidden || navigator.onLine === false || (!teacher && !classId)) return;
        timer = setTimeout(poll, delay);
    }

    async function poll() {
        if (controller || stopped || document.hidden || navigator.onLine === false) return;
        const currentGeneration = generation;
        controller = new AbortController();
        const currentController = controller;
        const timeout = setTimeout(() => currentController.abort(), 10000);
        let delay = 5000;
        try {
            const url = new URL(root.dataset.endpoint, location.origin);
            if (!teacher) url.searchParams.set('class', classId);
            if (cursor !== null) url.searchParams.set('since', cursor);
            const response = await fetch(url, {signal: currentController.signal, cache: 'no-store',
                headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
            if (!response.ok) throw new Error('Notification request failed');
            const data = await response.json();
            if (currentGeneration !== generation || stopped) return;
            policies = data.policies;
            if (cursor !== null && data.cursor < cursor) pending = []; // Database restore.
            pending = pending.filter(item => item.preview || policies[item.teacher_id]);
            const known = new Set(pending.map(item => item.id));
            let accepted = cursor;
            let full = false;
            for (const item of data.events) {
                if (!known.has(item.id)) {
                    // Keep DOM small and retain cursor backpressure for a long sticky queue.
                    if (pending.length >= 100) { full = true; break; }
                    pending.push(item);
                    known.add(item.id);
                }
                accepted = item.id;
            }
            cursor = full ? accepted : data.cursor;
            failures = 0;
            delay = data.has_more && !full ? 1000 : data.poll_after * 1000;
            render();
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
        storageKey = 'schoolnet-submission-notices:' + root.dataset.viewer + ':' + classId;
        cursor = null;
        pending = [];
        policies = {};
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
        if (document.hidden) {
            clearTimeout(timer);
            if (controller) controller.abort();
        } else schedule(0);
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
            };
            pending.unshift({id: 'preview-' + Date.now(), preview: true, policy,
                name: 'Шевченко Олександра', class_name: '7-А', assignment: 'Практична робота: створення презентації',
                resubmission: repeat, attempt: repeat ? 2 : 1, url: ''});
            render();
        });
    });
    selectClass();
})();
