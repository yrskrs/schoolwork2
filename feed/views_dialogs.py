"""Role-aware, lazy-loaded public help. No arbitrary template names are accepted."""
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def ui_dialog(request, kind):
    if kind not in ('guide', 'changelog'):
        raise Http404
    response = render(request, 'feed/components/' + kind + '_modal_body.html')
    response['Cache-Control'] = 'private, no-cache'
    response['Vary'] = 'Cookie'
    return response
