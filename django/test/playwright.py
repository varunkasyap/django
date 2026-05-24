import os
import sys
import unittest
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings, tag
from django.utils.functional import classproperty
from django.utils.text import capfirst
from playwright.sync_api import Playwright, sync_playwright


class PlaywrightTestCaseBase(type(LiveServerTestCase)):
    # List of browsers to dynamically create test classes for.
    browsers = []
    # Sentinel value to differentiate browser-specific instances.
    browser = None
    # Run browsers in headless mode.
    headless = False

    def __new__(cls, name, bases, attrs):
        """
        Dynamically create new classes and add them to the test module when
        multiple browser specs are provided (e.g. --playwright=XXXX).
        """
        test_class = super().__new__(cls, name, bases, attrs)
        # If the test class is either browser-specific or a test base, return
        # it.
        if test_class.browser or not any(
            name.startswith("test") and callable(value) for name, value in attrs.items()
        ):
            return test_class
        elif test_class.browsers:
            # Reuse the created test class to make it browser-specific.
            # We can't rename it to include the browser name or create a
            # subclass like we do with the remaining browsers as it would
            # either duplicate tests or prevent pickling of its instances.
            first_browser = test_class.browsers[0]
            test_class.browser = first_browser
            # Create subclasses for each of the remaining browsers and expose
            # them through the test's module namespace.
            module = sys.modules[test_class.__module__]
            for browser in test_class.browsers[1:]:
                browser_test_class = cls.__new__(
                    cls,
                    "%s%s" % (capfirst(browser), name),
                    (test_class,),
                    {
                        "browser": browser,
                        "__module__": test_class.__module__,
                    },
                )
                setattr(module, browser_test_class.__name__, browser_test_class)
            return test_class
        # If no browsers were specified, skip this class (it'll still be
        # discovered).
        return unittest.skip("No browsers specified.")(test_class)

    @classmethod
    def import_browser(cls, browser):
        if not hasattr(Playwright, browser):
            raise ImportError(
                "Playwright browser specification '%s' is not valid." % browser
            )


class ChangeViewportSize:
    def __init__(self, width, height, page):
        self.page = page
        self.new_size = {"width": width, "height": height}

    def __enter__(self):
        self.old_size = self.page.viewport_size
        self.page.set_viewport_size(self.new_size)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.old_size:
            self.page.set_viewport_size(self.old_size)


