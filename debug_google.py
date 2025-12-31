import logging
from google.oauth2 import service_account
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO)

def test():
    try:
        creds = service_account.Credentials.from_service_account_file(
            'service_account.json', 
            scopes=['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']
        )
        
        print("1. Подключение к Docs...")
        docs_service = build('docs', 'v1', credentials=creds, cache_discovery=False)
        
        print("2. Проверка создания документа...")
        doc = docs_service.documents().create(body={'title': 'Test'}).execute()
        print(f"УСПЕХ! Документ создан: {doc.get('documentId')}")
        
        print("3. Подключение к Drive...")
        drive_service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        
        print("4. Проверка прав доступа...")
        drive_service.permissions().create(
            fileId=doc.get('documentId'),
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        print("УСПЕХ! Права выданы.")

    except Exception as e:
        print(f"\n❌ ОШИБКА ПРОИЗОШЛА ТУТ: {e}")

test()