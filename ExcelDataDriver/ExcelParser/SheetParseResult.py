class SheetParseResult(dict):
    """
    Settlement information of parsing one worksheet.

    A result is created for every worksheet found in the workbook, so an operator
    can tell how many test data rows were loaded from each sheet, why a sheet was
    skipped and where a sheet stopped producing data.

    The object behaves like a dictionary (``result['success_count']``) and exposes
    the same values as attributes (``result.success_count``), which keeps it easy to
    consume from Robot Framework variables and from Python code.
    """

    # Alternative names accepted for the canonical keys, both as attributes and as
    # dictionary keys.
    _ALIASES = {
        'title': 'sheet_name',
        'ws_title': 'sheet_name',
        'row_count': 'success_count',
        'test_data_count': 'success_count',
        'failure_reason': 'error_message',
        'skip_reason': 'error_message',
        'skipped': 'is_skipped',
        'is_truncated': 'truncated',
        'truncated_at_row_no': 'truncated_row_no',
        'truncated_reason': 'truncation_reason',
    }

    def __init__(self, sheet_name):
        dict.__init__(self)
        self._test_data_rows = []
        dict.__setitem__(self, 'sheet_name', sheet_name)
        # Number of test data rows the worksheet produced.
        dict.__setitem__(self, 'success_count', 0)
        # Failure reason when the worksheet was skipped, empty otherwise.
        dict.__setitem__(self, 'error_message', '')
        dict.__setitem__(self, 'is_skipped', False)
        dict.__setitem__(self, 'is_pass', True)
        # True when parsing stopped before the end of the worksheet data.
        dict.__setitem__(self, 'truncated', False)
        dict.__setitem__(self, 'truncated_row_index', None)
        dict.__setitem__(self, 'truncated_row_no', None)
        # Row number where parsing stopped, even when no data was left behind it.
        dict.__setitem__(self, 'stop_row_no', None)
        # Row number of the first data row after the stop, None when nothing follows.
        dict.__setitem__(self, 'next_data_row_no', None)
        dict.__setitem__(self, 'truncation_reason', '')

    ####################################################
    #
    # Dictionary / attribute access
    #
    ####################################################
    def __getitem__(self, key):
        return dict.__getitem__(self, self._ALIASES.get(key, key))

    def __contains__(self, key):
        return dict.__contains__(self, self._ALIASES.get(key, key))

    def get(self, key, default=None):
        return dict.get(self, self._ALIASES.get(key, key), default)

    def __getattr__(self, name):
        key = self._ALIASES.get(name, name)
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        if name.startswith('_'):
            object.__setattr__(self, name, value)
            return
        key = self._ALIASES.get(name, name)
        if key in self:
            dict.__setitem__(self, key, value)
            return
        object.__setattr__(self, name, value)

    ####################################################
    #
    # Result state
    #
    ####################################################
    @property
    def test_data_rows(self):
        return self._test_data_rows

    def mark_skipped(self, error_message):
        """
        Mark this sheet as skipped with the given failure reason.
        """
        self._test_data_rows = []
        dict.__setitem__(self, 'is_skipped', True)
        dict.__setitem__(self, 'is_pass', False)
        dict.__setitem__(self, 'success_count', 0)
        dict.__setitem__(self, 'error_message', '' if error_message is None else str(error_message))
        return self

    def mark_parsed(self, test_data_rows, truncation=None):
        """
        Mark this sheet as parsed successfully.

        :param test_data_rows: List of ExcelTestDataRow produced by the sheet.
        :param truncation: Optional dict describing where parsing stopped.
        """
        rows = list(test_data_rows or [])
        self._test_data_rows = rows
        dict.__setitem__(self, 'is_skipped', False)
        dict.__setitem__(self, 'is_pass', True)
        dict.__setitem__(self, 'success_count', len(rows))
        dict.__setitem__(self, 'error_message', '')
        if truncation:
            # The stop position is always kept, even when the empty main key was the
            # natural end of the data and no row was left behind it.
            dict.__setitem__(self, 'stop_row_no', truncation.get('row_no'))
            dict.__setitem__(self, 'truncated_row_no', truncation.get('row_no'))
            dict.__setitem__(self, 'next_data_row_no', truncation.get('next_data_row_no'))
            dict.__setitem__(self, 'truncation_reason', truncation.get('reason', ''))
            if truncation.get('truncated', True):
                dict.__setitem__(self, 'truncated', True)
                dict.__setitem__(self, 'truncated_row_index', truncation.get('row_index'))
        return self

    def to_dict(self):
        """
        Return a plain dictionary representation without the parsed row objects.
        """
        return dict(self)

    def __repr__(self):
        return '<SheetParseResult sheet_name={0!r} is_skipped={1} success_count={2} truncated={3}>'.format(
            self['sheet_name'], self['is_skipped'], self['success_count'], self['truncated'])
