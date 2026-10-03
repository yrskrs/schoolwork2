/* Uploads remain unrestricted; show transfer progress and server confirmation. */
window.SchoolNetUploads = {
    send: function (form, button) {
        const panel = document.createElement('div');
        panel.className = 'upload-progress';
        panel.setAttribute('role', 'status');
        panel.setAttribute('aria-live', 'polite');
        const progress = document.createElement('progress');
        progress.max = 100;
        progress.value = 0;
        progress.setAttribute('aria-label', 'Прогрес завантаження');
        const text = document.createElement('p');
        text.textContent = 'Починаємо завантаження. Залишайте сторінку відкритою.';
        panel.append(progress, text);
        const progressSlot = form.querySelector('[data-upload-progress-slot]');
        if (progressSlot) progressSlot.replaceChildren(panel);
        else form.append(panel);
        const request = new XMLHttpRequest();
        request.open('POST', form.action || window.location.href);
        request.upload.onprogress = function (event) {
            if (!event.lengthComputable) return;
            const percent = Math.floor(event.loaded / event.total * 100);
            progress.value = percent;
            text.textContent = percent < 100
                ? 'Завантажено ' + percent + '% · ' + (event.loaded / 1048576).toFixed(1) + ' МБ з ' + (event.total / 1048576).toFixed(1) + ' МБ'
                : 'Файли передано. Сервер зберігає роботу — дочекайтеся підтвердження.';
        };
        function failed(message) {
            text.textContent = message;
            if (button) { button.disabled = false; button.textContent = 'Спробувати надіслати знову'; button.style.cursor = ''; }
        }
        request.onerror = function () { failed('З’єднання перервано. Перевірте розділ «Здані роботи» перед повторним надсиланням.'); };
        request.onload = function () {
            if (request.status >= 200 && request.status < 300) {
                const destination = new URL(request.responseURL, window.location.href);
                if (destination.pathname !== window.location.pathname) {
                    window.location.assign(destination.href);
                } else {
                    // Display the server-rendered validation response, including field errors.
                    document.open(); document.write(request.responseText); document.close();
                }
            } else {
                failed('Сервер не підтвердив збереження роботи (код ' + request.status + '). Файли залишилися у формі.');
            }
        };
        request.send(new FormData(form));
    }
};
