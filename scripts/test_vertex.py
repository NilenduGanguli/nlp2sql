"""
Vertex AI / Gemini diagnostic script.
Run from the project root: python scripts/test_vertex.py
"""
import os
import sys
import json

# Load .env explicitly to avoid the heredoc/stdin AssertionError
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

print("=" * 60)
print("Vertex AI / Gemini Diagnostic")
print("=" * 60)

# 1. Check all relevant env vars
print("\n[1] Environment variables:")
for var in ["LLM_PROVIDER", "LLM_MODEL", "GEMINI_API_KEY",
            "VERTEX_PROJECT", "VERTEX_LOCATION", "GOOGLE_APPLICATION_CREDENTIALS"]:
    val = os.getenv(var, "")
    if var == "GEMINI_API_KEY" and val:
        print(f"  {var}: {'*' * 8} (set, {len(val)} chars)")
    else:
        print(f"  {var}: {val!r}")

gemini_key = os.getenv("GEMINI_API_KEY", "")
creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

# 2. Check which auth path will be used
print("\n[2] Auth path detection:")
if gemini_key:
    print("  -> GEMINI_API_KEY is set: will use Google AI Studio (no Vertex AI API needed)")
elif creds_path:
    print(f"  -> GOOGLE_APPLICATION_CREDENTIALS: {creds_path}")
    if os.path.exists(creds_path):
        with open(creds_path) as f:
            sa = json.load(f)
        print(f"     type:         {sa.get('type')}")
        print(f"     project_id:   {sa.get('project_id')}")
        print(f"     client_email: {sa.get('client_email')}")
        print("  -> Will use Vertex AI service account (requires Vertex AI API enabled in GCP)")
    else:
        print(f"  ERROR: File not found: {creds_path}")
        sys.exit(1)
else:
    print("  -> Neither GEMINI_API_KEY nor GOOGLE_APPLICATION_CREDENTIALS set")
    print("  -> Will attempt ADC (gcloud application-default credentials)")

# 3. Load AppConfig and get_llm
print("\n[3] AppConfig + get_llm:")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from app_config import AppConfig
    config = AppConfig()
    print(f"  llm_provider:    {config.llm_provider}")
    print(f"  llm_model:       {config.llm_model}")
    print(f"  vertex_project:  {config.vertex_project}")
    print(f"  vertex_location: {config.vertex_location}")
except Exception as e:
    print(f"  ERROR building AppConfig: {e}")
    sys.exit(1)

if config.llm_provider.lower() != "vertex":
    print(f"\n  NOTE: LLM_PROVIDER={config.llm_provider!r}, not 'vertex'.")
    print("  Overriding to vertex for this test...")
    config.llm_provider = "vertex"
    config.llm_model = config.llm_model or "gemini-2.5-flash"

try:
    from agent.llm import get_llm
    llm = get_llm(config)
    print(f"  OK — {type(llm).__name__} instantiated")
except Exception as e:
    print(f"  ERROR in get_llm: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

# 4. Send a simple test prompt
print("\n[4] Sending test prompt to Gemini...")
try:
    response = llm.invoke("Say 'Hello from Gemini' and nothing else.")
    content = response.content if hasattr(response, "content") else str(response)
    print(f"  Response: {content!r}")
    print("\n  Gemini is working correctly.")
except Exception as e:
    print(f"  ERROR during invoke: {e}")
    print("\n  Likely cause: Vertex AI API not enabled on GCP project.")
    print("  Fix option 1: Set GEMINI_API_KEY in .env (get from https://aistudio.google.com/apikey)")
    print("  Fix option 2: Enable Vertex AI API at https://console.cloud.google.com/apis/library/aiplatform.googleapis.com")
    sys.exit(1)

print("\n" + "=" * 60)
print("Diagnostic complete.")
