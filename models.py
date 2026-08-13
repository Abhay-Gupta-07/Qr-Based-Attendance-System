from django.db import models

class Holiday(models.Model):
    holiday_date = models.DateField()
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name