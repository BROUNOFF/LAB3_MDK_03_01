"""Демо-данные: группы, пользователи, смена, приборы, склад, генераторы."""
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.utils import timezone

from station.models import (Employee, Generator, Instrument, Shift, ShiftMember, StockItem)
from station.permissions import ROLE_GROUPS
from station.services import ObservationService


class Command(BaseCommand):
    help = "Создаёт демонстрационные данные (пароль всех пользователей: demo12345)"

    def handle(self, *args, **opts):
        today = timezone.localdate()
        groups = {k: Group.objects.get_or_create(name=n)[0] for k, n in ROLE_GROUPS.items()}
        people = [  # username, ФИО, роль, специальность
            ("chief", "Орлов Сергей Петрович", "manager", None),
            ("meteo", "Волкова Анна Игоревна", "specialist", "meteorologist"),
            ("glacio", "Белов Игорь Николаевич", "specialist", "glaciologist"),
            ("radio", "Морозов Артём Сергеевич", "specialist", "radio_operator"),
            ("mech", "Кузнецов Павел Андреевич", "mechanic", "mechanic"),
            ("doc", "Лебедева Мария Олеговна", "doctor", "doctor"),
        ]
        users = {}
        for username, name, role, spec in people:
            u, created = User.objects.get_or_create(username=username, defaults={"first_name": name})
            if created:
                u.set_password("demo12345")
                u.save()
            u.groups.add(groups[role])
            users[username] = u
            if spec:
                Employee.objects.get_or_create(user=u, defaults={"full_name": name, "specialty": spec})
        shift, _ = Shift.objects.get_or_create(name="Ротация 2026-1", defaults={
            "start_date": today - timedelta(days=20), "end_date": today + timedelta(days=60)})
        for e in Employee.objects.all():
            ShiftMember.objects.get_or_create(shift=shift, employee=e, defaults={
                "qualification": "senior", "arrival_date": shift.start_date})
        specs = [("Термометр ТМ-1", "TM-001", "air_temp", 10), ("Гигрометр ГМ-2", "GM-002", "humidity", 30),
                 ("Анемометр АМ-3", "AM-003", "wind_speed", 20), ("Флюгер ФЛ-4", "FL-004", "wind_dir", 400),
                 ("Снегомерная рейка СР-5", "SR-005", "snow_depth", 5),
                 ("Солемер СЛ-6", "SL-006", "salinity", None)]  # None — не калибровался (заблокирован)
        for name, serial, kind, days_ago in specs:
            Instrument.objects.get_or_create(serial=serial, defaults={
                "name": name, "measures": kind,
                "last_calibration": today - timedelta(days=days_ago) if days_ago is not None else None})
        for name, cat, unit, qty, mn, tg in [
                ("Дизельное топливо", "fuel", "л", 4200, 3000, 9000), ("Крупы", "food", "кг", 180, 100, 400),
                ("Консервы", "food", "кг", 90, 120, 350), ("Антибиотики", "medicine", "уп.", 40, 25, 80)]:
            StockItem.objects.get_or_create(name=name, defaults={
                "category": cat, "unit": unit, "quantity": qty, "min_quantity": mn, "target_quantity": tg})
        for name, hours, last in [("ДГ-1", 1180, 1000), ("ДГ-2", 640, 500)]:
            Generator.objects.get_or_create(name=name, defaults={"motohours": hours, "last_service_motohours": last})
        tm = Instrument.objects.get(serial="TM-001")
        if not tm.observation_set.exists():
            for h in range(6):
                ObservationService.record(operator=users["meteo"], obs_type="air_temp", instrument=tm,
                                          value=-18 - h * 0.7, observed_at=timezone.now() - timedelta(hours=6 - h))
        self.stdout.write(self.style.SUCCESS("Готово. Логины: chief, meteo, glacio, radio, mech, doc / demo12345"))
