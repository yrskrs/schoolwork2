import hashlib
import io
import uuid
from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.db import transaction
from django.db.models import Count, F
from django.http import HttpResponse, HttpResponseForbidden, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_GET
from .models import TailscaleConnection, AccessLink, AccessVisitor, AccessLinkEvent
from .access_forms import AccessLinkForm, ConnectionForm, PublicAccessModeForm
from .access_control import COOKIE, SALT
from .views import teacher_required
from . import tailscale_service

BROWSER_COOKIE = 'schoolnet_access_browser'


def owned_links(request):
    return AccessLink.objects.filter(teacher=request.user.teacher_profile)


@teacher_required
@never_cache
@require_http_methods(['GET', 'POST'])
def access_links(request):
    teacher = request.user.teacher_profile
    connection = TailscaleConnection.objects.filter(pk=1).first() or TailscaleConnection()
    mode_form = PublicAccessModeForm(instance=connection)
    if request.method == 'POST' and request.POST.get('action') == 'access_mode':
        if not request.user.is_superuser:
            return HttpResponseForbidden('Загальний режим доступу змінює адміністратор.')
        mode_form = PublicAccessModeForm(request.POST, instance=connection)
        if mode_form.is_valid():
            # Update only this switch; do not overwrite a concurrently saved connection.
            if connection.pk:
                TailscaleConnection.objects.filter(pk=1).update(allow_without_link=mode_form.cleaned_data['allow_without_link'], updated_at=timezone.now())
            else:
                mode_form.save()
            AccessLinkEvent.objects.create(actor=request.user, action='public_open' if connection.allow_without_link else 'public_protected')
            messages.success(request, 'Режим доступу збережено.')
            return redirect('access_links')
    edit_id = request.POST.get('link_id') if request.method == 'POST' else request.GET.get('edit')
    if edit_id and (not edit_id.isdigit() or len(edit_id) > 18):
        raise Http404
    instance = get_object_or_404(owned_links(request), pk=edit_id) if edit_id else None
    form = AccessLinkForm(teacher=teacher, instance=instance)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'revoke' and instance:
            owned_links(request).filter(pk=instance.pk, revoked_at__isnull=True).update(revoked_at=timezone.now())
            AccessLinkEvent.objects.create(link=instance, actor=request.user, action='revoke')
            messages.success(request, 'Посилання скасовано. Доступ у відкритих браузерах припинено.')
            return redirect('access_links')
        if action == 'save':
            form = AccessLinkForm(request.POST, teacher=teacher, instance=instance)
            if form.is_valid():
                link = form.save(commit=False)
                link.teacher = teacher
                if instance:
                    owned_links(request).filter(pk=link.pk).update(**{key: getattr(link, key) for key in form.Meta.fields})
                else:
                    link.save()
                AccessLinkEvent.objects.create(link=link, actor=request.user, action='edit' if instance else 'create')
                messages.success(request, 'Посилання збережено.')
                return redirect('access_links')
    links = owned_links(request).select_related('class_group', 'student__class_group', 'assignment').annotate(browser_count=Count('browsers')).order_by('-created_at', '-pk')
    for link in links:
        link.public_address = connection.public_url + reverse('access_enter', args=[link.token]) if connection.public_url else ''
    return render(request, 'feed/access_links.html', {'teacher': teacher, 'form': form,
        'editing': instance, 'links': links, 'connection': connection, 'mode_form': mode_form})


