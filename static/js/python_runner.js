/* Same-origin teacher UI; the private runner and its credential never reach the browser. */
(() => {
    'use strict';
    function init() {
        const root = document.getElementById('python-runner');
        if (!root) return;
        const find = name => root.querySelector('[data-python-' + name + ']');
        const play = find('play'), stop = find('stop'), result = find('result');
        const status = find('status'), output = find('output'), error = find('error');
        const screen = find('screen'), form = find('form'), input = root.querySelector('#python-stdin');
        const keyboard = find('keyboard');
        const csrf = root.querySelector('[name=csrfmiddlewaretoken]').value;
        const finalStates = new Set(['finished', 'failed', 'stopped', 'timeout']);
        let token = null, cursor = 0, frame = 0, timer = null, generation = 0, running = false;
        let inputQueue = Promise.resolve(), lastMove = 0, pollFailures = 0;
        let chosenTab = false, showedDesktop = false;
        const heldKeys = new Set();

        async function request(data, method = 'POST', keepalive = false) {
            let url = root.dataset.url;
            const options = {method, credentials: 'same-origin', cache: 'no-store', keepalive};
            if (method === 'GET') url += '?' + new URLSearchParams(data);
            else {
                options.headers = {'Content-Type': 'application/json', 'X-CSRFToken': csrf};
                options.body = JSON.stringify(data);
            }
            const response = await fetch(url, options);
            let body;
            try { body = await response.json(); }
            catch (_) { throw new Error('Не вдалося отримати відповідь сервісу запуску.'); }
            if (!response.ok) throw new Error(body.error || 'Помилка запуску Python.');
            return body;
        }
        function showError(message) { error.textContent = message; error.hidden = !message; }
        function controls(state) {
            running = state === 'running';
            const active = state === 'preparing' || running;
            play.disabled = active;
            stop.disabled = !active;
            form.querySelectorAll('input,button').forEach(element => { element.disabled = !running; });
            const labels = {preparing: 'Підготовка Python та бібліотек…', running: 'Програма виконується',
                finished: 'Виконання завершено', failed: 'Помилка виконання',
                stopped: 'Запуск зупинено', timeout: 'Час виконання вичерпано'};
            status.textContent = labels[state] || 'Готово до запуску';
        }
        function terminalText(text) {
            // Render text only. Strip terminal escape/control sequences from untrusted output.
            return text.replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, '')
                .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
                .replace(/\r\n/g, '\n').replace(/\r/g, '\n')
                .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, '');
        }
        async function poll(myGeneration) {
            if (!token || generation !== myGeneration) return;
            try {
                const data = await request({token, cursor, frame}, 'GET');
                if (generation !== myGeneration) return;
                pollFailures = 0;
                showError('');
                if (data.output) {
                    const bottom = output.scrollHeight - output.scrollTop - output.clientHeight < 50;
                    if (data.truncated) output.textContent = '[Початок виводу скорочено]\n';
                    output.textContent = (output.textContent + terminalText(data.output)).slice(-131072);
                    if (bottom) output.scrollTop = output.scrollHeight;
                }
                cursor = data.offset;
                if (data.frame) {
                    screen.src = 'data:image/jpeg;base64,' + data.frame;
                    screen.hidden = false;
                    find('screen-wait').hidden = true;
                }
                frame = data.frame_id;
                if (data.has_desktop && !showedDesktop) {
                    showedDesktop = true;
                    if (!chosenTab) selectTab(tabs.find(tab => tab.dataset.pythonTab === 'desktop'));
                }
                controls(data.state);
                if (data.state === 'finished' && data.exit_code !== null) {
                    status.textContent += ' · код виходу ' + data.exit_code;
                }
                if (!finalStates.has(data.state)) timer = setTimeout(() => poll(myGeneration), 300);
            } catch (failure) {
                if (generation !== myGeneration) return;
                showError(failure.message);
                if (++pollFailures < 5) timer = setTimeout(() => poll(myGeneration), 1500);
                else { controls('failed'); releaseKeys(); }
            }
        }
        function send(data) {
            if (!running || !token) return Promise.resolve();
            const runToken = token;
            inputQueue = inputQueue.catch(() => {}).then(async () => {
                if (runToken !== token || !running) return;
                try { await request({action: 'input', token: runToken, input: data}); }
                catch (failure) { showError(failure.message); }
            });
            return inputQueue;
        }
        play.addEventListener('click', async () => {
            const myGeneration = ++generation;
            clearTimeout(timer); showError(''); controls('preparing'); result.hidden = false;
            output.textContent = ''; cursor = 0; frame = 0; token = null; pollFailures = 0;
            chosenTab = false; showedDesktop = false; keyboard.value = ''; selectTab(tabs[0]);
            screen.hidden = true; screen.removeAttribute('src'); find('screen-wait').hidden = false;
            try {
                const data = await request({action: 'start', file_id: root.dataset.fileId});
                if (generation !== myGeneration) {
                    await request({action: 'stop', token: data.token}); return;
                }
                token = data.token;
                poll(myGeneration);
            } catch (failure) {
                if (generation === myGeneration) { showError(failure.message); controls('failed'); }
            }
        });
        stop.addEventListener('click', async () => {
            // A start still in flight is cancelled as soon as its run token arrives.
            if (!token) { ++generation; controls('stopped'); return; }
            stop.disabled = true;
            try {
                await request({action: 'stop', token});
                ++generation; clearTimeout(timer); heldKeys.clear(); controls('stopped');
            } catch (failure) { showError(failure.message); stop.disabled = false; }
        });
        form.addEventListener('submit', event => {
            event.preventDefault();
            if (!running) return;
            const text = input.value; input.value = '';
            send({type: 'stdin', text: text + '\n'}); input.focus();
        });
        find('interrupt').addEventListener('click', () => send({type: 'stdin', text: '\x03'}));
        find('eof').addEventListener('click', () => send({type: 'stdin', text: '\x04'}));
        const tabs = Array.from(root.querySelectorAll('[data-python-tab]'));
        function selectTab(tab) {
            tabs.forEach(button => {
                const active = button === tab;
                button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1;
            });
            root.querySelectorAll('[data-python-panel]').forEach(panel => {
                panel.hidden = panel.dataset.pythonPanel !== tab.dataset.pythonTab;
            });
        }
        tabs.forEach((tab, index) => {
            tab.addEventListener('click', () => { chosenTab = true; selectTab(tab); });
            tab.addEventListener('keydown', event => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                chosenTab = true;
                const next = tabs[event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + 1) % tabs.length];
                selectTab(next); next.focus();
            });
        });
        function pointer(event, action, button = 0) {
            const rect = screen.getBoundingClientRect();
            send({type: 'pointer', event: action, button,
                x: Math.max(0, Math.min(1023, Math.floor((event.clientX - rect.left) / rect.width * 1024))),
                y: Math.max(0, Math.min(767, Math.floor((event.clientY - rect.top) / rect.height * 768)))});
        }
        screen.addEventListener('pointerdown', event => {
            if (!running) return;
            event.preventDefault(); screen.focus(); screen.setPointerCapture(event.pointerId);
            pointer(event, 'mousedown', event.button + 1);
        });
        screen.addEventListener('pointerup', event => pointer(event, 'mouseup', event.button + 1));
        screen.addEventListener('pointercancel', event => pointer(event, 'mouseup', 1));
        screen.addEventListener('pointermove', event => {
            if (performance.now() - lastMove < 100) return;
            lastMove = performance.now(); pointer(event, 'mousemove');
        });
        screen.addEventListener('contextmenu', event => event.preventDefault());
        screen.addEventListener('focus', () => keyboard.focus({preventScroll: true}));
        screen.addEventListener('wheel', event => {
            if (!running) return;
            event.preventDefault(); pointer(event, 'click', event.deltaY < 0 ? 4 : 5);
        }, {passive: false});
        const keys = {Enter: 'Return', Escape: 'Escape', Backspace: 'BackSpace', Tab: 'Tab',
            ArrowLeft: 'Left', ArrowRight: 'Right', ArrowUp: 'Up', ArrowDown: 'Down',
            Delete: 'Delete', Home: 'Home', End: 'End', PageUp: 'Prior', PageDown: 'Next',
            Shift: 'Shift_L', Control: 'Control_L', Alt: 'Alt_L', ' ': 'space'};
        keyboard.addEventListener('keydown', event => {
            if (!running || event.metaKey) return;
            if (event.isComposing || (event.ctrlKey && event.key.toLowerCase() === 'v')) return;
            const key = keys[event.key] || (/^[A-Za-z0-9]$/.test(event.key) ? event.key : null);
            if (key) {
                event.preventDefault(); heldKeys.add(key); send({type: 'key', event: 'keydown', key});
            } else if (event.key.length === 1) {
                event.preventDefault(); send({type: 'text', text: event.key});
            }
        });
        keyboard.addEventListener('keyup', event => {
            const key = keys[event.key] || (/^[A-Za-z0-9]$/.test(event.key) ? event.key : null);
            if (key && heldKeys.has(key)) {
                event.preventDefault(); heldKeys.delete(key); send({type: 'key', event: 'keyup', key});
            }
        });
        function releaseKeys() {
            heldKeys.forEach(key => send({type: 'key', event: 'keyup', key})); heldKeys.clear();
        }
        function flushText(event) {
            if (event.isComposing || !keyboard.value) return;
            send({type: 'text', text: keyboard.value.slice(0, 4096)}); keyboard.value = '';
        }
        keyboard.addEventListener('input', flushText);
        keyboard.addEventListener('compositionend', flushText);
        keyboard.addEventListener('blur', releaseKeys);
        window.addEventListener('pagehide', () => {
            ++generation; clearTimeout(timer);
            if (token) request({action: 'stop', token}, 'POST', true).catch(() => {});
        });
        window.addEventListener('pageshow', event => {
            if (event.persisted) { token = null; controls('stopped'); }
        });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
