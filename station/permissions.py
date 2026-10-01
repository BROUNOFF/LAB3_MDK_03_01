"""Разграничение доступа на базе встроенных групп Django."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

ROLE_GROUPS = {
    "manager": "Руководитель",
    "mechanic": "Механик",
    "doctor": "Врач",
    "specialist": "Специалист",
}


def user_roles(user):
    """Множество ключей ролей пользователя (суперпользователь имеет все)."""
    if not user.is_authenticated:
        return set()
    if user.is_superuser:
        return set(ROLE_GROUPS)
    names = set(user.groups.values_list("name", flat=True))
    return {k for k, g in ROLE_GROUPS.items() if g in names}


def has_role(user, *keys):
    return bool(user_roles(user) & set(keys))


def role_required(*keys):
    """Декоратор: вход обязателен, затем нужна одна из ролей, иначе 403."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not has_role(request.user, *keys):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapper
    return decorator
