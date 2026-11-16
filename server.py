import base64, hmac, json, os, re, sqlite3, threading, urllib.request, urllib.error
from browser_chat import translate_browser, BROWSER_LOCK
from remote_login import REMOTE, validate_action
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DB = ROOT / 'data' / 'library.sqlite'
STATIC = ROOT / 'static' if (ROOT / 'static').is_dir() else ROOT

class ChapterParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.active = 0
        self.skip = 0
        self.parts = []
        self.title = []
        self.in_title = False
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'title': self.in_title = True
        if tag in ('script', 'style'): self.skip += 1
        if tag == 'div':
            self.depth += 1
            if not self.active and 'chapter-content' in a.get('class', '').split(): self.active = self.depth
        if self.active and tag in ('p', 'br', 'hr'): self.parts.append('\n\n')
    def handle_endtag(self, tag):
        if tag == 'title': self.in_title = False
        if tag in ('script', 'style'): self.skip = max(0, self.skip - 1)
        if self.active and tag == 'p': self.parts.append('\n\n')
        if tag == 'div':
            if self.depth == self.active: self.active = 0
            self.depth -= 1
    def handle_data(self, data):
        if self.in_title: self.title.append(data)
        if self.active and not self.skip: self.parts.append(data)
    def result(self):
        return ''.join(self.title).strip(), re.sub(r'\n\s*\n+', '\n\n', ''.join(self.parts)).strip()