@teacher_required
@never_cache
@require_http_methods(['GET', 'POST'])
def tailscale_settings(request):
    connection = TailscaleConnection.objects.filter(pk=1).first() or TailscaleConnection()
    form = ConnectionForm(instance=connection)
    error = ''
    state = None
    if request.method == 'POST':
        if not request.user.is_superuser:
            return HttpResponseForbidden('Підключенням сервера керує адміністратор.')
        action = request.POST.get('action')
        try:
            if action == 'save':
                form = ConnectionForm(request.POST, instance=connection)
                if form.is_valid():
                    if connection.pk:
                        TailscaleConnection.objects.filter(pk=1).update(**form.cleaned_data, updated_at=timezone.now())
                    else:
                        form.save()
                    AccessLinkEvent.objects.create(actor=request.user, action='tailscale_save')
                    messages.success(request, 'Налаштування збережено. Публікація тунелю керується окремими кнопками.')
                    return redirect('tailscale_settings')
            elif action == 'status':
                state = tailscale_service.status()
            elif action in ('start', 'stop'):
                tailscale_service.set_funnel(connection, action == 'start')
                AccessLinkEvent.objects.create(actor=request.user, action='funnel_' + action)
                messages.success(request, 'Команду виконано. Перевірте публічну адресу в окремому браузері.')
                return redirect('tailscale_settings')
        except tailscale_service.TailscaleError as exc:
            error = str(exc)
    return render(request, 'feed/tailscale_settings.html', {'teacher': request.user.teacher_profile,
        'active_tab': 'tailscale', 'form': form, 'connection': connection, 'error': error, 'state': state,
        'tailscale_target_port': settings.SCHOOLNET_TAILSCALE_TARGET_PORT})


@never_cache
@require_http_methods(['GET', 'HEAD', 'POST'])
def access_enter(request, token):
    connection = TailscaleConnection.objects.filter(pk=1).first()
    link = get_object_or_404(AccessLink.objects.select_related('class_group', 'student__class_group', 'assignment'), token=token)
    if not connection or not connection.enabled or not link.active:
        return render(request, 'feed/access_required.html', {'disabled': not connection or not connection.enabled}, status=403)
    if request.method != 'POST':
        # Link unfurlers, HEAD and crawlers never grant access or increment counters.
        return render(request, 'feed/access_enter.html', {'link': link})
    browser = request.get_signed_cookie(BROWSER_COOKIE, default='', salt=SALT)
    try:
        uuid.UUID(browser)
    except (ValueError, TypeError, AttributeError):
        browser = str(uuid.uuid4())
    with transaction.atomic():
        # Serialize revocation/edit with the exchange and count accepted exchanges only.
        link = AccessLink.objects.select_for_update(of=('self',)).select_related('class_group', 'student__class_group', 'assignment').get(pk=link.pk)
        if not link.active:
            return render(request, 'feed/access_required.html', status=403)
        AccessVisitor.objects.get_or_create(link=link, browser_hash=hashlib.sha256(browser.encode()).hexdigest())
        AccessLink.objects.filter(pk=link.pk).update(visits=F('visits') + 1, last_used_at=timezone.now())
    request.session.pop('selected_class', None)
    request.session.pop('my_submission_ids', None)
    request.session.pop('last_submission_id', None)
    request.session.pop('submission_duplicate_warning', None)
    request.session.pop('submission_duplicate_type', None)
    if link.restricted_class_id:
        request.session['selected_class'] = link.restricted_class_id
    destination = reverse('assignment_detail', args=[link.assignment_id]) if link.kind == 'assignment' else reverse('index')
    response = redirect(destination)
    response.set_cookie(COOKIE, signing.dumps({'link': link.pk}, salt=SALT), max_age=60 * 60 * 24 * 365,
                        secure=True, httponly=True, samesite='Lax')
    response.set_signed_cookie(BROWSER_COOKIE, browser, salt=SALT, max_age=60 * 60 * 24 * 365,
                              secure=True, httponly=True, samesite='Lax')
    return response


@teacher_required
@never_cache
@require_GET
def access_qr(request, pk):
    import qrcode
    import qrcode.image.svg
    link = get_object_or_404(owned_links(request), pk=pk)
    connection = TailscaleConnection.objects.filter(pk=1).first()
    if not connection or not connection.public_url:
        raise Http404('Спочатку налаштуйте публічну адресу.')
    image = qrcode.make(connection.public_url + reverse('access_enter', args=[link.token]), image_factory=qrcode.image.svg.SvgPathImage)
    output = io.BytesIO()
    image.save(output)
    response = HttpResponse(output.getvalue(), content_type='image/svg+xml')
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response
