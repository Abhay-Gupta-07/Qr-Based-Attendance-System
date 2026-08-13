from django.urls import path
from . import views

urlpatterns = [
    path('holidays/', views.holidays, name='holidays'),
    path('edit-holiday/<int:id>/', views.edit_holiday, name='edit_holiday'),
]