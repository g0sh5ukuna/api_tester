"""Example DRF views. Bug on purpose: ReportView.post crashes on an empty body."""

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from rest_framework import serializers, viewsets
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Product


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name", "price", "in_stock"]


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer


@api_view(["GET"])
def ping(request):
    return Response({"pong": True})


class ReportSerializer(serializers.Serializer):
    title = serializers.CharField()
    quarter = serializers.IntegerField(min_value=1, max_value=4)


class ReportView(APIView):
    serializer_class = ReportSerializer

    def get(self, request, year):
        return Response({"year": year, "products": Product.objects.count()})

    def post(self, request, year):
        return Response({"title": request.data["title"].upper(), "year": year}, status=201)


@require_GET
def legacy(request):
    return JsonResponse({"legacy": True})
