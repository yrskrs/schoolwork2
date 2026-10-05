/* Shared, lazy material viewer: no page jumps, one request owner per dialog. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        const dialog = document.getElementById('assignment-file-modal');
        if (!dialog) return;
        const title = document.getElementById('assign-modal-title');
        const picker = document.getElementById('assign-modal-picker');
        const modes = document.getElementById('assign-modal-modes');
        const slideToolbar = document.getElementById('assign-modal-slides');
        const body = document.getElementById('assign-modal-body');
        const content = document.getElementById('assign-modal-content');
        const spinner = document.getElementById('assign-modal-spinner');
        const closeButton = document.getElementById('assign-modal-close');
        const files = new Map();
        const cache = new Map();
        const readingExtensions = new Set(['.docx','.doc','.odt','.xlsx','.xls','.ods','.pptx','.ppt','.odp']);
        let current, mode = 'document', opener, request, timer, generation = 0, slideIndex = 0, slides = [];

        document.querySelectorAll('[data-material-preview]').forEach(button => {
            const file = {
                id: button.dataset.materialPreview, name: button.dataset.materialName,
                extension: button.dataset.materialExtension,
                url: button.dataset.materialUrl, download: button.dataset.materialDownload
            };
            files.set(file.id, file);
            button.addEventListener('click', () => open(file.id, button));
            picker.add(new Option(file.name, file.id));
        });
        picker.closest('label').hidden = files.size < 2;
        function stop() {
            clearTimeout(timer);
            if (request) request.abort();
            generation++;
        }
        function notice(message, retry) {
            slideToolbar.hidden = true; slides = [];
            const box = document.createElement('div');
            box.className = 'material-preview-notice';
            const paragraph = document.createElement('p');
            paragraph.textContent = message;
            paragraph.setAttribute('role', 'status');
            box.append(paragraph);
            if (retry) {
                const button = document.createElement('button');
                button.type = 'button'; button.className = 'btn btn-secondary btn-sm';
                button.textContent = '↻ Спробувати ще раз';
                button.addEventListener('click', () => load(mode));
                box.append(button);
            }
            content.replaceChildren(box);
        }
        function updateSlide() {
            const image = content.querySelector('.material-slide-image img');
            image.src = slides[slideIndex]; image.alt = 'Слайд ' + (slideIndex + 1);
            slideToolbar.querySelector('select').value = String(slideIndex);
        }
        function moveSlide(delta) { slideIndex = (slideIndex + delta + slides.length) % slides.length; updateSlide(); }
        function renderSlides(data) {
            slides = data.slides; slideIndex = 0;
            const player = document.createElement('div'); player.className = 'material-slide-player';
            const controls = document.createElement('div'); controls.className = 'material-slide-controls';
            const selector = document.createElement('select'); selector.className = 'form-input';
            selector.setAttribute('aria-label', 'Номер слайда');
            slides.forEach((url, index) => selector.add(new Option('Слайд ' + (index + 1) + ' / ' + slides.length, index)));
            selector.addEventListener('change', () => { slideIndex = Number(selector.value); updateSlide(); });
            [-1, 1].forEach(delta => {
                const button = document.createElement('button');
                button.type = 'button'; button.className = 'btn btn-secondary btn-sm';
                button.textContent = delta < 0 ? '←' : '→';
                button.setAttribute('aria-label', delta < 0 ? 'Попередній слайд' : 'Наступний слайд');
                button.title = delta < 0 ? 'Попередній слайд' : 'Наступний слайд';
                button.addEventListener('click', () => moveSlide(delta));
                if (delta > 0) controls.append(selector);
                controls.append(button);
            });
            const frame = document.createElement('div'); frame.className = 'material-slide-image';
            const image = document.createElement('img');
            image.addEventListener('error', () => notice('Зображення слайда недоступне. Спробуйте текстовий вигляд або відкрийте матеріал у новій вкладці.', true));
            frame.append(image); player.append(frame); content.replaceChildren(player);
            slideToolbar.replaceChildren(controls); slideToolbar.hidden = false; updateSlide();
        }
        function render(data) {
            slides = [];
            slideToolbar.replaceChildren(); slideToolbar.hidden = true;
            if (data.type === 'slides' && Array.isArray(data.slides) && data.slides.length) {
                renderSlides(data);
            } else if (data.type === 'html') {
                // The endpoint sanitizes document HTML; scripts are never evaluated.
                content.innerHTML = data.content || '<p>Текстовий вміст відсутній.</p>';
            } else if (data.type === 'code' || data.type === 'text') {
                const pre = document.createElement('pre');
                const code = document.createElement('code'); code.textContent = data.content || 'Файл порожній.';
                if (data.type === 'code') {
                    code.className = 'language-' + (data.lang || 'plaintext');
                    pre.className = code.className;
                }
                pre.append(code); content.replaceChildren(pre);
                if (data.type === 'code' && window.Prism) window.Prism.highlightElement(code);
            } else if (data.type === 'url' && data.url) {
                let element;
                if (data.file_type === 'image') {
                    element = document.createElement('img'); element.alt = current.name;
                } else if (data.file_type === 'video' || data.file_type === 'audio') {
                    element = document.createElement(data.file_type); element.controls = true;
                } else {
                    element = document.createElement('iframe'); element.title = current.name;
                    element.setAttribute('allow', 'fullscreen');
                }
                const url = new URL(data.url, location.href);
                if (data.file_type === 'embedded') url.searchParams.set('theme', document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
                element.src = url.href; content.replaceChildren(element);
            } else if (data.type === 'archive') {
                content.replaceChildren();
                (data.items || []).forEach(item => {
                    const row = document.createElement('div'); row.className = 'material-archive-row';
                    const name = document.createElement('span'); name.textContent = (item.is_dir ? '📁 ' : '📄 ') + item.name;
                    const size = document.createElement('small'); size.textContent = item.is_dir ? '' : item.size + ' байт';
                    row.append(name, size); content.append(row);
                });
                if (!content.children.length) notice('Архів порожній.');
            } else {
                notice(data.message || 'Перегляд цього формату недоступний. Відкрийте матеріал у новій вкладці або завантажте файл.', true);
            }
        }
        async function load(nextMode, attempt = 0) {
            stop(); const owner = generation;
            slideToolbar.hidden = true; slides = [];
            mode = nextMode; body.scrollTop = 0;
            modes.querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.materialMode === mode)));
            const key = current.id + ':' + mode;
            if (cache.has(key)) { spinner.hidden = true; body.setAttribute('aria-busy','false'); render(cache.get(key)); return; }
            request = new AbortController();
            body.setAttribute('aria-busy','true');
            if (!attempt) { content.replaceChildren(); spinner.hidden = false; }
            try {
                const response = await fetch('/assignment/file/' + current.id + '/preview/' + (mode === 'text' ? '?mode=text' : ''), {signal:request.signal});
                if (!response.ok) throw new Error('HTTP ' + response.status);
                const data = await response.json();
                if (!dialog.open || owner !== generation) return;
                spinner.hidden = true;
                if (data.type === 'pending') {
                    notice(attempt < 24 ? '⏳ Презентація готується. Слайди з’являться автоматично. Поки що можна обрати «Текстовий вигляд».' : 'Підготовка слайдів триває довше. Перейдіть до текстового вигляду або відкрийте матеріал у новій вкладці.', attempt >= 24);
                    if (attempt < 24) timer = setTimeout(() => load(mode, attempt + 1), 2500);
                    else body.setAttribute('aria-busy','false');
                } else {
                    body.setAttribute('aria-busy','false');
                    if (data.type !== 'error') cache.set(key, data);
                    render(data);
                }
            } catch (error) {
                if (error.name === 'AbortError' || !dialog.open || owner !== generation) return;
                spinner.hidden = true; body.setAttribute('aria-busy','false');
                notice('Не вдалося завантажити матеріал. Перевірте з’єднання та спробуйте ще раз.', true);
            }
        }
        function choose(id) {
            current = files.get(String(id));
            if (!current) return;
            dialog.dataset.materialId = current.id; title.textContent = current.name; title.title = current.name; picker.value = current.id;
            document.getElementById('assign-modal-newtab').href = current.url;
            document.getElementById('assign-modal-download').href = current.download;
            modes.hidden = !readingExtensions.has(current.extension.toLowerCase());
            load('document');
        }
        function open(id, button) {
            if (!files.has(String(id))) return;
            if (!dialog.open) {
                opener = button || document.activeElement;
                document.documentElement.classList.add('material-preview-open'); dialog.showModal();
                closeButton.focus({preventScroll:true});
            }
            choose(id);
        }
        picker.addEventListener('change', () => choose(picker.value));
        modes.querySelectorAll('button').forEach(button => button.addEventListener('click', () => load(button.dataset.materialMode)));
        closeButton.addEventListener('click', () => dialog.close());
        dialog.addEventListener('close', () => {
            stop(); content.replaceChildren(); slideToolbar.replaceChildren(); slideToolbar.hidden = true; slides = []; delete dialog.dataset.materialId;
            body.setAttribute('aria-busy','false'); document.documentElement.classList.remove('material-preview-open');
            if (opener && opener.isConnected) opener.focus({preventScroll:true});
        });
        dialog.addEventListener('click', event => {
            if (event.target !== dialog) return;
            const bounds = dialog.getBoundingClientRect();
            if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
        });
        dialog.addEventListener('keydown', event => {
            if (!slides.length || event.altKey || event.ctrlKey || event.metaKey || ['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName)) return;
            if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); moveSlide(event.key === 'ArrowLeft' ? -1 : 1); }
        });
        window.openAssignmentFileModal = id => open(id);
        window.closeAssignmentFileModal = () => { if (dialog.open) dialog.close(); };
    });
}());
