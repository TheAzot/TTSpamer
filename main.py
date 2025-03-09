import asyncio
import random
import os
from datetime import datetime
import traceback
from typing import List, Dict
import json

from playwright.async_api import async_playwright
from playwright_stealth import stealth_async, StealthConfig
from tiktok_captcha_solver import AsyncPlaywrightSolver
from loguru import logger


class BotConfig:
    """Единый класс для всех настроек бота"""

    def __init__(self, config_path='config.json'):
        # Загружаем настройки из файла, если он существует
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.__dict__.update(config)
                logger.info(f"Настройки загружены из {config_path}")
            except Exception as e:
                logger.error(f"Ошибка при загрузке настроек: {e}")
                self._set_defaults()
        else:
            self._set_defaults()
            self.save(config_path)

    def _set_defaults(self):
        """Установка значений по умолчанию"""
        # Основные настройки
        self.sadcaptcha_api_key = "API_KEY" # https://www.sadcaptcha.com
        self.max_login_attempts = 3
        self.headless = False # Показывать браузер \ не показывать браузер

        # Пути к файлам
        self.accounts_file = "acc.txt"
        self.comments_file = "comments.txt"
        self.proxies_file = "proxies.txt"

        # Настройки времени (уменьшены согласно запросу)
        self.min_delay = 0.5
        self.max_delay = 1.0
        self.login_delay = 1.0

        # Настройки циклов
        self.cycles_per_account = 10
        self.min_next_clicks = 1
        self.max_next_clicks = 3

        # Настройки многопоточности
        self.max_concurrent_accounts = 3

        # Настройки прокси
        self.proxy_type = "http"  # По умолчанию используем http, но можно изменить в config.json

    def save(self, config_path='config.json'):
        """Сохранение настроек в файл"""
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.__dict__, f, indent=4, ensure_ascii=False)
            logger.info(f"Настройки сохранены в {config_path}")
        except Exception as e:
            logger.error(f"Ошибка при сохранении настроек: {e}")

    def random_delay(self):
        """Генерация случайной задержки"""
        return random.uniform(self.min_delay, self.max_delay)


