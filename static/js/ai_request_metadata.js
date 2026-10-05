(function () {
    'use strict';
    window.SchoolNetAIRequestMetadata = {
        html: function (metadata) {
            if (!metadata) return '';
            const section = document.createElement('section');
            section.className = 'ai-request-metadata';
            section.setAttribute('aria-label', 'Дані запиту ШІ');
            const title = document.createElement('h3'); title.textContent = 'Дані запиту ШІ'; section.append(title);
            const list = document.createElement('dl');
            [['Постачальник', metadata.provider || 'Не збережено'], ['Модель', metadata.model || 'Не збережено'],
             ['Вхідні токени', metadata.prompt_tokens], ['Вихідні токени', metadata.completion_tokens],
             ['Усього токенів', metadata.total_tokens]].forEach(function (item) {
                const row = document.createElement('div'), label = document.createElement('dt'), value = document.createElement('dd');
                label.textContent = item[0]; value.textContent = item[1] === null || item[1] === undefined ? 'Немає даних' : String(item[1]);
                row.append(label, value); list.append(row);
            });
            section.append(list);
            const hint = document.createElement('p');
            hint.textContent = metadata.total_tokens === null || metadata.total_tokens === undefined ?
                'Постачальник не повернув статистику токенів або її не було збережено під час старої перевірки.' :
                'Дані запиту, що сформував оцінку. Загальна кількість — за відповіддю API; вона може включати токени міркувань.';
            section.append(hint); return section.outerHTML;
        }
    };
})();
