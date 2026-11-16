"""Small screenshot-based remote control, avoiding an extra desktop/VNC process."""
import math
import queue
import threading
import time
from concurrent.futures import Future, TimeoutError
from browser_chat import BROWSER_LOCK, SITES, launch

KEYS = {'Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'ArrowUp', 'ArrowDown',
        'ArrowLeft', 'ArrowRight', 'Control+A', 'Space'}


def validate_action(body):
    action = body.get('action')
    if action == 'click':
        x, y = float(body['x']), float(body['y'])
        if not (math.isfinite(x) and math.isfinite(y) and 0 <= x < 1024 and 0 <= y < 768):
            raise ValueError('Координаты вне окна.')
        return action, (x, y)
    if action == 'text':
        value = body.get('text')
        if not isinstance(value, str) or not 0 < len(value) <= 20000:
            raise ValueError('Введите от 1 до 20 000 символов.')
        return action, value
    if action == 'key' and body.get('key') in KEYS: return action, body['key']
    if action == 'scroll':
        value = int(body.get('delta', 0))
        if abs(value) > 1000: raise ValueError('Слишком большая прокрутка.')
        return action, value
    raise ValueError('Недопустимая команда браузера.')


class RemoteLogin:
    def __init__(self):
        self.mutex = threading.Lock()
        self.state = {'state': 'closed', 'provider': '', 'message': ''}
        self.commands = None
        self.stop = None

    def status(self):
        with self.mutex: return dict(self.state)

    def update(self, **values):
        with self.mutex: self.state.update(values)

    def start(self, provider):
        if provider not in SITES: raise ValueError('Выберите ChatGPT или Gemini.')
        if not BROWSER_LOCK.acquire(blocking=False):
            raise ValueError('Браузер занят переводом или входом. Сначала завершите текущую операцию.')
        with self.mutex:
            self.commands, self.stop = queue.Queue(maxsize=8), threading.Event()
            self.state = {'state': 'starting', 'provider': provider, 'message': 'Запускаем браузер…'}
        threading.Thread(target=self.run, args=(provider, self.commands, self.stop), daemon=True).start()
        return self.status()

    def close(self):
        with self.mutex:
            if self.stop: self.stop.set()
        return self.status()

    def request(self, action, value=None):
        with self.mutex:
            if self.state['state'] != 'ready': raise ValueError('Окно браузера ещё не готово или уже закрыто.')
            commands = self.commands
        future = Future()
        try: commands.put_nowait((action, value, future))
        except queue.Full: raise ValueError('Браузер занят. Подождите и обновите экран.')
        try: return future.result(timeout=20)
        except TimeoutError:
            future.cancel()
            raise ValueError('Браузер не ответил. Обновите экран; команда автоматически не повторяется.')

    def run(self, provider, commands, stop):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                context = launch(p, provider)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.set_viewport_size({'width': 1024, 'height': 768})
                    page.goto(SITES[provider]['url'], wait_until='domcontentloaded', timeout=60000)
                    self.update(state='ready', message='Войдите вручную. После входа нажмите «Завершить вход». Окно закроется через 10 минут.')
                    deadline = time.monotonic() + 600
                    while not stop.is_set() and time.monotonic() < deadline:
                        try: action, value, future = commands.get(timeout=0.5)
                        except queue.Empty: continue
                        if not future.set_running_or_notify_cancel(): continue
                        try:
                            pages = [tab for tab in context.pages if not tab.is_closed()]
                            if not pages: raise ValueError('Окно браузера закрыто.')
                            page = pages[-1]  # Include login popups.
                            if action == 'shot':
                                result = page.screenshot(type='jpeg', quality=65, timeout=12000)
                            else:
                                if action == 'click': page.mouse.click(*value)
                                elif action == 'text': page.keyboard.insert_text(value)
                                elif action == 'key': page.keyboard.press(value)
                                elif action == 'scroll': page.mouse.wheel(0, value)
                                else: raise ValueError('Недопустимая команда.')
                                result = {'ok': True}
                            future.set_result(result)
                        except Exception:
                            future.set_exception(ValueError('Не удалось выполнить действие. Проверьте экран браузера.'))
                finally: context.close()
            self.update(state='closed', message='Браузер закрыт. Теперь можно запустить перевод. Вход сохранён только до остановки сервера.')
        except Exception:
            self.update(state='error', message='Chromium не запустился или страница недоступна. Проверьте журнал Render: возможна нехватка памяти.')
        finally:
            # Release only after pending requests are rejected; next session uses its own queue.
            while True:
                try:
                    _, _, future = commands.get_nowait()
                    if future.set_running_or_notify_cancel(): future.set_exception(ValueError('Сеанс завершён.'))
                except queue.Empty: break
            BROWSER_LOCK.release()


REMOTE = RemoteLogin()
