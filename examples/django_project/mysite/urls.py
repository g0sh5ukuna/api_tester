from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from shop import views

router = DefaultRouter()
router.register("products", views.ProductViewSet)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
    path("api/ping/", views.ping),
    path("api/report/<int:year>/", views.ReportView.as_view()),
    path("legacy/", views.legacy),
    path("account/", views.account),
]
