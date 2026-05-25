from django.urls import path

from . import views

app_name = "tools"

urlpatterns = [
    path("", views.ToolListView.as_view(), name="list"),
    path("calls/<uuid:pk>/", views.ToolCallDetailView.as_view(), name="call-detail"),
]
