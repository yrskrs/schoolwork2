/* Local chart: only aggregated log data, no CDN or AI calls. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        const root = document.querySelector('[data-ai-usage-chart]');
        const source = document.getElementById('ai-usage-chart-data');
        if (!root || !source) return;
        const data = JSON.parse(source.textContent);
        const plot = document.getElementById('ai-usage-plot');
        const totals = document.getElementById('ai-usage-totals');
        const info = document.getElementById('ai-usage-point-info');
        const filters = Array.from(root.querySelectorAll('[data-usage-filter]'));
        const format = new Intl.NumberFormat('uk-UA');
        const compact = new Intl.NumberFormat('uk-UA', {notation: 'compact', maximumFractionDigits: 1});
        const metrics = {requests: 'Запитів', total_tokens: 'Усі токени', prompt_tokens: 'Вхідні токени', completion_tokens: 'Вихідні токени'};
        let lastWidth = 0;
        function fromUrl() {
            const url = new URL(location.href);
            filters.forEach(select => {
                const requested = url.searchParams.get('usage_' + select.dataset.usageFilter);
                const known = Array.from(select.options).some(option => option.value === requested);
                if (requested && requested.length <= 100 && !known && ['model', 'provider', 'action'].includes(select.dataset.usageFilter)) {
                    const option = document.createElement('option');
                    option.value = requested; option.textContent = requested;
                    select.append(option);
                }
                select.value = Array.from(select.options).some(option => option.value === requested) ? requested : select.options[0].value;
            });
        }
        function element(tag, attrs, text) {
            const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
            Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
            if (text !== undefined) node.textContent = text;
            return node;
        }
        function render(updateUrl) {
            const selected = Object.fromEntries(filters.map(select => [select.dataset.usageFilter, select.value]));
            if (updateUrl) {
                const url = new URL(location.href);
                filters.forEach(select => {
                    const name = 'usage_' + select.dataset.usageFilter;
                    if (select.value === select.options[0].value) url.searchParams.delete(name);
                    else url.searchParams.set(name, select.value);
                });
                history.replaceState(null, '', url);
            }
            const values = data.labels.map(() => 0);
            const summary = {requests: 0, success: 0, failure: 0, tokens: 0};
            data.rows.forEach(row => {
                if (selected.model !== 'all' && row.model_name !== selected.model) return;
                if (selected.provider !== 'all' && row.provider !== selected.provider) return;
                if (selected.action !== 'all' && row.action !== selected.action) return;
                if (selected.status !== 'all' && row.is_success !== (selected.status === 'success')) return;
                values[row.index] += Number(row[selected.metric] || 0);
                summary.requests += row.requests;
                summary[row.is_success ? 'success' : 'failure'] += row.requests;
                summary.tokens += Number(row.total_tokens || 0);
            });
            totals.replaceChildren();
            [['requests', 'Запитів'], ['success', 'Успішних'], ['failure', 'Помилок'], ['tokens', 'Токенів']].forEach(([key, title]) => {
                const card = document.createElement('div');
                const value = document.createElement('strong');
                value.textContent = format.format(summary[key]);
                value.dataset.usageTotal = key;
                const caption = document.createElement('span'); caption.textContent = title;
                card.append(value, caption); totals.append(card);
            });
            plot.replaceChildren();
            info.textContent = summary.requests ? 'Натисніть стовпчик або оберіть його з клавіатури, щоб побачити точне значення.' : 'За обраними фільтрами запитів немає.';
            if (!data.labels.length) return;
            const width = Math.max(280, Math.round(plot.getBoundingClientRect().width));
            lastWidth = width;
            const height = 280, left = 54, right = 10, top = 18, bottom = 48;
            const innerWidth = width - left - right, innerHeight = height - top - bottom;
            const max = Math.max(1, ...values);
            const ceiling = Math.max(4, Math.ceil(max / 4) * 4);
            const svg = element('svg', {viewBox: `0 0 ${width} ${height}`, role: 'group', 'aria-label': `${metrics[selected.metric]} у часі`, 'aria-describedby': 'ai-usage-chart-note'});
            svg.append(element('title', {}, `${metrics[selected.metric]} за обраними фільтрами`));
            for (let step = 0; step <= 4; step++) {
                const y = top + innerHeight - innerHeight * step / 4;
                svg.append(element('line', {x1: left, x2: width - right, y1: y, y2: y, class: 'ai-usage-gridline'}));
                svg.append(element('text', {x: left - 8, y: y + 4, 'text-anchor': 'end', class: 'ai-usage-axis'}, compact.format(ceiling * step / 4)));
            }
            const stride = innerWidth / values.length;
            const showEvery = Math.max(1, Math.ceil(values.length / Math.max(2, Math.floor(innerWidth / 85))));
            const bars = [];
            values.forEach((value, index) => {
                const barHeight = Math.max(2, innerHeight * value / ceiling);
                const x = left + index * stride + stride * .15;
                const caption = `${data.labels[index]} · ${metrics[selected.metric]}: ${format.format(value)}`;
                const bar = element('rect', {x, y: top + innerHeight - barHeight, width: Math.max(1, stride * .7), height: barHeight, rx: 3, class: 'ai-usage-bar' + (value === 0 ? ' is-zero' : ''), tabindex: 0, role: 'button', 'aria-label': caption, 'data-usage-index': index});
                bar.append(element('title', {}, caption));
                function show() {info.textContent = caption;}
                bar.addEventListener('click', show);
                bar.addEventListener('focus', show);
                bar.addEventListener('keydown', event => {
                    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
                        event.preventDefault(); bars[(index + (event.key === 'ArrowRight' ? 1 : -1) + values.length) % values.length].focus();
                    } else if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); show();}
                });
                svg.append(bar); bars.push(bar);
                if (index % showEvery === 0) {
                    const label = data.labels[index];
                    const text = data.resolution === 'hour' ? label.slice(6) : data.resolution === 'day' ? label.slice(0, 5) : label;
                    svg.append(element('text', {x: left + (index + .5) * stride, y: height - 20, 'text-anchor': 'middle', class: 'ai-usage-axis'}, text));
                }
            });
            plot.append(svg);
        }
        filters.forEach(select => select.addEventListener('change', () => render(true)));
        root.querySelector('[data-usage-reset]').addEventListener('click', () => {
            filters.forEach(select => {select.selectedIndex = 0;}); render(true);
        });
        window.addEventListener('popstate', () => {fromUrl(); render(false);});
        if (window.ResizeObserver) new ResizeObserver(() => {
            if (Math.max(280, Math.round(plot.getBoundingClientRect().width)) !== lastWidth) render(false);
        }).observe(plot);
        fromUrl(); render(false);
    });
})();
