"""索引集（多库）元数据的单元测试：纯标准库操作临时目录，不碰真实 brain.db。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import libsets  # noqa: E402


class LibsetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_missing_file_gives_default_only(self):
        d = libsets.load(self.d)
        self.assertEqual(d['active'], 'default')
        self.assertEqual([x['id'] for x in d['libs']], ['default'])

    def test_02_default_paths_do_not_migrate(self):
        d = libsets.load(self.d)
        lib = libsets.active_lib(d)
        self.assertEqual(libsets.db_path(self.d, lib), self.d / 'brain.db')
        self.assertEqual(libsets.scope_path(self.d, lib), self.d / 'scope.json')

    def test_03_create_makes_independent_lib(self):
        lib, err = libsets.create(self.d, '工作资料')
        self.assertTrue(lib, err)
        self.assertEqual(err, '')
        self.assertTrue((self.d / 'libs' / lib['id'] / 'scope.json').is_file())
        self.assertEqual(libsets.db_path(self.d, lib),
                         self.d / 'libs' / lib['id'] / 'brain.db')
        # 新建不改变活动库
        self.assertEqual(libsets.load(self.d)['active'], 'default')

    def test_04_create_sanitizes_id(self):
        lib, _ = libsets.create(self.d, '../../etc/重要 资料')
        self.assertTrue(lib)
        self.assertRegex(lib['id'], r'^[a-z0-9_-]{1,48}$')
        self.assertNotIn('/', lib['id'])
        self.assertNotIn('..', lib['id'])

    def test_05_duplicate_name_gets_unique_id(self):
        a, _ = libsets.create(self.d, '同名')
        b, _ = libsets.create(self.d, '同名')
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual(len(libsets.load(self.d)['libs']), 3)

    def test_06_rename_and_set_active(self):
        lib, _ = libsets.create(self.d, '临时')
        self.assertTrue(libsets.rename(self.d, lib['id'], '改名后'))
        self.assertEqual(libsets.active_lib(libsets.load(self.d), lib['id'])['name'], '改名后')
        self.assertTrue(libsets.set_active(self.d, lib['id']))
        self.assertEqual(libsets.load(self.d)['active'], lib['id'])
        self.assertFalse(libsets.set_active(self.d, 'not-exist'))

    def test_07_cannot_remove_default(self):
        ok, why = libsets.remove(self.d, 'default')
        self.assertFalse(ok)
        self.assertIn('默认', why)
        self.assertEqual(libsets.load(self.d)['active'], 'default')

    def test_08_remove_falls_back_to_default(self):
        lib, _ = libsets.create(self.d, '待删')
        libsets.set_active(self.d, lib['id'])
        ok, _ = libsets.remove(self.d, lib['id'])
        self.assertTrue(ok)
        d = libsets.load(self.d)
        self.assertEqual(d['active'], 'default')
        self.assertFalse((self.d / 'libs' / lib['id']).exists())

    def test_09_corrupt_json_falls_back(self):
        libsets.path_for(self.d).write_text('{not json', encoding='utf-8')
        d = libsets.load(self.d)
        self.assertEqual(d['active'], 'default')
        self.assertEqual(len(d['libs']), 1)

    def test_10_save_is_atomic(self):
        lib, _ = libsets.create(self.d, 'A')
        self.assertFalse(libsets.path_for(self.d).with_suffix('.json.tmp').exists())
        self.assertEqual(json.loads(libsets.path_for(self.d).read_text(encoding='utf-8'))['libs'][0]['id'], lib['id'])

    def test_11_max_libs_enforced(self):
        for i in range(libsets.MAX_LIBS + 2):
            libsets.create(self.d, 'lib%d' % i)
        self.assertLessEqual(len(libsets.load(self.d)['libs']), libsets.MAX_LIBS)
        lib, err = libsets.create(self.d, 'overflow')
        self.assertIsNone(lib)
        self.assertIn('最多', err)


if __name__ == '__main__':
    unittest.main()
