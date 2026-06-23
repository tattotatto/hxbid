import urllib.request, json

data = json.dumps({"username": "admin", "password": "admin123"}).encode()
req = urllib.request.Request("http://localhost:8888/api/v1/auth/login", data=data, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req)
token = json.loads(resp.read())["access_token"]

req2 = urllib.request.Request("http://localhost:8888/api/v1/projects/", headers={"Authorization": "Bearer " + token})
resp2 = urllib.request.urlopen(req2)
projects = json.loads(resp2.read())
print("Projects:", len(projects))
for p in projects:
    print("  " + p["name"][:60] + " | " + p["status"] + " | " + str(p.get("bid_result", "")))

req3 = urllib.request.Request("http://localhost:8888/api/v1/bid/vector-stats")
resp3 = urllib.request.urlopen(req3)
print("Vector:", json.loads(resp3.read()))
