import os

ALLOWED_WRITE_PATHS = [
    "agent_pipeline/tests/"
]

PROTECTED_PATHS = [
    ".env",
    "secrets/",
    ".git/"
]

def validate_file_access(path, mode="read"):

    for protected in PROTECTED_PATHS:
        if protected in path:
            raise PermissionError(f"Access denied to protected path {path}")

    if mode == "write":
        allowed = any(path.startswith(p) for p in ALLOWED_WRITE_PATHS)
        if not allowed:
            raise PermissionError(f"Write access denied for {path}")

    return True