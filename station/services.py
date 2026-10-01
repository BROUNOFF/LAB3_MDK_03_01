"""Прикладные сервисы (бизнес-сценарии ИСУПС).

Каждый сервис инкапсулирует один сценарий из IDEF0/use-case диаграмм
и работает транзакционно.
"""
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from .models import (CalibrationLog, Generator, Instrument, MotoLog, Observation, ServiceRecord,
                     ShiftMember, Specialty, StockItem, StockMovement, SupplyRequest,
                     ExtremeEvent, validate_observation, ObservationType)


class ObservationService:
    """Сценарий «Ведение научных наблюдений»."""

    @staticmethod
    def record(*, operator, obs_type, instrument, value, observed_at=None, note=""):
        """Фиксирует измерение; бросает ValidationError, если прибор заблокирован."""
        observed_at = observed_at or timezone.now()
        validate_observation(obs_type, instrument, value, observed_at)
        return Observation.objects.create(obs_type=obs_type, instrument=instrument, value=value,
                                          observed_at=observed_at, operator=operator, note=note)


class InstrumentService:
    """Сценарий «Калибровка приборов»."""

    @staticmethod
    @transaction.atomic
    def calibrate(instrument, user, date=None, passed=True, note=""):
        date = date or timezone.localdate()
        log = CalibrationLog.objects.create(instrument=instrument, calibrated_at=date,
                                            performed_by=user, passed=passed, note=note)
        if passed and (instrument.last_calibration is None or date >= instrument.last_calibration):
            instrument.last_calibration = date
            instrument.save(update_fields=["last_calibration"])
        return log


class StockService:
    """Сценарий «Складской учёт» + автоформирование заявок при падении ниже нормы."""

    @classmethod
    @transaction.atomic
    def move(cls, item_id, delta, user, comment=""):
        """Расход (delta<0) или приход (delta>0). Возвращает автозаявку, если создана."""
        item = StockItem.objects.select_for_update().get(pk=item_id)
        if delta == 0:
            raise ValidationError("Количество должно быть ненулевым.")
        if item.quantity + delta < 0:
            raise ValidationError(f"Недостаточно запаса: на складе {item.quantity} {item.unit}.")
        item.quantity += delta
        item.save(update_fields=["quantity"])
        StockMovement.objects.create(item=item, delta=delta, user=user, comment=comment)
        return cls.ensure_request(item)

    @staticmethod
    def ensure_request(item):
        """Создаёт заявку, если остаток ниже нормы и открытой заявки нет."""
        if not item.below_norm or item.requests.filter(status__in=SupplyRequest.OPEN).exists():
            return None
        return SupplyRequest.objects.create(item=item, quantity=round(item.target_quantity - item.quantity, 2))


class SupplyService:
    """Утверждение и исполнение заявок."""

    @staticmethod
    @transaction.atomic
    def decide(req, user, approve):
        if req.status != SupplyRequest.Status.PENDING:
            raise ValidationError("Заявка уже обработана.")
        req.status = SupplyRequest.Status.APPROVED if approve else SupplyRequest.Status.REJECTED
        req.decided_by, req.decided_at = user, timezone.now()
        req.save()
        return req

    @staticmethod
    @transaction.atomic
    def receive(req, user):
        if req.status != SupplyRequest.Status.APPROVED:
            raise ValidationError("Принять можно только утверждённую заявку.")
        StockService.move(req.item_id, req.quantity, user, f"Поставка по заявке №{req.pk}")
        req.status = SupplyRequest.Status.FULFILLED
        req.save(update_fields=["status"])
        return req


class GeneratorService:
    """Сценарий «Мониторинг дизель-генераторов»."""

    @staticmethod
    @transaction.atomic
    def log_hours(generator_id, hours, user):
        if hours <= 0:
            raise ValidationError("Наработка должна быть положительной.")
        gen = Generator.objects.select_for_update().get(pk=generator_id)
        gen.motohours += hours
        gen.save(update_fields=["motohours"])
        MotoLog.objects.create(generator=gen, hours=hours, recorded_by=user)
        return gen

    @staticmethod
    @transaction.atomic
    def service(generator_id, user, note=""):
        gen = Generator.objects.select_for_update().get(pk=generator_id)
        gen.last_service_motohours = gen.motohours
        gen.save(update_fields=["last_service_motohours"])
        ServiceRecord.objects.create(generator=gen, motohours=gen.motohours, performed_by=user, note=note)
        return gen


class ReportService:
    """Отчёты руководителя станции."""

    @staticmethod
    def staffing(shift):
        """Укомплектованность смены по специальностям (учитываются годные к дежурству)."""
        have = Counter(m.employee.specialty for m in
                       ShiftMember.objects.filter(shift=shift).exclude(health="unfit").select_related("employee"))
        rows = []
        for key, label in Specialty.choices:
            need = settings.SHIFT_REQUIRED_STAFFING.get(key, 0)
            rows.append({"specialty": label, "need": need, "have": have.get(key, 0),
                         "ok": have.get(key, 0) >= need})
        return rows

    @classmethod
    def build(cls, date_from, date_to, shift=None):
        start = timezone.make_aware(timezone.datetime.combine(date_from, timezone.datetime.min.time()))
        end = timezone.make_aware(timezone.datetime.combine(date_to + timedelta(days=1),
                                                            timezone.datetime.min.time()))
        labels = dict(ObservationType.choices)
        obs = (Observation.objects.filter(observed_at__gte=start, observed_at__lt=end)
               .values("obs_type").annotate(n=Count("id")).order_by("obs_type"))
        cons = (StockMovement.objects.filter(moved_at__gte=start, moved_at__lt=end, delta__lt=0)
                .values("item__name", "item__unit").annotate(total=Sum("delta")).order_by("item__name"))
        events = (ExtremeEvent.objects.filter(occurred_at__gte=start, occurred_at__lt=end)
                  .values("kind").annotate(n=Count("id")))
        kinds = dict(ExtremeEvent.Kind.choices)
        return {
            "observations": [{"label": labels[r["obs_type"]], "n": r["n"]} for r in obs],
            "observations_total": sum(r["n"] for r in obs),
            "consumption": [{"name": r["item__name"], "unit": r["item__unit"], "total": -r["total"]} for r in cons],
            "events": [{"label": kinds[r["kind"]], "n": r["n"]} for r in events],
            "events_total": sum(r["n"] for r in events),
            "staffing": cls.staffing(shift) if shift else None,
        }
