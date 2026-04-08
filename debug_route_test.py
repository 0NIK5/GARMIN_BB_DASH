from fastapi.testclient import TestClient
from backend.app.main import app
import traceback
import sys

client = TestClient(app)
paths = ['/api/v1/config', '/api/v1/battery/current', '/api/v1/login']
for path in paths:
    sys.stdout.write('PATH ' + path + '\n')
    try:
        if path == '/api/v1/login':
            r = client.post(path, json={'username': 'test', 'password': 'test'})
        else:
            r = client.get(path)
        sys.stdout.write('STATUS ' + str(r.status_code) + '\n')
        sys.stdout.write('TEXT ' + r.text.replace('\n', '\\n') + '\n')
    except Exception:
        sys.stdout.write('EXC ' + str(sys.exc_info()[0]) + '\n')
        traceback.print_exc(file=sys.stdout)
