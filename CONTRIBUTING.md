# Contributing

## 开发原则

- 涉及可信边界、数据契约或较大行为变化，先在 `docs/superpowers/specs/` 写设计，再实现与测试。
- 三个协作模块保持松耦合，不相互 import；通过 CLI、JSON 与 frontmatter 契约协作。
- 测试和示例只能使用合成或充分脱敏的数据，禁止提交真实案件材料与凭据。
- Windows 是主要用户平台；路径、编码和 Office/COM 相关变化必须考虑 Windows 与 Ubuntu。

## 本地验证

```powershell
# lawiki
cd lawiki
python -m pytest skill/lawiki scripts test_install.py -q

# makeitdown
cd ../makeitdown
uv run --extra dev python -m pytest tests -q

# rag-retriever
cd ../rag-retriever
uv run --group dev python -m pytest tests -q

# 仓库门禁
cd ..
uvx ruff check --select E9,F .
git diff --check
```

提交前同步受影响的 README、skill/reference 文档、示例输出和机器可读契约测试。
