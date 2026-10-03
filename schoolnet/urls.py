from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve
from feed.file_serving import public_media

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('feed.urls')),  # Підключаємо маршрути застосунку feed
    re_path(r'^media/(?P<path>.*)$', public_media),
    re_path(r'^static/(?P<path>.*)$', serve, {'document_root': settings.BASE_DIR / 'static'}),
]
