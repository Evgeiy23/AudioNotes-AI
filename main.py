import os
import asyncio
import logging
import math
import re
from aiogram import Bot, Dispatcher, types, F
from groq import Groq
from pydub import AudioSegment
from dotenv import load_dotenv
from config import TELEGRAM_TOKEN, GROQ_API_KEY
import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# --- НАСТРОЙКИ ---
load_dotenv()

# ID папки в Google Drive (если хотите сохранять в конкретную папку, вставьте её ID)
# Если оставить None, будет сохраняться в корень диска
FOLDER_ID = None 

SCOPES = [
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive'
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """
Ты — профессиональный эксперт по анализу образовательного контента. 
ОБЯЗАТЕЛЬНО начни ответ строго с пункта:
1. ЗАГОЛОВОК: [Здесь краткое название лекции]

Затем составь подробнейший конспект по следующей структуре (22 пункта):
2. ЛЕКТОР, 3. ЦЕЛЬ ЛЕКЦИИ, 4. ГЛАВНАЯ МЫСЛЬ, 5. ВВЕДЕНИЕ, 
6. ОСНОВНЫЕ ЧАСТИ ЛЕКЦИИ, 7. КЛЮЧЕВЫЕ ТЕЗИСЫ, 8. ПОДРОБНЫЕ КЛЮЧЕВЫЕ ТЕЗИСЫ (с подпунктами), 
9. ПОДТЕМА, 10. КЛЮЧЕВЫЕ ОТКРЫТИЯ, 11. ПОНЯТИЯ И ОПРЕДЕЛЕНИЯ, 
12. ПРИМЕРЫ И СЛУЧАИ, 13. ПРИМЕРЫ И ЦИТАТЫ, 14. ЦИТАТЫ ЛЕКТОРА (Дословно), 15. ВАЖНЫЕ ЦИТАТЫ, 
16. ПРАКТИЧЕСКОЕ ПРИМЕНЕНИЕ, 17. ПРАКТИЧЕСКИЕ ПРИЕМЫ, 18. ВОПРОСЫ И ОТВЕТЫ, 
19. ОТКРЫТЫЕ ВОПРОСЫ, 20. ЗАКЛЮЧЕНИЕ, 21. ИТОГ ЛЕКЦИИ, 22. РЕЗЮМЕ.
Стиль: Академический, с эмодзи. Если информации нет — "Не упоминалось".
"""

# --- БЛОК GOOGLE DOCS С УМНЫМ ФОРМАТИРОВАНИЕМ ---

def get_credentials():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('client_secrets.json'):
                raise FileNotFoundError("Сначала скачайте client_secrets.json!")
            flow = InstalledAppFlow.from_client_secrets_file('client_secrets.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds

async def create_google_doc(title, summary_text):
    """Создание документа: пункт 1 как Заголовок, остальные - жирный префикс"""
    try:
        creds = await asyncio.to_thread(get_credentials)
        docs_service = build('docs', 'v1', credentials=creds, cache_discovery=False)
        drive_service = build('drive', 'v3', credentials=creds, cache_discovery=False)

        # Очищаем от Markdown
        clean_text = summary_text.replace("**", "").replace("__", "")

        # 1. Создаем файл (с указанием папки, если есть)
        file_metadata = {'name': title, 'mimeType': 'application/vnd.google-apps.document'}
        if FOLDER_ID:
            file_metadata['parents'] = [FOLDER_ID]
        
        doc_file = drive_service.files().create(body=file_metadata, fields='id').execute()
        doc_id = doc_file.get('id')

        # 2. Вставляем весь текст
        requests = [{'insertText': {'location': {'index': 1}, 'text': clean_text}}]
        
        # 3. Разбираем текст по строкам для стилизации
        lines = clean_text.split('\n')
        curr_idx = 1
        
        for line in lines:
            line_len = len(line) + 1
            # Ищем заголовки типа "1. ЗАГОЛОВОК:" или "2. ЛЕКТОР:"
            header_match = re.match(r'^(\d+)\.\s*([^:]+):', line)
            
            if header_match:
                point_num = header_match.group(1)
                full_header = header_match.group(0) # "2. ЛЕКТОР:"
                
                if point_num == "1":
                    # СТРОКА 1 (ЗАГОЛОВОК) -> Стиль HEADING_1
                    requests.append({
                        'updateParagraphStyle': {
                            'range': {'startIndex': curr_idx, 'endIndex': curr_idx + line_len - 1},
                            'paragraphStyle': {'namedStyleType': 'HEADING_1'},
                            'fields': 'namedStyleType'
                        }
                    })
                else:
                    # ПУНКТЫ 2-22 -> Только название ЖИРНЫМ
                    requests.append({
                        'updateTextStyle': {
                            'range': {'startIndex': curr_idx, 'endIndex': curr_idx + len(full_header)},
                            'textStyle': {'bold': True},
                            'fields': 'bold'
                        }
                    })
            
            curr_idx += line_len

        # Выполняем все запросы разом
        docs_service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()

        # 4. Права доступа (всем по ссылке)
        drive_service.permissions().create(fileId=doc_id, body={'type': 'anyone', 'role': 'reader'}).execute()

        return f"https://docs.google.com/document/d/{doc_id}/edit"
    except Exception as e:
        logging.error(f"Ошибка Google Docs: {e}")
        return None

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def sanitize_filename(text):
    text = text.replace("*", "").replace("#", "")
    text = re.sub(r'^(1\.)?\s*ЗАГОЛОВОК[:\s]*', '', text, flags=re.IGNORECASE).strip()
    clean_name = re.sub(r'[^\w\sа-яА-ЯёЁ-]', '', text)
    clean_name = re.sub(r'\s+', '_', clean_name).strip('_')
    return clean_name[:60] if clean_name else "Lektsiya"

async def transcribe_chunk(file_path, offset_seconds):
    with open(file_path, "rb") as f:
        response = client.audio.transcriptions.create(
            file=f,
            model="whisper-large-v3",
            response_format="verbose_json",
            language="ru"
        )
    
    full_text = response.text
    timed_text = ""
    if hasattr(response, 'segments'):
        for seg in response.segments:
            start = int(seg.get('start', 0)) + offset_seconds
            timed_text += f"[{start // 60:02d}:{start % 60:02d}] {seg.get('text', '')}\n"
    return full_text, timed_text

# --- ОСНОВНОЙ ОБРАБОТЧИК ---

@dp.message(F.audio | F.voice)
async def handle_audio(message: types.Message):
    audio_obj = message.audio if message.audio else message.voice
    
    if audio_obj.file_size > 20 * 1024 * 1024:
        await message.answer("❌ Файл более 20 МБ не поддерживается.")
        return

    status_msg = await message.answer("📥 Начинаю обработку...")
    file_id = audio_obj.file_id
    raw_file = f"raw_{file_id}.ogg"
    
    # Список файлов для удаления в конце
    files_to_clean = [raw_file]

    try:
        # Скачиваем
        file_info = await bot.get_file(file_id)
        await bot.download_file(file_info.file_path, raw_file)

        # Конвертируем
        await status_msg.edit_text("⚙️ Подготовка аудио...")
        audio = AudioSegment.from_file(raw_file).set_channels(1).set_frame_rate(16000)
        
        chunk_len = 15 * 60 * 1000 
        num_chunks = math.ceil(len(audio) / chunk_len)
        
        all_text, all_timed = [], []

        for i in range(num_chunks):
            await status_msg.edit_text(f"🤖 Расшифровка: {i+1}/{num_chunks}...")
            start_ms = i * chunk_len
            chunk_path = f"chunk_{i}_{file_id}.mp3"
            files_to_clean.append(chunk_path)
            
            audio[start_ms : start_ms + chunk_len].export(chunk_path, format="mp3", bitrate="32k")
            
            t, tc = await transcribe_chunk(chunk_path, start_ms // 1000)
            all_text.append(t)
            all_timed.append(tc)
            
            # Удаляем чанк сразу после использования
            if os.path.exists(chunk_path): os.remove(chunk_path)

        full_transcription = " ".join(all_text)
        
        await status_msg.edit_text("🧠 Создаю конспект и Google Doc...")
        completion = client.chat.completions.create(
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": full_transcription}],
            model="llama-3.1-8b-instant",
            temperature=0.3,
        )
        summary = completion.choices[0].message.content

        # Заголовок для Google Doc
        title_search = re.search(r"(?:1\.|#)\s*ЗАГОЛОВОК[:\s]*(.*)", summary, re.IGNORECASE)
        raw_title = title_search.group(1).split('\n')[0].strip() if title_search else "Конспект лекции"
        
        # 1. Создаем Google Doc (только КОНСПЕКТ)
        doc_url = await create_google_doc(raw_title, summary)

        # 2. Создаем TXT (ВСЁ: конспект + таймкоды + текст)
        safe_name = sanitize_filename(raw_title)
        txt_filename = f"{safe_name}.txt"
        files_to_clean.append(txt_filename)
        
        with open(txt_filename, "w", encoding="utf-8") as f:
            f.write(summary)
            f.write("\n\n" + "="*30 + "\nТАЙМКОДЫ:\n" + "".join(all_timed))
            f.write("\n\n" + "="*30 + "\nПОЛНЫЙ ТЕКСТ:\n" + full_transcription)

        caption = f"✅ <b>Конспект готов!</b>\n\n"
        if doc_url:
            caption += f'🌐 <a href="{doc_url}">Открыть Google Doc (Конспект)</a>'
        
        await bot.send_document(message.chat.id, types.FSInputFile(txt_filename), caption=caption, parse_mode="HTML")
        await status_msg.delete()

    except Exception as e:
        logging.exception("Ошибка:")
        await message.answer(f"❌ Ошибка: {str(e)[:100]}")
    finally:
        # УДАЛЕНИЕ ВСЕХ ВРЕМЕННЫХ ФАЙЛОВ
        for f in files_to_clean:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())