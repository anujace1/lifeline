"""Shows what the configured AI understands from a message (no server needed).
Usage:  python test_llm.py "PO-1042 will be 8 days late, wafer shortage"
A line saying 'falling back to mock' means the AI call failed and the simple rules answered instead.
"""

import json
import sys
import time

import config
import llm

text = " ".join(sys.argv[1:]) or "PO-1042 will be 8 days late, wafer shortage"
print("provider:", config.LLM_PROVIDER, "| message:", text)
start = time.time()
print(json.dumps(llm.extract(text, {}), indent=2))
print(f"took {time.time() - start:.1f}s")
