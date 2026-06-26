SYSTEM_ENABLED = True


def check_system():

    if not SYSTEM_ENABLED:
        raise SystemExit(
            "System halted by governance kill switch."
        )