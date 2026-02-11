from __future__ import annotations

SECRET_KEYWORDS = [
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "secret_key",
    "client_secret",
    "private_key",
    "auth_token",
    "jwt",
    "bearer",
    "webhook",
    "stripe",
    "aws",
    "gcp",
    "azure",
    "github",
    "gitlab",
    "slack",
    "sendgrid",
    "twilio",
    "mailgun",
    "postgres",
    "mysql",
    "redis",
    "mongo",
    "smtp",
    "dsn",
    "encryption",
    "rsa",
    "ssh",
    "cookie",
    "session",
    "csrf",
    "salt",
    "nonce",
]

HARDCODED_SECRET_PATTERNS: list[tuple[str, str]] = []

for keyword in SECRET_KEYWORDS:
    HARDCODED_SECRET_PATTERNS.append(
        (f"secret_assign_{keyword}", rf"{keyword}\s*[:=]\s*['\"][^'\"]{{6,}}['\"]")
    )
    HARDCODED_SECRET_PATTERNS.append(
        (f"secret_env_{keyword}", rf"{keyword}\s*[:=]\s*os\.environ")
    )
    HARDCODED_SECRET_PATTERNS.append(
        (f"secret_config_{keyword}", rf"{keyword}\s*[:=]\s*config")
    )

HARDCODED_SECRET_PATTERNS.extend(
    [
        ("aws_access_key_id", r"AKIA[0-9A-Z]{16}"),
        ("aws_secret_access_key", r"(?i)aws_secret_access_key"),
        ("github_token", r"ghp_[0-9A-Za-z]{36}"),
        ("slack_token", r"xox[baprs]-[0-9A-Za-z-]+"),
        ("google_api_key", r"AIza[0-9A-Za-z\-_]{35}"),
        ("stripe_key", r"sk_live_[0-9a-zA-Z]{24}"),
        ("private_key_block", r"BEGIN PRIVATE KEY"),
        ("jwt_token", r"eyJ[0-9A-Za-z_-]+\.[0-9A-Za-z_-]+\.[0-9A-Za-z_-]+"),
        ("bearer_token", r"Bearer\s+[0-9A-Za-z\-\._~\+\/=]+=*"),
    ]
)
