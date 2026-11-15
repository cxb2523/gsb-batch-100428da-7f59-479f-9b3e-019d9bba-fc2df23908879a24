"""In-memory fakes of the small slice of the Selenium API used by main.py.

No browser is launched: the "page" is a list of sections, each with an h3
header and transaction rows, plus an optional Load more button that reveals
the next page of sections. Rows that want a panel timeout simply raise
NoSuchElementException when their panel xpath is looked up.
"""

from selenium.common.exceptions import NoSuchElementException, TimeoutException

import main


class FakeElement:
    def __init__(self, text='', xpath_children=None, tag_name='span', click_fn=None):
        self.text = text
        self.tag_name = tag_name
        self._xpath_children = xpath_children or {}
        self._click_fn = click_fn
        self._dd = None
        self._section_header = None  # set on rows for the ancestor:: lookup

    @staticmethod
    def _step_index(step):
        # 'span[2]' -> 2 (1-based); 'span[last()]' -> -1 meaning "last".
        if 'last()' in step:
            return -1
        digits = ''.join(ch for ch in step if ch.isdigit())
        return int(digits) if digits else 1

    def _walk(self, steps):
        node = self
        for step in steps:
            tag = step.split('[', 1)[0]
            # Children are grouped by tag in document order; the step's index
            # selects within that sibling group.
            group = node._xpath_children.get(f'./{tag}', [])
            if not group:
                return None
            index = self._step_index(step)
            try:
                node = group[index - 1] if index > 0 else group[index]
            except IndexError:
                return None
        return node

    def find_element(self, by, value):
        if value == main.XPATH_SECTION_HEADER:
            if self._section_header is not None:
                return self._section_header
            raise NoSuchElementException("no section header")
        if value.startswith('./'):
            node = self._walk(value[2:].split('/'))
            if node is not None:
                return node
        raise NoSuchElementException(f"fake element: no xpath {value}")

    def find_elements(self, by, value):
        if value == './following-sibling::dd[1]':
            return [self._dd] if self._dd is not None else []
        if value.startswith('./'):
            node = self._walk(value[2:].split('/'))
            return [node] if node is not None else []
        return self._xpath_children.get(value, [])

    def click(self):
        if self._click_fn:
            self._click_fn()

    def __repr__(self):
        return f"FakeElement(text={self.text!r})"


class FakeWebDriverWait:
    """Replacement for selenium's WebDriverWait that never sleeps."""

    def __init__(self, driver, timeout):
        self.driver = driver
        self.timeout = timeout

    def until(self, condition, message=''):
        # Selenium waits call the condition with the driver as its argument;
        # both EC predicates and lambda d: ... expect that single argument.
        try:
            result = condition(self.driver)
        except NoSuchElementException:
            result = False
        if result:
            return result
        raise TimeoutException(message or "fake wait timed out")


def _make_panel(details):
    panel = FakeElement(tag_name='div')
    dts = []
    for label, value in details.items():
        dt = FakeElement(text=label, tag_name='dt')
        dd = FakeElement(text=value, tag_name='dd')
        dt._dd = dd
        dts.append(dt)

    def panel_find_elements(by, value):
        # _read_panel_details looks up dt elements by tag name.
        return list(dts) if value == 'dt' else []

    panel.find_elements = panel_find_elements
    return panel


