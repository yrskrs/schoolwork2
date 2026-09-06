/**
 * SchoolNet — Головний JavaScript (Vanilla JS, без CDN)
 * Функції: перемикач теми, модальне вікно, фільтри, форми
 */

/* ══════════════════════════════════════════════════════
   ТЕМА (Темна / Світла)
══════════════════════════════════════════════════════ */
(function () {
    'use strict';

    // Зчитуємо збережену тему або визначаємо системну
    function getInitialTheme() {
        const saved = localStorage.getItem('sn-theme');
        if (saved) return saved;
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }

    // Застосовуємо тему до документу
    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('sn-theme', theme);

        // Оновлюємо іконку кнопки
        const btn = document.getElementById('theme-toggle-btn');
        if (btn) {
            btn.textContent = theme === 'dark' ? '☀️' : '🌙';
            btn.setAttribute('aria-label', theme === 'dark' ? 'Перейти до світлої теми' : 'Перейти до темної теми');
        }
    }

    // Ініціалізуємо тему одразу (до DOMContentLoaded, щоб уникнути миготіння)
    applyTheme(getInitialTheme());

    document.addEventListener('DOMContentLoaded', function () {
        const btn = document.getElementById('theme-toggle-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                const current = document.documentElement.getAttribute('data-theme');
                applyTheme(current === 'dark' ? 'light' : 'dark');
            });
        }
    });
})();


