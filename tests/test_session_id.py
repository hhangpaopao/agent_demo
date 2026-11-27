"""session_id 白名单校验测试（纯标准库，无需 pytest 即可运行）。

运行：python -m unittest tests.test_session_id -v
覆盖：合法 UUID / 短 ID / 边界长度，以及路径穿越、绝对路径、注入字符等攻击样本。
"""
import unittest

from core.context import is_valid_session_id


class TestValidSessionId(unittest.TestCase):
    """合法样本：应全部通过。"""

    def test_uuid_format(self):
        # 前端常见：带短横线的 UUID（36 字符）
        self.assertTrue(is_valid_session_id("550e8400-e29b-41d4-a716-446655440000"))

    def test_short_business_id(self):
        for sid in ("abc123", "session_01", "s-1", "A", "2026_11_22", "user-abc_9"):
            with self.subTest(sid=sid):
                self.assertTrue(is_valid_session_id(sid))

    def test_boundary_length_64(self):
        self.assertTrue(is_valid_session_id("a" * 64))


class TestInvalidSessionId(unittest.TestCase):
    """攻击与异常样本：应全部拒绝。"""

    def test_path_traversal(self):
        for sid in ("../etc", "a/../../b", "session_..", "..%2Fetc", "/etc/passwd"):
            with self.subTest(sid=sid):
                self.assertFalse(is_valid_session_id(sid))

    def test_absolute_and_backslash(self):
        for sid in ("/tmp/x", "C:\\Windows", "a\\b", "session_/x"):
            with self.subTest(sid=sid):
                self.assertFalse(is_valid_session_id(sid))

    def test_empty_and_too_long(self):
        self.assertFalse(is_valid_session_id(""))
        self.assertFalse(is_valid_session_id("a" * 65))

    def test_special_and_non_ascii(self):
        for sid in ("hello world", "a;b", "a$b", "会话1", "a.b", "a@b", "a:b"):
            with self.subTest(sid=sid):
                self.assertFalse(is_valid_session_id(sid))

    def test_non_string_input(self):
        for sid in (None, 123, ["abc"]):
            with self.subTest(sid=sid):
                self.assertFalse(is_valid_session_id(sid))


if __name__ == "__main__":
    unittest.main(verbosity=2)
