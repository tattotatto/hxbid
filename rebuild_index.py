"""Re-index all archived historical bids into the vector store."""
import urllib.request, json, sys

BASE = "http://localhost:8888/api/v1"

# Login
data = json.dumps({"username": "admin", "password": "admin123"}).encode()
req = urllib.request.Request(BASE + "/auth/login", data=data, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req)
token = json.loads(resp.read())["access_token"]
headers = {"Authorization": "Bearer " + token}

# Get all projects
req = urllib.request.Request(BASE + "/projects/", headers=headers)
resp = urllib.request.urlopen(req)
projects = json.loads(resp.read())
archived = [p for p in projects if p["status"] == "archived"]
print(f"Found {len(archived)} archived projects")

# Trigger rebuild-index
req = urllib.request.Request(
    BASE + "/bid/rebuild-index",
    method="POST",
    headers=headers,
)
try:
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    print(f"Rebuild result: {result}")
except Exception as e:
    print(f"Rebuild endpoint returned: {e}")

# Check vector stats
req = urllib.request.Request(BASE + "/bid/vector-stats")
resp = urllib.request.urlopen(req)
stats = json.loads(resp.read())
print(f"Vector store: {stats}")

# The rebuild-index only processes chapters with ai_generated_content.
# Historical bids are raw documents, not chapters.
# We need to directly re-parse and re-index each one.
# Let's do it via Python on the server side.

# Actually, let me try the feedback loop approach first.
# Check if any project has edited chapters
for p in projects:
    req = urllib.request.Request(BASE + "/projects/" + p["id"], headers=headers)
    resp = urllib.request.urlopen(req)
    detail = json.loads(resp.read())
    chapters = detail.get("chapters", [])
    if chapters:
        print(f"\n{p['name'][:50]} has {len(chapters)} chapters")
        for ch in chapters[:3]:
            has_ai = bool(ch.get("ai_generated_content"))
            has_final = bool(ch.get("final_content"))
            print(f"  {ch['title']}: ai={has_ai} final={has_final}")
