from django.contrib import admin
from . import models

for m in (models.Employee, models.Shift, models.ShiftMember, models.DutySlot, models.Instrument,
          models.CalibrationLog, models.Generator, models.Observation, models.ExtremeEvent,
          models.StockItem, models.StockMovement, models.SupplyRequest):
    admin.site.register(m)
