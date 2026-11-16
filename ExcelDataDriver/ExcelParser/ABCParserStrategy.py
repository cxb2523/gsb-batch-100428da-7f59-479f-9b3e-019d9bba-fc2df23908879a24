from datetime import datetime
from abc import ABCMeta, abstractmethod
from ExcelDataDriver.ExcelTestDataRow.MandatoryTestDataColumn import MANDATORY_TEST_DATA_COLUMN
from openpyxl.utils import column_index_from_string
from openpyxl.utils.cell import coordinate_from_string


class ABCParserStrategy:

    __metaclass__ = ABCMeta

    def __init__(self, main_column_key):
        self.MANDATORY_TEST_DATA_COLUMN = MANDATORY_TEST_DATA_COLUMN
        self.DEFAULT_COLUMN_INDEXS = self.MANDATORY_TEST_DATA_COLUMN.values()
        self.start_row = 1
        self.max_column = 50
        self.maximum_column_index_row = 5
        self.main_column_key = main_column_key

    def is_ws_column_valid(self, ws, ws_column_indexes, validate_result):
        diff_column_list = list(set(self.DEFAULT_COLUMN_INDEXS) - set(ws_column_indexes))
        if len(diff_column_list) > 0:
            validate_result['is_pass'] = False
            validate_result['error_message'] += "[" + ws.title + "] Excel column " + ", ".join(
                diff_column_list) + " are missing.\r\n"
            print(str(datetime.now())+": [" + ws.title + "] Excel column " + ", ".join(diff_column_list) + " are missing.")
        return validate_result

    @abstractmethod
    def is_test_data_valid(self, ws_column_indexes, ws_title, row_index, row): pass

    @abstractmethod
    def map_data_row_into_test_data_obj(self, ws_column_indexes, ws_title, row_index, row): pass

    def get_all_worksheet(self, wb):
        return list(wb)

    def parsing_major_column_indexs(self, ws):
        ws_column_indexs = {}
        key_index_row = 0
        found_default_column_indexs = False

        # Parse mandatory property
        for index, row in enumerate(ws.rows):
            if index > self.maximum_column_index_row:
                break
            for cell in row:
                if (cell.value is not None) and (cell.value in self.DEFAULT_COLUMN_INDEXS):
                    ws_column_indexs[cell.value] = column_index_from_string(coordinate_from_string(cell.coordinate)[0])
                    print(str(datetime.now()) + ': Mandatory : ' + str(cell.value) + ' : ' + str(
                        cell.coordinate) + ' : ' + str(
                        column_index_from_string(coordinate_from_string(cell.coordinate)[0])))
                    key_index_row = index + 1
                    found_default_column_indexs = True

                if (cell.value is not None) and (cell.value not in self.DEFAULT_COLUMN_INDEXS) and (found_default_column_indexs is False):
                    field_name = self._cleanup_fieldname(cell.value)
                    if field_name == self.main_column_key:
                        key_index_row = index + 1

            if len(ws_column_indexs) > 0:
                break

        return ws_column_indexs, key_index_row

    def parsing_column_indexs(self, ws):
        ws_column_indexs = {}

        # Parse mandatory property
        for index, row in enumerate(ws.rows):
            if index > self.maximum_column_index_row:
                break
            for cell in row:
                if (cell.value is not None) and (cell.value in self.DEFAULT_COLUMN_INDEXS):
                    found_mandatory_property = True
                    ws_column_indexs[cell.value] = column_index_from_string(coordinate_from_string(cell.coordinate)[0])
                    print(str(datetime.now())+': Mandatory : '+str(cell.value) + ' : ' + str(cell.coordinate) + ' : ' + str(column_index_from_string(coordinate_from_string(cell.coordinate)[0])))
                    self.start_row = index + 1
            if len(ws_column_indexs) > 0:
                break

        # Parse optional property
        for index, row in enumerate(ws.rows):
            if index > self.maximum_column_index_row:
                break
            if index != self.start_row - 1:
                continue
            for cell in row:
                if (cell.value is not None) and (cell.value not in self.DEFAULT_COLUMN_INDEXS):
                    field_name = self._cleanup_fieldname(cell.value)
                    ws_column_indexs[field_name] = column_index_from_string(coordinate_from_string(cell.coordinate)[0])
                    print(str(datetime.now())+': Optional : '+field_name + ' : ' + str(cell.coordinate) + ' : ' + str(column_index_from_string(coordinate_from_string(cell.coordinate)[0])))
            break
        return ws_column_indexs

    def parse_test_data_properties(self, ws, ws_column_indexs):
        test_datas, truncation = self.parse_test_data_properties_with_settlement(ws, ws_column_indexs)
        return test_datas

    def parse_test_data_properties_with_settlement(self, ws, ws_column_indexs):
        """
        Parse test data rows of one worksheet and report where parsing stopped.

        An empty main column key keeps the original meaning: the worksheet data ends
        at that row, so rows after it are not loaded. The returned truncation
        information reports that stop position, which lets an operator see that the
        worksheet was cut even though the loaded row order is unchanged.

        :return: (list of ExcelTestDataRow, truncation dict or None)
        """
        test_datas = []
        truncation = None
        for index, row in enumerate(ws.rows):
            if index < self.start_row:
                continue
            self.is_test_data_valid(ws_column_indexs, ws.title, index, row)
            test_data = self.map_data_row_into_test_data_obj(ws_column_indexs, ws.title, index, row)
            if test_data is not None:
                test_datas.append(test_data)
            else:
                truncation = self._get_truncation_info(ws, ws_column_indexs, index)
                break
        print(str(datetime.now())+': Total test datas: ' + str(len(test_datas)))
        return test_datas, truncation

    def find_main_column_data_after(self, ws, ws_column_indexs, row_index):
        """
        Return the row number of the first data row after ``row_index`` whose main
        column key is not empty, or None when nothing was left behind.
        """
        main_column_index = ws_column_indexs.get(self.main_column_key)
        if main_column_index is None:
            return None
        for index, row in enumerate(ws.rows):
            if index <= row_index:
                continue
            if main_column_index > len(row):
                continue
            value = row[main_column_index - 1].value
            if value is not None and value != '':
                return index + 1
        return None

    def _get_truncation_info(self, ws, ws_column_indexs, row_index):
        """
        Describe the row where parsing stopped on an empty main column key.

        The stop position is always reported. ``truncated`` is only True when data
        rows are left behind the stop, which is the case an operator cannot see from
        the loaded test data.

        :return: dict with the stop position, or None when no main column is known.
        """
        if ws_column_indexs.get(self.main_column_key) is None:
            return None
        next_data_row_no = self.find_main_column_data_after(ws, ws_column_indexs, row_index)
        return {
            'row_index': row_index,
            'row_no': row_index + 1,
            'truncated': next_data_row_no is not None,
            'next_data_row_no': next_data_row_no,
            'reason': 'Empty main column "' + str(self.main_column_key) + '" stops parsing this worksheet.',
        }

    def _cleanup_fieldname(self, field_name):
        field_name = str(field_name).lower().strip().replace(" ", "_")
        field_name = field_name.replace("\r", "_")
        field_name = field_name.replace("\n", "_")
        field_name = field_name.replace("__", "_")
        return field_name
