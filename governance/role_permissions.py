

class Roles:

    PM = "product_manager"
    ARCHITECT = "architect"
    QA = "qa_engineer"
    SUPERVISOR = "supervisor"
    OPENHANDS = "openhands"
    CHAT = "chat"


ROLE_PERMISSIONS = {

    Roles.PM: [
        "create_spec",
        "read_repo"
    ],

    Roles.ARCHITECT: [
        "design_system",
        "read_repo"
    ],


    Roles.QA: [
        "run_tests",
        "write_memory",
        "log_results"
    ],

    Roles.SUPERVISOR: [
        "approve_merge",
        "write_memory",
        "shutdown_system"
    ],
    Roles.OPENHANDS: [
        "edit_code", "run_tests", "network"
    ],
    Roles.CHAT: [
        "read_docs"
    ]
}


def check_permission(role, action):

    if action not in ROLE_PERMISSIONS.get(role, []):
        raise PermissionError(
            f"{role} cannot perform {action}"
        )

    return True