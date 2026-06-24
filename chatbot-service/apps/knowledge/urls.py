from django.urls import path

from . import views

app_name = "knowledge"

urlpatterns = [
    path("", views.DocumentListView.as_view(), name="list"),
    path("upload/", views.DocumentUploadView.as_view(), name="upload"),
    path("queue/", views.IngestionQueueView.as_view(), name="queue"),
    path("queue/data/", views.IngestionQueueJsonView.as_view(), name="queue-data"),
    path("queue/<uuid:job_id>/", views.IngestionQueueJsonView.as_view(), name="queue-action"),
    path("<uuid:pk>/", views.DocumentDetailView.as_view(), name="detail"),
    path("<uuid:pk>/status/", views.DocumentStatusJsonView.as_view(), name="status"),
    path("<uuid:pk>/delete/", views.DocumentDeleteView.as_view(), name="delete"),
]