/* ══════════════════════════════════════════════════════
   МОДАЛЬНЕ ВІКНО ПЕРЕГЛЯДУ ЗАВДАННЯ
══════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', function () {

    const overlay = document.getElementById('assignment-modal-overlay');
    const modalBody = document.getElementById('modal-body-content');
    const closeBtn = document.getElementById('modal-close-btn');

    if (!overlay) {
        // На цій сторінці немає модального вікна — пропускаємо тільки цей блок
        return;
    }

    // Закриття модального вікна
    function closeModal() {
        overlay.classList.remove('active');
        document.body.style.overflow = '';
        // Скидаємо вміст після анімації
        setTimeout(function () {
            if (!overlay.classList.contains('active') && modalBody) {
                modalBody.innerHTML = '<div class="d-flex align-center justify-between" style="padding:40px;"><div class="spinner"></div></div>';
            }
        }, 300);
    }

    if (closeBtn) closeBtn.addEventListener('click', closeModal);

    overlay.addEventListener('click', function (e) {
        if (e.target === overlay) closeModal();
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && overlay.classList.contains('active')) closeModal();
    });

    // Отримуємо CSRF-токен
    function getCsrfToken() {
        const el = document.querySelector('[name=csrfmiddlewaretoken]');
        return el ? el.value : '';
    }

    // Відкриття детальної сторінки завдання
    window.openAssignment = function (id) {
        window.location.href = '/assignment/' + id + '/';
    };


    // Рендер даних у модальному вікні
    function renderModal(data) {
        if (!modalBody) return;

        // Заголовок
        const titleEl = document.getElementById('modal-title');
        if (titleEl) titleEl.textContent = data.title;

        let html = '';

        // Метадані
        html += '<div class="modal-meta" style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">';
        html += '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">';
        if (data.classes) {
            html += '<span class="class-prominent-badge"><span class="class-icon">🏫</span><span class="class-text">' + escHtml(data.classes) + '</span></span>';
        }
        if (data.subject) {
            html += '<span class="subject-tag" style="background-color:' + data.subject_color + '22;color:' + data.subject_color + '">' + escHtml(data.subject) + '</span>';
        }
        if (data.is_individual && (data.student_display || data.student_name)) {
            html += '<span class="individual-tag">👤 ' + escHtml(data.student_display || ('Для учня / учениці: ' + data.student_name)) + '</span>';
        }
        if (data.relative_published_at || data.published_at) {
            var dateBadgeClass = data.is_today ? 'badge-today' : (data.is_yesterday ? 'badge-yesterday' : 'badge-earlier');
            var pulseDot = data.is_today ? '<span class="live-dot-pulse"></span>' : '';
            var dateText = data.relative_published_at || data.published_at;
            html += '<span class="publish-date-badge ' + dateBadgeClass + '">' + pulseDot + '📅 ' + escHtml(dateText) + '</span>';
        }
        if (data.due_date) {
            html += '<span class="due-badge">⏰ до ' + escHtml(data.due_date) + '</span>';
        }
        if (data.unarchived_display) {
            html += '<span class="unarchive-badge" title="Завдання відновлено з архіву">♻️ ' + escHtml(data.unarchived_display) + '</span>';
        }
        html += '</div>';
        if (data.is_teacher || data.can_edit) {
            html += '<span class="views-badge" title="Кількість переглядів учнями"><span class="views-icon">👁️</span> ' + (data.views_count || 0) + ' переглядів</span>';
        }
        html += '</div>';

        // Вчитель
        var avatarHtml = data.teacher_avatar_url
            ? '<img src="' + escHtml(data.teacher_avatar_url) + '" alt="' + escHtml(data.teacher) + '" class="avatar-mini-img" style="width:24px;height:24px">'
            : '<div class="avatar-mini" style="background:' + escHtml(data.teacher_avatar_color || 'var(--color-primary)') + ';width:24px;height:24px;font-size:10px">' + getInitials(data.teacher) + '</div>';

        html += '<div class="teacher-mini mb-4">' + avatarHtml + '<span>' + escHtml(data.teacher) + '</span></div>';

        // Опис
        if (data.description) {
            html += '<div class="modal-description">' + escHtml(data.description) + '</div>';
        }

        // Посилання
        if (data.link_url) {
            html += '<div class="link-section">';
            html += '<span>🔗</span>';
            html += '<a href="' + escHtml(data.link_url) + '" target="_blank" rel="noopener">' +
                escHtml(data.link_label || data.link_url) + ' ↗</a>';
            html += '</div>';
        }

        // Додаткові посилання
        if (data.extra_links && data.extra_links.length > 0) {
            data.extra_links.forEach(function(lnk) {
                html += '<div class="link-section" style="margin-top:4px;">';
                html += '<span>🔗</span>';
                html += '<a href="' + escHtml(lnk.url) + '" target="_blank" rel="noopener">' +
                    escHtml(lnk.label || lnk.url) + ' ↗</a>';
                html += '</div>';
            });
        }

        // YouTube відео
        var ytVideos = (data.youtube_videos && data.youtube_videos.length > 0)
            ? data.youtube_videos
            : (data.youtube_url ? [{ title: '▶ Відеоматеріал', embed_url: data.youtube_url, watch_url: data.youtube_watch_url || data.youtube_url }] : []);

        if (ytVideos.length > 0) {
            html += '<div style="margin-top:16px;">';
            html += '<h4 style="font-size:13px;font-weight:600;color:var(--color-text-muted);margin:0 0 10px 0;">▶ Відеоматеріали (' + ytVideos.length + ')</h4>';
            html += '<div style="display:flex;flex-direction:column;gap:12px;">';

            ytVideos.forEach(function (v) {
                html += '<div style="background:var(--color-bg-secondary);padding:10px;border-radius:8px;border:1px solid var(--color-border);">';
                html += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;flex-wrap:wrap;gap:6px;">';
                html += '<span style="font-size:12px;font-weight:600;color:var(--color-text-primary);">' + escHtml(v.title) + '</span>';
                if (v.watch_url) {
                    html += '<a href="' + escHtml(v.watch_url) + '" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-sm" style="font-size:11px;padding:2px 8px;">▶ Відкрити на YouTube ↗</a>';
                }
                html += '</div>';
                html += '<div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;border-radius:6px;box-shadow:0 2px 6px rgba(0,0,0,.15);background:#000;">';
                html += '<iframe src="' + escHtml(v.embed_url) + '" frameborder="0" allowfullscreen ';
                html += 'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" ';
                html += 'referrerpolicy="strict-origin-when-cross-origin" ';
                html += 'style="position:absolute;top:0;left:0;width:100%;height:100%;border:none;border-radius:6px;"></iframe>';
                html += '</div></div>';
            });

            html += '</div></div>';
        }

        // Файли
        if (data.files && data.files.length > 0) {
            html += '<div class="files-section">';
            html += '<h4>📎 Прикріплені файли (' + data.files.length + ')</h4>';
            html += '<div class="file-list">';

            data.files.forEach(function (f) {
                html += '<div class="file-item">';
                html += '<div class="file-icon-wrap">' + escHtml(f.icon) + '</div>';
                html += '<div class="file-info"><div class="file-name">' + escHtml(f.name) + '</div>';
                html += '<div class="file-size">' + escHtml(f.size) + '</div></div>';
                html += '<div class="file-actions">';

                html += '<a href="' + escHtml(f.download_url || f.url) + '" class="btn-download">⬇ Завантажити</a>';

                html += '</div></div>';
            });

            html += '</div></div>';
        }

        // Кнопка переходу на окрему сторінку та редагування (для вчителя)
        html += '<div style="margin-top:24px;padding-top:16px;border-top:1px solid var(--color-border);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">';
        if (data.can_edit) {
            html += '<a href="/teacher/assignment/' + data.id + '/edit/" class="btn btn-primary btn-sm" style="display:inline-flex;align-items:center;gap:6px;font-weight:600;">✏️ Редагувати завдання</a>';
        } else {
            html += '<span></span>';
        }
        html += '<a href="/assignment/' + data.id + '/" class="btn btn-secondary btn-sm">📄 Детальніше ↗</a>';
        html += '</div>';

        modalBody.innerHTML = html;
    }

    // Допоміжні функції
    function escHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function getInitials(name) {
        if (!name) return '?';
        const parts = name.trim().split(' ');
        if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
        return name.substring(0, 2).toUpperCase();
    }
});


/* ══════════════════════════════════════════════════════
   АВТОЗАКРИТТЯ ПОВІДОМЛЕНЬ
══════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', function () {
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(function (alert) {
        // Кнопка закриття
        const closeBtn = alert.querySelector('.alert-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', function () {
                dismissAlert(alert);
            });
        }
        // Автозакриття через 5 секунд
        setTimeout(function () { dismissAlert(alert); }, 5000);
    });

    function dismissAlert(el) {
        el.style.opacity = '0';
        el.style.transform = 'translateX(100%)';
        el.style.transition = 'all 0.3s ease';
        setTimeout(function () { el.remove(); }, 300);
    }
});


/* ══════════════════════════════════════════════════════
   ФОРМА ЗАВДАННЯ — інтерактивність
══════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', function () {

    // ── Перемикач "Індивідуальне завдання" ───────────────────────────
    const individualCheckbox = document.getElementById('id_is_individual');
    const studentSection = document.getElementById('student-name-section');
    const classesSection = document.getElementById('classes-section');

    if (individualCheckbox) {
        function toggleIndividual() {
            if (individualCheckbox.checked) {
                if (studentSection) studentSection.style.display = 'block';
                if (classesSection) {
                    classesSection.style.opacity = '1';
                    classesSection.querySelectorAll('input[type=checkbox]').forEach(function (cb) {
                        cb.disabled = false;
                    });
                }
            } else {
                if (studentSection) studentSection.style.display = 'none';
                if (classesSection) {
                    classesSection.style.opacity = '1';
                    classesSection.querySelectorAll('input[type=checkbox]').forEach(function (cb) {
                        cb.disabled = false;
                    });
                }
            }
        }
        individualCheckbox.addEventListener('change', toggleIndividual);
        toggleIndividual(); // Ініціалізація
    }

    // ── Перемикач способу публікації ────────────────────────────────
    const publishRadios = document.querySelectorAll('.publish-radio');
    const scheduledSection = document.getElementById('scheduled-section');

    function updatePublishUI() {
        const selected = document.querySelector('.publish-radio:checked');
        if (!selected) return;

        // Підсвічуємо активний варіант
        document.querySelectorAll('.publish-option').forEach(function (opt) {
            opt.classList.remove('selected');
        });
        if (selected.closest('.publish-option')) {
            selected.closest('.publish-option').classList.add('selected');
        }

        // Показуємо/ховаємо поле відкладеної публікації
        if (scheduledSection) {
            scheduledSection.style.display = selected.value === 'scheduled' ? 'block' : 'none';
        }
    }

    publishRadios.forEach(function (r) {
        r.addEventListener('change', updatePublishUI);
    });
    updatePublishUI();

    // ── Завантаження файлів (drag & drop та клік з накопиченням) ────────────────────
    const uploadArea = document.getElementById('upload-area');
    const fileInput = document.getElementById('id_files');
    const uploadPreview = document.getElementById('upload-preview');

    if (uploadArea && fileInput) {
        let selectedFiles = [];

        // Клік відкриває діалог
        uploadArea.addEventListener('click', function (e) {
            if (e.target !== fileInput) {
                fileInput.click();
            }
        });

        // Drag & drop
        uploadArea.addEventListener('dragover', function (e) {
            e.preventDefault();
            uploadArea.classList.add('drag-over');
        });
        uploadArea.addEventListener('dragleave', function () {
            uploadArea.classList.remove('drag-over');
        });
        uploadArea.addEventListener('drop', function (e) {
            e.preventDefault();
            uploadArea.classList.remove('drag-over');
            if (e.dataTransfer.files.length > 0) {
                addFiles(e.dataTransfer.files);
            }
        });

        fileInput.addEventListener('change', function () {
            if (fileInput.files.length > 0) {
                addFiles(fileInput.files);
            }
        });

        function addFiles(filesList) {
            Array.from(filesList).forEach(file => {
                const isDuplicate = selectedFiles.some(f => f.name === file.name && f.size === file.size);
                if (!isDuplicate) {
                    selectedFiles.push(file);
                }
            });
            syncFileInput();
            updatePreview();
        }

        window.removeSelectedFile = function (index) {
            selectedFiles.splice(index, 1);
            syncFileInput();
            updatePreview();
        };

        function syncFileInput() {
            const dt = new DataTransfer();
            selectedFiles.forEach(file => dt.items.add(file));
            fileInput.files = dt.files;
        }

        function updatePreview() {
            if (!uploadPreview) return;
            uploadPreview.innerHTML = '';

            selectedFiles.forEach(function (file, index) {
                const size = file.size < 1024 * 1024
                    ? (file.size / 1024).toFixed(1) + ' КБ'
                    : (file.size / 1024 / 1024).toFixed(1) + ' МБ';

                const div = document.createElement('div');
                div.className = 'upload-preview-item';
                div.innerHTML = getFileIcon(file.name) + 
                                ' <span>' + file.name + '</span>' + 
                                '<span class="text-muted text-sm" style="margin-left:auto; margin-right:8px;">' + size + '</span>' +
                                '<button type="button" onclick="removeSelectedFile(' + index + ')" class="btn-remove-chip" style="color:var(--color-danger); background:none; border:none; cursor:pointer; font-weight:bold; font-size:14px; padding:0 4px;" title="Видалити зі списку">✕</button>';
                uploadPreview.appendChild(div);
            });
        }
    }

    function getFileIcon(filename) {
        const ext = filename.split('.').pop().toLowerCase();
        const icons = {
            'pdf': '📄', 'doc': '📝', 'docx': '📝', 'xls': '📊', 'xlsx': '📊',
            'ppt': '📑', 'pptx': '📑', 'jpg': '🖼️', 'jpeg': '🖼️', 'png': '🖼️',
            'gif': '🖼️', 'mp4': '🎬', 'avi': '🎬', 'mp3': '🎵', 'wav': '🎵',
            'zip': '🗜️', 'rar': '🗜️', 'txt': '📃', 'csv': '📊', 'py': '🐍',
            'js': '📜', 'css': '🎨', 'html': '🌐', 'json': '⚙️', 'sh': '🐚',
        };
        return icons[ext] || '📎';
    }

    // ── Видалення існуючих файлів ────────────────────────────────────
    document.querySelectorAll('.delete-file-checkbox').forEach(function (cb) {
        cb.addEventListener('change', function () {
            const item = this.closest('.existing-file-item');
            if (item) {
                if (this.checked) {
                    item.style.opacity = '0.4';
                    item.style.textDecoration = 'line-through';
                } else {
                    item.style.opacity = '1';
                    item.style.textDecoration = '';
                }
            }
        });
    });

});


/* ══════════════════════════════════════════════════════
   ПІДТВЕРДЖЕННЯ ВИДАЛЕННЯ (data-confirm) — завжди активний
══════════════════════════════════════════════════════ */
document.addEventListener('click', function (e) {
    const target = e.target.closest('[data-confirm]');
    if (target) {
        const msg = target.getAttribute('data-confirm');
        if (msg && !confirm(msg)) {
            e.preventDefault();
            e.stopPropagation();
        }
    }
});


