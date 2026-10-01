from django.urls import path
from . import views as v

urlpatterns = [
    path("", v.dashboard, name="dashboard"),
    path("shifts/", v.shifts, name="shifts"),
    path("shifts/new/", v.shift_create, name="shift_create"),
    path("shifts/<int:pk>/", v.shift_detail, name="shift_detail"),
    path("shifts/<int:pk>/member/", v.member_add, name="member_add"),
    path("shifts/<int:pk>/duty/", v.duty_add, name="duty_add"),
    path("members/<int:pk>/health/", v.member_health, name="member_health"),
    path("observations/", v.observations, name="observations"),
    path("observations/new/", v.observation_add, name="observation_add"),
    path("instruments/", v.instruments, name="instruments"),
    path("instruments/<int:pk>/calibrate/", v.calibrate, name="calibrate"),
    path("generators/", v.generators, name="generators"),
    path("generators/<int:pk>/hours/", v.moto_add, name="moto_add"),
    path("generators/<int:pk>/service/", v.gen_service, name="gen_service"),
    path("stock/", v.stock, name="stock"),
    path("stock/move/", v.stock_move, name="stock_move"),
    path("requests/", v.supply_requests, name="requests"),
    path("requests/<int:pk>/receive/", v.request_receive, name="request_receive"),
    path("requests/<int:pk>/<str:action>/", v.request_decide, name="request_decide"),
    path("events/", v.events, name="events"),
    path("events/new/", v.event_add, name="event_add"),
    path("reports/", v.reports, name="reports"),
]
