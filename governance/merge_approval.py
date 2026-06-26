import os
import requests

GITHUB_API = "https://api.github.com"


class MergeApprovalError(Exception):
    pass


def _get_repo():
    repo = os.getenv("GOVERNANCE_REPO")
    if not repo:
        raise MergeApprovalError("GOVERNANCE_REPO not configured")
    return repo


def _get_headers():

    token = os.getenv("GITHUB_TOKEN")

    if not token:
        raise MergeApprovalError("Missing GITHUB_TOKEN")

    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }


def _fetch_pr_reviews(repo, pr_number):

    url = f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/reviews"

    r = requests.get(url, headers=_get_headers())

    if r.status_code != 200:
        raise MergeApprovalError(
            f"GitHub API error: {r.status_code} {r.text}"
        )

    return r.json()


def _check_github_approval(repo, pr_number):

    reviews = _fetch_pr_reviews(repo, pr_number)

    approved = [
        r for r in reviews
        if r.get("state") == "APPROVED"
    ]

    return len(approved) > 0


def request_merge_approval(pr_number):
    """
    Governance gate before merge.
    Checks:
    1. Repo match
    2. GitHub reviewer approval
    3. Optional human console approval
    """

    repo = _get_repo()

    approval_required = os.getenv(
        "REQUIRE_HUMAN_APPROVAL",
        "true"
    ).lower()

    print(f"[Governance] Checking approvals for PR #{pr_number} in {repo}")

    github_approved = _check_github_approval(repo, pr_number)

    if not github_approved:
        raise MergeApprovalError(
            f"PR #{pr_number} has no GitHub approvals"
        )

    if approval_required == "true":

        user_input = input(
            f"Supervisor approval required for PR {pr_number}. Approve merge? (yes/no): "
        )

        if user_input.strip().lower() != "yes":
            raise MergeApprovalError("Merge rejected by supervisor")

    print("[Governance] Merge approved")

    return True