@tag("playwright")
class PlaywrightTestCase(LiveServerTestCase, metaclass=PlaywrightTestCaseBase):
    default_timeout = 10000  # milliseconds
    external_host = None
    screenshots = False

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not cls.screenshots:
            return

        for name, func in list(cls.__dict__.items()):
            if not hasattr(func, "_screenshot_cases"):
                continue
            # Remove the main test.
            delattr(cls, name)
            # Add separate tests for each screenshot type.
            for screenshot_case in getattr(func, "_screenshot_cases"):

                @wraps(func)
                def test(self, *args, _func=func, _case=screenshot_case, **kwargs):
                    with getattr(self, _case)():
                        return _func(self, *args, **kwargs)

                test.__name__ = f"{name}_{screenshot_case}"
                test.__qualname__ = f"{test.__qualname__}_{screenshot_case}"
                test._screenshot_name = name
                test._screenshot_case = screenshot_case
                setattr(cls, test.__name__, test)

    @classproperty
    def live_server_url(cls):
        return "http://%s:%s" % (cls.external_host or cls.host, cls.server_thread.port)

    @classproperty
    def allowed_host(cls):
        return cls.external_host or cls.host

    @classmethod
    def setUpClass(cls):
        cls._old_async_unsafe = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls._pw = sync_playwright().start()
        browser_type = getattr(cls._pw, cls.browser)
        cls._browser = browser_type.launch(headless=cls.headless)
        cls._browser_context = cls._browser.new_context()
        cls._browser_logs = []
        cls.page = cls._browser_context.new_page()
        # Set up CDP session for browser-level logs (Chromium only).
        if cls.browser == "chromium":
            cls._cdp_session = cls._browser_context.new_cdp_session(cls.page)
            cls._cdp_session.send("Log.enable")
            cls._cdp_session.on(
                "Log.entryAdded",
                lambda params: cls._browser_logs.append(params["entry"]),
            )
        cls.page.set_default_timeout(cls.default_timeout)
        cls.addClassCleanup(cls._quit_playwright)

    @contextmanager
    def desktop_size(self):
        with ChangeViewportSize(1280, 720, self.page):
            yield

    @contextmanager
    def small_screen_size(self):
        with ChangeViewportSize(1024, 768, self.page):
            yield

    @contextmanager
    def mobile_size(self):
        with ChangeViewportSize(360, 800, self.page):
            yield

    @contextmanager
    def rtl(self):
        with self.desktop_size():
            with override_settings(LANGUAGE_CODE=settings.LANGUAGES_BIDI[-1]):
                yield

    @contextmanager
    def dark(self):
        # Navigate to a page before executing a script.
        self.page.goto(self.live_server_url)
        self.page.evaluate("localStorage.setItem('theme', 'dark');")
        with self.desktop_size():
            try:
                yield
            finally:
                self.page.evaluate("localStorage.removeItem('theme');")

    # def set_emulated_media(self, *, media=None, color_scheme=None, forced_colors=None):
    #     """
    #     Use Playwright's native emulate_media API. Unlike Selenium's CDP-based
    #     approach, this works on all browsers (Chromium, Firefox, WebKit).
    #     """
    #     kwargs = {}
    #     if media is not None:
    #         kwargs["media"] = media
    #     if color_scheme is not None:
    #         kwargs["color_scheme"] = color_scheme
    #     if forced_colors is not None:
    #         kwargs["forced_colors"] = forced_colors
    #     self.page.emulate_media(**kwargs)

    @contextmanager
    def high_contrast(self):
        self.page.emulate_media(forced_colors="active")
        with self.desktop_size():
            try:
                yield
            finally:
                self.page.emulate_media(forced_colors="none")

    def take_screenshot(self, name):
        if not self.screenshots:
            return
        test = getattr(self, self._testMethodName)
        filename = f"{test._screenshot_name}--{name}--{test._screenshot_case}.png"
        path = Path.cwd() / "screenshots" / filename
        path.parent.mkdir(exist_ok=True, parents=True)
        self.page.screenshot(path=str(path))
        
    def get_browser_logs(self, source=None, level="ALL"):
        """
        Return browser logs filtered by level and optionally source.
        CDP sessions are only supported in Chromium; return an empty list
        for other browsers.
        """
        if not hasattr(self, "_cdp_session"):
            return []
        return [
            log
            for log in self._browser_logs
            if (level == "ALL" or log["level"] == level)
            and (source is None or log["source"] == source)
        ]

    @classmethod
    def _quit_playwright(cls):
        # Close resources before attempting to terminate and join the
        # single-threaded LiveServerThread to avoid a dead lock if the browser
        # kept a connection alive.
        if hasattr(cls, "_cdp_session"):
            cls._cdp_session.detach()
        if hasattr(cls, "page"):
            cls.page.close()
        if hasattr(cls, "_browser_context"):
            cls._browser_context.close()
        if hasattr(cls, "_browser"):
            cls._browser.close()
        if hasattr(cls, "_pw"):
            cls._pw.stop()
        # Restore the original DJANGO_ALLOW_ASYNC_UNSAFE value.
        if cls._old_async_unsafe is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = cls._old_async_unsafe

    @contextmanager
    def disable_implicit_wait(self):
        """Disable the default timeout temporarily."""
        self.page.set_default_timeout(0)
        try:
            yield
        finally:
            self.page.set_default_timeout(self.default_timeout)


def screenshot_cases(method_names):
    if isinstance(method_names, str):
        method_names = method_names.split(",")

    def wrapper(func):
        func._screenshot_cases = method_names
        setattr(func, "tags", {"screenshot"}.union(getattr(func, "tags", set())))
        return func

    return wrapper