class FakeRow(FakeElement):
    """One activity-row button with the nested span layout main.py expects.

    The real row is a button whose first span holds description/type and
    whose third span holds the amount; the xpaths walk several nesting levels
    deep, so the fake mirrors that full tree rather than a flattened one.
    """

    def __init__(self, description, transaction_type, amount,
                 details=None, panel_timeout=False):
        super().__init__(tag_name='button', click_fn=self._click)
        self.details = details or {}
        self.panel_timeout = panel_timeout
        self.expanded = False
        self.panel = _make_panel(self.details) if not panel_timeout else None

        # ./span[1]/span[2]/span[1] -> description (the leaf itself holds text).
        desc_leaf = FakeElement(text=description)

        # ./span[1]/span[2]/span[2]/span[1]/span[1]/span[1] -> type.
        # From the shared container: span[2]=l2, then three span[1] steps
        # (l3 -> l4 -> text leaf) to satisfy the full six-step xpath.
        type_leaf = FakeElement(text=transaction_type)
        type_l4 = FakeElement(xpath_children={'./span': [type_leaf]})
        type_l3 = FakeElement(xpath_children={'./span': [type_l4]})
        type_l2 = FakeElement(xpath_children={'./span': [type_l3]})

        # The description/type pair shares the span[1]/span[2] node:
        # span[1] is the description leaf, span[2] starts the type chain.
        span1_span2 = FakeElement(xpath_children={
            './span': [desc_leaf, type_l2],
        })
        # Row's span[1]: an icon span then the description/type container.
        span1 = FakeElement(xpath_children={
            './span': [FakeElement(), span1_span2],
        })

        # ./span[1]/span[3]/span[last()]/span[1]/span[1] -> amount.
        amount_leaf = FakeElement(text=amount)
        amount_l2 = FakeElement(xpath_children={'./span': [amount_leaf]})
        amount_l1 = FakeElement(xpath_children={'./span': [amount_l2]})
        amount_last = FakeElement(xpath_children={'./span': [amount_l1]})
        span3 = FakeElement(xpath_children={'./span': [amount_last]})

        self._xpath_children = {
            './span': [span1, FakeElement(), span3],
        }

    def _click(self):
        if self.panel_timeout:
            return
        self.expanded = not self.expanded


class FakeSection(FakeElement):
    def __init__(self, header_text, rows):
        super().__init__(tag_name='div')
        self.h3 = FakeElement(text=header_text, tag_name='h3')
        self.rows = rows
        for row in rows:
            row._section_header = self.h3


class _LoadMoreButton(FakeElement):
    def __init__(self, driver):
        super().__init__(tag_name='button', click_fn=driver._advance_page)
        label = FakeElement(text='Load more', tag_name='span')
        self._xpath_children = {'.//span[text()="Load more"]': [label]}


class FakeDriver:
    """A driver over a paged list of FakeSection objects."""

    def __init__(self, pages, revealed_pages=1):
        # pages: list of pages; each page is a list of FakeSection.
        self.pages = pages
        self.revealed_pages = max(1, revealed_pages)
        self.advanced = 0
        self.load_more = _LoadMoreButton(self)
        self._assign_xpaths()

    def _all_revealed_sections(self):
        sections = []
        for page in self.pages[:self.revealed_pages]:
            sections.extend(page)
        return sections

    def _all_rows(self):
        return [row for section in self._all_revealed_sections() for row in section.rows]

    def _assign_xpaths(self):
        for position, row in enumerate(self._all_rows(), start=1):
            row.panel_xpath = f"({main.XPATH_ROWS})[{position}]/following-sibling::div[1]"

    def _advance_page(self):
        if self.revealed_pages < len(self.pages):
            self.revealed_pages += 1
            self.advanced += 1
            self._assign_xpaths()

    def execute_script(self, script, *args):
        for arg in args:
            if isinstance(arg, FakeElement):
                arg.click()

    def find_element(self, by, value):
        if value == main.XPATH_LOAD_MORE:
            if self.revealed_pages < len(self.pages):
                return self.load_more
            raise NoSuchElementException("no more pages")
        rows = self._all_rows()
        if value == main.XPATH_ROWS:
            if not rows:
                raise NoSuchElementException("no rows")
            return rows[0]
        if value.startswith(f"({main.XPATH_ROWS})["):
            position = int(value.split(')[', 1)[1].split(']', 1)[0])
            rows = self._all_rows()
            if position <= len(rows):
                row = rows[position - 1]
                if row.panel_timeout or not row.expanded:
                    raise NoSuchElementException(f"panel not present for row {position}")
                return row.panel
            raise NoSuchElementException(value)
        if value == main.XPATH_HEADERS:
            sections = self._all_revealed_sections()
            if sections:
                return sections[-1].h3
            raise NoSuchElementException("no headers")
        raise NoSuchElementException(value)

    def find_elements(self, by, value):
        if value == main.XPATH_ROWS:
            return self._all_rows()
        if value == main.XPATH_HEADERS:
            return [section.h3 for section in self._all_revealed_sections()]
        if value == main.XPATH_LOAD_MORE:
            return [self.load_more] if self.revealed_pages < len(self.pages) else []
        return []
