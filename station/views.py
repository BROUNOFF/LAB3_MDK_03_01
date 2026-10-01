"""Контроллеры: серверный рендеринг страниц с проверкой ролей."""
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import forms, services
from .models import (ExtremeEvent, Generator, Instrument, Observation, ObservationType, Shift,
                     ShiftMember, StockItem, SupplyRequest)
from .permissions import has_role, role_required

ANY = ("manager", "mechanic", "doctor", "specialist")
STOCK_ROLES = {"manager": None, "mechanic": ["fuel"], "doctor": ["medicine"]}


def _form_page(request, form, title, back, hint=""):
    return render(request, "station/form.html", {"form": form, "title": title, "back": back, "hint": hint})


def _errors(request, exc):
    for m in getattr(exc, "messages", [str(exc)]):
        messages.error(request, m)


@role_required(*ANY)
def dashboard(request):
    today = timezone.localdate()
    ctx = {
        "shift": next((s for s in Shift.objects.all() if s.is_active), None),
        "blocked": [i for i in Instrument.objects.all() if not i.is_calibrated()],
        "due_gens": [g for g in Generator.objects.all() if g.service_due],
        "low_stock": [s for s in StockItem.objects.all() if s.below_norm],
        "pending": SupplyRequest.objects.filter(status="pending").count(),
        "events": ExtremeEvent.objects.all()[:5],
        "obs_today": Observation.objects.filter(observed_at__date=today).count(),
    }
    return render(request, "station/dashboard.html", ctx)


# ----------------------------------------------------------------- смены
@role_required(*ANY)
def shifts(request):
    return render(request, "station/shifts.html", {"shifts": Shift.objects.all()})


@role_required("manager")
def shift_create(request):
    form = forms.ShiftForm(request.POST or None)
    if form.is_valid():
        s = form.save()
        return redirect("shift_detail", pk=s.pk)
    return _form_page(request, form, "Новая смена", "shifts")


@role_required(*ANY)
def shift_detail(request, pk):
    from .services import ReportService
    shift = get_object_or_404(Shift, pk=pk)
    return render(request, "station/shift_detail.html", {
        "shift": shift,
        "members": shift.members.select_related("employee"),
        "duties": shift.duties.select_related("employee"),
        "staffing": ReportService.staffing(shift),
    })


@role_required("manager")
def member_add(request, pk):
    shift = get_object_or_404(Shift, pk=pk)
    form = forms.MemberForm(request.POST or None, shift=shift)
    if form.is_valid():
        form.save()
        messages.success(request, "Сотрудник зачислен в смену.")
        return redirect("shift_detail", pk=pk)
    return _form_page(request, form, f"Зачисление в смену «{shift}»", "shifts")


@role_required("manager", "doctor")
def member_health(request, pk):
    member = get_object_or_404(ShiftMember, pk=pk)
    form = forms.HealthForm(request.POST or None, instance=member)
    if form.is_valid():
        form.save()
        messages.success(request, "Состояние здоровья обновлено.")
        return redirect("shift_detail", pk=member.shift_id)
    return _form_page(request, form, f"Здоровье: {member.employee.full_name}", "shifts")


@role_required("manager")
def duty_add(request, pk):
    shift = get_object_or_404(Shift, pk=pk)
    form = forms.DutyForm(request.POST or None, shift=shift)
    if form.is_valid():
        form.save()
        messages.success(request, "Дежурство назначено.")
        return redirect("shift_detail", pk=pk)
    return _form_page(request, form, f"Назначить дежурство — «{shift}»", "shifts")


# ------------------------------------------------------------ наблюдения
@role_required(*ANY)
def observations(request):
    qs = Observation.objects.select_related("instrument", "operator")
    t = request.GET.get("type", "")
    if t:
        qs = qs.filter(obs_type=t)
    if request.GET.get("from"):
        qs = qs.filter(observed_at__date__gte=request.GET["from"])
    if request.GET.get("to"):
        qs = qs.filter(observed_at__date__lte=request.GET["to"])
    return render(request, "station/observations.html", {
        "items": qs[:200], "types": ObservationType.choices, "sel": t,
        "dfrom": request.GET.get("from", ""), "dto": request.GET.get("to", "")})


