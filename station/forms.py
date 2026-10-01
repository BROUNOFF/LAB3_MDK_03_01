"""Формы (слой View-input)."""
from django import forms
from django.utils import timezone

from .models import (DutySlot, ExtremeEvent, Generator, Instrument, Observation, Shift, ShiftMember,
                     StockItem, Employee)

DT_FMT = "%Y-%m-%dT%H:%M"


def dt_widget():
    return forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DT_FMT)


def date_widget():
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class _Base(forms.ModelForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            if isinstance(f.widget, forms.DateTimeInput):
                f.input_formats = [DT_FMT, "%Y-%m-%d %H:%M:%S"]


class ShiftForm(_Base):
    class Meta:
        model = Shift
        fields = ["name", "start_date", "end_date"]
        widgets = {"start_date": date_widget(), "end_date": date_widget()}


class MemberForm(_Base):
    class Meta:
        model = ShiftMember
        fields = ["employee", "qualification", "arrival_date", "health", "health_note"]
        widgets = {"arrival_date": date_widget()}

    def __init__(self, *a, shift=None, **kw):
        super().__init__(*a, **kw)
        self.instance.shift = shift
        self.fields["employee"].queryset = Employee.objects.exclude(memberships__shift=shift)


class HealthForm(_Base):
    class Meta:
        model = ShiftMember
        fields = ["health", "health_note"]


class DutyForm(_Base):
    class Meta:
        model = DutySlot
        fields = ["employee", "date", "part"]
        widgets = {"date": date_widget()}

    def __init__(self, *a, shift=None, **kw):
        super().__init__(*a, **kw)
        self.instance.shift = shift
        self.fields["employee"].queryset = Employee.objects.filter(memberships__shift=shift)


class ObservationForm(_Base):
    class Meta:
        model = Observation
        fields = ["obs_type", "instrument", "value", "observed_at", "note"]
        widgets = {"observed_at": dt_widget()}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["observed_at"].initial = timezone.localtime().strftime(DT_FMT)
        self.fields["instrument"].label_from_instance = lambda i: (
            f"{i.name} [{i.get_measures_display()}] — " + ("годен" if i.is_calibrated() else "ЗАБЛОКИРОВАН"))


class CalibrationForm(forms.Form):
    calibrated_at = forms.DateField(label="Дата калибровки", widget=date_widget())
    passed = forms.BooleanField(label="Калибровка пройдена", required=False, initial=True)
    note = forms.CharField(label="Примечание", required=False, max_length=200)


class MotoForm(forms.Form):
    hours = forms.FloatField(label="Наработка за период, м/ч", min_value=0.1)


class ServiceForm(forms.Form):
    note = forms.CharField(label="Что выполнено", required=False, max_length=200)


class StockMoveForm(forms.Form):
    KIND = [("out", "Расход"), ("in", "Приход")]
    item = forms.ModelChoiceField(StockItem.objects.none(), label="Позиция")
    kind = forms.ChoiceField(choices=KIND, label="Операция")
    amount = forms.FloatField(label="Количество", min_value=0.01)
    comment = forms.CharField(label="Комментарий", required=False, max_length=200)

    def __init__(self, *a, categories=None, **kw):
        super().__init__(*a, **kw)
        qs = StockItem.objects.all()
        if categories is not None:
            qs = qs.filter(category__in=categories)
        self.fields["item"].queryset = qs


class EventForm(_Base):
    class Meta:
        model = ExtremeEvent
        fields = ["kind", "occurred_at", "description", "measures_taken"]
        widgets = {"occurred_at": dt_widget(), "description": forms.Textarea(attrs={"rows": 3}),
                   "measures_taken": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["occurred_at"].initial = timezone.localtime().strftime(DT_FMT)


class ReportForm(forms.Form):
    date_from = forms.DateField(label="С", widget=date_widget())
    date_to = forms.DateField(label="По", widget=date_widget())
    shift = forms.ModelChoiceField(Shift.objects.all(), required=False, label="Смена (для укомплектованности)")

    def clean(self):
        d = super().clean()
        if d.get("date_from") and d.get("date_to") and d["date_to"] < d["date_from"]:
            raise forms.ValidationError("Конец периода раньше начала.")
        return d
