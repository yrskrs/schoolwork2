/**
 * teacher_live_poll.js
 * Періодичне динамічне оновлення стану для робочого кабінету вчителя без F5:
 * - Стан поточного уроку та відлік часу до дзвінка
 * - Центр сповіщень та лічильники нових сповіщень
 * - Кількість зданих робіт, що очікують оцінювання
 * - Статистика та лічильники завдань на панелі вчителя
 * - Лічильник проведених уроків
 *
 * Не скидає введені дані у відкритих формах.
 * Автоматично призупиняється, якщо вкладка браузера неактивна (Page Visibility API).
 */

(function() {
    if (typeof window === 'undefined' || !window.fetch) return;

    var pollInterval = 20000; // 20 секунд
    var pollTimer = null;
    var isPolling = false;

    function updateTeacherLiveStatus() {
        if (isPolling) return;
        isPolling = true;

        fetch('/api/teacher/live-status/', {
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        })
        .then(function(res) {
            if (!res.ok) throw new Error('Live status fetch failed');
            return res.json();
        })
        .then(function(data) {
            isPolling = false;
            if (data.status !== 'success') return;

            // ── 1. Центр сповіщень та лічильники ──────────────────────────────
            var unread = data.unread_notifications_count || 0;
            var notifsCount = data.notifications_count || 0;
            var pendingSubs = data.pending_submissions_count || 0;

            var notifsBtn = document.getElementById('notifications-btn');
            if (notifsBtn) {
                var pulseBadge = notifsBtn.querySelector('.nav-badge-pulse');
                var regularBadge = notifsBtn.querySelector('.nav-badge');

                if (unread > 0) {
                    if (!pulseBadge) {
                        pulseBadge = document.createElement('span');
                        pulseBadge.className = 'nav-badge-pulse';
                        pulseBadge.style.cssText = 'position:absolute;top:-4px;right:-4px;background:#ef4444;color:#fff;font-size:10px;font-weight:800;border-radius:10px;padding:1px 5px;min-width:16px;line-height:14px;text-align:center;box-shadow:0 0 0 2px var(--color-surface);';
                        notifsBtn.appendChild(pulseBadge);
                    }
                    pulseBadge.textContent = unread;
                    pulseBadge.style.display = 'inline-block';
                    if (regularBadge) regularBadge.style.display = 'none';
                } else {
                    if (pulseBadge) pulseBadge.style.display = 'none';
                    if (notifsCount > 0) {
                        if (!regularBadge) {
                            regularBadge = document.createElement('span');
                            regularBadge.className = 'nav-badge';
                            regularBadge.style.cssText = 'position:absolute;top:-4px;right:-4px;background:var(--color-primary);color:#fff;font-size:10px;font-weight:700;border-radius:10px;padding:1px 5px;min-width:16px;line-height:14px;text-align:center;box-shadow:0 0 0 2px var(--color-surface);';
                            notifsBtn.appendChild(regularBadge);
                        }
                        regularBadge.textContent = notifsCount;
                        regularBadge.style.display = 'inline-block';
                    } else if (regularBadge) {
                        regularBadge.style.display = 'none';
                    }
                }
            }

            // Оновлюємо список сповіщень у панелі (тільки коли вона закрита, щоб не заважати користувачу)
            var notifsPanel = document.getElementById('notifications-panel');
            if (notifsPanel && notifsPanel.style.display === 'none') {
                var container = notifsPanel.querySelector('div[style*="overflow-y:auto"]');
                var counterHeader = notifsPanel.querySelector('span[style*="border-radius:10px"]');
                if (counterHeader) {
                    counterHeader.textContent = notifsCount + ' активних';
                    counterHeader.style.display = notifsCount > 0 ? 'inline-block' : 'none';
                }

                if (container) {
                    if (data.notifications && data.notifications.length > 0) {
                        var html = '';
                        data.notifications.forEach(function(n) {
                            var isMissing = (n.type === 'missing_task');
                            html += '<div class="notification-item" style="padding:10px 12px;border-radius:var(--radius-md);background:' +
                                (isMissing ? 'rgba(239,68,68,0.06)' : 'var(--color-surface)') + ';border:1px solid ' +
                                (isMissing ? 'rgba(239,68,68,0.25)' : 'var(--color-border)') + ';margin-bottom:8px;transition:all 0.15s ease;">' +
                                '<div style="display:flex;align-items:flex-start;gap:8px;">' +
                                '<span style="font-size:16px;flex-shrink:0;">' + (n.icon || '🔔') + '</span>' +
                                '<div style="flex:1;">' +
                                '<div style="display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:2px;">' +
                                '<span style="font-size:12.5px;font-weight:800;color:' + (isMissing ? '#dc2626' : 'var(--color-text-primary)') + ';">' + (n.title || '') + '</span>' +
                                (n.badge ? '<span style="font-size:10px;font-weight:700;padding:1px 6px;border-radius:8px;background:' + (isMissing ? '#ef4444' : 'var(--color-primary)') + ';color:#fff;">' + n.badge + '</span>' : '') +
                                '</div>' +
                                '<div style="font-size:12px;color:var(--color-text-secondary);line-height:1.4;margin-bottom:8px;">' + (n.message || '') + '</div>' +
                                (n.action_url ? '<a href="' + n.action_url + '" class="btn btn-primary btn-sm" style="font-size:11px;font-weight:700;padding:4px 10px;border-radius:4px;display:inline-flex;align-items:center;gap:4px;text-decoration:none;">' + (n.action_label || 'Перейти') + '</a>' : '') +
                                '</div></div></div>';
                        });
                        container.innerHTML = html;
                    } else {
                        container.innerHTML = '<div style="padding:28px 16px;text-align:center;color:var(--color-text-muted);">' +
                            '<div style="font-size:28px;margin-bottom:6px;">✨</div>' +
                            '<div style="font-size:13px;font-weight:600;">Немає нових сповіщень</div>' +
                            '<div style="font-size:11.5px;margin-top:2px;">Всі завдання опубліковано вчасно!</div>' +
                            '</div>';
                    }
                }
            }

            // Оновлюємо лічильники неоцінених робіт у шапці та сайдбарі
            document.querySelectorAll('.nav-badge').forEach(function(el) {
                if (el.closest('#notifications-btn')) return;
                if (pendingSubs > 0) {
                    el.textContent = pendingSubs;
                    el.style.display = 'inline-block';
                } else {
                    el.style.display = 'none';
                }
            });

            // ── 2. Стан уроку / дзвінка у шапці сайту ───────────────────────────
            var navBtn = document.getElementById('today-schedule-nav-btn');
            var bellText = document.getElementById('live-bell-text') || (navBtn ? navBtn.querySelector('.nav-btn-text') : null);
            var pillIcon = navBtn ? navBtn.querySelector('.schedule-pill-icon') : null;

            if (navBtn && data.live_status) {
                var ls = data.live_status;
                navBtn.classList.toggle('status-in-lesson', ls.status_type === 'in_lesson');
                navBtn.classList.toggle('status-in-break', ls.status_type === 'in_break');

                if (ls.title) {
                    navBtn.title = ls.title + (ls.time_info ? ' — ' + ls.time_info : '');
                }

                if (pillIcon) {
                    if (ls.status_type === 'in_lesson') {
                        pillIcon.innerHTML = '<span class="live-dot-pulse green"></span>';
                    } else if (ls.status_type === 'in_break') {
                        pillIcon.innerHTML = '<span class="live-dot-pulse amber"></span>';
                    } else {
                        pillIcon.textContent = '📅';
                    }
                }

                if (bellText) {
                    if (ls.status_type === 'in_lesson') {
                        bellText.innerHTML = '<span style="color:#10b981;">' + (ls.badge_text || 'Урок') + '</span>';
                    } else if (ls.status_type === 'in_break') {
                        bellText.innerHTML = '<span style="color:#f59e0b;">' + (ls.badge_text || 'Перерва') + '</span>';
                    } else if (ls.status_type === 'before_school') {
                        bellText.innerHTML = '<span style="color:#3b82f6;">' + (ls.badge_text || 'До дзвінка') + '</span>';
                    } else {
                        bellText.textContent = 'Розклад';
                    }
                }
            }

            var navProg = document.getElementById('nav-schedule-progress-bar');
            if (navProg && data.live_status) {
                var lsProg = data.live_status;
                if (lsProg.status_type === 'in_lesson' || lsProg.status_type === 'in_break') {
                    var pct = lsProg.progress_percent || 0;
                    navProg.style.display = 'block';
                    navProg.style.width = pct + '%';
                    navProg.style.background = (lsProg.status_type === 'in_break') ? '#f59e0b' : '#10b981';
                } else {
                    navProg.style.display = 'none';
                }
            }

            // ── 3. Інформер поточного уроку (live-lesson-widget) ───────────────
            var liveWidgets = document.querySelectorAll('.live-lesson-widget');
            if (liveWidgets.length > 0 && data.live_status && data.live_status.has_schedule) {
                var ls = data.live_status;
                liveWidgets.forEach(function(liveWidget) {
                    var timeInfoEl = liveWidget.querySelector('#live-lesson-time-info') || liveWidget.querySelector('.live-lesson-time-info');
                    if (timeInfoEl && ls.time_info) {
                        timeInfoEl.textContent = ls.time_info;
                    }
                    var badgeEl = liveWidget.querySelector('.badge');
                    if (badgeEl && ls.badge_text) {
                        badgeEl.textContent = ls.badge_text;
                        if (ls.status_type === 'in_lesson') {
                            badgeEl.style.background = 'rgba(16, 185, 129, 0.15)';
                            badgeEl.style.color = '#059669';
                            badgeEl.style.border = '1px solid rgba(16, 185, 129, 0.3)';
                        } else if (ls.status_type === 'in_break') {
                            badgeEl.style.background = 'rgba(245, 158, 11, 0.15)';
                            badgeEl.style.color = '#d97706';
                            badgeEl.style.border = '1px solid rgba(245, 158, 11, 0.3)';
                        }
                    }
                    var titleEl = liveWidget.querySelector('h3');
                    if (titleEl && ls.title) {
                        titleEl.textContent = ls.title;
                    }
                    var card = liveWidget.querySelector('.live-lesson-card');
                    if (card) {
                        card.className = 'live-lesson-card status-' + ls.status_type;
                        var stripe = card.children[0];
                        if (stripe) {
                            stripe.style.background = (ls.status_type === 'in_lesson') ? '#10b981' :
                                                      (ls.status_type === 'in_break') ? '#f59e0b' :
                                                      (ls.status_type === 'before_school') ? '#3b82f6' : 'var(--color-primary)';
                        }
                    }
                    var iconBubble = liveWidget.querySelector('.status-icon-bubble');
                    if (iconBubble) {
                        if (ls.status_type === 'in_lesson') {
                            iconBubble.textContent = '🔔';
                            iconBubble.style.background = 'rgba(16, 185, 129, 0.15)';
                            iconBubble.style.color = '#059669';
                        } else if (ls.status_type === 'in_break') {
                            iconBubble.textContent = '☕';
                            iconBubble.style.background = 'rgba(245, 158, 11, 0.15)';
                            iconBubble.style.color = '#d97706';
                        } else if (ls.status_type === 'before_school') {
                            iconBubble.textContent = '🌅';
                            iconBubble.style.background = 'rgba(59, 130, 246, 0.15)';
                            iconBubble.style.color = '#2563eb';
                        } else {
                            iconBubble.textContent = '📅';
                        }
                    }
                    var progressFill = liveWidget.querySelector('#live-lesson-progress-fill') || liveWidget.querySelector('.live-lesson-progress-fill');
                    if (progressFill && ls.progress_percent !== undefined) {
                        progressFill.style.width = ls.progress_percent + '%';
                        progressFill.style.background = (ls.status_type === 'in_break') ? 'linear-gradient(90deg, #f59e0b, #fbbf24, #d97706)' : 'linear-gradient(90deg, #10b981, #34d399, #059669)';
                    }
                });
            }

            // ── 3.1. Синхронізація модалки розкладу на сьогодні, якщо вона відкрита ──
            if (typeof window.refreshTodayScheduleModal === 'function') {
                var todayModal = document.getElementById('today-schedule-modal');
                if (todayModal && todayModal.style.display !== 'none') {
                    window.refreshTodayScheduleModal();
                }
            }

            // ── 4. Лічильники дашборду вчителя ────────────────────────────────
            if (data.counts) {
                var cPub = document.getElementById('count-published');
                var cDraft = document.getElementById('count-draft');
                var cSched = document.getElementById('count-scheduled');
                var cArch = document.getElementById('count-archived');

                if (cPub) cPub.textContent = data.counts.published;
                if (cDraft) cDraft.textContent = data.counts.draft;
                if (cSched) cSched.textContent = data.counts.scheduled;
                if (cArch) cArch.textContent = data.counts.archived;

                var statValues = document.querySelectorAll('.stat-grid .stat-value');
                if (statValues.length >= 4) {
                    statValues[0].textContent = data.counts.published;
                    statValues[1].textContent = data.counts.draft;
                    statValues[2].textContent = data.counts.scheduled;
                    statValues[3].textContent = data.counts.archived;
                }
            }

            // ── 5. Лічильник проведених уроків ────────────────────────────────
            if (data.conducted_lessons) {
                var condCountEl = document.getElementById('calculated-lessons-count');
                if (condCountEl && data.conducted_lessons.calculated_count !== undefined) {
                    condCountEl.textContent = data.conducted_lessons.calculated_count;
                }
                var condBadge = document.getElementById('teacher-conducted-lessons-badge');
                if (condBadge && data.conducted_lessons.count !== undefined) {
                    condBadge.textContent = data.conducted_lessons.count;
                }
            }
        })
        .catch(function() {
            isPolling = false;
        });
    }

    function startLivePolling() {
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(function() {
            if (!document.hidden) {
                updateTeacherLiveStatus();
            }
        }, pollInterval);
    }

    document.addEventListener('visibilitychange', function() {
        if (!document.hidden) {
            updateTeacherLiveStatus();
            startLivePolling();
        } else {
            if (pollTimer) clearInterval(pollTimer);
        }
    });

    // Перший запит через 3 секунди після завантаження сторінки
    setTimeout(function() {
        updateTeacherLiveStatus();
        startLivePolling();
    }, 3000);
})();
