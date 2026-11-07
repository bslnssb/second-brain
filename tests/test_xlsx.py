# -*- coding: utf-8 -*-
"""xlsx_writer 的单元测试。

重点不是「能生成文件」，而是**生成的每个 XML 都能被解析器读懂** ——
手写 XML 最容易出的错是转义漏了某个字符，字符串拼起来看着没问题，
Excel 一打开就报「文件已损坏」，而且不会有任何提示告诉你哪一处错了。
所以这里拿 ElementTree 把六个 part 全解析一遍。
"""
import io
import unittest
import xml.etree.ElementTree as ET
import zipfile

import xlsx_writer as X

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def open_parts(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


class TestStructure(unittest.TestCase):
    def test_all_parts_are_valid_xml(self):
        data = X.build_xlsx([['a', 1]], head=X.HEAD, widths=X.WIDTHS)
        parts = open_parts(data)
        need = {'[Content_Types].xml', '_rels/.rels', 'xl/workbook.xml',
                'xl/_rels/workbook.xml.rels', 'xl/styles.xml',
                'xl/worksheets/sheet1.xml'}
        self.assertEqual(need, set(parts))
        for name, blob in parts.items():
            with self.subTest(part=name):
                ET.fromstring(blob)

    def test_header_and_rows_land_in_expected_cells(self):
        data = X.build_xlsx([['报告.pdf', 'D:/a', 123, '2026-11-07 15:30', '文件名', '']],
                            head=X.HEAD)
        root = ET.fromstring(open_parts(data)['xl/worksheets/sheet1.xml'])
        rows = root.findall('.//m:row', NS)
        self.assertEqual(len(rows), 2)
        first = rows[0].findall('m:c', NS)
        self.assertEqual(first[0].get('r'), 'A1')
        self.assertEqual(first[0].get('s'), '1')
        second = rows[1].findall('m:c', NS)
        self.assertEqual(second[0].get('r'), 'A2')
        self.assertEqual(second[0].get('t'), 'inlineStr')

    def test_numbers_written_as_values_not_text(self):
        data = X.build_xlsx([[1234]], head=['大小'])
        sheet = open_parts(data)['xl/worksheets/sheet1.xml'].decode()
        self.assertIn('<v>1234</v>', sheet)
        self.assertNotIn('inlineStr"><is><t xml:space="preserve">1234', sheet)

    def test_autofilter_and_freeze_only_when_there_are_rows(self):
        with_rows = open_parts(X.build_xlsx([['x']], head=['h']))['xl/worksheets/sheet1.xml'].decode()
        self.assertIn('<autoFilter', with_rows)
        self.assertIn('state="frozen"', with_rows)
        empty = open_parts(X.build_xlsx([], head=['h']))['xl/worksheets/sheet1.xml'].decode()
        self.assertNotIn('<autoFilter', empty)


class TestEscaping(unittest.TestCase):
    def test_xml_metacharacters_survive(self):
        txt = 'A&B <C> "D" \'E\''
        root = ET.fromstring(open_parts(X.build_xlsx([[txt]]))['xl/worksheets/sheet1.xml'])
        got = root.find('.//m:t', NS).text
        self.assertEqual(got, txt)

    def test_control_chars_are_dropped_not_escaped(self):
        root = ET.fromstring(open_parts(X.build_xlsx([['a\x00b\x08c\td']]))
                             ['xl/worksheets/sheet1.xml'])
        got = root.find('.//m:t', NS).text
        self.assertEqual(got, 'abc\td')

    def test_overlong_text_is_truncated(self):
        root = ET.fromstring(open_parts(X.build_xlsx([['x' * 40000]]))
                             ['xl/worksheets/sheet1.xml'])
        self.assertEqual(len(root.find('.//m:t', NS).text), X._MAX_CELL)


class TestNames(unittest.TestCase):
    def test_column_names(self):
        self.assertEqual(X._col_name(0), 'A')
        self.assertEqual(X._col_name(25), 'Z')
        self.assertEqual(X._col_name(26), 'AA')
        self.assertEqual(X._col_name(27), 'AB')
        self.assertEqual(X._col_name(701), 'ZZ')
        self.assertEqual(X._col_name(702), 'AAA')

    def test_sheet_name_sanitised(self):
        self.assertEqual(X._sheet_name('a/b:c*d'), 'a_b_c_d')
        self.assertEqual(len(X._sheet_name('长' * 50)), 31)
        self.assertEqual(X._sheet_name(''), 'Sheet1')


class TestResultRows(unittest.TestCase):
    def test_maps_fields_and_falls_back_to_z(self):
        hits = [{'n': '合同.pdf', 'p': 'D:/案卷/合同.pdf', 'z': 2048,
                 'm': 1762500000, 'b': '逾期一日按万分之三'}]
        r = X.result_rows(hits)[0]
        self.assertEqual(r[0], '合同.pdf')
        self.assertEqual(r[1], 'D:/案卷')
        self.assertEqual(r[2], 2048)
        self.assertEqual(r[4], '正文')
        self.assertIn('万分之三', r[5])

    def test_dir_row_marked(self):
        r = X.result_rows([{'n': '案卷', 'p': 'D:/案卷', 'dir': True}])[0]
        self.assertEqual(r[4], '文件夹')

    def test_missing_size_and_time_do_not_crash(self):
        r = X.result_rows([{'n': 'x', 'p': 'D:/x'}])[0]
        self.assertEqual(r[2], 0)
        self.assertEqual(r[3], '')


if __name__ == '__main__':
    unittest.main()
