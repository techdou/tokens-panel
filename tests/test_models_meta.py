"""模型能力元数据测试（纯动态版）。

静态表已移除，本文件验证：
  - list_models 返回空占位（接口兼容，前端不再依赖）
  - format_context 格式化正确
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_context_formatting():
    from app import models_meta
    assert models_meta.format_context(1000) == "1K"
    assert models_meta.format_context(131072) == "131K"
    assert models_meta.format_context(1048576) == "1M"
    assert models_meta.format_context(64000) == "64K"
    assert models_meta.format_context(None) == ""  # None 返回空串，前端用 — 占位
    print("[PASS] 上下文长度格式化正确")


def test_list_models_returns_empty():
    """静态表已移除，list_models 返回空占位。"""
    from app import models_meta
    r = models_meta.list_models("glm")
    assert r["models"] == {}
    r2 = models_meta.list_models()
    assert r2["models"] == {}
    print("[PASS] list_models 返回空占位（纯动态）")


def test_no_static_models_attr():
    """确认 MODELS 静态字典已彻底移除。"""
    from app import models_meta
    assert not hasattr(models_meta, "MODELS"), "models_meta.MODELS 应已移除"
    print("[PASS] MODELS 静态表已移除")


def test_no_merge_function():
    """确认 merge_live_with_static 已被 normalize_live_models 取代。"""
    from app import models_meta
    assert not hasattr(models_meta, "merge_live_with_static")
    assert hasattr(models_meta, "normalize_live_models")
    print("[PASS] normalize_live_models 替代 merge_live_with_static")


if __name__ == "__main__":
    test_context_formatting()
    test_list_models_returns_empty()
    test_no_static_models_attr()
    test_no_merge_function()
    print("\n=== 模型元数据测试全部通过 ===")
