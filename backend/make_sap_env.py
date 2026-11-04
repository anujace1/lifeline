"""Turns your downloaded SAP AI Core service key (JSON file) into a ready-to-use env file.
Usage:  python make_sap_env.py servicekey.json default      (second argument = your resource group)
Then:   source sap_env.sh"""

import json
import shlex
import sys

key = json.load(open(sys.argv[1]))
key = key.get("credentials", key)
env = {
    "AICORE_AUTH_URL": key["url"].rstrip("/") + "/oauth/token",
    "AICORE_CLIENT_ID": key["clientid"],
    "AICORE_CLIENT_SECRET": key["clientsecret"],
    "AICORE_BASE_URL": key["serviceurls"]["AI_API_URL"].rstrip("/") + "/v2",
    "AICORE_RESOURCE_GROUP": sys.argv[2] if len(sys.argv) > 2 else "default",
}
with open("sap_env.sh", "w") as f:
    f.writelines(f"export {k}={shlex.quote(v)}\n" for k, v in env.items())
print("Wrote sap_env.sh with", ", ".join(env), "\nNow run:  source sap_env.sh")
