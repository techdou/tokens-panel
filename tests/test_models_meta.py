"""模型能力表测试。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_all_providers_have_models():
    from app import models_meta
    for p in ["deepseek", "glm", "kimi", "minimax"]:
        assert p in models_meta.MODELS, f"{p} 缺失"
        assert len(models_meta.MODELS[p]) > 0, f"{p} 没有模型"
    print("[PASS] 4 家都有模型数据")


def test_each_model_has_required_fields():
    from app import models_meta
    required = {"id", "name", "context", "thinking", "notes", "source"}
    valid_thinking = ("supported", "unsupported", "default_on", "supported_param_unknown", "unknown")
    for provider, models in models_meta.MODELS.items():
        for m in models:
            missing = required - set(m.keys())
            assert not missing, f"{provider}/{m.get('id')} 缺字段: {missing}"
            # thinking 值校验
            assert m["thinking"] in valid_thinking, \
                f"{m['id']} thinking 值非法: {m['thinking']}"
            # 明确"支持"思考的必须给参数示例；参数未公开的用 supported_param_unknown
            if m["thinking"] == "supported":
                assert m.get("thinking_param"), f"{m['id']} 标 supported 但没给 thinking_param（若参数未知请用 supported_param_unknown）"
    print("[PASS] 所有模型字段完整且 thinking 值合法")


def test_context_formatting():
    from app import models_meta
    assert models_meta.format_context(1000) == "1K"
    assert models_meta.format_context(131072) == "131K"
    assert models_meta.format_context(1048576) == "1M"
    assert models_meta.format_context(64000) == "64K"
    assert models_meta.format_context(None) == "未公开"
    print("[PASS] 上下文长度格式化正确")


def test_list_models_filter():
    from app import models_meta
    # 单家过滤
    r = models_meta.list_models("glm")
    assert "glm" in r["models"]
    assert "deepseek" not in r["models"]
    assert r["last_updated"]
    # 全部
    r2 = models_meta.list_models()
    assert len(r2["models"]) == 4
    print("[PASS] list_models 过滤正确")


def test_context_values_reasonable():
    """上下文长度应在合理范围（防止写错数量级）。"""
    from app import models_meta
    for provider, models in models_meta.MODELS.items():
        for m in models:
            if m["context"] is not None:
                assert 1000 <= m["context"] <= 10_000_000, \
                    f"{m['id']} 上下文 {m['context']} 不在合理范围"
    print("[PASS] 上下文长度数值合理")


if __name__ == "__main__":
    test_all_providers_have_models()
    test_each_model_has_required_fields()
    test_context_formatting()
    test_list_models_filter()
    test_context_values_reasonable()
    print("\n=== 模型能力表测试全部通过 ===")
