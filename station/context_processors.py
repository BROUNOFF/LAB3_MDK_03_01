from .permissions import user_roles


def roles(request):
    return {"roles": user_roles(request.user)}
