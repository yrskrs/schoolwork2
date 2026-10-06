(function () {
    document.addEventListener('DOMContentLoaded', function () {
        const tools = document.getElementById('teacher-page-tools');
        if (tools && window.matchMedia('(max-width: 960px)').matches) tools.open = false;
        if (tools) {
            tools.addEventListener('invalid', function () { tools.open = true; }, true);
        }
    });

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function toggleTeacherAIGroups(btn, shouldOpen) {
        if (!btn) return;
        const container = btn.closest('.teacher-ai-groups-container') || document.querySelector('.teacher-ai-groups-container');
        if (!container) return;
        container.querySelectorAll('.teacher-ai-group-card').forEach(function (card) {
            card.open = Boolean(shouldOpen);
        });
    }

    function renderTeacherAIGroupsHtml(feedbackText) {
        if (!feedbackText || !feedbackText.trim()) return '';
        var text = String(feedbackText).trim();
        var lines = text.split('\n');
        var groups = [];
        var currentGroup = null;

        var headerRegex = /^(?:#{2,4}\s+([^\n]+)|([⚠️📌🎯✅💡💬📋🔍📝⚡])\s*(?:\*\*)?([^*:\n]+)(?:\*\*)?[:\s]*|(?:\*\*)([^*:\n]+)(?:\*\*)[:\s]*)/;

        lines.forEach(function (line) {
            var trimmed = line.trim();
            var match = trimmed.match(headerRegex);
            if (match) {
                if (currentGroup) {
                    groups.push(currentGroup);
                }
                var icon = match[2] || '📝';
                var title = (match[1] || match[3] || match[4] || '').trim();
                var lower = title.toLowerCase();
                if (!match[2]) {
                    if (lower.indexOf('виснов') !== -1 || lower.indexOf('підсум') !== -1) icon = '📌';
                    else if (lower.indexOf('сильн') !== -1 || lower.indexOf('плюс') !== -1) icon = '✅';
                    else if (lower.indexOf('зауважен') !== -1 || lower.indexOf('недолік') !== -1 || lower.indexOf('помилк') !== -1) icon = '💡';
                    else if (lower.indexOf('рекоменд') !== -1 || lower.indexOf('порад') !== -1) icon = '💬';
                    else if (lower.indexOf('формат') !== -1) icon = '⚠️';
                    else if (lower.indexOf('чому') !== -1 || lower.indexOf('обґрунт') !== -1 || lower.indexOf('оцінк') !== -1) icon = '🎯';
                    else if (lower.indexOf('критер') !== -1) icon = '📋';
                }
                currentGroup = {
                    icon: icon,
                    title: title || 'Розділ перевірки',
                    lines: []
                };
            } else if (currentGroup) {
                currentGroup.lines.push(line);
            } else {
                if (trimmed) {
                    currentGroup = {
                        icon: '📌',
                        title: 'Загальний огляд',
                        lines: [line]
                    };
                }
            }
        });
        if (currentGroup) {
            groups.push(currentGroup);
        }

        if (groups.length < 2) {
            return '<div style="font-size:12px;color:var(--color-text-secondary);line-height:1.4;white-space:pre-wrap;border-top:1px dashed var(--color-border);padding-top:6px;margin-top:6px;">' +
                escapeHtml(text) +
            '</div>';
        }

        var html = '<div class="teacher-ai-groups-container" style="border-top:1px dashed var(--color-border);padding-top:8px;margin-top:8px;">' +
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;gap:6px;flex-wrap:wrap;">' +
                '<span style="font-size:11px;font-weight:800;color:var(--color-text-muted);text-transform:uppercase;letter-spacing:0.3px;">' +
                    '📝 Звіт ШІ за розділами (' + groups.length + '):' +
                '</span>' +
                '<div style="display:flex;gap:4px;">' +
                    '<button type="button" class="btn btn-secondary btn-sm" onclick="toggleTeacherAIGroups(this, true)" style="font-size:10px;padding:2px 6px;line-height:1.2;font-weight:700;" title="Розгорнути всі розділи">📂 Розгорнути всі</button>' +
                    '<button type="button" class="btn btn-secondary btn-sm" onclick="toggleTeacherAIGroups(this, false)" style="font-size:10px;padding:2px 6px;line-height:1.2;font-weight:700;" title="Згорнути всі розділи">📁 Згорнути всі</button>' +
                '</div>' +
            '</div>' +
            '<div class="teacher-ai-groups-list" style="display:flex;flex-direction:column;gap:6px;">';

        groups.forEach(function (grp) {
            var content = grp.lines.join('\n').trim();
            var bullets = grp.lines.filter(function (l) { return l.trim().startsWith('•') || l.trim().startsWith('-'); });
            var badgeHtml = '';
            if (bullets.length >= 2) {
                badgeHtml = '<span class="badge" style="font-size:9.5px;padding:1px 5px;background:rgba(99,102,241,0.12);color:var(--color-primary);font-weight:750;">' + bullets.length + ' пункти</span>';
            }
            html += '<details class="teacher-ai-group-card" style="background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:var(--radius-sm);overflow:hidden;">' +
                '<summary style="padding:6px 10px;font-size:11.5px;font-weight:750;color:var(--color-text-primary);cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:8px;user-select:none;">' +
                    '<span style="display:inline-flex;align-items:center;gap:6px;">' +
                        '<span style="font-size:13px;">' + grp.icon + '</span>' +
                        '<span>' + escapeHtml(grp.title) + '</span>' +
                    '</span>' +
                    badgeHtml +
                '</summary>' +
                '<div style="padding:8px 10px;border-top:1px dashed var(--color-border);background:var(--color-surface);font-size:11.5px;color:var(--color-text-secondary);line-height:1.45;white-space:pre-wrap;">' + escapeHtml(content) + '</div>' +
            '</details>';
        });

        html += '</div></div>';
        return html;
    }

    window.toggleTeacherAIGroups = toggleTeacherAIGroups;
    window.renderTeacherAIGroupsHtml = renderTeacherAIGroupsHtml;
})();

