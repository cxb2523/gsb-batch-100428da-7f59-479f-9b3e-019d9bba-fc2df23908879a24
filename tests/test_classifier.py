import unittest
from core.classifier import classify_js_file, classify_all


def jf(filename, **extra):
    base = {"filename": filename, "url": f"https://x/{filename}", "size": 1000}
    base.update(extra)
    return base


class TestClassifyVendorByPrefix(unittest.TestCase):
    def test_polyfills_is_vendor(self):
        self.assertEqual(classify_js_file(jf("polyfills.e91f7f348316d619.js")), "vendor")

    def test_runtime_is_vendor(self):
        self.assertEqual(classify_js_file(jf("runtime.abc123.js")), "vendor")

    def test_framework_is_vendor(self):
        self.assertEqual(classify_js_file(jf("framework-abc.js")), "vendor")

    def test_webpack_is_vendor(self):
        self.assertEqual(classify_js_file(jf("webpack-runtime.js")), "vendor")

    def test_main_app_hash_is_vendor(self):
        self.assertEqual(classify_js_file(jf("main-app-167dc0b703fedacb.js")), "vendor")


class TestClassifyVendorByNodeModules(unittest.TestCase):
    def test_node_modules_chunk_is_vendor(self):
        self.assertEqual(classify_js_file(jf("node_modules_primeng_fesm2022_primeng-api_mjs.f5a655f534fb0211.js")), "vendor")

    def test_node_modules_angular_is_vendor(self):
        self.assertEqual(classify_js_file(jf("node_modules_angular_material_fesm2022_core_mjs.c953a3333e04f195.js")), "vendor")


class TestClassifyVendorByLibName(unittest.TestCase):
    def test_es_module_shims_is_vendor(self):
        self.assertEqual(classify_js_file(jf("es-module-shims.min-03900212.js")), "vendor")

    def test_swiper_is_vendor(self):
        self.assertEqual(classify_js_file(jf("swiper-bundle.min.js")), "vendor")


class TestClassifyVendorByHashOnlyChunk(unittest.TestCase):
    def test_5_digit_hash_chunk_is_vendor(self):
        self.assertEqual(classify_js_file(jf("55016-ea5d70cdbfce98c5.js")), "vendor")

    def test_4_digit_hash_chunk_is_vendor(self):
        self.assertEqual(classify_js_file(jf("4238.e1fec18a2851c99c.js")), "vendor")

    def test_pure_hash_is_vendor(self):
        self.assertEqual(classify_js_file(jf("f2e31419f88d021f.js")), "vendor")


class TestClassifyInline(unittest.TestCase):
    def test_inline_is_inline(self):
        self.assertEqual(classify_js_file(jf("inline_1_f6155f7c.js")), "inline")

    def test_inline_long_index(self):
        self.assertEqual(classify_js_file(jf("inline_221_3dadb711.js")), "inline")


class TestClassifyApp(unittest.TestCase):
    def test_apps_onehub_platform_is_app(self):
        self.assertEqual(classify_js_file(jf("apps_onehub-platform_src_bootstrap_ts.81239eec126b2984.js")), "app")

    def test_named_module_is_app(self):
        self.assertEqual(classify_js_file(jf("checkout-flow.js")), "app")

    def test_index_with_short_hash_is_app(self):
        self.assertEqual(classify_js_file(jf("index-B1r3dKG_.js")), "app")


class TestClassifyAll(unittest.TestCase):
    def test_returns_dict_with_three_buckets(self):
        files = [
            jf("polyfills.abc.js"),
            jf("apps_main.js"),
            jf("inline_1_x.js"),
        ]
        result = classify_all(files)
        self.assertEqual(set(result.keys()), {"vendor", "app", "inline"})
        self.assertEqual(len(result["vendor"]), 1)
        self.assertEqual(len(result["app"]), 1)
        self.assertEqual(len(result["inline"]), 1)

    def test_tags_each_file_in_place(self):
        files = [jf("polyfills.abc.js"), jf("apps_main.js")]
        classify_all(files)
        self.assertEqual(files[0]["category"], "vendor")
        self.assertEqual(files[1]["category"], "app")


if __name__ == "__main__":
    unittest.main()
