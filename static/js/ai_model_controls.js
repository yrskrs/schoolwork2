/* Provider-aware model controls and visible results. No credentials are written to the DOM result. */
(function () {
    'use strict';
    function savedConnections() {
        const source = document.getElementById('ai-connections-data');
        return source ? JSON.parse(source.textContent) : [];
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
        result.scrollIntoView({block: 'nearest', behavior: window.SchoolNetPerformance ? window.SchoolNetPerformance.scrollBehavior() : 'smooth'});
    }
    async function runTest(config, button, result) {
        if (button && button.disabled) return;
        if (!config.connection_id && ((config.provider !== 'custom' && config.provider !== 'cloudflare' && !config.api_key) || ((config.provider === 'custom' || config.provider === 'cloudflare') && !config.custom_url))) {
            status(result, '⚠️ Налаштуйте ключ або Account ID / Base URL для ' + config.provider.toUpperCase() + ' у «Підключення».', 'error'); return;
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
    window.testModelDirect = function (name, button, provider, connectionId) {
        const candidates = savedConnections().filter(item => item.provider === provider && (!connectionId || item.id === connectionId));
        const result = resultFor(button);
        if (candidates.length !== 1) {
            status(result, 'Оберіть підключення та натисніть «Тест» біля моделі на вкладці «Моделі».', 'error'); return;
        }
        runTest({connection_id: candidates[0].id, provider: provider, model_name: name}, button, result);
    };
    window.filterCatalogByProvider = function (providerName) {
        const sections = document.querySelectorAll('[data-catalog-provider]');
        sections.forEach(group => {
            if (!providerName || providerName === 'all') {
                group.hidden = false;
            } else {
                group.hidden = group.dataset.catalogProvider !== providerName;
            }
        });
        const buttons = document.querySelectorAll('.ai-catalog-tab-btn');
        buttons.forEach(btn => {
            if (btn.dataset.catalogTab === (providerName || 'all')) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });
    };

    window.quickCreateConnectionFor = function (providerName, modelName) {
        if (window.SchoolNetAISettings) {
            window.SchoolNetAISettings.select('connection');
        }
        const supplier = document.getElementById('connection-provider-new');
        if (supplier) {
            const details = supplier.closest('details');
            if (details) details.open = true;
            if (providerName) {
                supplier.value = providerName;
                supplier.dispatchEvent(new Event('change', {bubbles: true}));
            }
        }
        if (modelName) {
            const modelInput = document.getElementById('connection-model-new');
            if (modelInput) modelInput.value = modelName;
        }
        const labelInput = document.getElementById('connection-label-new');
        if (labelInput && !labelInput.value && providerName) {
            const titles = {
                cloudflare: 'Cloudflare Workers AI',
                gemini: 'Google Gemini',
                openai: 'OpenAI API',
                groq: 'Groq Cloud',
                deepseek: 'DeepSeek API',
                openrouter: 'OpenRouter',
                custom: 'Власний сервер API'
            };
            labelInput.value = titles[providerName] || providerName.toUpperCase();
        }
        const keyInput = document.getElementById('connection-key-new');
        const urlInput = document.getElementById('connection-url-new');
        if (providerName === 'cloudflare' && urlInput && !urlInput.closest('[hidden]')) {
            urlInput.scrollIntoView({behavior: window.SchoolNetPerformance ? window.SchoolNetPerformance.scrollBehavior() : 'smooth', block: 'center'});
            urlInput.focus();
        } else if (keyInput) {
            keyInput.scrollIntoView({behavior: window.SchoolNetPerformance ? window.SchoolNetPerformance.scrollBehavior() : 'smooth', block: 'center'});
            keyInput.focus();
        }
    };

    document.addEventListener('DOMContentLoaded', function () {
        const newProvider = document.getElementById('connection-provider-new');
        if (newProvider) {
            const url = document.querySelector('[data-new-connection-url]');
            const toggleUrl = () => {
                const isCustomOrCf = newProvider.value === 'custom' || newProvider.value === 'cloudflare';
                url.hidden = !isCustomOrCf;
                const urlLabel = url.querySelector('label');
                const urlInput = url.querySelector('input');
                if (newProvider.value === 'cloudflare') {
                    if (urlLabel) urlLabel.textContent = 'Cloudflare Account ID або Base URL';
                    if (urlInput) {
                        urlInput.placeholder = 'наприклад: 01a23b45c678... або повний URL';
                        urlInput.required = false;
                    }
                } else if (newProvider.value === 'custom') {
                    if (urlLabel) urlLabel.textContent = 'Base URL власного API';
                    if (urlInput) {
                        urlInput.placeholder = 'http://localhost:11434/v1';
                        urlInput.required = true;
                    }
                }
            };
            newProvider.addEventListener('change', toggleUrl); toggleUrl();
        }
        const provider = document.getElementById('new_model_connection');
        const catalogSource = document.getElementById('ai-provider-catalog-data');
        if (provider && catalogSource) {
            const catalog = JSON.parse(catalogSource.textContent);
            function selectProvider(clear) {
                const selected = provider.options[provider.selectedIndex];
                const providerName = selected ? selected.dataset.provider : '';
                if (clear) document.getElementById('new_model_name').value = '';
                const suggestions = document.getElementById('ai-model-suggestions');
                if (suggestions) {
                    suggestions.replaceChildren();
                    const group = catalog.find(g => g.provider === providerName);
                    (group ? group.models : []).forEach(model => {
                        const option = document.createElement('option');
                        option.value = model.name;
                        suggestions.append(option);
                    });
                }
            }
            provider.addEventListener('change', () => selectProvider(true));
            selectProvider(false);
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
