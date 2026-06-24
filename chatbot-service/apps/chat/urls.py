from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("", views.ChatListView.as_view(), name="list"),
    path("new/", views.ChatCreateView.as_view(), name="new"),
    path("<uuid:chat_id>/", views.ChatDetailView.as_view(), name="detail"),
    path("<uuid:chat_id>/archive/", views.ChatArchiveView.as_view(), name="archive"),
    path("<uuid:chat_id>/compact/", views.ChatCompactView.as_view(), name="compact"),
    path("<uuid:chat_id>/lora/", views.ChatLoraView.as_view(), name="lora"),
    path("<uuid:chat_id>/share/", views.ChatShareView.as_view(), name="share"),
    path("<uuid:chat_id>/share/json/", views.ChatShareJsonView.as_view(), name="share-json"),
    path("<uuid:chat_id>/stream/", views.ChatStreamView.as_view(), name="stream"),
    path("<uuid:chat_id>/cancel/", views.ChatCancelStreamView.as_view(), name="cancel"),
    path("<uuid:chat_id>/attachment/<uuid:msg_id>/<path:filename>", views.ChatAttachmentView.as_view(), name="attachment"),
    path("<uuid:chat_id>/attachment/<uuid:msg_id>/<int:index>/ingest/", views.ChatAttachmentIngestView.as_view(), name="attachment-ingest"),
]
