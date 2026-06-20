"""一键运行所有测试套件。

用法：python run_tests.py
"""
import os
import subprocess
import sys

TESTS = [
    ("冒烟测试 (加密/DB/DeepSeek)", "tests/test_smoke.py"),
    ("Adapter parser (GLM/Kimi/MiniMax)", "tests/test_providers.py"),
    ("中转站 adapter (OpenAI 兼容)", "tests/test_openai_proxy.py"),
    ("真实样本回归", "tests/test_real_samples.py"),
    ("定时 + 历史趋势", "tests/test_scheduler_history.py"),
    ("告警 + 每日报告", "tests/test_alerts.py"),
    ("动态模型查询", "tests/test_live_models.py"),
    ("模型能力表", "tests/test_models_meta.py"),
    ("端到端 HTTP (自动拉起服务)", "tests/test_e2e.py"),
]

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    total_pass, total_all = 0, 0
    failed_suites = []

    for name, path in TESTS:
        print(f"\n{'='*50}")
        print(f"▶ {name}")
        print('='*50)
        result = subprocess.run(
            [sys.executable, "-u", os.path.join(here, path)],
            capture_output=False,
        )
        if result.returncode != 0:
            failed_suites.append(name)

    print(f"\n{'='*50}")
    print("汇总")
    print('='*50)
    if failed_suites:
        print(f"❌ 失败的套件：{', '.join(failed_suites)}")
        sys.exit(1)
    else:
        print("✅ 所有测试套件通过")
        sys.exit(0)

if __name__ == "__main__":
    main()
