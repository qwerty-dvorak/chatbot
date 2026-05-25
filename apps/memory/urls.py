from django.urls import path

from . import views

app_name = "memory"

urlpatterns = [
    path("", views.MemoryListView.as_view(), name="list"),
    path("settings/", views.MemorySettingsView.as_view(), name="settings"),
]