/* ══════════════════════════════════════════════════════
   ФОРМА ПОШУКУ — звичайна відправка форми за межами головної
══════════════════════════════════════════════════════ */


/* ══════════════════════════════════════════════════════
   МОБІЛЬНЕ МЕНЮ КЛАСІВ
══════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', function () {
    const mobileMenuBtn = document.getElementById('mobile-menu-btn');
    if (!mobileMenuBtn) return;

    function currentSidebar() {
        return document.querySelector('.sidebar');
    }

    // Якщо на сторінці немає сайдбару, приховуємо кнопку меню взагалі
    if (!currentSidebar()) {
        mobileMenuBtn.style.display = 'none';
    }

    mobileMenuBtn.addEventListener('click', function () {
        const sidebar = currentSidebar();
        if (!sidebar) return;
        const open = sidebar.classList.toggle('mobile-open');
        document.body.classList.toggle('sidebar-open', open);
        this.textContent = open ? '✕' : '☰';
        this.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    document.addEventListener('click', function (e) {
        const sidebar = currentSidebar();
        if (!sidebar || !sidebar.classList.contains('mobile-open')) return;
        if (sidebar.contains(e.target) || mobileMenuBtn.contains(e.target)) return;
        sidebar.classList.remove('mobile-open');
        document.body.classList.remove('sidebar-open');
        mobileMenuBtn.textContent = '☰';
        mobileMenuBtn.setAttribute('aria-expanded', 'false');
    });
});

document.addEventListener('DOMContentLoaded', function () {
    const menu = document.getElementById('user-menu');
    const btn = document.getElementById('user-menu-btn');
    if (menu && btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            const open = menu.classList.toggle('open');
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
        document.addEventListener('click', function () {
            menu.classList.remove('open');
            btn.setAttribute('aria-expanded', 'false');
        });
    }

    // Центр сповіщень
    const notifBtn = document.getElementById('notifications-btn');
    const notifPanel = document.getElementById('notifications-panel');
    if (notifBtn && notifPanel) {
        notifBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            const isShown = notifPanel.style.display === 'block';
            notifPanel.style.display = isShown ? 'none' : 'block';
            notifBtn.setAttribute('aria-expanded', isShown ? 'false' : 'true');
        });
        document.addEventListener('click', function (e) {
            if (!notifPanel.contains(e.target) && e.target !== notifBtn) {
                notifPanel.style.display = 'none';
                notifBtn.setAttribute('aria-expanded', 'false');
            }
        });
    }
});


/* ══════════════════════════════════════════════════════
   AJAX КЕРУВАННЯ ПРОФІЛЕМ ВЧИТЕЛЯ (без перезавантаження)
══════════════════════════════════════════════════════ */
document.addEventListener('submit', function (e) {
    const profileWrapper = document.getElementById('profile-content-wrapper');
    if (!profileWrapper) return; // Працює тільки на сторінці профілю
    
    const form = e.target;
    
    // Дозволяємо звичайний сабміт, якщо це не POST форма профілю
    if (form.method.toLowerCase() !== 'post') return;
    
    e.preventDefault();
    
    const formData = new FormData(form);
    
    // Додаємо значення кнопки сабміту (для розрізнення дій)
    if (e.submitter && e.submitter.name) {
        formData.append(e.submitter.name, e.submitter.value);
    } else if (e.submitter && e.submitter.getAttribute('type') === 'submit') {
        // Якщо кнопка не має імені, але це кнопка видалення/дії, іноді корисно передати значення кнопки як дію
        const actionInput = form.querySelector('input[name="action"]');
        if (actionInput) {
            formData.append('action', actionInput.value);
        }
    }
    
    // Вимикаємо кнопки форми
    const buttons = form.querySelectorAll('button, input[type="submit"]');
    buttons.forEach(btn => btn.disabled = true);
    
    fetch(window.location.href, {
        method: 'POST',
        body: formData,
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    })
    .then(response => response.text())
    .then(htmlText => {
        const parser = new DOMParser();
        const doc = parser.parseFromString(htmlText, 'text/html');
        
        // Оновлюємо контент профілю
        const newWrapper = doc.getElementById('profile-content-wrapper');
        if (newWrapper) {
            profileWrapper.innerHTML = newWrapper.innerHTML;
        }
        
        // Оновлюємо та показуємо спливаючі повідомлення
        const oldMessages = document.querySelector('.messages-container');
        const newMessages = doc.querySelector('.messages-container');
        if (newMessages) {
            if (oldMessages) {
                oldMessages.replaceWith(newMessages);
            } else {
                const nav = document.querySelector('nav.navbar');
                if (nav) {
                    nav.insertAdjacentElement('afterend', newMessages);
                } else {
                    document.body.insertAdjacentElement('afterbegin', newMessages);
                }
            }
            // Ховаємо повідомлення через 3 секунди
            setTimeout(() => {
                const msgEl = document.querySelector('.messages-container');
                if (msgEl) msgEl.remove();
            }, 3000);
        } else if (oldMessages) {
            oldMessages.remove();
        }
    })
    .catch(err => {
        console.error('Помилка AJAX відправки форми:', err);
        // Запасний варіант: відправляємо форму класичним методом при помилці
        form.submit();
    });
});


