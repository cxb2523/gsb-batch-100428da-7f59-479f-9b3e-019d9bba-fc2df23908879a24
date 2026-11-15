from crawlforge.engine.normalize import canonicalize, url_id


def test_lowercases_scheme_and_host():
    assert canonicalize("HTTP://Example.COM/Path") == "http://example.com/Path"


def test_strips_default_ports():
    assert canonicalize("https://example.com:443/x") == "https://example.com/x"
    assert canonicalize("http://example.com:80/x") == "http://example.com/x"


def test_keeps_non_default_ports():
    assert canonicalize("http://example.com:8080/x") == "http://example.com:8080/x"


def test_strips_fragment():
    assert canonicalize("https://a.com/x#frag") == "https://a.com/x"


def test_sorts_query_params():
    assert canonicalize("https://a.com/?b=2&a=1") == "https://a.com/?a=1&b=2"


def test_collapses_trailing_slash_except_root():
    assert canonicalize("https://a.com/") == "https://a.com/"
    assert canonicalize("https://a.com/path/") == "https://a.com/path"


def test_url_id_is_stable():
    assert url_id("https://a.com/") == url_id("https://A.COM/?")
    assert url_id("https://a.com/") != url_id("https://a.com/x")


def test_url_id_is_unsigned_64bit():
    uid = url_id("https://a.com/")
    assert 0 <= uid < 2**64
