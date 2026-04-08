from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)
response = client.post('/api/v1/login', json={'username': 'test', 'password': 'test'})
print(response.status_code)
print(response.text)
print(response.headers)
print(response.json() if response.headers.get('content-type','').startswith('application/json') else 'no json')
