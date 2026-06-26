ALLOWED_EXECUTION_ENV = [
    "agent_pipeline",
    "repo_context",
    "tests"
]


def validate_execution(module):

    if module not in ALLOWED_EXECUTION_ENV:
        raise PermissionError(
            f"Execution outside sandbox blocked: {module}"
        )