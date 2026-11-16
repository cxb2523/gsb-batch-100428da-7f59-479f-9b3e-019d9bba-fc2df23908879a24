import os
import shutil
import tempfile
from unittest import TestCase

from openpyxl import Workbook

from ExcelDataDriver import ExcelDataDriver
from ExcelDataDriver.ExcelParser.DefaultParserStrategy import DefaultParserStrategy
from ExcelDataDriver.ExcelParser.ParserContext import ParserContext
from ExcelDataDriver.Util.OpenpyxlHelper import OpenpyxlHelper


MANDATORY_COLUMNS = ['[Status]', '[Log Message]', '[Screenshot]', '[Tags]']
TEST_DATA_COLUMNS = ['username', 'Password']


class TestSheetSettlement(TestCase):
    """
    Multi worksheet loading must settle every worksheet separately.
    """

    def setUp(self):
        self.workdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _create_workbook(self, filename, sheets):
        """
        Create a workbook from an ordered list of (sheet_title, rows) pairs.
        """
        wb = Workbook()
        wb.remove(wb.active)
        for sheet_title, rows in sheets:
            ws = wb.create_sheet(title=sheet_title)
            for row in rows:
                ws.append(row)
        path = os.path.join(self.workdir, filename)
        wb.save(path)
        return path

    def _valid_sheet_rows(self, usernames):
        return [MANDATORY_COLUMNS + TEST_DATA_COLUMNS] + [
            [None, None, None, None, username, 'Welcome1'] for username in usernames]

    def _invalid_sheet_rows(self, usernames):
        # [Screenshot] and [Tags] are missing.
        return [['[Status]', '[Log Message]'] + TEST_DATA_COLUMNS] + [
            [None, None, username, 'Welcome1'] for username in usernames]

    ####################################################
    #
    # Per worksheet settlement
    #
    ####################################################
    def test_load_test_data_WithOneInvalidSheet_LoadsRemainingSheets(self):
        # Arrange
        path = self._create_workbook('mixed.xlsx', [
            ('first', self._valid_sheet_rows(['demo1', 'demo2'])),
            ('broken', self._invalid_sheet_rows(['lost'])),
            ('last', self._valid_sheet_rows(['demo3'])),
        ])
        core_rpa = ExcelDataDriver()

        # Act
        core_rpa.load_test_data(path, 'username')
        test_data_list = core_rpa.get_all_test_data()

        # Assert
        self.assertEqual(['demo1', 'demo2', 'demo3'],
                         [test_data.get_test_data_property('username') for test_data in test_data_list])
        self.assertEqual(['first', 'first', 'last'], [test_data.excel_title for test_data in test_data_list])
        self.assertEqual(2, core_rpa.get_sheet_test_data_count('first'))
        self.assertEqual(0, core_rpa.get_sheet_test_data_count('broken'))
        self.assertEqual(1, core_rpa.get_sheet_test_data_count('last'))
        self.assertEqual(['broken'], core_rpa.get_skipped_sheet_names())

    def test_get_sheet_parse_results_WithInvalidSheet_ReportsFailureReason(self):
        # Arrange
        path = self._create_workbook('mixed.xlsx', [
            ('first', self._valid_sheet_rows(['demo1'])),
            ('broken', self._invalid_sheet_rows(['lost'])),
        ])
        core_rpa = ExcelDataDriver()
        core_rpa.load_test_data(path, 'username')

        # Act
        sheet_results = core_rpa.get_sheet_parse_results()

        # Assert
        self.assertEqual(['first', 'broken'], list(sheet_results.keys()))
        self.assertFalse(sheet_results['first']['is_skipped'])
        self.assertEqual('', sheet_results['first']['error_message'])
        self.assertTrue(sheet_results['broken']['is_skipped'])
        self.assertTrue(sheet_results['broken']['is_pass'] is False)
        self.assertIn('[Screenshot]', sheet_results['broken']['error_message'])
        self.assertIn('[Tags]', sheet_results['broken']['error_message'])
        self.assertNotIn('[Status]', sheet_results['broken']['error_message'])

    def test_get_sheet_parse_result_WithUnknownSheet_ReturnsNone(self):
        # Arrange
        path = self._create_workbook('single.xlsx', [('only', self._valid_sheet_rows(['demo1']))])
        core_rpa = ExcelDataDriver()
        core_rpa.load_test_data(path, 'username')

        # Act
        result = core_rpa.get_sheet_parse_result('missing sheet')

        # Assert
        self.assertIsNone(result)

    def test_load_test_data_WithEmptyMainKey_ReportsTruncatedRow(self):
        # Arrange
        rows = [MANDATORY_COLUMNS + TEST_DATA_COLUMNS,
                [None, None, None, None, 'demo1', 'Welcome1'],
                [None, None, None, None, None, None],
                [None, None, None, None, 'demo2', 'Welcome1']]
        path = self._create_workbook('truncated.xlsx', [('truncated', rows)])
        core_rpa = ExcelDataDriver()

        # Act
        core_rpa.load_test_data(path, 'username')
        test_data_list = core_rpa.get_all_test_data()
        sheet_result = core_rpa.get_sheet_parse_result('truncated')

        # Assert
        self.assertEqual(1, len(test_data_list))
        self.assertEqual('demo1', test_data_list[0].get_test_data_property('username'))
        self.assertEqual(1, sheet_result['success_count'])
        self.assertTrue(sheet_result['truncated'])
        self.assertEqual(3, sheet_result['truncated_row_no'])
        self.assertEqual(4, sheet_result['next_data_row_no'])
        self.assertIn('username', sheet_result['truncation_reason'])

    def test_get_sheet_parse_result_WithCompleteData_IsNotTruncated(self):
        # Arrange
        path = self._create_workbook('complete.xlsx', [('complete', self._valid_sheet_rows(['demo1']))])
        core_rpa = ExcelDataDriver()

        # Act
        core_rpa.load_test_data(path, 'username')
        sheet_result = core_rpa.get_sheet_parse_result('complete')

        # Assert
        self.assertFalse(sheet_result['truncated'])
        self.assertIsNone(sheet_result['truncated_row_no'])
        self.assertIsNone(sheet_result['stop_row_no'])

    def test_get_sheet_parse_result_WithTrailingEmptyRow_ReportsStoppedRow(self):
        # Arrange
        rows = [MANDATORY_COLUMNS + TEST_DATA_COLUMNS,
                [None, None, None, None, 'demo1', 'Welcome1'],
                [None, None, None, None, None, 'trailing value']]
        path = self._create_workbook('trailing.xlsx', [('trailing', rows)])
        core_rpa = ExcelDataDriver()

        # Act
        core_rpa.load_test_data(path, 'username')
        sheet_result = core_rpa.get_sheet_parse_result('trailing')

        # Assert
        self.assertEqual(1, sheet_result['success_count'])
        self.assertFalse(sheet_result['truncated'])
        self.assertEqual(3, sheet_result['truncated_row_no'])
        self.assertIsNone(sheet_result['next_data_row_no'])

    def test_parse_WithEverySheetInvalid_RaisesErrorAndKeepsSettlement(self):
        # Arrange
        path = self._create_workbook('all_broken.xlsx', [
            ('broken one', self._invalid_sheet_rows(['lost1'])),
            ('broken two', [['[Status]']]),
        ])
        parser_context = ParserContext(DefaultParserStrategy('username'))

        # Act
        with self.assertRaises(ValueError) as raised:
            parser_context.parse(OpenpyxlHelper.load_excel_file(path))

        # Assert
        self.assertIn('broken one', str(raised.exception))
        self.assertIn('broken two', str(raised.exception))
        self.assertEqual(['broken one', 'broken two'], list(parser_context.get_sheet_parse_results().keys()))
        for sheet_result in parser_context.get_sheet_parse_results().values():
            self.assertTrue(sheet_result.is_skipped)

    def test_load_test_data_WithEverySheetInvalid_RaisesErrorAndKeepsSettlement(self):
        # Arrange
        path = self._create_workbook('all_broken.xlsx', [
            ('broken one', self._invalid_sheet_rows(['lost1'])),
        ])
        from ExcelDataDriver.ExcelParser.ExcelTestDataService import ExcelTestDataService
        service = ExcelTestDataService()

        # Act
        with self.assertRaises(ValueError):
            service.load_test_data(path, DefaultParserStrategy('username'))

        # Assert
        self.assertEqual(['broken one'], service.get_skipped_sheet_names())
        self.assertEqual(0, service.get_sheet_test_data_count('broken one'))

    def test_parse_WithEverySheetValid_KeepsSheetOrder(self):
        # Arrange
        path = self._create_workbook('ordered.xlsx', [
            ('second', self._valid_sheet_rows(['demo2'])),
            ('first', self._valid_sheet_rows(['demo1'])),
        ])
        parser_context = ParserContext(DefaultParserStrategy('username'))

        # Act
        ws_test_datas = parser_context.parse(OpenpyxlHelper.load_excel_file(path))

        # Assert
        self.assertEqual(['second', 'first'], list(ws_test_datas.keys()))
        self.assertEqual(['demo2'], [row.get_test_data_property('username') for row in ws_test_datas['second']])
        self.assertEqual(['demo1'], [row.get_test_data_property('username') for row in ws_test_datas['first']])

    ####################################################
    #
    # Compatibility of the loaded test data rows
    #
    ####################################################
    def test_get_all_test_data_WithSkippedSheet_KeepsTestDataRowFields(self):
        # Arrange
        path = self._create_workbook('mixed.xlsx', [
            ('broken', self._invalid_sheet_rows(['lost'])),
            ('first', self._valid_sheet_rows(['demo1', 'demo2'])),
        ])
        core_rpa = ExcelDataDriver()

        # Act
        core_rpa.load_test_data(path, 'username')
        test_data_list = core_rpa.get_all_test_data()

        # Assert
        self.assertEqual(2, len(test_data_list))
        first_row = test_data_list[0]
        self.assertEqual('first', first_row.excel_title)
        self.assertEqual(1, first_row.excel_row_index)
        self.assertEqual('first', first_row.get_sheet_name())
        self.assertEqual(2, first_row.get_row_no())
        self.assertEqual('demo1', first_row.get_test_data_property('username'))
        self.assertEqual('Welcome1', first_row.get_test_data_property('password'))
        self.assertEqual([], first_row.get_testcase_tags())
        # get_all_test_data clears previous results, the same as before.
        self.assertEqual('', first_row.get_status())
        self.assertEqual('', first_row.get_log_message())
        self.assertEqual('', first_row.get_screenshot())
        self.assertTrue(first_row.is_not_run())
        self.assertFalse(first_row.is_pass())
        self.assertFalse(first_row.is_fail())

    def test_parse_WithCustomParserLoop_ReportsTruncationForEverySheet(self):
        # Arrange
        rows = [MANDATORY_COLUMNS + TEST_DATA_COLUMNS,
                [None, None, None, None, 'demo1', 'Welcome1'],
                [None, None, None, None, None, None],
                [None, None, None, None, 'demo2', 'Welcome1']]
        path = self._create_workbook('custom.xlsx', [('custom', rows)])
        parser_context = ParserContext(_OwnParseLoopStrategy('username'))

        # Act
        ws_test_datas = parser_context.parse(OpenpyxlHelper.load_excel_file(path))
        sheet_result = parser_context.get_sheet_parse_result('custom')

        # Assert
        self.assertEqual(1, len(ws_test_datas['custom']))
        self.assertEqual(1, sheet_result.success_count)
        self.assertTrue(sheet_result.truncated)
        self.assertEqual(3, sheet_result.truncated_row_no)
        self.assertEqual(4, sheet_result.next_data_row_no)

    def test_parse_WithLegacyValidationSignature_ValidatesEverySheet(self):
        # Arrange
        path = self._create_workbook('legacy.xlsx', [
            ('first', self._valid_sheet_rows(['demo1'])),
            ('broken', self._invalid_sheet_rows(['lost'])),
        ])
        parser_context = ParserContext(_LegacyValidationStrategy('username'))

        # Act
        ws_test_datas = parser_context.parse(OpenpyxlHelper.load_excel_file(path))

        # Assert
        self.assertEqual(['demo1'], [row.get_test_data_property('username') for row in ws_test_datas['first']])
        self.assertEqual([], ws_test_datas['broken'])
        self.assertFalse(parser_context.get_sheet_parse_result('first').is_skipped)
        self.assertTrue(parser_context.get_sheet_parse_result('broken').is_skipped)


class _OwnParseLoopStrategy(DefaultParserStrategy):
    """
    Custom parser strategy that owns its parsing loop, like the documented examples.
    """

    def parse_test_data_properties(self, ws, ws_column_indexs):
        test_datas = []
        for index, row in enumerate(ws.rows):
            if index < self.start_row:
                continue
            test_data = self.map_data_row_into_test_data_obj(ws_column_indexs, ws.title, index, row)
            if test_data is not None:
                test_datas.append(test_data)
            else:
                break
        return test_datas


class _LegacyValidationStrategy(DefaultParserStrategy):
    """
    Custom parser strategy using the legacy two argument validation signature.
    """

    def is_ws_column_valid(self, ws, validate_result):
        diff_column_list = [
            column for column in self.DEFAULT_COLUMN_INDEXS
            if column not in [cell.value for cell in list(ws.rows)[0]]]
        if len(diff_column_list) > 0:
            validate_result['is_pass'] = False
            validate_result['error_message'] += '[' + ws.title + '] Excel column ' + ', '.join(
                diff_column_list) + ' are missing.\r\n'
        return validate_result

