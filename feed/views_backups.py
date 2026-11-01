import re
import time
from django.http import FileResponse, Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.contrib import messages
from django.middleware.csrf import get_token
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from .views import teacher_required
from .cloud_backups import Adapter, state
from cloud_backup.control import begin_oauth, finish_oauth, page_data, save_config, enqueue


def scope_of(request):
    scope = request.GET.get('scope','personal')
    if scope not in ('personal','server'): raise ValueError('Некоректний обсяг')
    Adapter().authorize(Adapter.owner(request.user.teacher_profile),scope)
    return scope


@teacher_required
@never_cache
@require_http_methods(['GET','POST'])
def backups(request):
    from urllib.parse import urlencode
    try: scope = scope_of(request)
    except ValueError as error: return HttpResponseForbidden(str(error))
    owner = Adapter.owner(request.user.teacher_profile)
    store = state()
    if request.method == 'POST':
        try:
            operation = request.POST.get('action','save')
            if operation == 'save':
                form = request.POST.copy()
                if request.POST.get('reuse_nextcloud'):
                    existing = request.user.teacher_profile.get_cloud_storage()
                    if not existing.has_nextcloud():
                        raise ValueError('У профілі ще немає підключеного Nextcloud. Укажіть адресу, користувача й пароль додатка')
                    form['url'] = form.get('url') or existing.nextcloud_url.split('/remote.php/',1)[0]
                    form['username'] = form.get('username') or existing.nextcloud_username
                    form['app_password'] = form.get('app_password') or existing.nextcloud_password
                save_config(store,owner,scope,form)
                messages.success(request,'Налаштування збережено. Збережіть ідентифікатор сховища поза сервером.')
            elif operation == 'read':
                store.mark_seen(owner)
                from .models import SystemNotification
                SystemNotification.objects.filter(teacher=request.user.teacher_profile,action_url='/teacher/backups/').update(is_read=True)
                messages.success(request,'Сповіщення позначено прочитаними')
            elif operation == 'oauth':
                uri = request.build_absolute_uri(reverse('backup_google_callback'))+'?scope='+scope
                return redirect(begin_oauth(store,owner,scope,uri))
            else:
                enqueue(store,owner,scope,operation,request.POST)
                messages.success(request,'Операцію додано до черги. Підсумок з’явиться у центрі сповіщень.')
        except ValueError as error: messages.error(request,str(error))
        return redirect(reverse('cloud_backups')+'?'+urlencode({'scope':scope}))
    return render(request,'feed/backups.html', {**page_data(store,owner,scope),
        'teacher':request.user.teacher_profile,'backup_csrf':get_token(request),
        'backup_admin':request.user.is_superuser,'backup_base':'/teacher/backups', 'active_tab':'backups'})


@teacher_required
@never_cache
def callback(request):
    from urllib.parse import urlencode
    try:
        scope = scope_of(request)
        if request.GET.get('error'): raise ValueError('Доступ Google не надано')
        finish_oauth(state(),Adapter.owner(request.user.teacher_profile),scope,request.GET.get('state'),request.GET.get('code'))
        messages.success(request,'Google Drive підключено для резервування')
    except ValueError as error:
        messages.error(request,str(error)); scope = 'personal'
    return redirect(reverse('cloud_backups')+'?'+urlencode({'scope':scope}))


@teacher_required
@never_cache
def status(request):
    try: scope = scope_of(request)
    except ValueError as error: return JsonResponse({'error':str(error)},status=403)
    return JsonResponse(page_data(state(),Adapter.owner(request.user.teacher_profile),scope))


@teacher_required
@never_cache
def download(request, job):
    if not re.fullmatch('[a-f0-9]{32}',job): raise Http404
    store = state()
    for record in store.history(Adapter.owner(request.user.teacher_profile)):
        if record['id'] == job and record.get('scope') == 'server':
            try: Adapter().authorize(Adapter.owner(request.user.teacher_profile),'server')
            except ValueError: return HttpResponseForbidden()
    if not any(r['id']==job and r['kind']=='download' and r['status']=='completed' for r in store.history(Adapter.owner(request.user.teacher_profile))):
        raise Http404
    path = store.root/(job+'.zip')
    if not path.is_file() or path.stat().st_mtime<time.time()-86400: raise Http404('Архів уже видалено. Підготуйте завантаження повторно')
    return FileResponse(path.open('rb'),as_attachment=True,filename='schoolwork-backup.zip',content_type='application/zip')
