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

echo "1/4 创建虚拟环境 .venv ..."
if [[ -d .venv ]]; then
  echo "   已存在 .venv，复用"
else
  python3 -m venv .venv
fi

echo "2/4 安装业务依赖 + lbp ..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install lbp

if [[ ! -f .env ]]; then
  echo "3/4 从 .env.example 生成 .env ..."
  cp .env.example .env
  echo "   请编辑 .env：生产环境必须设置稳定的 HENU_MASTER_KEY"
else
  echo "3/4 已存在 .env，跳过复制"
fi

echo "4/4 检查 lbp ..."
if ! .venv/bin/lbp --help >/dev/null 2>&1; then
  echo "lbp 安装后仍不可用，请检查 pip 与 PATH。"
  exit 1
fi

echo ""
echo "安装完成。"
echo ""
echo "下一步："
echo "  调试运行:  .venv/bin/lbp run"
echo "  构建包:    .venv/bin/lbp build"
echo "  跑测试:    .venv/bin/pip install pytest && .venv/bin/pytest"
echo ""
echo "终端用户若只想装插件：从 GitHub Release 下载 dist/*.lbpkg，在 LangBot 中安装，"
echo "并配置稳定的 HENU_MASTER_KEY。详见 README「安装」。"
echo ""
