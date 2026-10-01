"""Доменные модели ИСУПС (слой Model).

Бизнес-инварианты (блокировка измерений по калибровке, нормы запасов,
порог ТО по моточасам) выражены методами моделей; сценарии,
затрагивающие несколько сущностей, вынесены в ``services``.
"""
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL


# --------------------------------------------------------------- справочники
class Specialty(models.TextChoices):
    GLACIOLOGIST = "glaciologist", "Гляциолог"
    METEOROLOGIST = "meteorologist", "Метеоролог"
    RADIO_OPERATOR = "radio_operator", "Радист"
    MECHANIC = "mechanic", "Механик"
    DOCTOR = "doctor", "Врач"


class Qualification(models.TextChoices):
    JUNIOR = "junior", "Начальный"
    MIDDLE = "middle", "Средний"
    SENIOR = "senior", "Высокий"
    EXPERT = "expert", "Эксперт"


class Health(models.TextChoices):
    GOOD = "good", "Здоров"
    SATISFACTORY = "satisfactory", "Удовлетворительно"
    LIMITED = "limited", "Ограниченно годен"
    UNFIT = "unfit", "Не годен к дежурству"


class ObservationType(models.TextChoices):
    AIR_TEMP = "air_temp", "Температура воздуха"
    HUMIDITY = "humidity", "Влажность воздуха"
    WIND_SPEED = "wind_speed", "Скорость ветра"
    WIND_DIR = "wind_dir", "Направление ветра"
    SNOW_DEPTH = "snow_depth", "Толщина снежного покрова"
    WATER_TEMP = "water_temp", "Температура морской воды"
    SALINITY = "salinity", "Солёность морской воды"
    ICE_RECON = "ice_recon", "Ледовая разведка"


UNITS = {
    "air_temp": "°C", "humidity": "%", "wind_speed": "м/с", "wind_dir": "°",
    "snow_depth": "см", "water_temp": "°C", "salinity": "‰", "ice_recon": "% сплочённости",
}
RANGES = {  # физически допустимые значения
    "air_temp": (-90, 40), "humidity": (0, 100), "wind_speed": (0, 100), "wind_dir": (0, 360),
    "snow_depth": (0, 1000), "water_temp": (-3, 30), "salinity": (0, 50), "ice_recon": (0, 100),
}


