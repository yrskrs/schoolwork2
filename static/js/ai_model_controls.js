/* Provider-aware model controls and visible results. No credentials are written to the DOM result. */
(function () {
    'use strict';
    const slotProviders = new Map(), slotCredentials = new Map();
    function field(id) {const el = document.getElementById(id); return el ? el.value.trim() : '';}
    function connection(slot) {
        const backup = slot === 'backup';
        return {provider: field(backup ? 'backup_ai_provider' : 'ai_provider'),
            api_key: field(backup ? 'backup_api_key' : 'api_key'), model_name: field(backup ? 'backup_model_name' : 'model_name'),
            custom_url: field(backup ? 'backup_custom_api_url' : 'custom_api_url')};
    }
    function resultFor(button) {
        const container = button.closest('[data-model-record]') || button.closest('td') || button.parentElement;
        let result = container.querySelector('[data-model-test-result]');
        if (!result) {result = document.createElement('p'); result.dataset.modelTestResult = ''; result.className = 'ai-model-test-result'; result.setAttribute('role', 'status'); result.setAttribute('aria-live', 'polite'); container.append(result);}
        return result;
    }
    function status(result, message, state) {
        result.hidden = false; result.style.display = 'block'; result.dataset.state = state;
        result.classList.add('ai-model-test-result'); result.textContent = message;
        result.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    }
    async function runTest(config, button, result) {
        if (button && button.disabled) return;
        if ((config.provider !== 'custom' && !config.api_key) || (config.provider === 'custom' && !config.custom_url)) {
            status(result, '⚠️ Налаштуйте ключ або Base URL для ' + config.provider.toUpperCase() + ' у «Підключення».', 'error'); return;
        }
        const original = button ? button.textContent : '';
        if (button) {button.disabled = true; button.textContent = '⏳ Тестування…'; button.setAttribute('aria-busy', 'true');}
        status(result, '⏳ Перевіряємо ' + config.provider.toUpperCase() + ' / ' + config.model_name + '…', 'pending');
        const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 22000);
        try {
            const body = new FormData(); Object.entries(config).forEach(([key,value]) => body.append(key, value));
            const token = document.querySelector('[name="csrfmiddlewaretoken"]');
            const response = await fetch('/api/ai/test-connection/', {method: 'POST', body, signal: controller.signal, headers: {'X-CSRFToken': token ? token.value : '', 'X-Requested-With': 'XMLHttpRequest'}});
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const data = await response.json();
            status(result, (data.success ? '✅ ' : '❌ ') + (data.provider || config.provider).toUpperCase() + ' / ' + (data.model || config.model_name) + ': ' + (data.message || 'Відповідь без повідомлення.'), data.success ? 'success' : 'error');
        } catch (error) {status(result, error.name === 'AbortError' ? '⚠️ Час очікування відповіді минув. Спробуйте ще раз.' : '⚠️ Не вдалося отримати результат тесту: ' + error.message, 'error');}
        finally {clearTimeout(timer); if (button) {button.disabled = false; button.textContent = original; button.removeAttribute('aria-busy');}}
    }
    window.refreshProviderModels = function(slot, reset) {
        const backup = slot === 'backup', provider = field(backup ? 'backup_ai_provider' : 'ai_provider');
        const select = document.getElementById(backup ? 'backup_model_select' : 'primary_model_select');
        const input = document.getElementById(backup ? 'backup_model_name' : 'model_name');
        const wrap = document.getElementById(backup ? 'backup_model_input_wrap' : 'primary_model_input_wrap');
        const keyInput = document.getElementById(backup ? 'backup_api_key' : 'api_key');
        const urlInput = document.getElementById(backup ? 'backup_custom_api_url' : 'custom_api_url');
        const previous = slotProviders.get(slot);
        let restoredModel = '';
        if (reset && previous && previous !== provider) {
            slotCredentials.set(slot + ':' + previous, {key: keyInput.value, url: urlInput.value, model: input.value});
            const stored = slotCredentials.get(slot + ':' + provider) || {key: '', url: ''};
            keyInput.value = stored.key; urlInput.value = stored.url; restoredModel = stored.model || '';
        }
        slotProviders.set(slot, provider);
        const catalog = JSON.parse(document.getElementById('ai-provider-catalog-data').textContent);
        const saved = JSON.parse(document.getElementById('ai-saved-models-data').textContent);
        const group = catalog.find(group => group.provider === provider);
        const names = Array.from(new Set([...(group ? group.models.map(model => model.name) : []), ...saved.filter(model => model.provider === provider).map(model => model.name)]));
        const current = reset ? (restoredModel || names[0] || '') : input.value.trim();
        select.replaceChildren();
        if (backup && !current) {const option = document.createElement('option'); option.value = ''; option.textContent = 'За замовчуванням для провайдера'; select.append(option);}
        if (current && !names.includes(current)) names.unshift(current);
        names.forEach(name => {const option = document.createElement('option'); option.value = name; option.textContent = name; select.append(option);});
        const custom = document.createElement('option'); custom.value = '__custom__'; custom.textContent = '✏️ Ввести ідентифікатор вручну'; select.append(custom);
        input.value = current; select.value = current || (backup ? '' : '__custom__');
        wrap.style.display = select.value === '__custom__' ? 'block' : 'none';
    };
    window.testConnection = function (slot, callerBtn) {
        const backup = slot === 'backup';
        const button = callerBtn || document.getElementById(backup ? 'btn-test-backup' : 'btn-test-primary');
        const result = document.getElementById(backup ? 'backup-test-result' : 'primary-test-result');
        runTest(connection(slot), button, result);
    };
    window.testModelDirect = function (name, button, provider) {
        const active = document.querySelector('[name="active_api_type"]:checked');
        const order = active && active.value === 'backup' ? ['backup','primary'] : ['primary','backup'];
        const configs = order.map(connection);
        const config = configs.find(item => item.provider === provider && (item.api_key || (provider === 'custom' && item.custom_url))) || configs.find(item => item.provider === provider);
        const result = resultFor(button);
        if (!config) {status(result, '⚠️ Для ' + provider.toUpperCase() + ' немає підключення. Налаштуйте його на вкладці «Підключення».', 'error'); return;}
        runTest({...config, model_name: name}, button, result);
    };
    document.addEventListener('DOMContentLoaded', function () {
        if (document.getElementById('ai-provider-catalog-data')) {refreshProviderModels('primary', false); refreshProviderModels('backup', false);}
        const provider = document.getElementById('new_model_provider');
        const catalogSource = document.getElementById('ai-provider-catalog-data');
        if (provider && catalogSource) {
            const catalog = JSON.parse(catalogSource.textContent);
            function selectProvider(clear) {
                if (clear) document.getElementById('new_model_name').value = '';
                document.querySelectorAll('[data-catalog-provider]').forEach(group => {group.hidden = group.dataset.catalogProvider !== provider.value;});
                const suggestions = document.getElementById('ai-model-suggestions'); suggestions.replaceChildren();
                const group = catalog.find(group => group.provider === provider.value);
                (group ? group.models : []).forEach(model => {const option = document.createElement('option'); option.value = model.name; suggestions.append(option);});
            }
            provider.addEventListener('change', () => selectProvider(true)); selectProvider(false);
        }
        const panels = Array.from(document.querySelectorAll('[data-stats-panel]'));
        const tabs = Array.from(document.querySelectorAll('[data-stats-tab]'));
        function selectStats(view, update) {
            if (!panels.some(panel => panel.dataset.statsPanel === view)) view = 'usage';
            panels.forEach(panel => {panel.hidden = panel.dataset.statsPanel !== view;});
            tabs.forEach(tab => {if (tab.dataset.statsTab === view) tab.setAttribute('aria-current', 'page'); else tab.removeAttribute('aria-current');});
            if (update) {const url = new URL(location.href); url.searchParams.set('stats_view', view); url.hash = ''; history.replaceState(null, '', url);}
        }
        tabs.forEach(tab => tab.addEventListener('click', event => {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            event.preventDefault(); selectStats(tab.dataset.statsTab, true);
        }));
        window.addEventListener('popstate', () => selectStats(new URL(location.href).searchParams.get('stats_view'), false));
        const records = Array.from(document.querySelectorAll('[data-model-record]'));
        const search = document.querySelector('[data-model-search]');
        let page = 0;
        function paginate() {
            if (!search) return;
            const query = search.value.trim().toLocaleLowerCase('uk-UA');
            const matches = records.filter(record => (record.dataset.modelName + ' ' + record.dataset.modelProvider).toLocaleLowerCase('uk-UA').includes(query));
            const pages = Math.max(1, Math.ceil(matches.length / 6)); page = Math.min(page, pages - 1);
            records.forEach(record => {record.hidden = true;}); matches.slice(page * 6, (page + 1) * 6).forEach(record => {record.hidden = false;});
            document.querySelector('[data-model-page-info]').textContent = matches.length ? `Сторінка ${page + 1} з ${pages} · моделей: ${matches.length}` : 'Моделей за цим пошуком немає.';
            document.querySelector('[data-model-prev]').disabled = page === 0;
            document.querySelector('[data-model-next]').disabled = page === pages - 1;
        }
        if (search) {
            search.addEventListener('input', () => {page = 0; paginate();});
            document.querySelector('[data-model-prev]').addEventListener('click', () => {page--; paginate();});
            document.querySelector('[data-model-next]').addEventListener('click', () => {page++; paginate();}); paginate();
        }
    });
})();
