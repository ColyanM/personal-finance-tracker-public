READ_ONLY_PERMISSIONS = {
    "accounts.read",
    "transactions.read",
    "investments.read",
}


def validate_read_only_permissions(permissions):  #Only allows the data this tracker needs
    permissions = {permission.lower().strip() for permission in permissions if permission.strip()}
    blocked = sorted(permissions - READ_ONLY_PERMISSIONS)
    if blocked:
        raise ValueError(f"Blocked bank permissions: {', '.join(blocked)}")

    if "accounts.read" not in permissions:
        raise ValueError("Missing read-only permission: accounts.read")

    return sorted(permissions)