@role_required("specialist", "manager")
def observation_add(request):
    form = forms.ObservationForm(request.POST or None)
    if form.is_valid():
        d = form.cleaned_data
        try:
            services.ObservationService.record(operator=request.user, obs_type=d["obs_type"],
                                               instrument=d["instrument"], value=d["value"],
                                               observed_at=d["observed_at"], note=d["note"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, "Измерение записано.")
            return redirect("observations")
    return _form_page(request, form, "Новое измерение", "observations",
                      "Прибор с просроченной калибровкой заблокирован — измерение не будет принято.")


# ------------------------------------------------------------- оборудование
@role_required(*ANY)
def instruments(request):
    return render(request, "station/instruments.html", {"items": Instrument.objects.all()})


@role_required("mechanic", "manager")
def calibrate(request, pk):
    inst = get_object_or_404(Instrument, pk=pk)
    form = forms.CalibrationForm(request.POST or None, initial={"calibrated_at": timezone.localdate()})
    if form.is_valid():
        d = form.cleaned_data
        services.InstrumentService.calibrate(inst, request.user, d["calibrated_at"], d["passed"], d["note"])
        messages.success(request, "Калибровка записана." if d["passed"] else "Неуспешная калибровка записана; прибор остаётся заблокированным.")
        return redirect("instruments")
    return _form_page(request, form, f"Калибровка: {inst.name}", "instruments")


@role_required(*ANY)
def generators(request):
    return render(request, "station/generators.html", {"items": Generator.objects.all()})


@role_required("mechanic", "manager")
def moto_add(request, pk):
    form = forms.MotoForm(request.POST or None)
    if form.is_valid():
        try:
            gen = services.GeneratorService.log_hours(pk, form.cleaned_data["hours"], request.user)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            if gen.service_due:
                messages.warning(request, f"{gen.name}: достигнут порог планового ТО.")
            return redirect("generators")
    return _form_page(request, form, "Внести моточасы", "generators")


@role_required("mechanic", "manager")
def gen_service(request, pk):
    form = forms.ServiceForm(request.POST or None)
    if form.is_valid():
        services.GeneratorService.service(pk, request.user, form.cleaned_data["note"])
        messages.success(request, "Плановое ТО зафиксировано, счётчик сброшен.")
        return redirect("generators")
    return _form_page(request, form, "Плановое ТО", "generators")


# ------------------------------------------------------------------ склад
@role_required(*ANY)
def stock(request):
    return render(request, "station/stock.html", {"items": StockItem.objects.all()})


def _allowed_categories(user):
    """None — все категории; список — только перечисленные; [] — нет доступа."""
    cats, any_role = set(), False
    for role, c in STOCK_ROLES.items():
        if has_role(user, role):
            if c is None:
                return None
            cats |= set(c)
            any_role = True
    return list(cats) if any_role else []


@role_required("manager", "mechanic", "doctor")
def stock_move(request):
    form = forms.StockMoveForm(request.POST or None, categories=_allowed_categories(request.user))
    if form.is_valid():
        d = form.cleaned_data
        delta = d["amount"] if d["kind"] == "in" else -d["amount"]
        try:
            req = services.StockService.move(d["item"].pk, delta, request.user, d["comment"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, "Операция учтена.")
            if req:
                messages.warning(request, f"Остаток ниже нормы — сформирована заявка №{req.pk} на {req.quantity} {d['item'].unit}.")
            return redirect("stock")
    return _form_page(request, form, "Расход / приход", "stock")


@role_required(*ANY)
def supply_requests(request):
    return render(request, "station/requests.html",
                  {"items": SupplyRequest.objects.select_related("item", "decided_by")})


@role_required("manager")
@require_POST
def request_decide(request, pk, action):
    req = get_object_or_404(SupplyRequest, pk=pk)
    try:
        services.SupplyService.decide(req, request.user, approve=(action == "approve"))
        messages.success(request, "Заявка утверждена." if action == "approve" else "Заявка отклонена.")
    except ValidationError as e:
        _errors(request, e)
    return redirect("requests")


@role_required("manager", "mechanic", "doctor")
@require_POST
def request_receive(request, pk):
    req = get_object_or_404(SupplyRequest, pk=pk)
    cats = _allowed_categories(request.user)
    if cats is not None and req.item.category not in cats:
        messages.error(request, "Эта категория вам недоступна.")
    else:
        try:
            services.SupplyService.receive(req, request.user)
            messages.success(request, "Поставка принята на склад.")
        except ValidationError as e:
            _errors(request, e)
    return redirect("requests")


# ------------------------------------------------------------ НС и отчёты
@role_required(*ANY)
def events(request):
    return render(request, "station/events.html", {"items": ExtremeEvent.objects.select_related("reported_by")})


@role_required(*ANY)
def event_add(request):
    form = forms.EventForm(request.POST or None)
    if form.is_valid():
        ev = form.save(commit=False)
        ev.reported_by = request.user
        ev.save()
        messages.success(request, "Нештатная ситуация записана в журнал.")
        return redirect("events")
    return _form_page(request, form, "Нештатная ситуация", "events")


@role_required("manager")
def reports(request):
    today = timezone.localdate()
    form = forms.ReportForm(request.GET or None, initial={
        "date_from": today - timedelta(days=30), "date_to": today})
    data = None
    if request.GET and form.is_valid():
        d = form.cleaned_data
        data = services.ReportService.build(d["date_from"], d["date_to"], d["shift"])
        data["period"] = d
    return render(request, "station/reports.html", {"form": form, "data": data})
