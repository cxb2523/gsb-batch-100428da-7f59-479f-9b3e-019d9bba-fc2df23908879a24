This internal package provides a unified way to extract main body from `html` and `pdf`.

### Usage

Feed a url to the `parser_factory` and you will get a page object

 ```
 page = parser_factory('https://github.com/polyrabbit/hacker-news-digest')
 ```

From the page you can get the main body via the `aritcle` attribute, the summary of body via the `get_summary` method, the illustration via the `get_illustration` method, and the favicon url via the `get_favicon_url` method.

```
>>> page.article
lots of html stuff

>>> page.get_summary(max_length=100)
u'This service extracts summaries and images from  hacker newsarticles for people who want to get the  ...'

>>> page.get_illustration()
<page_content_extractor.html.WebImage at 0x10bc28cd0>

>>> page.get_favicon_url()
'https://github.com/fluidicon.png'
```

### How the body was fetched

Every page object also carries a `parse_result` describing the dispatch
decision, so a bad summary can be traced back to the chain that produced it:

```
>>> page.parse_result.to_dict()
{
  'requested_url': 'https://example.com/article',
  'final_url': 'https://www.example.com/article',   # after redirects
  'parser_type': 'html',                            # embeddable | pdf | html
  'via_jina': False,                                # body came from r.jina.ai
  'response_url': 'https://www.example.com/article',
  'declared_content_type': 'text/html',
  'sniffed_format': 'html',                         # pdf | html | text | binary | empty
  'content_type_missing': False,
  'content_type_mismatch': False,
  'redirected': True,
  'redirects': ['https://example.com/article'],
  'notes': ['followed redirect https://example.com/article'],
  'attempts': [...]                                 # one entry per request
}
```

`final_url` is the article address after following redirects, and is restored to
the original article address when the body was fetched through the `r.jina.ai`
proxy, so favicon and relative image urls never leak the proxy host. A jina
retry only ever happens when the original html body is empty, and it shows up as
a second entry in `attempts`.

When the `content-type` header is missing or disagrees with the body, the
dispatch still runs in the existing pdf-before-html order; `sniffed_format`,
`content_type_missing`, `content_type_mismatch` and `notes` record what was
decided and why.