from google.oauth2 import service_account
from googleapiclient.discovery import build

def clean():
    creds = service_account.Credentials.from_service_account_file(
        'service_account.json', 
        scopes=['https://www.googleapis.com/auth/drive']
    )
    drive_service = build('drive', 'v3', credentials=creds)
    
    # Ищем все файлы, которыми владеет бот
    results = drive_service.files().list(pageSize=100, fields="files(id, name)").execute()
    files = results.get('files', [])
    
    if not files:
        print("У бота нет накопленных файлов.")
        return

    for f in files:
        print(f"Удаляю невидимый файл: {f['name']}")
        drive_service.files().delete(fileId=f['id']).execute()
    print("Лимит бота очищен!")

clean()