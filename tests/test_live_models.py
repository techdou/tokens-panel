"""动态模型查询：合并逻辑 + 解析工具的单元测试。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models_meta
from app.providers.base import LiveModel


def test_merge_static_only_when_no_live():
    """动态拉取为空时，回退为静态表全部。"""
    merged = models_meta.merge_live_with_static("deepseek", [])
    assert len(merged) >= 2  # deepseek 静态表有 2 个
    assert all(m["source"] == "static" for m in merged)
    print("[PASS] 无动态数据时回退静态表")


def test_merge_live_supplements_context():
    """动态返回 context_length（如 GLM）时优先用动态值。"""
    live = [LiveModel(id="glm-4.6", context_length=200000)]
    merged = models_meta.merge_live_with_static("glm", live)
    glm = next(m for m in merged if m["id"] == "glm-4.6")
    assert glm["context"] == 200000
    assert glm["thinking"] == "supported"  # 静态补充
    assert glm["source"] == "live"
    print("[PASS] 动态 context 优先 + 静态 thinking 补充")


def test_merge_new_model_marked_unknown():
    """动态出现了静态表没有的新模型 → 标记未知。"""
    live = [
        LiveModel(id="glm-4.6", context_length=200000),
        LiveModel(id="glm-5.0-future"),  # 静态表没有的新模型
    ]
    merged = models_meta.merge_live_with_static("glm", live)
    new = next(m for m in merged if m["id"] == "glm-5.0-future")
    assert new["thinking"] == "unknown"
    assert new["notes"] == "未知（待补充）"
    print("[PASS] 新模型标记为未知（待补充）")


def test_merge_case_insensitive():
    """id 大小写不敏感匹配（如 MiniMax-M2 vs minimax-m2）。"""
    live = [LiveModel(id="minimax-m2")]  # 小写
    merged = models_meta.merge_live_with_static("minimax", live)
    m = next(x for x in merged if x["id"].lower() == "minimax-m2")
    assert m["thinking"] == "supported"  # 静态表是 MiniMax-M2，能匹配上
    print("[PASS] id 大小写不敏感匹配")


def test_merge_dedup():
    """重复 id 去重。"""
    live = [
        LiveModel(id="glm-4.6", context_length=200000),
        LiveModel(id="GLM-4.6", context_length=200000),  # 大小写重复
    ]
    merged = models_meta.merge_live_with_static("glm", live)
    glm_count = sum(1 for m in merged if m["id"].lower() == "glm-4.6")
    assert glm_count == 1
    print("[PASS] 重复 id 去重")


def test_merge_keeps_static_when_missing_in_live():
    """静态表有、动态没返回的模型仍保留（避免 key 无权限时模型消失）。"""
    live = [LiveModel(id="deepseek-chat")]  # 只返回了 1 个
    merged = models_meta.merge_live_with_static("deepseek", live)
    ids = [m["id"] for m in merged]
    assert "deepseek-chat" in ids
    assert "deepseek-reasoner" in ids  # 静态表的另一个也保留
    print("[PASS] 静态表模型在动态缺失时仍保留")


def test_live_model_parsing():
    """LiveModel 字段提取。"""
    m = LiveModel(id="test-model", created=123, owned_by="vendor", context_length=8192)
    assert m.id == "test-model"
    assert m.context_length == 8192
    print("[PASS] LiveModel 字段正确")


if __name__ == "__main__":
    test_live_model_parsing()
    test_merge_static_only_when_no_live()
    test_merge_live_supplements_context()
    test_merge_new_model_marked_unknown()
    test_merge_case_insensitive()
    test_merge_dedup()
    test_merge_keeps_static_when_missing_in_live()
    print("\n=== 动态模型合并逻辑测试全部通过 ===")
