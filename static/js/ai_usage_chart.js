/* Provider/model series from aggregated local data. No CDN or AI calls. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        const root = document.querySelector('[data-ai-usage-chart]');
        const source = document.getElementById('ai-usage-chart-data');
        if (!root || !source) return;
        const data = JSON.parse(source.textContent);
        const plot = document.getElementById('ai-usage-plot'), totals = document.getElementById('ai-usage-totals');
        const info = document.getElementById('ai-usage-point-info'), legend = document.getElementById('ai-usage-legend');
        const filters = Array.from(root.querySelectorAll('[data-usage-filter]'));
        const format = new Intl.NumberFormat('uk-UA');
        const compact = new Intl.NumberFormat('uk-UA', {notation: 'compact', maximumFractionDigits: 1});
        const metrics = {requests: 'Запитів', total_tokens: 'Усі токени', prompt_tokens: 'Вхідні токени', completion_tokens: 'Вихідні токени'};
        const seriesKey = row => JSON.stringify([row.provider, row.model_name]);
        const identities = Array.from(new Set(data.rows.map(seriesKey))).sort();
        const hues = new Map(), usedHues = new Set();
        identities.forEach(key => {
            let hash = 0; for (const ch of key) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
            let hue = hash % 360, tries = 0; while (usedHues.has(hue) && tries++ < 360) hue = (hue + 137) % 360;
            if (usedHues.has(hue)) hue += identities.indexOf(key) / identities.length;
            usedHues.add(hue); hues.set(key, hue);
        });
        const color = key => `hsl(${hues.get(key)} 72% var(--ai-series-lightness))`;
        let lastWidth = 0;
        function fromUrl() {
            const url = new URL(location.href);
            filters.forEach(select => {
                const requested = url.searchParams.get('usage_' + select.dataset.usageFilter);
                const known = Array.from(select.options).some(option => option.value === requested);
                if (requested && requested.length <= 100 && !known && ['model', 'provider', 'action'].includes(select.dataset.usageFilter)) {
                    const option = document.createElement('option'); option.value = requested; option.textContent = requested; select.append(option);
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
                    if (select.value === select.options[0].value) url.searchParams.delete(name); else url.searchParams.set(name, select.value);
                });
                history.replaceState(null, '', url);
            }
            const series = new Map(), values = data.labels.map(() => 0);
            const summary = {requests: 0, success: 0, failure: 0, tokens: 0};
            data.rows.forEach(row => {
                if (selected.model !== 'all' && row.model_name !== selected.model) return;
                if (selected.provider !== 'all' && row.provider !== selected.provider) return;
                if (selected.action !== 'all' && row.action !== selected.action) return;
                if (selected.status !== 'all' && row.is_success !== (selected.status === 'success')) return;
                const key = seriesKey(row);
                if (!series.has(key)) series.set(key, {key, name: row.model_name || 'Без назви', provider: row.provider || 'Не вказано', values: data.labels.map(() => 0)});
                const value = Number(row[selected.metric] || 0);
                series.get(key).values[row.index] += value; values[row.index] += value;
                summary.requests += row.requests; summary[row.is_success ? 'success' : 'failure'] += row.requests; summary.tokens += Number(row.total_tokens || 0);
            });
            totals.replaceChildren();
            [['requests','Запитів'],['success','Успішних'],['failure','Помилок'],['tokens','Токенів']].forEach(([key,title]) => {
                const card = document.createElement('div'), value = document.createElement('strong'), caption = document.createElement('span');
                value.textContent = format.format(summary[key]); value.dataset.usageTotal = key; caption.textContent = title; card.append(value,caption); totals.append(card);
            });
            legend.replaceChildren();
            const rows = Array.from(series.values()).sort((a,b) => a.key.localeCompare(b.key));
            rows.forEach(row => {
                const item = document.createElement('span'), dot = document.createElement('i'), label = document.createElement('span');
                dot.style.background = color(row.key); dot.setAttribute('aria-hidden', 'true');
                label.textContent = `${row.provider.toUpperCase()} / ${row.name} · ${format.format(row.values.reduce((sum,n) => sum+n,0))}`;
                item.dataset.usageSeries = row.key; item.append(dot,label); legend.append(item);
            });
            plot.replaceChildren();
            info.textContent = summary.requests ? 'Виберіть стовпчик або точку, щоб побачити точне значення. Стрілки перемикають позначки.' : 'За обраними фільтрами запитів немає.';
            if (!data.labels.length) return;
            const width = Math.max(280, Math.round(plot.getBoundingClientRect().width)); lastWidth = width;
            const height = 260, left = 54, right = 12, top = 18, bottom = 44;
            const innerWidth = width-left-right, innerHeight = height-top-bottom;
            const max = selected.display === 'bars' ? Math.max(1,...values) : Math.max(1,...rows.map(row => Math.max(...row.values)));
            const ceiling = Math.max(4,Math.ceil(max/4)*4);
            const svg = element('svg', {viewBox:`0 0 ${width} ${height}`, role:'group', 'aria-label':`${metrics[selected.metric]} у часі за моделями`, 'aria-describedby':'ai-usage-chart-note'});
            svg.append(element('title', {}, `${metrics[selected.metric]} · ${selected.display === 'bars' ? 'Стовпчики' : selected.display === 'lines' ? 'Лінії' : 'Криві'}`));
            for (let step=0;step<=4;step++) {
                const y=top+innerHeight-innerHeight*step/4;
                svg.append(element('line',{x1:left,x2:width-right,y1:y,y2:y,class:'ai-usage-gridline'}));
                svg.append(element('text',{x:left-8,y:y+4,'text-anchor':'end',class:'ai-usage-axis'},compact.format(ceiling*step/4)));
            }
            const stride=innerWidth/values.length, points=[];
            const xAt=index=>left+(index+.5)*stride, yAt=value=>top+innerHeight-innerHeight*value/ceiling;
            const showEvery=Math.max(1,Math.ceil(values.length/Math.max(2,Math.floor(innerWidth/85))));
            values.forEach((_,index)=>{
                if (index%showEvery!==0) return;
                const label=data.labels[index], text=data.resolution==='hour' ? label.slice(6) : data.resolution==='day' ? label.slice(0,5) : label;
                svg.append(element('text',{x:xAt(index),y:height-16,'text-anchor':'middle',class:'ai-usage-axis'},text));
            });
            function interactive(node, row, index) {
                const caption=`${data.labels[index]} · ${row.provider.toUpperCase()} / ${row.name} · ${metrics[selected.metric]}: ${format.format(row.values[index])}`;
                node.setAttribute('tabindex',points.length===0 ? '0' : '-1'); node.setAttribute('role','button'); node.setAttribute('aria-label',caption); node.dataset.usageIndex=index; node.dataset.usageSeries=row.key;
                node.append(element('title',{},caption));
                const position=points.length;
                node.addEventListener('click',()=>{info.textContent=caption;});
                node.addEventListener('focus',()=>{info.textContent=caption;});
                node.addEventListener('keydown',event=>{
                    if (['ArrowRight','ArrowLeft','ArrowUp','ArrowDown','Home','End'].includes(event.key)) {
                        event.preventDefault(); let target=position+(event.key==='ArrowLeft' || event.key==='ArrowUp' ? -1 : 1);
                        if (event.key==='Home') target=0; if (event.key==='End') target=points.length-1;
                        points[(target+points.length)%points.length].focus();
                    } else if (event.key==='Enter' || event.key===' ') {event.preventDefault();info.textContent=caption;}
                });
                svg.append(node); points.push(node);
            }
            if (selected.display==='bars') {
                const stacked=values.map(()=>0);
                rows.forEach(row=>row.values.forEach((value,index)=>{
                    if (!value) return;
                    interactive(element('rect',{x:left+index*stride+stride*.15,y:yAt(stacked[index]+value),width:Math.max(1,stride*.7),height:innerHeight*value/ceiling,rx:2,class:'ai-usage-bar',style:`fill:${color(row.key)}`}),row,index);
                    stacked[index]+=value;
                }));
                values.forEach((value,index)=>{if(!value) svg.append(element('rect',{x:xAt(index)-2,y:yAt(0)-2,width:4,height:2,class:'ai-usage-zero'}));});
            } else {
                rows.forEach(row=>{
                    let path=`M ${xAt(0)} ${yAt(row.values[0])}`;
                    for(let i=1;i<row.values.length;i++) {
                        const x=xAt(i),y=yAt(row.values[i]);
                        if(selected.display==='curves') {const middle=(xAt(i-1)+x)/2;path+=` C ${middle} ${yAt(row.values[i-1])}, ${middle} ${y}, ${x} ${y}`;}
                        else path+=` L ${x} ${y}`;
                    }
                    svg.append(element('path',{d:path,class:'ai-usage-line',style:`stroke:${color(row.key)}`,'data-usage-series':row.key}));
                    row.values.forEach((value,index)=>interactive(element('circle',{cx:xAt(index),cy:yAt(value),r:4,class:'ai-usage-point',style:`fill:${color(row.key)}`}),row,index));
                });
            }
            plot.append(svg);
        }
        filters.forEach(select=>select.addEventListener('change',()=>render(true)));
        root.querySelector('[data-usage-reset]').addEventListener('click',()=>{filters.forEach(select=>{select.selectedIndex=0;});render(true);});
        window.addEventListener('popstate',()=>{fromUrl();render(false);});
        if(window.ResizeObserver) new ResizeObserver(()=>{if(Math.max(280,Math.round(plot.getBoundingClientRect().width))!==lastWidth) render(false);}).observe(plot);
        fromUrl();render(false);
    });
})();
