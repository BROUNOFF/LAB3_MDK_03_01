"""Тесты ключевых сценариев."""
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (Employee, Generator, Instrument, Shift, ShiftMember, StockItem, SupplyRequest,
                     ExtremeEvent)
from .permissions import ROLE_GROUPS
from .services import (GeneratorService, InstrumentService, ObservationService, ReportService,
                       StockService, SupplyService)


def make_user(name, role):
    u = User.objects.create_user(name, password="pw")
    u.groups.add(Group.objects.get_or_create(name=ROLE_GROUPS[role])[0])
    return u


class ObservationBlocking(TestCase):
    def setUp(self):
        self.u = make_user("meteo", "specialist")
        today = timezone.localdate()
        self.ok = Instrument.objects.create(name="T", serial="1", measures="air_temp",
                                            last_calibration=today - timedelta(days=10))
        self.old = Instrument.objects.create(name="Old", serial="2", measures="air_temp",
                                             last_calibration=today - timedelta(days=400))
        self.never = Instrument.objects.create(name="New", serial="3", measures="air_temp")

    def test_calibrated_instrument_accepts(self):
        o = ObservationService.record(operator=self.u, obs_type="air_temp", instrument=self.ok, value=-20)
        self.assertEqual(o.unit, "°C")

    def test_expired_and_never_calibrated_blocked(self):
        for inst in (self.old, self.never):
            with self.assertRaises(ValidationError):
                ObservationService.record(operator=self.u, obs_type="air_temp", instrument=inst, value=-5)

    def test_recalibration_unblocks(self):
        InstrumentService.calibrate(self.old, self.u)
        self.old.refresh_from_db()
        ObservationService.record(operator=self.u, obs_type="air_temp", instrument=self.old, value=-5)

    def test_wrong_type_and_range(self):
        with self.assertRaises(ValidationError):
            ObservationService.record(operator=self.u, obs_type="humidity", instrument=self.ok, value=50)
        with self.assertRaises(ValidationError):
            ObservationService.record(operator=self.u, obs_type="air_temp", instrument=self.ok, value=-200)


class StockAndSupply(TestCase):
    def setUp(self):
        self.u = make_user("chief", "manager")
        self.item = StockItem.objects.create(name="ДТ", category="fuel", unit="л", quantity=1000,
                                             min_quantity=500, target_quantity=2000)

    def test_auto_request_once(self):
        self.assertIsNone(StockService.move(self.item.pk, -400, self.u))
        req = StockService.move(self.item.pk, -200, self.u)
        self.assertEqual(req.quantity, 1600)
        self.assertIsNone(StockService.move(self.item.pk, -10, self.u))  # дубль не создаётся
        self.assertEqual(SupplyRequest.objects.count(), 1)

    def test_cannot_overdraw(self):
        with self.assertRaises(ValidationError):
            StockService.move(self.item.pk, -5000, self.u)

    def test_approve_and_receive(self):
        req = StockService.move(self.item.pk, -700, self.u)
        SupplyService.decide(req, self.u, True)
        SupplyService.receive(req, self.u)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 300 + req.quantity)
        self.assertFalse(self.item.below_norm)


class GeneratorService250(TestCase):
    def test_service_threshold(self):
        u = make_user("mech", "mechanic")
        g = Generator.objects.create(name="ДГ")
        GeneratorService.log_hours(g.pk, 249, u)
        g.refresh_from_db()
        self.assertFalse(g.service_due)
        GeneratorService.log_hours(g.pk, 1, u)
        g.refresh_from_db()
        self.assertTrue(g.service_due)
        GeneratorService.service(g.pk, u)
        g.refresh_from_db()
        self.assertEqual(g.hours_since_service, 0)


class AccessAndReports(TestCase):
    def test_roles_enforced(self):
        spec, chief = make_user("s", "specialist"), make_user("c", "manager")
        self.client.force_login(spec)
        self.assertEqual(self.client.get(reverse("reports")).status_code, 403)
        self.assertEqual(self.client.get(reverse("shift_create")).status_code, 403)
        self.client.force_login(chief)
        self.assertEqual(self.client.get(reverse("reports")).status_code, 200)

    def test_anonymous_redirected(self):
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)

    def test_report_end_to_end(self):
        chief, mech = make_user("c", "manager"), make_user("m", "mechanic")
        today = timezone.localdate()
        inst = Instrument.objects.create(name="T", serial="1", measures="air_temp", last_calibration=today)
        ObservationService.record(operator=chief, obs_type="air_temp", instrument=inst, value=-10)
        item = StockItem.objects.create(name="ДТ", category="fuel", unit="л", quantity=1000,
                                        min_quantity=100, target_quantity=2000)
        StockService.move(item.pk, -150, mech)
        ExtremeEvent.objects.create(kind="storm", description="x", measures_taken="y", reported_by=mech)
        shift = Shift.objects.create(name="S", start_date=today, end_date=today + timedelta(days=70))
        e = Employee.objects.create(user=mech, full_name="Механик", specialty="mechanic")
        ShiftMember.objects.create(shift=shift, employee=e, qualification="senior", arrival_date=today)
        r = ReportService.build(today, today, shift)
        self.assertEqual(r["observations_total"], 1)
        self.assertEqual(r["consumption"][0]["total"], 150)
        self.assertEqual(r["events_total"], 1)
        self.assertTrue(next(x for x in r["staffing"] if x["specialty"] == "Механик")["ok"])
        self.assertFalse(next(x for x in r["staffing"] if x["specialty"] == "Врач")["ok"])

    def test_observation_page_blocks_expired(self):
        spec = make_user("s", "specialist")
        inst = Instrument.objects.create(name="X", serial="9", measures="air_temp")
        self.client.force_login(spec)
        resp = self.client.post(reverse("observation_add"), {
            "obs_type": "air_temp", "instrument": inst.pk, "value": -5,
            "observed_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M")})
        self.assertContains(resp, "заблокированы")
