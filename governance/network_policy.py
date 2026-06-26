ALLOWED_DOMAINS = [
    "localhost",
    "github.com"
]

def validate_network_access(url):

    for domain in ALLOWED_DOMAINS:
        if domain in url:
            return True

    raise PermissionError(f"Network access blocked: {url}")