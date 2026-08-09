#!/usr/bin/env bash

# 河大校园助手 MCP 服务器 - 虚拟环境安装脚本

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "🚀 河大校园助手 MCP 服务器安装"
echo ""

# 1. 检查 Python
echo "1️⃣ 检查 Python..."
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 Python3，请先安装："
    echo "   sudo apt install python3 python3-venv python3-pip"
    exit 1
fi
echo "✅ Python3 已安装"

LOCK_FILE="$(python3 scripts/select_lockfile.py --check)"
HENU_PYPI_INDEX_URL="${HENU_PYPI_INDEX_URL:-https://pypi.org/simple}"
echo "✅ 使用冻结依赖: ${LOCK_FILE#$PROJECT_ROOT/}"

# 2. 创建虚拟环境
echo ""
echo "2️⃣ 创建虚拟环境..."
if [ -L "venv" ]; then
    echo "❌ 拒绝清理符号链接 venv，请人工确认后移除"
    exit 1
fi
python3 -m venv --clear venv
echo "✅ 虚拟环境创建完成"

# 3. 激活并安装依赖
echo ""
echo "3️⃣ 安装依赖包..."
PIP_CONFIG_FILE=/dev/null \
PIP_INDEX_URL="$HENU_PYPI_INDEX_URL" \
PIP_EXTRA_INDEX_URL= \
venv/bin/python -m pip install --require-hashes -r "$LOCK_FILE"
echo "✅ 依赖安装完成"

# 4. 验证安装
echo ""
echo "4️⃣ 验证安装..."
venv/bin/python diagnose_mcp.py

echo ""
echo "🎉 安装成功！"
echo ""
echo "使用方法："
echo "1. 激活环境: source venv/bin/activate"
echo "2. 运行服务: python3 mcp_server.py --transport stdio"
echo "3. 退出环境: deactivate"
echo ""