class FileHandler:
    """Класс для работы с файлами"""

    def __init__(self, config: BotConfig):
        self.config = config

    def read_accounts(self) -> List[Dict[str, str]]:
        """Чтение аккаунтов из файла"""
        accounts = []
        try:
            with open(self.config.accounts_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if ':' in line:
                        email, password = line.strip().split(':', 1)
                        accounts.append({'email': email, 'password': password})
            logger.info(f"Загружено {len(accounts)} аккаунтов")
        except Exception as e:
            logger.error(f"Ошибка чтения аккаунтов: {e}")
        return accounts

    def read_comments(self) -> List[str]:
        """Чтение комментариев из файла"""
        comments = []
        try:
            with open(self.config.comments_file, 'r', encoding='utf-8') as f:
                comments = [line.strip() for line in f if line.strip()]
            logger.info(f"Загружено {len(comments)} комментариев")
        except Exception as e:
            logger.error(f"Ошибка чтения комментариев: {e}")
            # Если файл не найден, используем базовые комментарии
            comments = [
                "Полностью согласен с вами! 👍",
                "Очень интересная точка зрения!",
                "Спасибо за комментарий! 😊",
                "Вы абсолютно правы!",
                "Это действительно так! 💯",
                "Полностью поддерживаю!",
                "Отличный комментарий! 👏",
                "Согласен на все 100%",
                "Так точно сказано! 👌",
                "Очень хорошо подмечено!"
            ]
            logger.info(f"Используем {len(comments)} стандартных комментариев")
        return comments

    def read_proxies(self) -> List[Dict[str, str]]:
        """Чтение прокси из файла (упрощенный формат)"""
        proxies = []
        try:
            with open(self.config.proxies_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        parts = line.split(':')

                        # Формат: ip:port:username:password
                        if len(parts) >= 4:
                            proxy = {
                                'server': f"{self.config.proxy_type}://{parts[0]}:{parts[1]}",
                                'username': parts[2],
                                'password': parts[3]
                            }
                            proxies.append(proxy)

                        # Формат: ip:port
                        elif len(parts) == 2:
                            proxy = {
                                'server': f"{self.config.proxy_type}://{parts[0]}:{parts[1]}"
                            }
                            proxies.append(proxy)

            logger.info(f"Загружено {len(proxies)} прокси")
        except FileNotFoundError:
            logger.warning(f"Файл прокси '{self.config.proxies_file}' не найден. Продолжаем без прокси.")
        except Exception as e:
            logger.error(f"Ошибка чтения прокси: {e}")
        return proxies


class TikTokBot:
    """Бот для автоматизации TikTok с поддержкой прокси и многопоточности"""

    def __init__(self, config: BotConfig = None):
        """Инициализация бота"""
        self.config = config or BotConfig()
        self.file_handler = FileHandler(self.config)
        self.comments = self.file_handler.read_comments()
        self.proxies = self.file_handler.read_proxies()

    async def setup_browser(self, proxy_config=None):
        """Настройка браузера с прокси (если указано)"""
        p = await async_playwright().start()

        browser_args = ['--no-sandbox', '--disable-gpu']

        browser = await p.chromium.launch(
            headless=self.config.headless,
            args=browser_args
        )

        context_options = {
            "viewport": {'width': 1920, 'height': 1080}
        }

        if proxy_config:
            context_options["proxy"] = proxy_config

        context = await browser.new_context(**context_options)
        page = await context.new_page()

        # Настройка стелс-режима
        config = StealthConfig(
            navigator_languages=False,
            navigator_vendor=False,
            navigator_user_agent=False
        )
        await stealth_async(page, config)

        return p, browser, context, page

    async def login(self, account, proxy=None):
        """Вход в аккаунт TikTok с использованием прокси"""
        current_attempt = 1
        while current_attempt <= self.config.max_login_attempts:
            playwright = None
            browser = None

            try:
                logger.info(f"Вход: {account['email']} (попытка {current_attempt}/{self.config.max_login_attempts})")

                # Настраиваем браузер с прокси, если он указан
                playwright, browser, context, page = await self.setup_browser(proxy)

                # Инициализация решателя капчи
                sadcaptcha = AsyncPlaywrightSolver(
                    page=page,
                    sadcaptcha_api_key=self.config.sadcaptcha_api_key,
                    mouse_step_size=1,
                    mouse_step_delay_ms=10
                )

                # Выполняем вход
                await page.goto('https://www.tiktok.com/login/phone-or-email/email')
                await asyncio.sleep(self.config.login_delay)

                await page.locator('input[type="text"]').fill(account['email'])
                await asyncio.sleep(self.config.min_delay)
                await page.locator('input[type="password"]').fill(account['password'])
                await asyncio.sleep(self.config.min_delay)

                # Кнопка входа
                await page.locator('button[data-e2e="login-button"], button[type="submit"]').click()
                await asyncio.sleep(self.config.login_delay)

                # Проверка капчи
                await sadcaptcha.solve_captcha_if_present()
                await asyncio.sleep(self.config.login_delay)
                await sadcaptcha.solve_captcha_if_present()

                # Проверяем успешность входа
                if not "login" in page.url:
                    proxy_info = f" (Прокси: {proxy['server']})" if proxy else ""
                    logger.success(f"✓ Успешный вход: {account['email']}{proxy_info}")
                    return {
                        "playwright": playwright,
                        "browser": browser,
                        "context": context,
                        "page": page,
                        "sadcaptcha": sadcaptcha,
                        "account": account,
                        "proxy": proxy
                    }
                else:
                    logger.warning(f"Неудачный вход: {account['email']}")
                    await browser.close()
                    await playwright.stop()
                    current_attempt += 1

            except Exception as e:
                logger.error(f"Ошибка входа {account['email']}: {e}")
                if browser:
                    await browser.close()
                if playwright:
                    await playwright.stop()
                current_attempt += 1

        logger.error(f"Не удалось войти: {account['email']} после {self.config.max_login_attempts} попыток")
        return None

    async def process_videos(self, session_data):
        """Обработка видео на TikTok - упрощенная версия с обновлением страницы"""
        try:
            page = session_data["page"]
            sadcaptcha = session_data["sadcaptcha"]
            account = session_data["account"]
            proxy_info = f" (Прокси: {session_data['proxy']['server']})" if 'proxy' in session_data and session_data[
                'proxy'] else ""

            logger.info(f"Начинаем обработку видео для аккаунта {account['email']}{proxy_info}")

            for cycle in range(1, self.config.cycles_per_account + 1):
                logger.info(f"Цикл #{cycle} - переход на страницу For You")

                # Переходим на страницу For You
                await page.goto("https://www.tiktok.com/foryou", timeout=60000)
                await asyncio.sleep(self.config.random_delay())

                # Решаем капчу, если она появилась
                await sadcaptcha.solve_captcha_if_present()

                # Делаем скриншот для отладки
                try:
                    await page.screenshot(path=f"{account['email'].split('@')[0]}_cycle_{cycle}_foryou.png")
                except Exception as e:
                    logger.warning(f"Не удалось сделать скриншот: {e}")

                # Открываем первое видео в ленте
                logger.info("Открываем первое видео в ленте")
                await self._open_first_video(page)
                await asyncio.sleep(self.config.random_delay())

                # Решаем капчу после открытия видео
                await sadcaptcha.solve_captcha_if_present()

                # Открываем комментарии
                logger.info("Открываем комментарии...")
                comments_opened = await self._open_comments(page)

                if comments_opened:
                    logger.success("✓ Комментарии успешно открыты")
                    await asyncio.sleep(self.config.random_delay())

                    # Решаем капчу, если она появилась
                    await sadcaptcha.solve_captcha_if_present()

                    # 1. Оставляем новый комментарий
                    logger.info("Оставляем новый комментарий...")
                    comment_text = random.choice(self.comments)
                    comment_success = await self._post_comment(page, comment_text)

                    if comment_success:
                        logger.success(f"✓ Успешно оставили комментарий: '{comment_text}'")
                    else:
                        logger.warning("× Не удалось оставить комментарий")

                    await asyncio.sleep(self.config.random_delay())
                    await sadcaptcha.solve_captcha_if_present()

                    # 2. Пытаемся ответить на существующий комментарий
                    logger.info("Пытаемся ответить на существующий комментарий...")
                    reply_text = random.choice(self.comments)
                    reply_success = await self._reply_to_comment(page, reply_text)

                    if reply_success:
                        logger.success(f"✓ Успешно ответили на комментарий: '{reply_text}'")
                    else:
                        logger.warning("× Не удалось ответить на комментарий")

                    # Делаем скриншот для отладки
                    try:
                        await page.screenshot(path=f"{account['email'].split('@')[0]}_cycle_{cycle}_after_comments.png")
                    except Exception as e:
                        logger.warning(f"Не удалось сделать скриншот: {e}")
                else:
                    logger.error("× Не удалось открыть комментарии")

                # Нажимаем на кнопку "Следующее видео" случайное количество раз
                clicks_count = random.randint(self.config.min_next_clicks, self.config.max_next_clicks)
                logger.info(f"Нажимаем на кнопку 'Следующее видео' {clicks_count} раз")
                await self._click_next_button(page, clicks_count)

                # Ждем немного перед следующим циклом
                await asyncio.sleep(self.config.random_delay())

            logger.success(f"Обработано {self.config.cycles_per_account} циклов для {account['email']}{proxy_info}")
            return True

        except Exception as e:
            logger.error(f"Ошибка при обработке видео: {e}")
            logger.error(traceback.format_exc())
            return False

    async def _click_next_button(self, page, clicks_count=1):
        """Нажатие на кнопку 'Следующее видео' указанное количество раз"""
        try:
            button_selectors = [
                "#app > div.css-1yczxwx-DivBodyContainer.e1irlpdw0 > div:nth-child(4) > div > div.css-1pzb4a7-DivPlayerErrorPlaceHolder.e1fz9kua0 > div > button.css-1s9jpf8-ButtonBasicButtonContainer-StyledVideoSwitch.e11s2kul11",
                "xpath=/html/body/div[1]/div[2]/div[3]/div/div[1]/div/button[2]",
                "button.css-1s9jpf8-ButtonBasicButtonContainer-StyledVideoSwitch",
                "[data-e2e='arrow-right']",
                "[aria-label='Следующее видео']",
                "[aria-label='Next video']"
            ]

            success = False

            # Пробуем клик через JavaScript
            try:
                for _ in range(clicks_count):
                    result = await page.evaluate('''
                        const buttons = document.querySelectorAll(
                            'button.css-1s9jpf8-ButtonBasicButtonContainer-StyledVideoSwitch, ' +
                            '[data-e2e="arrow-right"], ' +
                            '[aria-label="Следующее видео"], ' +
                            '[aria-label="Next video"]'
                        );

                        for (const btn of buttons) {
                            if (btn.getBoundingClientRect().width > 0) {
                                btn.click();
                                return true;
                            }
                        }

                        return false;
                    ''')

                    if result:
                        logger.debug("Кнопка найдена и нажата через JavaScript")
                        success = True
                        await asyncio.sleep(self.config.min_delay)
                    else:
                        break
            except Exception as js_error:
                logger.debug(f"Ошибка при нажатии кнопки через JavaScript: {js_error}")

            # Если JS не сработал, пробуем через селекторы
            if not success:
                for selector in button_selectors:
                    try:
                        count = await page.locator(selector).count()
                        if count > 0:
                            for _ in range(clicks_count):
                                await page.locator(selector).first.click()
                                logger.debug(f"Кнопка нажата через селектор: {selector}")
                                await asyncio.sleep(self.config.min_delay)
                            success = True
                            break
                    except Exception as e:
                        logger.debug(f"Не удалось нажать кнопку по селектору {selector}: {e}")

            # Если все предыдущие методы не сработали, пробуем клавишу 'N'
            if not success:
                for _ in range(clicks_count):
                    await page.keyboard.press("n")
                    logger.debug("Нажата клавиша 'N' для перехода к следующему видео")
                    await asyncio.sleep(self.config.min_delay)
                success = True

            return success
        except Exception as e:
            logger.error(f"Ошибка при нажатии кнопки 'Следующее видео': {e}")
            return False

    async def _open_first_video(self, page):
        """Открытие первого видео в ленте For You"""
        try:
            # Сначала попробуем через JavaScript
            try:
                await page.evaluate('''
                    // Найдем первый видео-элемент и кликнем по нему
                    const videos = document.querySelectorAll('div[data-e2e="recommend-list-item-container"]');
                    if (videos.length > 0) {
                        videos[0].click();
                        return true;
                    }

                    // Альтернативный поиск первого видео
                    const altVideos = document.querySelectorAll('.DivItemContainer, article, [data-e2e="user-post"], .tiktok-feed-item');
                    if (altVideos.length > 0) {
                        altVideos[0].click();
                        return true;
                    }

                    return false;
                ''')
                await asyncio.sleep(self.config.random_delay())
                return True
            except Exception as js_error:
                logger.debug(f"Ошибка при открытии видео через JavaScript: {js_error}")

            # Если JS не сработал, попробуем обычные селекторы
            video_selectors = [
                'div[data-e2e="recommend-list-item-container"]',
                'article',
                '[data-e2e="user-post"]',
                '.DivItemContainer',
                '.tiktok-feed-item'
            ]

            for selector in video_selectors:
                try:
                    count = await page.locator(selector).count()
                    if count > 0:
                        await page.locator(selector).first.click()
                        await asyncio.sleep(self.config.random_delay())
                        return True
                except Exception as e:
                    logger.debug(f"Не удалось найти/кликнуть на видео по селектору {selector}: {e}")

            logger.warning("Не удалось открыть первое видео ни одним способом")
            return False

        except Exception as e:
            logger.error(f"Ошибка при открытии первого видео: {e}")
            return False

    async def _open_comments(self, page):
        """Открыть комментарии к текущему видео"""
        try:
            # Через JavaScript
            try:
                result = await page.evaluate('''
                    // Найдем все кнопки комментариев
                    const commentButtons = Array.from(document.querySelectorAll(
                        'span[data-e2e="comment-icon"], button[data-e2e="comment-icon"]'
                    ));

                    // Найдем первую видимую кнопку
                    for (const btn of commentButtons) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            btn.click();
                            return true;
                        }
                    }

                    // Альтернативный поиск
                    const commentAttr = document.querySelector(
                        '[aria-label="Открыть комментарии"], [aria-label="Open comments"]'
                    );
                    if (commentAttr) {
                        commentAttr.click();
                        return true;
                    }

                    return false;
                ''')

                if result:
                    await asyncio.sleep(self.config.random_delay())
                    return True
            except Exception as js_error:
                logger.debug(f"Ошибка при открытии комментариев через JavaScript: {js_error}")

            # Попробуем обычные селекторы
            comment_selectors = [
                'span[data-e2e="comment-icon"]',
                'button[data-e2e="comment-icon"]',
                '.comment-icon',
                '[aria-label="Открыть комментарии"]',
                '[aria-label="Open comments"]'
            ]

            for selector in comment_selectors:
                try:
                    count = await page.locator(selector).count()
                    if count > 0:
                        await page.locator(selector).first.click()
                        await asyncio.sleep(self.config.random_delay())
                        return True
                except Exception as e:
                    logger.debug(f"Не удалось найти/нажать кнопку по селектору {selector}: {e}")

            # Попробуем клавишу "C"
            try:
                await page.keyboard.press("c")
                await asyncio.sleep(self.config.random_delay())
                return True
            except Exception as e:
                logger.debug(f"Ошибка при использовании шортката: {e}")

            return False
        except Exception as e:
            logger.error(f"Ошибка при открытии комментариев: {e}")
            return False

    async def _post_comment(self, page, comment_text):
        """Оставить новый комментарий"""
        try:
            # Найдем поле для ввода комментария
            input_selectors = [
                'div[contenteditable="true"]',
                '[data-e2e="comment-input"]',
                'div[role="textbox"]',
                '.public-DraftEditor-content',
                '[placeholder="Добавить комментарий..."]',
                '[placeholder="Add comment..."]'
            ]

            for selector in input_selectors:
                try:
                    count = await page.locator(selector).count()
                    if count > 0:
                        # Нажимаем на поле ввода
                        input_field = page.locator(selector).first
                        await input_field.click()
                        await asyncio.sleep(self.config.min_delay)

                        # Пробуем разные методы для ввода текста
                        try:
                            await input_field.fill(comment_text)
                        except Exception:
                            try:
                                await input_field.type(comment_text)
                            except Exception:
                                await page.keyboard.type(comment_text)

                        await asyncio.sleep(self.config.min_delay)

                        # Отправляем комментарий
                        post_success = False

                        # Пробуем найти кнопку отправки
                        post_selectors = [
                            '[data-e2e="comment-post-btn"]',
                            'button.css-fdy45n-DivPostButton',
                            'button.css-1gjo2yl-DivPostButton',
                            'div[role="button"]:has-text("Опубликовать")',
                            'div[role="button"]:has-text("Post")'
                        ]

                        for post_selector in post_selectors:
                            try:
                                post_count = await page.locator(post_selector).count()
                                if post_count > 0:
                                    await page.locator(post_selector).first.click()
                                    post_success = True
                                    break
                            except Exception:
                                continue

                        # Если не нашли кнопку, пробуем Enter
                        if not post_success:
                            await page.keyboard.press("Enter")

                        await asyncio.sleep(self.config.random_delay())
                        return True
                except Exception as e:
                    logger.debug(f"Ошибка при работе с селектором {selector}: {e}")

            # Если не нашли поле ввода, пробуем Tab и ввод
            await page.keyboard.press("Tab")
            await asyncio.sleep(self.config.min_delay)
            await page.keyboard.type(comment_text)
            await asyncio.sleep(0.1)
            await page.keyboard.press("Enter")
            await asyncio.sleep(self.config.random_delay())

            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении комментария: {e}")
            return False

    async def _reply_to_comment(self, page, reply_text):
        """Ответить на существующий комментарий"""
        try:
            # Прокрутим панель комментариев, чтобы найти комментарии
            try:
                await page.mouse.wheel(0, random.randint(200, 300))
                await asyncio.sleep(self.config.min_delay)
            except Exception:
                pass

            # Ищем кнопки "Ответить"
            reply_selectors = [
                'span:has-text("Ответить")',
                'span:has-text("Reply")',
                'span.css-cpmlpt-SpanReplyButton',
                '[data-e2e="comment-reply-btn"]'
            ]

            for selector in reply_selectors:
                try:
                    count = await page.locator(selector).count()
                    if count > 0:
                        # Берем случайную кнопку ответа, если их много
                        index = random.randint(0, min(count - 1, 5))  # Первые 5 комментариев
                        reply_button = page.locator(selector).nth(index)

                        if await reply_button.is_visible():
                            await reply_button.click()
                            await asyncio.sleep(self.config.random_delay())

                            # Вводим текст ответа
                            input_selectors = [
                                'div[contenteditable="true"]',
                                '[data-e2e="comment-input"]',
                                'div[role="textbox"]'
                            ]

                            for input_selector in input_selectors:
                                try:
                                    input_count = await page.locator(input_selector).count()
                                    if input_count > 0:
                                        input_field = page.locator(input_selector).first

                                        await input_field.click()
                                        await asyncio.sleep(self.config.min_delay)

                                        try:
                                            await input_field.fill(reply_text)
                                        except Exception:
                                            try:
                                                await input_field.type(reply_text)
                                            except Exception:
                                                await page.keyboard.type(reply_text)

                                        await asyncio.sleep(self.config.min_delay)

                                        # Отправляем ответ
                                        post_success = False
                                        post_selectors = [
                                            '[data-e2e="comment-post-btn"]',
                                            'button.css-fdy45n-DivPostButton',
                                            'div[role="button"]:has-text("Опубликовать")',
                                            'div[role="button"]:has-text("Post")'
                                        ]

                                        for post_selector in post_selectors:
                                            try:
                                                post_count = await page.locator(post_selector).count()
                                                if post_count > 0:
                                                    await page.locator(post_selector).first.click()
                                                    post_success = True
                                                    break
                                            except Exception:
                                                continue

                                        # Если не нашли кнопку, пробуем Enter
                                        if not post_success:
                                            await page.keyboard.press("Enter")

                                        await asyncio.sleep(self.config.random_delay())
                                        return True
                                except Exception as e:
                                    logger.debug(f"Ошибка при работе с селектором {input_selector}: {e}")
                except Exception as e:
                    logger.debug(f"Ошибка при работе с селектором {selector}: {e}")

            # Если не нашли кнопки "Ответить", пробуем кликнуть на комментарий
            comment_selectors = [
                'div.comment-item',
                '.DivCommentItemContainer',
                '[data-e2e="comment-item"]'
            ]

            for selector in comment_selectors:
                try:
                    count = await page.locator(selector).count()
                    if count > 0:
                        # Выбираем случайный комментарий из первых 5
                        index = random.randint(0, min(count - 1, 5))
                        comment = page.locator(selector).nth(index)

                        await comment.click()
                        await asyncio.sleep(self.config.min_delay)

                        # Ищем кнопку ответа рядом с комментарием
                        reply_texts = ["Ответить", "Reply"]
                        for text in reply_texts:
                            try:
                                await page.get_by_text(text, exact=True).click()
                                await asyncio.sleep(self.config.min_delay)

                                # Вводим текст
                                await page.keyboard.type(reply_text)
                                await asyncio.sleep(self.config.min_delay)
                                await page.keyboard.press("Enter")
                                await asyncio.sleep(self.config.random_delay())
                                return True
                            except Exception:
                                pass
                except Exception as e:
                    logger.debug(f"Ошибка при работе с селектором {selector}: {e}")

            logger.warning("Не найдены кнопки для ответа на комментарий")
            return False
        except Exception as e:
            logger.error(f"Ошибка при ответе на комментарий: {e}")
            return False

    async def close_session(self, session_data):
        """Закрытие сессии и освобождение ресурсов"""
        try:
            if session_data:
                if "browser" in session_data:
                    await session_data["browser"].close()
                if "playwright" in session_data:
                    await session_data["playwright"].stop()
                proxy_info = f" (Прокси: {session_data['proxy']['server']})" if 'proxy' in session_data and \
                                                                                session_data['proxy'] else ""
                logger.info(f"Сессия закрыта для {session_data['account']['email']}{proxy_info}")
        except Exception as e:
            logger.error(f"Ошибка при закрытии сессии: {e}")

    async def process_account(self, account, proxy=None):
        """Обработка одного аккаунта"""
        try:
            # Вход в аккаунт
            session_data = await self.login(account, proxy)

            if session_data:
                # Обработка видео
                await self.process_videos(session_data)

                # Закрытие сессии
                await self.close_session(session_data)
                return True
            else:
                logger.error(f"Не удалось войти с аккаунтом {account['email']}")
                return False

        except Exception as e:
            logger.error(f"Ошибка при обработке аккаунта {account['email']}: {e}")
            return False


async def main():
    """Главная функция с многопоточной обработкой аккаунтов"""
    # Создаем и загружаем конфигурацию
    config = BotConfig()

    # Настройка логирования
    logger.remove()
    logger.add(f"tiktok_bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log", level="INFO")
    logger.add(
        lambda msg: print(msg, end=""),
        level="INFO",
        format="{time:HH:mm:ss} | <level>{message}</level>"
    )

    # Инициализация бота
    bot = TikTokBot(config)

    # Чтение аккаунтов и прокси
    file_handler = FileHandler(config)
    accounts = file_handler.read_accounts()
    proxies = file_handler.read_proxies()

    if not accounts:
        logger.error("Аккаунты не найдены. Пожалуйста, добавьте аккаунты в файл acc.txt")
        return

    # Ограничение на количество параллельных задач
    semaphore = asyncio.Semaphore(config.max_concurrent_accounts)

    async def process_with_semaphore(account, proxy=None):
        """Обработка аккаунта с ограничением по семафору"""
        async with semaphore:
            start_time = datetime.now()
            result = await bot.process_account(account, proxy)
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            proxy_info = f" (Прокси: {proxy['server']})" if proxy else ""
            logger.info(f"Аккаунт {account['email']}{proxy_info} обработан за {duration:.1f} сек")
            return result

    # Создаем задачи для всех аккаунтов
    tasks = []
    for i, account in enumerate(accounts):
        # Если есть доступные прокси, назначаем их аккаунтам по кругу
        proxy = None
        if proxies:
            proxy = proxies[i % len(proxies)]
        tasks.append(process_with_semaphore(account, proxy))

    # Запускаем все задачи параллельно
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Выводим статистику
    success_count = sum(1 for result in results if result is True)
    logger.info(f"Работа завершена. Обработано аккаунтов: {len(accounts)}, успешно: {success_count}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Программа принудительно остановлена")
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")
        logger.critical(traceback.format_exc())