def validate_url(url):
    p = urlparse(url)
    if p.scheme != 'https' or p.hostname not in ('www.royalroad.com', 'royalroad.com') or p.port not in (None, 443) or p.username or p.password or not re.match(r'^/fiction/\d+/[^/]+/chapter/\d+(?:/|$)', p.path):
        raise ValueError('Нужна HTTPS-ссылка на главу Royal Road: /fiction/…/chapter/…')
    return url

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def browser_import_chapter(url):
    """One ordinary browser attempt; no proxies, stealth, or challenge solving."""
    validate_url(url)
    if not BROWSER_LOCK.acquire(blocking=False):
        raise ValueError('Браузер занят входом или переводом. Завершите текущую операцию и загрузите главу снова.')
    try:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ValueError('Для автоматической загрузки на сервере нужен Chromium/Playwright.') from exc
        with sync_playwright() as p:
            profile = ROOT / 'data' / 'royalroad-browser'
            profile.mkdir(parents=True, exist_ok=True)
            context = p.chromium.launch_persistent_context(
                str(profile), headless=True,
                viewport={'width': 1024, 'height': 768},
                args=['--disable-dev-shm-usage'],
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                denied_redirect = []
                def restrict_navigation(route):
                    request = route.request
                    if request.is_navigation_request() and request.frame == page.main_frame:
                        try: validate_url(request.url)
                        except ValueError:
                            denied_redirect.append(True)
                            return route.abort()
                    return route.continue_()
                page.route('**/*', restrict_navigation)
                try:
                    response = page.goto(url, wait_until='domcontentloaded', timeout=25000)
                except Exception as exc:
                    if denied_redirect:
                        raise ValueError('Royal Road перенаправил на страницу вне открытой главы. Автоматическая загрузка остановлена.') from exc
                    raise ValueError('Браузер не смог загрузить Royal Road за 25 секунд. Проверьте доступность сайта и журнал Render.') from exc
                if response and response.status == 429:
                    raise ValueError('Royal Road ограничил частоту запросов (HTTP 429). Автоматического повтора нет; попробуйте позже.')
                content = page.locator('.chapter-content').first
                try:
                    content.wait_for(state='visible', timeout=15000)
                except Exception as exc:
                    if response and response.status in (401, 403):
                        raise ValueError('Royal Road отказал и обычному браузеру на Render (HTTP %s). Автоматический импорт с этого сервера сейчас недоступен.' % response.status) from exc
                    raise ValueError('В браузере не появился текст главы: возможна проверка доступа, удалённая глава или изменение разметки Royal Road.') from exc
                validate_url(page.url)
                text = content.inner_text(timeout=5000).strip()
                if not text or len(text) > 150000:
                    raise ValueError('Глава пуста или длиннее 150 000 символов.')
                return {'title': page.title().strip(), 'original': text, 'url': url}
            finally:
                context.close()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Не удалось запустить браузер для Royal Road. Проверьте журнал Render: возможна нехватка памяти.') from exc
    finally:
        BROWSER_LOCK.release()


def import_chapter(url):
    validate_url(url)
    req = urllib.request.Request(url, headers={'User-Agent': 'PersonalNovelReader/1.0', 'Accept': 'text/html'})
    try:
        with urllib.request.build_opener(SafeRedirect()).open(req, timeout=25) as res:
            raw = res.read(2_000_001)
            if len(raw) > 2_000_000: raise ValueError('Страница слишком большая.')
    except urllib.error.HTTPError as e:
        if e.code == 403:
            # Some public pages require a regular JavaScript-capable browser.
            return browser_import_chapter(url)
        if e.code == 429:
            raise ValueError('Royal Road ограничил частоту запросов (HTTP 429). Попробуйте позже.') from e
        raise ValueError(f'Royal Road вернул HTTP {e.code}. Автоматическая загрузка не выполнена.') from e
    parser = ChapterParser()
    parser.feed(raw.decode('utf-8', errors='replace'))
    title, text = parser.result()
    if not text: return browser_import_chapter(url)
    if len(text) > 150000: raise ValueError('Глава длиннее 150 000 символов.')
    return {'title': title, 'original': text, 'url': url}

def chunks(text, limit=10000):
    # Split at paragraph boundaries where possible; never discard long paragraphs.
    rest = text
    while rest:
        end = min(limit, len(rest))
        if end < len(rest):
            boundary = rest.rfind('\n\n', 0, end)
            if boundary > end // 2: end = boundary + 2
        yield rest[:end]
        rest = rest[end:]

def connection():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

class Handler(BaseHTTPRequestHandler):
    def reply(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)
    def authorized(self):
        password = os.getenv('READER_PASSWORD')
        if not password: return True  # Loopback only without a password (enforced at startup).
        try:
            scheme, encoded = self.headers.get('Authorization', '').split(' ', 1)
            userpass = base64.b64decode(encoded, validate=True).decode()
            ok = scheme.lower() == 'basic' and hmac.compare_digest(userpass.encode(), ('reader:' + password).encode())
        except (ValueError, UnicodeError): ok = False
        if ok: return True
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Novel Reader", charset="UTF-8"')
        self.end_headers()
        return False

    def do_GET(self):
        if self.path == '/healthz': return self.reply({'ok': True})
        if not self.authorized(): return
        if self.path == '/api/chapters':
            with connection() as con: rows = con.execute('SELECT * FROM chapters ORDER BY id DESC').fetchall()
            return self.reply([dict(r) for r in rows])
        if self.path == '/api/browser/status': return self.reply(REMOTE.status())
        if self.path == '/api/browser/screen':
            try:
                raw = REMOTE.request('shot')
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return self.wfile.write(raw)
            except ValueError as e: return self.reply({'error': str(e)}, 409)
        if self.path == '/api/config':
            return self.reply({'mode': 'browser', 'providers': ['chatgpt', 'gemini']})
        if self.path == '/api/jobs':
            with connection() as con: rows = con.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 100').fetchall()
            return self.reply([dict(r) for r in rows])
        filename = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/manifest.webmanifest': 'manifest.webmanifest', '/icon.svg': 'icon.svg', '/sw.js': 'sw.js', '/login': 'login.html', '/login.js': 'login.js'}.get(self.path)
        if not filename: return self.reply({'error': 'Не найдено'}, 404)
        raw = (STATIC / filename).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', {'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8','webmanifest':'application/manifest+json','svg':'image/svg+xml'}[filename.split('.')[-1]])
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        self.wfile.write(raw)
    def do_POST(self):
        if not self.authorized(): return
        # Reject cross-site requests. Host is set by the private reverse proxy in production.
        origin = self.headers.get('Origin')
        if origin and urlparse(origin).netloc != self.headers.get('Host'): return self.reply({'error': 'Недопустимый источник запроса'}, 403)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 500000: raise ValueError('Слишком большой или пустой запрос.')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict): raise ValueError('Нужен JSON-объект.')
            if self.path == '/api/browser/start': return self.reply(REMOTE.start(body.get('provider')))
            if self.path == '/api/browser/close': return self.reply(REMOTE.close())
            if self.path == '/api/browser/action':
                action, value = validate_action(body)
                return self.reply(REMOTE.request(action, value))
            if self.path == '/api/import': return self.reply(import_chapter(body.get('url', '').strip()))
            if self.path == '/api/save':
                title = str(body.get('title', '')).strip()[:300] or 'Без названия'
                original = str(body.get('original', '')).strip()
                if not original or len(original) > 150000: raise ValueError('Введите текст главы (до 150 000 символов).')
                with connection() as con:
                    cur = con.execute('INSERT INTO chapters(title,original,translation,url) VALUES(?,?,?,?)', (title, original, '', str(body.get('url',''))[:2000]))
                    id_ = cur.lastrowid
                return self.reply({'id': id_})
            if self.path == '/api/translate':
                id_ = int(body['id'])
                with connection() as con: row = con.execute('SELECT * FROM chapters WHERE id=?', (id_,)).fetchone()
                if not row: raise ValueError('Глава не найдена.')
                if REMOTE.status()['state'] in ('starting', 'ready'):
                    raise ValueError('Сначала нажмите «Завершить вход» и дождитесь закрытия браузера.')
                provider = body.get('provider')
                if provider not in ('chatgpt', 'gemini'): raise ValueError('Выберите ChatGPT или Gemini.')
                return self.reply({'job_id': enqueue(id_, provider)}, 202)
            if self.path == '/api/delete':
                with connection() as con:
                    if con.execute("SELECT 1 FROM jobs WHERE chapter_id=? AND state IN ('queued','running')", (int(body['id']),)).fetchone():
                        raise ValueError('Дождитесь завершения перевода перед удалением главы.')
                    con.execute('DELETE FROM chapters WHERE id=?', (int(body['id']),))
                return self.reply({'ok': True})
            self.reply({'error': 'Не найдено'}, 404)
        except (ValueError, KeyError, TypeError) as e: self.reply({'error': str(e)}, 400)
        except Exception: self.reply({'error': 'Не удалось выполнить запрос. Проверьте соединение и настройки сервера.'}, 502)

