# Website to PDF

A web crawler that prints a website to .pdf format

:globe_with_meridians::spider_web:  :fast_forward:  :open_file_folder::scroll:

## Requirements:

:heavy_check_mark: :snake: [python 3.x](https://www.python.org/downloads/) environment

:heavy_check_mark: :file_folder: [wkHTMLtoPDF](https://wkhtmltopdf.org/) installed on system

:heavy_check_mark: :snake: [pdfkit](https://pypi.org/project/pdfkit/) pypi library. pdfkit is a python wrapper for wkHTMLtoPDF.

:heavy_check_mark: :snake: [BeautifulSoup 4](https://pypi.org/project/beautifulsoup4/) pypi library
</br></br>

## How to use:

:arrow_forward: Set list `urls_to_parse` with all URLs to save to .pdf format.
```python
urls_to_parse = ["<URL_1>", "<URL_2>", ..., "<URL_N>"] # Where URL_n is your desired URL.
```
The list can be collected by either:

:a: :arrow_right: Using return from `get_url_list_from_site( <MY SITE eg. http://example.com> )`

or

:b: :arrow_right: Using return from `get_url_list_from_file( <MY FILE | DEFAULT = input/urls.txt> )` 

:arrow_forward: Run *website-to-pdf.py*

:arrow_forward: All URLs will be saved as .pdf to the `output/` directory from source *website-to-pdf.py*
</br></br>

## Link collection (`get_url_list_from_site`):

- only absolute `http://` / `https://` addresses are returned — anchors without `href`, empty `href`, fragment-only (`#...`) links and `javascript:`, `mailto:`, `tel:`, `data:` etc. are skipped;
- relative links are resolved against the homepage with standard URL rules, including root-relative paths (`/path`) and parent fallbacks (`../path`);
- duplicate links on the same page are only collected once;
- if the homepage request itself fails, a message is printed and an empty list is returned (no unhandled exception), so `set_url_list_to_file` can still write `input/urls_from_site.txt`.
</br></br>

## Tests:

Offline unit tests (no real network access — `requests.get` is mocked) live in `tests/`:

```
python -m unittest discover -s tests -v
```

They use an HTML fragment containing absolute links, `/`-relative links, `../` fallback links, duplicates, anchors without `href` and `javascript:`/`mailto:` links, and assert that only the deduplicated, correctly expanded `http`/`https` URLs remain.
</br></br>

## License:

MIT license compliant. Software provided as is. All content is free to use and modify.

![andrewkhassapov github](https://github.com/AndrewKhassapov/website-to-pdf/assets/53222142/903caf24-211d-41e8-b526-3b474198f9fe)[^1]
[^1]: GitHub shields provided by [Shields.io](https://shields.io/)
