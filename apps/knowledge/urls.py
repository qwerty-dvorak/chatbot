from django.urls import path

from . import views

app_name = "knowledge"

urlpatterns = [
    path("", views.DocumentListView.as_view(), name="list"),
    path("upload/", views.DocumentUploadView.as_view(), name="upload"),
    path("<uuid:pk>/", views.DocumentDetailView.as_view(), name="detail"),
]