def enqueue(chapter_id, provider):
    with connection() as con:
        # Write lock makes duplicate detection atomic across simultaneous requests.
        con.execute('BEGIN IMMEDIATE')
        existing = con.execute("SELECT id FROM jobs WHERE chapter_id=? AND state IN ('queued','running')", (chapter_id,)).fetchone()
        if existing: return existing['id']
        return con.execute("INSERT INTO jobs(chapter_id,provider,state) VALUES(?,?,'queued')", (chapter_id, provider)).lastrowid


def process_next(translator=translate_browser):
    with connection() as con:
        con.execute('BEGIN IMMEDIATE')
        job = con.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
        if not job: return False
        con.execute("UPDATE jobs SET state='running' WHERE id=?", (job['id'],))
        chapter = con.execute('SELECT original FROM chapters WHERE id=?', (job['chapter_id'],)).fetchone()
    def progress(done, total):
        with connection() as con: con.execute('UPDATE jobs SET done=?, total=? WHERE id=?', (done,total,job['id']))
    try:
        if not chapter: raise ValueError('Глава удалена.')
        result = translator(chapter['original'], job['provider'], chunks, progress)
        if not result.strip(): raise ValueError('Пустой перевод.')
        with connection() as con:
            con.execute('UPDATE chapters SET translation=? WHERE id=?', (result,job['chapter_id']))
            con.execute("UPDATE jobs SET state='completed' WHERE id=?", (job['id'],))
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else 'Перевод прерван. Проверьте браузер на сервере.'
        with connection() as con: con.execute("UPDATE jobs SET state='error', error=? WHERE id=?", (message,job['id']))
    return True


def worker():
    while True:
        try:
            if not process_next(): threading.Event().wait(2)
        except Exception:
            threading.Event().wait(5)


def initialize():
    DB.parent.mkdir(exist_ok=True)
    with connection() as con:
        con.execute('CREATE TABLE IF NOT EXISTS chapters (id INTEGER PRIMARY KEY, title TEXT NOT NULL, original TEXT NOT NULL, translation TEXT NOT NULL, url TEXT NOT NULL)')
        con.execute("CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, chapter_id INTEGER NOT NULL, provider TEXT NOT NULL, state TEXT NOT NULL, done INTEGER DEFAULT 0, total INTEGER DEFAULT 0, error TEXT DEFAULT '')")
        con.execute("UPDATE jobs SET state='error', error='Сервер перезапущен во время перевода. Проверьте чат перед повтором.' WHERE state='running'")


if __name__ == '__main__':
    host, port = os.getenv('HOST','127.0.0.1'), int(os.getenv('PORT','8000'))
    if host not in ('127.0.0.1', 'localhost', '::1') and not os.getenv('READER_PASSWORD'):
        raise SystemExit('Для доступа с других устройств задайте READER_PASSWORD (имя пользователя: reader).')
    initialize()
    httpd = ThreadingHTTPServer((host,port), Handler)
    threading.Thread(target=worker, daemon=True).start()
    print(f'Novel Reader: http://{host}:{port}', flush=True)
    httpd.serve_forever()
