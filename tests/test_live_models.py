"""动态模型查询：normalize_live_models 转换逻辑的单元测试。

模型表已改为纯动态（无静态补充），normalize_live_models 负责把 /v1/models
拉取的 LiveModel 列表转成前端展示结构。能力字段（thinking/max_output 等）
接口拿不到，统一填「未知」/None。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models_meta
from app.providers.base import LiveModel


def test_normalize_empty():
    """空列表返回空。"""
    assert models_meta.normalize_live_models([]) == []
    print("[PASS] 空动态列表返回空")


def test_normalize_basic_fields():
    """透传 id / context / owned_by，能力字段填未知。"""
    live = [LiveModel(id="glm-4.6", context_length=200000, owned_by="zhipu")]
    out = models_meta.normalize_live_models(live)
    assert len(out) == 1
    m = out[0]
    assert m["id"] == "glm-4.6"
    assert m["context"] == 200000
    assert m["owned_by"] == "zhipu"
    # 能力字段接口拿不到，统一未知
    assert m["thinking"] == "unknown"
    assert m["max_output"] is None
    assert m["thinking_param"] is None
    assert m["notes"] == "未知（待补充）"
    assert m["source"] == "live"
    print("[PASS] 透传 live 字段 + 能力统一未知")


def test_normalize_name_from_description():
    """name 优先取 description/display_name，否则回退到 id。"""
    with_desc = [LiveModel(id="m1", description="My Model")]
    out1 = models_meta.normalize_live_models(with_desc)
    assert out1[0]["name"] == "My Model"

    no_desc = [LiveModel(id="m2")]
    out2 = models_meta.normalize_live_models(no_desc)
    assert out2[0]["name"] == "m2"  # 回退到 id
    print("[PASS] name 取 description，无则回退 id")


def test_normalize_dedup_case_insensitive():
    """同 id 大小写不敏感去重（厂商偶发重复返回）。"""
    live = [
        LiveModel(id="glm-4.6", context_length=200000),
        LiveModel(id="GLM-4.6", context_length=128000),  # 大小写重复
        LiveModel(id="glm-4.5"),
    ]
    out = models_meta.normalize_live_models(live)
    assert len(out) == 2  # glm-4.6 去重，剩 glm-4.6 + glm-4.5
    ids = {m["id"] for m in out}
    assert "glm-4.5" in ids
    print("[PASS] id 大小写不敏感去重")


def test_normalize_context_none_when_missing():
    """接口没返回 context_length 时为 None（前端显示「未公开」）。"""
    live = [LiveModel(id="m1")]  # 无 context_length
    out = models_meta.normalize_live_models(live)
    assert out[0]["context"] is None
    print("[PASS] context 缺失时为 None")


def test_normalize_accepts_dict_input():
    """兼容直接传 dict 列表（部分场景不走 LiveModel）。"""
    live = [
        {"id": "m1", "context_length": 64000, "display_name": "Model One"},
    ]
    out = models_meta.normalize_live_models(live)
    assert len(out) == 1
    assert out[0]["id"] == "m1"
    assert out[0]["context"] == 64000
    assert out[0]["name"] == "Model One"  # display_name 也作为 name 候选
    print("[PASS] 兼容 dict 输入 + display_name 作为 name")


def test_format_context():
    """format_context 格式化。"""
    assert models_meta.format_context(None) == "未公开"
    assert models_meta.format_context(500) == "500"
    assert models_meta.format_context(64000) == "64K"
    assert models_meta.format_context(200000) == "200K"
    assert models_meta.format_context(1048576) == "1M"
    print("[PASS] format_context 各档格式化正确")


def test_live_model_parsing():
    """LiveModel 字段提取。"""
    m = LiveModel(id="test-model", created=123, owned_by="vendor", context_length=8192)
    assert m.id == "test-model"
    assert m.context_length == 8192
    print("[PASS] LiveModel 字段正确")


if __name__ == "__main__":
    test_live_model_parsing()
    test_format_context()
    test_normalize_empty()
    test_normalize_basic_fields()
    test_normalize_name_from_description()
    test_normalize_dedup_case_insensitive()
    test_normalize_context_none_when_missing()
    test_normalize_accepts_dict_input()
    print("\n=== 动态模型 normalize 逻辑测试全部通过 ===")