/* ══════════════════════════════════════════════════════
   ГЛОБАЛЬНИЙ ПРОГРЕС-БАР ТА АНІМАЦІЇ ПЕРЕХОДІВ
══════════════════════════════════════════════════════ */
(function () {
    'use strict';
    let progressTimer = null;
    let progressBar = null;

    let indicatorTimer = null;

    function getBar() {
        if (!progressBar) {
            progressBar = document.getElementById('global-progress-bar');
            if (!progressBar) {
                progressBar = document.createElement('div');
                progressBar.id = 'global-progress-bar';
                document.body.appendChild(progressBar);
            }
        }
        return progressBar;
    }

    function getIndicator() {
        return document.getElementById('global-loading-indicator');
    }

    window.startProgressBar = function () {
        const bar = getBar();
        const indicator = getIndicator();
        clearTimeout(progressTimer);
        clearTimeout(indicatorTimer);

        // Миттєвий старт смужки без затримок
        bar.classList.add('active');
        bar.style.width = '25%';
        bar.style.opacity = '1';

        // Плавний рух вперед
        progressTimer = setTimeout(function () {
            bar.style.width = '68%';
        }, 120);

        // Якщо завантаження триває більше 180мс — м'яко показуємо бейдж
        indicatorTimer = setTimeout(function () {
            if (indicator) indicator.classList.add('active');
            bar.style.width = '88%';
        }, 180);
    };

    window.finishProgressBar = function () {
        const bar = getBar();
        const indicator = getIndicator();
        clearTimeout(progressTimer);
        clearTimeout(indicatorTimer);

        if (indicator) indicator.classList.remove('active');

        bar.style.width = '100%';
        progressTimer = setTimeout(() => {
            bar.style.opacity = '0';
            setTimeout(() => {
                bar.classList.remove('active');
                bar.style.width = '0%';
            }, 250);
        }, 150);
    };

    // Автоматична візуалізація завантаження при переході за внутрішніми посиланнями
    document.addEventListener('click', function (e) {
        const link = e.target.closest('a');
        if (!link) return;
        const href = link.getAttribute('href');
        if (!href || href.startsWith('#') || href.startsWith('javascript:') ||
            link.getAttribute('target') === '_blank' || link.hasAttribute('download')) {
            return;
        }
        if (link.hasAttribute('onclick') && link.getAttribute('onclick').includes('return false')) {
            return;
        }
        if (href === window.location.pathname + window.location.search) {
            return;
        }
        if (href.startsWith('/') || href.startsWith('?') || href.includes(window.location.host)) {
            window.startProgressBar();
        }
    });

    // При відправці звичайних форм також показуємо індикатор
    document.addEventListener('submit', function (e) {
        if (!e.defaultPrevented && e.target && !e.target.hasAttribute('data-no-progress')) {
            window.startProgressBar();
        }
    });

    // Завершення індикатора при завантаженні або поверненні з кешу
    window.addEventListener('load', function () {
        window.finishProgressBar();
    });
    window.addEventListener('pageshow', function () {
        window.finishProgressBar();
    });
})();


