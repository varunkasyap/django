from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import modify_settings, override_settings
from django.test.playwright import PlaywrightTestCase
from django.utils.csp import CSP
from django.utils.translation import gettext as _

# Make unittest ignore frames in this module when reporting failures.
__unittest = True


@modify_settings(
    MIDDLEWARE={"append": "django.middleware.csp.ContentSecurityPolicyMiddleware"}
)
@override_settings(
    SECURE_CSP={
        "default-src": [CSP.NONE],
        "connect-src": [CSP.SELF],
        "img-src": [CSP.SELF],
        "script-src": [CSP.SELF],
        "style-src": [CSP.SELF],
    },
)
class AdminPlaywrightTestCase(PlaywrightTestCase, StaticLiveServerTestCase):
    available_apps = [
        "django.contrib.admin",
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.sites",
    ]

    def tearDown(self):
        # Ensure that no CSP violations were logged in the browser.
        # self.assertEqual(self.get_browser_logs(source="security"), [])
        super().tearDown()

    def trigger_resize(self):
        size = self.page.viewport_size
        self.page.set_viewport_size(
            {"width": size["width"] + 1, "height": size["height"]}
        )
        self.page.wait_for_load_state("domcontentloaded")
        self.page.set_viewport_size(size)
        self.page.wait_for_load_state("domcontentloaded")

    def admin_login(self, username, password, login_url="/admin/"):
        """
        Log in to the admin.
        """
        self.page.goto(f"{self.live_server_url}{login_url}")
        self.page.fill("input[name='username']", username)
        self.page.fill("input[name='password']", password)
        login_text = _("Log in")
        self.page.click(f"input[value='{login_text}']")
        self.page.wait_for_load_state("load")

    def select_option(self, selector, value):
        """
        Select the <OPTION> with the value `value` inside the <SELECT> widget
        identified by the CSS selector `selector`.
        """
        self.page.select_option(selector, value=value)

    def deselect_option(self, selector, value):
        """
        Deselect the <OPTION> with the value `value` inside the <SELECT> widget
        identified by the CSS selector `selector`.
        """
        option_selector = f"{selector} option[value='{value}']"
        self.page.eval_on_selector(option_selector, "el => el.selected = false")

    def assertCountPlaywrightElements(self, selector, count, root_element=None):
        """
        Assert number of matches for a CSS selector.

        `root_element` allows restriction to a pre-selected Locator.
        """
        root = root_element if root_element is not None else self.page
        self.assertEqual(root.locator(selector).count(), count)

    def _assertOptionsValues(self, options_selector, values):
        if values:
            options = self.page.locator(options_selector).all()
            actual_values = [opt.get_attribute("value") for opt in options]
            self.assertEqual(values, actual_values)
        else:
            from playwright.sync_api import expect

            expect(self.page.locator(options_selector)).to_have_count(0)

    def assertSelectOptions(self, selector, values):
        """
        Assert that the <SELECT> widget identified by `selector` has the
        options with the given `values`.
        """
        self._assertOptionsValues(f"{selector} > option", values)

    def assertSelectedOptions(self, selector, values):
        """
        Assert that the <SELECT> widget identified by `selector` has the
        selected options with the given `values`.
        """
        self._assertOptionsValues(f"{selector} > option:checked", values)

    def is_disabled(self, selector):
        """
        Return True if the element identified by `selector` has the `disabled`
        attribute.
        """
        return self.page.is_disabled(selector)
