import json
with open('service_account.json', 'r') as f:
    data = json.load(f)
    print(f"Аккаунт в файле: {data.get('client_email')}")
    print(f"Проект в файле: {data.get('project_id')}")