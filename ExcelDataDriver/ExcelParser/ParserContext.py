from collections import OrderedDict
from datetime import datetime
import inspect

from ExcelDataDriver.ExcelParser.SheetParseResult import SheetParseResult


class ParserContext:

    def __init__(self, parser_strategy):
        self.parser_strategy = parser_strategy
        # Settlement information of every worksheet in the latest parsed workbook.
        self.sheet_parse_results = OrderedDict()

    def parse(self, wb):
        """
        Parse excel test data row based on Excel Parser Strategy.

        Every worksheet is settled separately. A worksheet that misses any of the
        mandatory columns is recorded as skipped, then the remaining worksheets are
        still parsed. The whole workbook only fails when every worksheet failed.

        :return: OrderedDict of worksheet title -> list of ExcelTestDataRow
        """
        ws_test_data_rows = OrderedDict()
        self.sheet_parse_results = OrderedDict()
        failed_sheet_messages = []

        for ws in self.parser_strategy.get_all_worksheet(wb):
            sheet_result = SheetParseResult(ws.title)
            self.sheet_parse_results[ws.title] = sheet_result

            ws_column_indexs = self.parser_strategy.parsing_column_indexs(ws)

            validate_result = self._validate_worksheet(ws, ws_column_indexs)
            if validate_result['is_pass'] is not True:
                # The worksheet is skipped as a whole, the remaining worksheets are
                # still parsed so a broken sheet cannot hide usable test data.
                # The title is kept with an empty row list so positional worksheet
                # alignment used by report merging stays intact.
                sheet_result.mark_skipped(validate_result['error_message'])
                failed_sheet_messages.append(validate_result['error_message'])
                ws_test_data_rows[ws.title] = []
                print(str(datetime.now()) + ': [' + str(ws.title) + "] skipped : " + str(validate_result['error_message']).strip())
                continue

            test_data_rows, truncation = self._parse_worksheet_rows(ws, ws_column_indexs)
            sheet_result.mark_parsed(test_data_rows, truncation)
            ws_test_data_rows[ws.title] = test_data_rows
            if sheet_result.truncated:
                print(str(datetime.now()) + ': [' + str(ws.title) + '] parsing stopped at row ' + str(sheet_result.truncated_row_no)
                      + ' with ' + str(sheet_result.success_count) + ' test data row(s) loaded.')

        # The workbook is only invalid when every worksheet failed.
        if len(self.sheet_parse_results) > 0 and len(failed_sheet_messages) == len(self.sheet_parse_results):
            raise ValueError(''.join(failed_sheet_messages))

        print(str(datetime.now()) + ': Done parse data...')
        return ws_test_data_rows

    ####################################################
    #
    # Per worksheet settlement helpers
    #
    ####################################################
    def get_sheet_parse_results(self):
        """
        Return settlement information of every parsed worksheet.

        :return: OrderedDict of worksheet title -> SheetParseResult
        """
        return self.sheet_parse_results

    def get_sheet_parse_result(self, sheet_name):
        """
        Return settlement information of one worksheet, or None if not found.
        """
        return self.sheet_parse_results.get(sheet_name)

    def _validate_worksheet(self, ws, ws_column_indexs):
        """
        Validate one worksheet against the mandatory columns of the parser strategy.

        Custom parser strategies may expose the legacy two argument signature
        ``is_ws_column_valid(ws, validate_result)``, so both signatures are supported.
        """
        validate_result = {'is_pass': True, 'error_message': ''}
        if self._accepts_column_indexes():
            result = self.parser_strategy.is_ws_column_valid(ws, ws_column_indexs, validate_result)
        else:
            result = self.parser_strategy.is_ws_column_valid(ws, validate_result)

        if not isinstance(result, dict):
            return validate_result
        return result

    def _accepts_column_indexes(self):
        """
        Return True when the strategy validation accepts the parsed column indexes.

        Custom parser strategies may still expose the legacy
        ``is_ws_column_valid(ws, validate_result)`` signature.
        """
        try:
            parameters = list(inspect.signature(self.parser_strategy.is_ws_column_valid).parameters.values())
        except (TypeError, ValueError):
            return True
        if parameters and parameters[0].name == 'self':
            parameters = parameters[1:]
        positional_parameters = [
            parameter for parameter in parameters
            if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                  inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        if any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters):
            return True
        return len(positional_parameters) >= 3

    def _parse_worksheet_rows(self, ws, ws_column_indexs):
        """
        Parse one valid worksheet and return its test data rows with truncation info.

        A custom parser strategy that owns its parsing loop keeps being used as is.
        """
        settlement_parser = getattr(self.parser_strategy, 'parse_test_data_properties_with_settlement', None)
        strategy_class = type(self.parser_strategy)
        overrides_parse_loop = 'parse_test_data_properties' in strategy_class.__dict__
        overrides_settlement = 'parse_test_data_properties_with_settlement' in strategy_class.__dict__

        if callable(settlement_parser) and (overrides_settlement or not overrides_parse_loop):
            test_data_rows, truncation = settlement_parser(ws, ws_column_indexs)
            test_data_rows = list(test_data_rows or [])
        else:
            # Custom parser with its own parsing loop keeps its behaviour and the
            # truncation position is detected from the last produced data row.
            test_data_rows = list(self.parser_strategy.parse_test_data_properties(ws, ws_column_indexs) or [])
            truncation = self._detect_truncation(ws, ws_column_indexs, test_data_rows)

        return test_data_rows, truncation

    def _detect_truncation(self, ws, ws_column_indexs, parsed_rows):
        """
        Detect where a custom parsing loop stopped before the end of the worksheet.

        A custom strategy owns its parsing loop, so the stop is the row right after
        the last produced test data row. When the worksheet still has rows after that
        position, parsing stopped early and the position is reported.
        """
        main_column_key = getattr(self.parser_strategy, 'main_column_key', None)
        main_column_index = ws_column_indexs.get(main_column_key)
        if main_column_index is None:
            return None

        last_produced_index = getattr(self.parser_strategy, 'start_row', 1) - 1
        for row in parsed_rows or []:
            row_index = getattr(row, 'excel_row_index', None)
            if row_index is not None and row_index > last_produced_index:
                last_produced_index = row_index

        rows = list(ws.rows)
        if last_produced_index + 1 >= len(rows):
            return None

        next_data_row_no = None
        for index, row in enumerate(rows):
            if index <= last_produced_index:
                continue
            if main_column_index > len(row):
                continue
            value = row[main_column_index - 1].value
            if value is not None and value != '':
                next_data_row_no = index + 1
                break

        stop_index = last_produced_index + 1
        return {
            'row_index': stop_index,
            'row_no': stop_index + 1,
            'truncated': next_data_row_no is not None,
            'next_data_row_no': next_data_row_no,
            'reason': 'Empty main column "' + str(main_column_key) + '" stops parsing this worksheet.'
        }

    def insert_extra_columns(self, wb, columns):
        ws_list = self.parser_strategy.get_all_worksheet(wb)
        for ws in ws_list:
            print(str(datetime.now()) + ': start parse data...')
            ws_column_indexs, key_index_row = self.parser_strategy.parsing_major_column_indexs(ws)
            for column in reversed(columns):
                if column in ws_column_indexs:
                    continue
                ws.insert_cols(1)
                ws['A' + str(key_index_row)] = column
            print(str(datetime.now()) + ': Done parse data...')
