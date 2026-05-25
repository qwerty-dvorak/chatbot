from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("", views.ChatListView.as_view(), name="list"),
    path("new/", views.ChatCreateView.as_view(), name="new"),
    path("<uuid:chat_id>/", views.ChatDetailView.as_view(), name="detail"),
    path("<uuid:chat_id>/archive/", views.ChatArchiveView.as_view(), name="archive"),
    path("<uuid:chat_id>/share/", views.ChatShareView.as_view(), name="share"),
    path("<uuid:chat_id>/stream/", views.ChatStreamView.as_view(), name="stream"),
]