# ---------------------------------------------------------------- персонал
class Employee(models.Model):
    """Сотрудник станции (профиль пользователя Django)."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="employee")
    full_name = models.CharField("ФИО", max_length=150)
    specialty = models.CharField("Специальность", max_length=20, choices=Specialty.choices)

    class Meta:
        verbose_name, verbose_name_plural = "сотрудник", "сотрудники"
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} ({self.get_specialty_display()})"


class Shift(models.Model):
    """Смена (ротация 2–3 месяца)."""
    name = models.CharField("Название", max_length=80)
    start_date = models.DateField("Начало")
    end_date = models.DateField("Окончание")

    class Meta:
        verbose_name, verbose_name_plural = "смена", "смены"
        ordering = ["-start_date"]

    def clean(self):
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValidationError("Дата окончания раньше даты начала.")
            days = (self.end_date - self.start_date).days
            if not 45 <= days <= 110:
                raise ValidationError("Ротация длится 2–3 месяца (около 60–95 дней).")

    @property
    def is_active(self):
        return self.start_date <= timezone.localdate() <= self.end_date

    def __str__(self):
        return self.name


class ShiftMember(models.Model):
    """Зачисление сотрудника в смену: квалификация, прибытие, здоровье."""
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="members")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="memberships")
    qualification = models.CharField("Квалификация", max_length=10, choices=Qualification.choices)
    arrival_date = models.DateField("Дата прибытия")
    health = models.CharField("Состояние здоровья", max_length=15, choices=Health.choices, default=Health.GOOD)
    health_note = models.CharField("Примечание врача", max_length=200, blank=True)

    class Meta:
        verbose_name, verbose_name_plural = "участник смены", "участники смен"
        constraints = [models.UniqueConstraint(fields=["shift", "employee"], name="uniq_member")]

    def clean(self):
        if self.shift_id and self.arrival_date and self.arrival_date > self.shift.end_date:
            raise ValidationError("Дата прибытия позже окончания смены.")

    def __str__(self):
        return f"{self.employee} — {self.shift}"


class DutySlot(models.Model):
    """Назначение дежурства."""
    class Part(models.TextChoices):
        DAY = "day", "День (08–20)"
        NIGHT = "night", "Ночь (20–08)"

    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="duties")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT)
    date = models.DateField("Дата")
    part = models.CharField("Время суток", max_length=5, choices=Part.choices)

    class Meta:
        verbose_name, verbose_name_plural = "дежурство", "график дежурств"
        ordering = ["date", "part"]
        constraints = [models.UniqueConstraint(fields=["employee", "date", "part"], name="uniq_duty")]

    def clean(self):
        if not (self.shift_id and self.employee_id and self.date):
            return
        member = ShiftMember.objects.filter(shift=self.shift, employee=self.employee).first()
        if member is None:
            raise ValidationError("Сотрудник не зачислен в эту смену.")
        if member.health == Health.UNFIT:
            raise ValidationError("Сотрудник признан непригодным к дежурству.")
        if not self.shift.start_date <= self.date <= self.shift.end_date:
            raise ValidationError("Дата вне периода смены.")


# ------------------------------------------------------------- оборудование
class Instrument(models.Model):
    """Измерительный прибор с периодической калибровкой."""
    name = models.CharField("Название", max_length=100)
    serial = models.CharField("Серийный номер", max_length=50, unique=True)
    measures = models.CharField("Измеряет", max_length=20, choices=ObservationType.choices)
    calibration_interval_days = models.PositiveIntegerField("Интервал калибровки, дн.", default=180)
    last_calibration = models.DateField("Последняя калибровка", null=True, blank=True)

    class Meta:
        verbose_name, verbose_name_plural = "прибор", "приборы"
        ordering = ["name"]

    @property
    def calibrated_until(self):
        if not self.last_calibration:
            return None
        return self.last_calibration + timedelta(days=self.calibration_interval_days)

    def is_calibrated(self, at=None):
        """Прибор годен к измерениям на дату ``at`` (по умолчанию — сегодня)."""
        until = self.calibrated_until
        if until is None:
            return False
        day = at.date() if hasattr(at, "date") else (at or timezone.localdate())
        return day <= until

    def __str__(self):
        return f"{self.name} [{self.serial}]"


class CalibrationLog(models.Model):
    """Журнал калибровок."""
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="calibrations")
    calibrated_at = models.DateField("Дата")
    performed_by = models.ForeignKey(User, on_delete=models.PROTECT)
    passed = models.BooleanField("Успешно", default=True)
    note = models.CharField("Примечание", max_length=200, blank=True)

    class Meta:
        verbose_name, verbose_name_plural = "запись о калибровке", "журнал калибровок"
        ordering = ["-calibrated_at", "-id"]


class Generator(models.Model):
    """Дизель-генератор: учёт моточасов и ТО каждые 250 м/ч."""
    name = models.CharField("Название", max_length=60)
    motohours = models.FloatField("Наработка, м/ч", default=0)
    last_service_motohours = models.FloatField("Наработка на последнем ТО", default=0)

    class Meta:
        verbose_name, verbose_name_plural = "дизель-генератор", "дизель-генераторы"
        ordering = ["name"]

    @property
    def hours_since_service(self):
        return round(self.motohours - self.last_service_motohours, 1)

    @property
    def hours_to_service(self):
        return round(settings.SERVICE_INTERVAL_MOTOHOURS - self.hours_since_service, 1)

    @property
    def service_due(self):
        return self.hours_to_service <= 0

    def __str__(self):
        return self.name


class MotoLog(models.Model):
    generator = models.ForeignKey(Generator, on_delete=models.CASCADE, related_name="logs")
    hours = models.FloatField("Моточасы")
    recorded_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(User, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-recorded_at"]


class ServiceRecord(models.Model):
    generator = models.ForeignKey(Generator, on_delete=models.CASCADE, related_name="services")
    performed_at = models.DateTimeField(default=timezone.now)
    motohours = models.FloatField()
    performed_by = models.ForeignKey(User, on_delete=models.PROTECT)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-performed_at"]


# --------------------------------------------------------------- наблюдения
def validate_observation(obs_type, instrument, value, observed_at):
    """Единые правила допустимости измерения (используются моделью и сервисом)."""
    if instrument is not None and obs_type and instrument.measures != obs_type:
        raise ValidationError(f"Прибор «{instrument.name}» не предназначен для этого вида измерений.")
    if instrument is not None and not instrument.is_calibrated(observed_at):
        raise ValidationError(
            f"Измерения заблокированы: калибровка прибора «{instrument.name}» просрочена или не проводилась.")
    if value is not None and obs_type in RANGES:
        lo, hi = RANGES[obs_type]
        if not lo <= value <= hi:
            raise ValidationError(f"Значение вне допустимого диапазона {lo}…{hi} {UNITS[obs_type]}.")


class Observation(models.Model):
    """Единичное измерение (точка временного ряда)."""
    obs_type = models.CharField("Вид измерения", max_length=20, choices=ObservationType.choices)
    value = models.FloatField("Значение")
    observed_at = models.DateTimeField("Дата и время", default=timezone.now)
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, verbose_name="Прибор")
    operator = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name="Оператор")
    note = models.CharField("Примечание", max_length=200, blank=True)

    class Meta:
        verbose_name, verbose_name_plural = "наблюдение", "наблюдения"
        ordering = ["-observed_at"]
        indexes = [models.Index(fields=["obs_type", "-observed_at"])]

    @property
    def unit(self):
        return UNITS.get(self.obs_type, "")

    def clean(self):
        validate_observation(self.obs_type, self.instrument if self.instrument_id else None,
                             self.value, self.observed_at)

    def save(self, *args, **kwargs):
        self.full_clean()  # гарантия: заблокированное измерение не попадёт в БД
        super().save(*args, **kwargs)


class ExtremeEvent(models.Model):
    """Журнал нештатных ситуаций."""
    class Kind(models.TextChoices):
        STORM = "storm", "Шторм"
        BLIZZARD = "blizzard", "Пурга"
        ICE_DRIFT = "ice_drift", "Ледовый дрейф"

    kind = models.CharField("Характер", max_length=12, choices=Kind.choices)
    occurred_at = models.DateTimeField("Время", default=timezone.now)
    description = models.TextField("Описание")
    measures_taken = models.TextField("Принятые меры")
    reported_by = models.ForeignKey(User, on_delete=models.PROTECT)

    class Meta:
        verbose_name, verbose_name_plural = "нештатная ситуация", "журнал нештатных ситуаций"
        ordering = ["-occurred_at"]


# ------------------------------------------------------------------- склад
class StockItem(models.Model):
    """Позиция склада: топливо, продовольствие, медикаменты."""
    class Category(models.TextChoices):
        FUEL = "fuel", "Топливо"
        FOOD = "food", "Продовольствие"
        MEDICINE = "medicine", "Медикаменты"

    name = models.CharField("Наименование", max_length=100)
    category = models.CharField("Категория", max_length=10, choices=Category.choices)
    unit = models.CharField("Ед. изм.", max_length=10)
    quantity = models.FloatField("Остаток", default=0)
    min_quantity = models.FloatField("Норма (минимум)")
    target_quantity = models.FloatField("Целевой запас")

    class Meta:
        verbose_name, verbose_name_plural = "позиция склада", "склад"
        ordering = ["category", "name"]

    def clean(self):
        if self.target_quantity is not None and self.min_quantity is not None \
                and self.target_quantity <= self.min_quantity:
            raise ValidationError("Целевой запас должен превышать норму.")

    @property
    def below_norm(self):
        return self.quantity < self.min_quantity

    def __str__(self):
        return self.name


class StockMovement(models.Model):
    item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="movements")
    delta = models.FloatField("Изменение (− расход, + приход)")
    moved_at = models.DateTimeField(default=timezone.now)
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    comment = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-moved_at"]


class SupplyRequest(models.Model):
    """Заявка на пополнение запасов."""
    class Status(models.TextChoices):
        PENDING = "pending", "На утверждении"
        APPROVED = "approved", "Утверждена"
        REJECTED = "rejected", "Отклонена"
        FULFILLED = "fulfilled", "Исполнена"

    item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="requests")
    quantity = models.FloatField("Количество")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name, verbose_name_plural = "заявка на снабжение", "заявки на снабжение"
        ordering = ["-created_at"]

    OPEN = ("pending", "approved")
