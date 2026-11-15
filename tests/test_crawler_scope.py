import unittest

from core.crawler import registrable_domain, resolve_link_base


class TestRegistrableDomain(unittest.TestCase):
    def test_shared_hosting_tenant_is_its_own_scope(self):
        """One tenant on shared hosting must not scope to every other tenant."""
        self.assertEqual(registrable_domain("juice-shop.herokuapp.com"),
                         "juice-shop.herokuapp.com")
        self.assertEqual(registrable_domain("myapp.vercel.app"), "myapp.vercel.app")
        self.assertEqual(registrable_domain("user.github.io"), "user.github.io")

    def test_ordinary_domains_unchanged(self):
        self.assertEqual(registrable_domain("foo.example.com"), "example.com")
        self.assertEqual(registrable_domain("shop.example.co.uk"), "example.co.uk")
        self.assertEqual(registrable_domain("example.com"), "example.com")

    def test_empty_host(self):
        self.assertEqual(registrable_domain(""), "")




class TestResolveLinkBase(unittest.TestCase):
    def test_offsite_redirect_yields_no_base(self):
        """Harvesting after an off-site redirect grafts foreign paths onto our host."""
        regs = {"juice-shop.herokuapp.com"}
        self.assertIsNone(resolve_link_base(
            "https://juice-shop.herokuapp.com/redirect?to=gh",
            "https://github.com/juice-shop/juice-shop", regs))

    def test_base_is_final_url_not_requested_url(self):
        regs = {"example.com"}
        self.assertEqual(
            resolve_link_base("https://example.com/a", "https://example.com/b/", regs),
            "https://example.com/b/")

    def test_falls_back_to_requested_when_page_url_empty(self):
        regs = {"example.com"}
        self.assertEqual(
            resolve_link_base("https://example.com/a", "", regs),
            "https://example.com/a")


if __name__ == "__main__":
    unittest.main()