/* ══════════════════════════════════════════════════════
   АВТОМАТИЧНЕ ЗБЕРЕЖЕННЯ ТА ВІДНОВЛЕННЯ ПОЗИЦІЇ СКРОЛУ
   (Запобігає підстрибуванню на початок сторінки при діях вчителя:
   збереження, видалення, оцінювання, налаштування тощо)
══════════════════════════════════════════════════════ */
(function () {
    'use strict';

    const STORAGE_KEY = 'sn_action_scroll_pos';

    function getScrollPos() {
        return window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0;
    }

    // 1. При відправці будь-якої форми зберігаємо позицію скролу
    document.addEventListener('submit', function (e) {
        try {
            const y = getScrollPos();
            if (y > 30) {
                const state = {
                    path: window.location.pathname,
                    search: window.location.search,
                    scrollY: y,
                    time: Date.now()
                };
                sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
            }
        } catch (err) {}
    }, true);

    // Також фіксуємо скрол при кліку на кнопки/посилання дій із підтвердженням
    document.addEventListener('click', function (e) {
        const btn = e.target.closest('button[type="submit"], a[data-confirm], button[data-confirm]');
        if (btn) {
            try {
                const y = getScrollPos();
                if (y > 30) {
                    const state = {
                        path: window.location.pathname,
                        search: window.location.search,
                        scrollY: y,
                        time: Date.now()
                    };
                    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
                }
            } catch (err) {}
        }
    }, true);

    // 2. Відновлюємо скрол після перенаправлення чи оновлення сторінки
    function tryRestoreScroll() {
        try {
            const raw = sessionStorage.getItem(STORAGE_KEY);
            if (!raw) return;

            const state = JSON.parse(raw);
            // Перевіряємо свіжість збереженого положення (до 25 секунд)
            if (Date.now() - state.time > 25000) {
                sessionStorage.removeItem(STORAGE_KEY);
                return;
            }

            const currentPath = window.location.pathname;
            // Відновлюємо, якщо це та сама сторінка або споріднений розділ вчителя
            if (state.path === currentPath || (state.path.startsWith('/teacher') && currentPath.startsWith('/teacher'))) {
                const targetY = state.scrollY;

                // Багаторазове відновлення для врахування завантаження динамічного контенту (зображень, шрифтів, карток)
                window.scrollTo({ top: targetY, behavior: 'instant' });

                requestAnimationFrame(function () {
                    window.scrollTo({ top: targetY, behavior: 'instant' });
                });

                setTimeout(function () {
                    window.scrollTo({ top: targetY, behavior: 'instant' });
                }, 50);

                setTimeout(function () {
                    window.scrollTo({ top: targetY, behavior: 'instant' });
                }, 150);

                setTimeout(function () {
                    window.scrollTo({ top: targetY, behavior: 'instant' });
                    sessionStorage.removeItem(STORAGE_KEY);
                }, 350);
            } else {
                sessionStorage.removeItem(STORAGE_KEY);
            }
        } catch (err) {}
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', tryRestoreScroll);
    } else {
        tryRestoreScroll();
    }

    window.addEventListener('load', tryRestoreScroll);
})();


