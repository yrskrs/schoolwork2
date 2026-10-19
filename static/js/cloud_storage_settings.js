/* Connection checks never interpret provider messages as HTML. */
(() => {
    'use strict';
    function toggle(prefix,show){
        const form=document.getElementById(prefix==='nc'?'nextcloud-form-container':'google-form-container');
        const actions=document.getElementById(prefix+'-connected-actions');
        if(form)form.style.display=show?'block':'none';if(actions)actions.style.display=show?'none':'flex';
    }
    window.toggleNcEditForm=show=>toggle('nc',show);
    window.toggleGdEditForm=show=>toggle('gd',show);
    async function check(kind,setup){
        const prefix=kind==='nextcloud'?'nc':'gd';
        const box=document.getElementById(prefix+(setup?'-setup-test-result':'-test-result'));
        if(!box)return;box.style.display='block';box.textContent='Перевірка фактичного доступу…';
        const form=document.getElementById(kind==='nextcloud'?'nextcloud-setup-form':'google-setup-form');
        const data=setup&&form?new FormData(form):new FormData();
        if(setup&&kind==='nextcloud')data.set('ignore_ssl',document.getElementById('nc-input-ignore-ssl')?.checked?'1':'0');
        try{
            const response=await fetch('/teacher/cloud/test-'+kind+'/',{method:'POST',body:data,
                headers:{'X-CSRFToken':document.querySelector('[name=csrfmiddlewaretoken]')?.value||'','X-Requested-With':'XMLHttpRequest'}});
            if(response.redirected)throw new Error('Увійдіть до кабінету повторно.');
            const result=await response.json();box.textContent=result.message||'Не вдалося перевірити доступ.';
            box.style.color=result.success?'var(--color-success)':'var(--color-danger)';
        }catch(_){box.textContent='Не вдалося виконати перевірку. Перевірте з’єднання та сесію входу.';box.style.color='var(--color-danger)';}
    }
    window.testNextcloudConnection=()=>check('nextcloud',true);
    window.testSavedNextcloud=()=>check('nextcloud',false);
    window.testGoogleSetupConnection=()=>check('google',true);
    window.testSavedGoogleDrive=()=>check('google',false);
})();
