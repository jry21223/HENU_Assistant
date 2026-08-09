#!/usr/bin/env bash
# 河大校园助手 · Langbot 插件 — 开发环境安装脚本
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "河大校园助手 Langbot 插件 — 开发环境安装"
echo "工作目录: $ROOT"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3。请先安装 Python 3.10+（建议 3.11–3.13）。"
  exit 1
fi

echo "1/5 创建运行虚拟环境 .venv ..."
if [[ -L .venv ]]; then
  echo "拒绝清理符号链接 .venv，请人工确认后移除。"
  exit 1
fi
LOCK_FILE="$(python3 scripts/select_lockfile.py --check)"
HENU_PYPI_INDEX_URL="${HENU_PYPI_INDEX_URL:-https://pypi.org/simple}"
python3 -m venv --clear .venv

echo "2/5 安装 hash 校验的冻结运行依赖 ..."
echo "   ${LOCK_FILE#$ROOT/}"
PIP_CONFIG_FILE=/dev/null \
PIP_INDEX_URL="$HENU_PYPI_INDEX_URL" \
PIP_EXTRA_INDEX_URL= \
.venv/bin/python -m pip install --require-hashes -r "$LOCK_FILE"

if [[ ! -f .env ]]; then
  echo "3/5 从 env.example 生成 .env ..."
  cp env.example .env
  echo "   请编辑 .env：生产环境必须设置稳定的 HENU_MASTER_KEY"
else
  echo "3/5 已存在 .env，跳过复制"
fi

echo "4/5 检查现代 LangBot runtime 与插件入口 ..."
.venv/bin/python - <<'PY'
from importlib.metadata import version
from main import HenuAssistantPlugin

assert HenuAssistantPlugin.__name__ == "HenuAssistantPlugin"
assert version("langbot-plugin") == "0.5.0"
assert version("mcp") == "2.0.0"
PY

echo "5/5 准备隔离的 lbp==0.1.2 构建环境 ..."
if command -v python3.13 >/dev/null 2>&1; then
  if [[ -L .lbp-build-venv ]]; then
    echo "拒绝清理符号链接 .lbp-build-venv，请人工确认后移除。"
    exit 1
  fi
  python3.13 -m venv --clear .lbp-build-venv
  PIP_CONFIG_FILE=/dev/null \
  PIP_INDEX_URL="$HENU_PYPI_INDEX_URL" \
  PIP_EXTRA_INDEX_URL= \
  .lbp-build-venv/bin/python -m pip install --require-hashes \
    -r requirements-lock/lbp-py313.txt
  .lbp-build-venv/bin/python - <<'PY'
from importlib.metadata import version

assert version("lbp") == "0.1.2"
PY
  .venv/bin/python scripts/build_plugin.py --help >/dev/null
else
  echo "   未找到 python3.13；运行和测试已就绪，构建发布包前需安装 Python 3.13。"
fi

echo ""
echo "安装完成。"
echo ""
echo "下一步："
echo "  调试运行:   .venv/bin/lbp run"
echo "  构建并验证（需独立 Python 3.13 builder）: .venv/bin/python scripts/build_plugin.py"
echo "  跑测试:     .venv/bin/python -m pytest"
echo ""
echo "终端用户若只想装插件：从 GitHub Release 下载 dist/*.lbpkg，在 LangBot 中安装，"
echo "并配置稳定的 HENU_MASTER_KEY。详见 README「安装」。"
echo ""
