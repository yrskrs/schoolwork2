/* Cloud browser and durable imports. Provider messages and names are always text. */
(() => {
    'use strict';
    const modal = document.getElementById('cloud-picker-modal');
    if (!modal) return;
    const $ = id => document.getElementById(id);
    let drive = modal.dataset.drive, path = '', folder = 'root', items = [], generation = 0, controller;
    let activeJobs = [], cancelRequested = false, previousFocus, resolvingGeneration = -1;
    const selected = new Map();
    const zoomKey = 'schoolnet-cloud-font:' + modal.dataset.owner;
    let zoom = 100;
    function applyZoom(value) {
        zoom = Math.max(100, Math.min(150, value));
        modal.style.setProperty('--cloud-font-size', (16 * zoom / 100) + 'px');
        $('cloud-font-value').textContent = zoom + '%';
        $('cloud-font-smaller').disabled = zoom === 100;
        $('cloud-font-larger').disabled = zoom === 150;
        try { localStorage.setItem(zoomKey, String(zoom)); } catch (_) {}
    }
    window.changeCloudFont = delta => applyZoom(zoom + delta);
    try { zoom = Number(localStorage.getItem(zoomKey)) || 100; } catch (_) {}
    applyZoom(zoom);
    const csrf = () => document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
    const key = item => item.drive + ':' + (item.path || item.id);
    const bytes = value => value < 1024 ? value + ' Б' : value < 1048576 ? (value/1024).toFixed(1)+' КБ' : (value/1048576).toFixed(1)+' МБ';
    const visible = () => {
        const query = ($('cloud-search-input')?.value || '').toLocaleLowerCase().trim();
        return items.filter(item => item.name.toLocaleLowerCase().includes(query));
    };
    function error(message) {
        $('cloud-error-box').textContent = message;
        $('cloud-error-box').style.display = message ? 'block' : 'none';
    }
    function busy(value) {
        window.cloudImportBusy = value;
        modal.querySelectorAll('.cloud-tab-btn, #google-direct-add, #cloud-select-all-cb').forEach(el => { el.disabled = value; });
        $('cloud-cancel-import').hidden = !value;
        selectionUI();
    }
    async function api(url, data, signal) {
        const response = await fetch(url, {method: data ? 'POST' : 'GET', signal,
            headers: {'X-Requested-With':'XMLHttpRequest', 'X-CSRFToken':csrf(), ...(data ? {'Content-Type':'application/json'} : {})},
            body: data ? JSON.stringify(data) : undefined});
        if (response.redirected || !response.headers.get('Content-Type')?.includes('application/json')) throw new Error('Сесія завершилась. Увійдіть знову та повторіть імпорт.');
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.error || 'Не вдалося виконати запит до хмарного диска.');
        return result;
    }
    function selectionUI() {
        $('cloud-selection-counter').textContent = 'Обрано файлів: ' + selected.size;
        $('btn-attach-cloud-files').disabled = !selected.size || window.cloudImportBusy;
        const files = visible().filter(item => !item.is_dir);
        const count = files.filter(item => selected.has(key(item))).length;
        const all = $('cloud-select-all-cb');
        all.checked = !!files.length && count === files.length;
        all.indeterminate = count > 0 && count < files.length;
        all.disabled = !files.length || window.cloudImportBusy;
    }
    function render() {
        const rows = $('cloud-files-tbody'); rows.replaceChildren();
        const shown = visible();
        $('cloud-files-table').style.display = shown.length ? 'table' : 'none';
        $('cloud-empty-box').style.display = shown.length ? 'none' : 'block';
        $('cloud-empty-title').textContent = ($('cloud-search-input').value || '').trim() ? 'Файлів за цим пошуком немає' : 'У цій папці немає файлів';
        $('cloud-empty-desc').textContent = drive === 'google' ? 'Можна додати файл за посиланням у рядку вище.' : 'Відкрийте іншу папку або оновіть список.';
        for (const item of shown) {
            const row = document.createElement('tr'); row.className = 'cloud-file-row';
            const checkCell = document.createElement('td');
            if (!item.is_dir) {
                const check = document.createElement('input'); check.type='checkbox';
                check.checked=selected.has(key(item)); check.disabled=!!window.cloudImportBusy;
                check.setAttribute('aria-label', 'Обрати ' + item.name);
                check.addEventListener('change', () => { if (check.checked) selected.set(key(item),item); else selected.delete(key(item)); selectionUI(); });
                checkCell.append(check);
            }
            const name = document.createElement('td'); name.style.overflowWrap='anywhere';
            const title = document.createElement(item.is_dir ? 'button' : 'span');
            title.textContent=(item.icon || (item.is_dir?'📁':'📄'))+' '+item.name;
            if (item.is_dir) {
                title.type='button'; title.className='btn btn-secondary btn-sm'; title.disabled=!!window.cloudImportBusy;
                title.addEventListener('click', () => load(item.drive,item.path || '',item.id || 'root'));
            }
            name.append(title);
            const size=document.createElement('td');
            size.textContent=item.is_dir?'':(item.size_display || (item.size_known || item.size ? bytes(item.size) : 'Розмір невідомий'));
            size.title=item.exported && !item.export_size_known ? 'Розмір у Drive може відрізнятися від розміру експортованого файла.' : '';
            const date=document.createElement('td');
            const parsed=new Date(item.modified); date.textContent=item.modified && !Number.isNaN(parsed.getTime()) ? parsed.toLocaleDateString('uk-UA') : '';
            const action=document.createElement('td');
            if (item.is_custom_link) {
                const remove=document.createElement('button'); remove.type='button'; remove.textContent='✕'; remove.title='Видалити посилання';
                remove.disabled=!!window.cloudImportBusy;
                remove.addEventListener('click', () => {items=items.filter(entry=>key(entry)!==key(item));selected.delete(key(item));saveCustom();render();});
                action.append(remove);
            }
            row.append(checkCell,name,size,date,action); rows.append(row);
        }
        selectionUI();
    }
    // Public folder HTML omits sizes. Resolve only visible unknown binaries,
    // using response headers, with two requests at a time and no file buffering.
    async function resolveSizes(mine, signal) {
        if(resolvingGeneration===mine)return;
        resolvingGeneration=mine;
        const queue=visible().filter(item=>!item.is_dir && !item.exported && !item.size_known && !item.size && !item.size_checked);
        try { await Promise.all([0,1].map(async()=>{
            while(queue.length && mine===generation && !signal.aborted){
                const item=queue.shift(); item.size_checked=true;
                try {
                    const data=await api(modal.dataset.resolveUrl,{link:item.file_url||item.id},signal);
                    if(mine!==generation)return;
                    Object.assign(item,{size:data.item.size,size_known:data.item.size_known,size_display:data.item.size_display,exported:data.item.exported,export_size_known:data.item.export_size_known});
                } catch(failure){if(failure.name==='AbortError')return;item.size_display='Розмір недоступний';}
                if(mine===generation)render();
            }
        })); } finally {
            if(resolvingGeneration===mine)resolvingGeneration=-1;
        }
    }
    function storageKey() { return 'schoolnet_gd_files_' + modal.dataset.owner + '_' + folder; }
    function saveCustom() { try { localStorage.setItem(storageKey(),JSON.stringify(items.filter(item=>item.is_custom_link))); } catch (_) {} }
    async function load(nextDrive,nextPath='',nextFolder='root') {
        if (window.cloudImportBusy) return;
        controller?.abort(); controller=new AbortController(); const mine=++generation;
        drive=nextDrive; path=nextPath; folder=nextFolder;
        modal.querySelectorAll('.cloud-tab-btn').forEach(button=>button.classList.toggle('active',button.dataset.drive===drive));
        $('google-quick-link-bar').style.display=drive==='google'?'flex':'none';
        $('google-folder-info-bar').style.display='none'; $('cloud-restricted-notice').style.display='none';
        $('cloud-search-input').value=''; error('');
        $('cloud-loading-spinner').style.display='flex';
        $('cloud-files-table').style.pointerEvents='none'; $('cloud-files-table').style.opacity='.35';
        const params=new URLSearchParams({drive,path:nextPath,folder_id:nextFolder});
        try {
            const data=await api(modal.dataset.browseUrl+'?'+params,undefined,controller.signal);
            if (mine!==generation) return;
            path=data.current_path || ''; folder=data.current_folder_id || 'root';
            items=(data.items || []).map(item=>({...item,drive:nextDrive}));
            if (drive==='google') {
                try {
                    const custom=JSON.parse(localStorage.getItem(storageKey()) || '[]');
                    if (Array.isArray(custom)) for (const entry of custom.slice(0,100)) {
                        if (typeof entry.name==='string' && typeof entry.id==='string' && !items.some(item=>item.id===entry.id)) items.push({...entry,drive:'google'});
                    }
                } catch (_) {}
            }
            const crumbs=$('cloud-breadcrumbs'); crumbs.replaceChildren();
            for (const crumb of data.breadcrumbs || []) {
                const button=document.createElement('button'); button.type='button'; button.className='btn btn-secondary btn-sm'; button.textContent=crumb.name;
                button.addEventListener('click',()=>load(nextDrive,crumb.path || '',crumb.id || 'root')); crumbs.append(button);
            }
            if (data.warning) {$('cloud-download-progress').textContent=data.warning;$('cloud-download-progress').style.display='inline';}
            else $('cloud-download-progress').style.display='none';
            render();
            if(drive==='google')void resolveSizes(mine,controller.signal);
        } catch (failure) {
            if (failure.name!=='AbortError' && mine===generation) {items=[];render();error(failure.message);}
        } finally {
            if (mine===generation) {$('cloud-loading-spinner').style.display='none';$('cloud-files-table').style.pointerEvents='';$('cloud-files-table').style.opacity='1';}
        }
    }
    window.openCloudPickerModal=() => {
        previousFocus=document.activeElement; modal.style.display='flex';document.body.style.overflow='hidden';modal.focus();
        if (!window.cloudImportBusy) {resetProgress();selected.clear();selectionUI();load(drive,path,folder);}
    };
    window.closeCloudPickerModal=() => {
        if (window.cloudImportBusy) return;
        controller?.abort();++generation;modal.style.display='none';document.body.style.overflow='';previousFocus?.focus();
    };
    window.switchCloudDrive=next => {if(!window.cloudImportBusy && next!==drive){selected.clear();load(next,'','root');}};
    window.reloadCurrentCloudFolder=()=>load(drive,path,folder);
    window.filterCloudFilesList=()=>{render();if(drive==='google'&&controller)void resolveSizes(generation,controller.signal);};
    window.toggleSelectAllCloudFiles=checked => {
        if(window.cloudImportBusy)return;
        for(const item of visible().filter(item=>!item.is_dir)){if(checked)selected.set(key(item),item);else selected.delete(key(item));} render();
    };
    window.addGoogleDirectLink=async()=>{
        if(window.cloudImportBusy || drive!=='google')return;
        const input=$('google-direct-link-input'),link=input.value.trim();if(!link)return;
        const ownGeneration=generation,ownFolder=folder; $('google-direct-add').disabled=true;error('');
        try {
            const result=await api(modal.dataset.resolveUrl,{link});
            if(ownGeneration!==generation || ownFolder!==folder || drive!=='google')return;
            const item={...result.item,drive:'google',is_custom_link:true};
            items=items.filter(other=>other.id!==item.id);items.unshift(item);selected.set(key(item),item);
            input.value='';saveCustom();render();
        } catch(failure){error(failure.message);}
        finally{$('google-direct-add').disabled=!!window.cloudImportBusy;}
    };
    function progress(jobs){
        const ready=jobs.filter(job=>job.status==='ready'||job.status==='attached').length;
        const running=jobs.find(job=>job.status==='running');
        const fraction=jobs.reduce((sum,job)=>sum+(job.status==='ready'||job.status==='attached'?1:job.status==='running'&&job.total?Math.min(.95,job.received/job.total*.95):0),0);
        $('cloud-transfer-panel').hidden=false;
        $('cloud-transfer-bar').value=jobs.length?Math.floor(fraction/jobs.length*100):0;
        $('cloud-transfer-label').textContent='Перевірено '+ready+' із '+jobs.length+' файлів';
        $('cloud-transfer-detail').textContent=running?(running.received===running.total?'Перевірка файла: ':'Завантаження: ')+running.name+' · '+bytes(running.received)+(running.total?' / '+bytes(running.total):''):'Очікування обробки…';
    }
    function resetProgress(){
        $('cloud-transfer-panel').hidden=true;
        $('cloud-transfer-bar').value=0;
        $('cloud-transfer-label').textContent='Підготовка імпорту…';
        $('cloud-transfer-detail').textContent='';
    }
    async function follow(ids){
        activeJobs=ids;window.cloudFormState?.set('pending',ids);
        let complete=false;
        try {
            while(true){
                const result=await api(modal.dataset.importsUrl+'?'+new URLSearchParams({ids:ids.join(',')}));
                progress(result.imports);
                if(!result.worker_available && result.imports.some(job=>job.status==='queued')) $('cloud-transfer-detail').textContent='Обробник імпорту недоступний. Запит збережено; можна зупинити імпорт або повторити пізніше.';
                if(result.imports.every(job=>['ready','failed','cancelled','attached'].includes(job.status))){
                    window.cloudFormState?.set('pending',[]);
                    const ready=result.imports.filter(job=>job.status==='ready');
                    window.addCloudImportsToStore(ready);
                    const failed=result.imports.filter(job=>job.status==='failed');
                    $('cloud-transfer-bar').value=100;
                    $('cloud-transfer-label').textContent='Додано та перевірено: '+ready.length+' із '+ids.length;
                    $('cloud-transfer-detail').textContent=cancelRequested?'Імпорт зупинено.':'Перевірені файли додано до матеріалів нижче. Збережіть завдання.';
                    if(failed.length)error(failed.map(job=>job.name+': '+job.error).join('\n'));
                    selected.clear();render();
                    complete=ready.length===ids.length&&!cancelRequested;
                    return true;
                }
                await new Promise(resolve=>setTimeout(resolve,600));
            }
        } catch(failure){error(failure.message+' Готові імпорти зберігаються на сервері; можна оновити сторінку.');return false;}
        finally{
            activeJobs=[];busy(false);
            if(complete){
                window.closeCloudPickerModal();resetProgress();
                const status=$('cloud-materials-status');status?.focus();status?.scrollIntoView({block:'center',behavior:'smooth'});
            }
        }
    }
    window.attachSelectedCloudFiles=async()=>{
        if(!selected.size||window.cloudImportBusy)return;
        if(typeof window.addCloudImportsToStore!=='function'){error('Форма файлів ще не готова. Оновіть сторінку.');return;}
        busy(true);cancelRequested=false;error('');progress([]);
        const payload=[...selected.values()].map(item=>{
            if(!item.request_key)item.request_key=Date.now().toString(36)+'-'+Math.random().toString(36).slice(2)+'-'+Math.random().toString(36).slice(2);
            return {drive:item.drive,source:item.drive==='nextcloud'?item.path:(item.file_url||item.id),name:item.name,request_key:item.request_key};
        });
        let settled=false;
        try{const result=await api(modal.dataset.importsUrl,{items:payload});activeJobs=result.imports.map(job=>job.id);if(cancelRequested)await window.cancelCloudImport();settled=await follow(activeJobs);}
        catch(failure){error(failure.message);busy(false);}
        finally{if(settled)for(const item of selected.values())delete item.request_key;}
    };
    window.cancelCloudImport=async()=>{
        cancelRequested=true;$('cloud-cancel-import').disabled=true;
        try{await Promise.all(activeJobs.map(id=>api(modal.dataset.importsUrl+id+'/cancel/',{})));}
        catch(failure){error(failure.message);}
        finally{$('cloud-cancel-import').disabled=false;}
    };
    modal.addEventListener('click',event=>{if(event.target===modal)window.closeCloudPickerModal();});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&modal.style.display==='flex')window.closeCloudPickerModal();});
    async function resume(){
        const restore=window.cloudFormRestoreIds||[],pending=window.cloudFormState?.get('pending')||[];
        if(!restore.length&&!pending.length)return;
        busy(true);
        try{
            if(restore.length){
                const result=await api(modal.dataset.importsUrl+'?'+new URLSearchParams({ids:restore.join(',')}));
                window.addCloudImportsToStore(result.imports.filter(job=>job.status==='ready'));
            }
            if(pending.length){window.openCloudPickerModal();await follow(pending);}
        } catch(failure){error(failure.message);}
        finally{busy(false);}
    }
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',resume);else resume();
})();
