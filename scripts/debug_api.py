"""Debug script to check raw Gamma API response."""
import httpx
import json

# Test 1: With active filter
resp = httpx.get('https://gamma-api.polymarket.com/markets',
                 params={'limit': 5, 'active': 'true', 'closed': 'false'})
print(f'Status: {resp.status_code}')
data = resp.json()
print(f'With active=true, closed=false: {len(data)} markets')
if data:
    m = data[0]
    print(f"  Keys: {list(m.keys())[:15]}")
    print(f"  condition_id: {m.get('condition_id')}")
    print(f"  question: {m.get('question', '')[:80]}")
    print(f"  active: {m.get('active')}")
    print(f"  tokens: {m.get('tokens', [])[:1]}")

# Test 2: Without filters
print("\n--- Without filters ---")
resp2 = httpx.get('https://gamma-api.polymarket.com/markets',
                  params={'limit': 5})
data2 = resp2.json()
print(f'No filter: {len(data2)} markets')
if data2:
    m = data2[0]
    print(f"  Keys: {list(m.keys())[:20]}")
    print(f"  condition_id: {repr(m.get('condition_id'))}")
    print(f"  question: {m.get('question', '')[:80]}")
    print(f"  active: {m.get('active')}")
    print(f"  closed: {m.get('closed')}")

# Test 3: CLOB API
print("\n--- CLOB API ---")
resp3 = httpx.get('https://clob.polymarket.com/markets', params={'limit': 5})
print(f'CLOB Status: {resp3.status_code}')
clob_data = resp3.json()
print(f'CLOB response type: {type(clob_data)}')
if isinstance(clob_data, dict):
    print(f'CLOB keys: {list(clob_data.keys())}')
    if 'data' in clob_data:
        items = clob_data['data']
        print(f'CLOB items: {len(items)}')
        if items:
            print(json.dumps(items[0], indent=2, default=str)[:800])
elif isinstance(clob_data, list):
    print(f'CLOB items: {len(clob_data)}')
    if clob_data:
        print(json.dumps(clob_data[0], indent=2, default=str)[:800])
