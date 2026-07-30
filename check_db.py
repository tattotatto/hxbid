"""Check DB state on server."""
import subprocess, json

# Get generation state for latest project
cmd = """echo hx123456 | sudo -S docker exec hongxi-db psql -U hongxi -d hongxi_bid -t -c "SELECT generation_state_json FROM bid_projects ORDER BY updated_at DESC LIMIT 1" """
result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
raw = result.stdout.strip()
if raw:
    state = json.loads(raw)
    sections = state.get("sections", {})
    done = sum(1 for s in sections.values() if s.get("status") == "done")
    failed = sum(1 for s in sections.values() if s.get("status") == "failed")
    pending = sum(1 for s in sections.values() if s.get("status") == "pending")
    total = state.get("total_leaves", 0)
    print(f"Total leaves: {total}")
    print(f"Done: {done}, Failed: {failed}, Pending: {pending}")
    # Show first few failed sections with their errors
    if failed > 0:
        print("\nSample failed sections (first 5):")
        count = 0
        for path, sec in sections.items():
            if sec.get("status") == "failed":
                print(f"  {path}: {sec.get('error', 'no error')[:150]}")
                count += 1
                if count >= 5:
                    break
    # Also check project chapters
    cmd2 = """echo hx123456 | sudo -S docker exec hongxi-db psql -U hongxi -d hongxi_bid -t -c "SELECT title, status, LENGTH(ai_generated_content) as content_len FROM project_chapters ORDER BY order_index LIMIT 5" """
    result2 = subprocess.run(cmd2, shell=True, capture_output=True, text=True)
    print("\nProject chapters (first 5):")
    print(result2.stdout[:500])
else:
    print("No data found")
    print("stderr:", result.stderr[:300])
