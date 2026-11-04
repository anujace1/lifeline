"""Check your SAP AI Core connection BEFORE running the app:  python test_sap.py"""

import os

need = [
    "AICORE_AUTH_URL",
    "AICORE_CLIENT_ID",
    "AICORE_CLIENT_SECRET",
    "AICORE_BASE_URL",
    "AICORE_RESOURCE_GROUP",
]
missing = [k for k in need if not os.getenv(k)]
if missing:
    raise SystemExit("Missing environment variables: " + ", ".join(missing))

from litellm import completion

model = os.getenv("SAP_MODEL", "sap/anthropic--claude-4.5-haiku")
print("Resource group:", os.getenv("AICORE_RESOURCE_GROUP"), "| model:", model)
r = completion(
    model=model,
    max_tokens=60,
    messages=[{"role": "user", "content": "Reply with the single word: connected"}],
)
print("Reply:", r.choices[0].message.content)
