from django.shortcuts import render, redirect, get_object_or_404
from .models import Holiday

def holidays(request):
    if request.method == 'POST':
        holiday_date = request.POST.get('holiday_date')
        name = request.POST.get('name')

        Holiday.objects.create(
            holiday_date=holiday_date,
            name=name
        )
        return redirect('holidays')

    holiday_list = Holiday.objects.all().order_by('holiday_date')
    return render(request, 'holidays.html', {'holidays': holiday_list})


def edit_holiday(request, id):
    holiday = get_object_or_404(Holiday, id=id)

    if request.method == 'POST':
        holiday.holiday_date = request.POST.get('holiday_date')
        holiday.name = request.POST.get('name')
        holiday.save()
        return redirect('holidays')

    return render(request, 'edit_holiday.html', {'holiday': holiday})