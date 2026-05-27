#!/usr/bin/env python3
"""
MCP服务器诊断脚本
用于检查MCP服务器是否能正常启动和运行
"""

import sys
import json
from pathlib import Path

def check_dependencies():
    """检查依赖是否安装"""
    print("🔍 检查依赖...")
    missing = []
    
    try:
        import requests
        print("  ✅ requests")
    except ImportError:
        print("  ❌ requests")
        missing.append("requests")
    
    try:
        import mcp
        print("  ✅ mcp")
    except ImportError:
        print("  ❌ mcp")
        missing.append("mcp")
    
    try:
        import lxml
        print("  ✅ lxml")
    except ImportError:
        print("  ❌ lxml")
        missing.append("lxml")
    
    try:
        from Crypto.Cipher import AES
        print("  ✅ pycryptodome")
    except ImportError:
        print("  ❌ pycryptodome")
        missing.append("pycryptodome")
    
    if missing:
        print(f"\n❌ 缺少依赖: {', '.join(missing)}")
        print(f"请运行: pip install {' '.join(missing)}")
        return False
    
    print("✅ 所有依赖已安装\n")
    return True

def check_files():
    """检查必要文件是否存在"""
    print("🔍 检查文件...")
    base_dir = Path(__file__).parent
    
    required_files = [
        "mcp_server.py",
        "course_schedule.py",
        "schedule_cleaner.py",
        "campus_core/__init__.py",
        "campus_core/bot.py",
        "campus_core/config/library_locations.json",
        "requirements.txt"
    ]
    
    missing = []
    for file in required_files:
        file_path = base_dir / file
        if file_path.exists():
            print(f"  ✅ {file}")
        else:
            print(f"  ❌ {file}")
            missing.append(file)
    
    if missing:
        print(f"\n❌ 缺少文件: {', '.join(missing)}")
        return False
    
    print("✅ 所有必要文件存在\n")
    return True

def check_mcp_server():
    """检查MCP服务器是否能导入"""
    print("🔍 检查MCP服务器...")
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import mcp_server
        print("  ✅ MCP服务器模块导入成功")
        
        # 检查工具是否定义
        if hasattr(mcp_server, 'mcp'):
            print("  ✅ MCP实例已创建")
        else:
            print("  ❌ MCP实例未找到")
            return False
        
        print("✅ MCP服务器检查通过\n")
        return True
    except Exception as e:
        print(f"  ❌ 导入失败: {e}")
        return False

def generate_config():
    """生成配置示例"""
    print("📝 生成配置示例...")
    base_dir = Path(__file__).parent.resolve()
    
    configs = {
        "Cherry Studio - 方式1 (推荐)": {
            "mcpServers": {
                "henu-campus": {
                    "command": "python3",
                    "args": [
                        str(base_dir / "mcp_server.py"),
                        "--transport",
                        "stdio"
                    ]
                }
            }
        },
        "Cherry Studio - 方式2 (使用bash)": {
            "mcpServers": {
                "henu-campus": {
                    "command": "bash",
                    "args": [
                        "-c",
                        f'cd "{base_dir}" && python3 mcp_server.py --transport stdio'
                    ]
                }
            }
        },
        "其他MCP客户端": {
            "mcpServers": {
                "henu-campus": {
                    "command": "python3",
                    "args": [str(base_dir / "mcp_server.py")],
                    "transport": "stdio"
                }
            }
        }
    }
    
    for name, config in configs.items():
        print(f"\n{name}:")
        print(json.dumps(config, indent=2, ensure_ascii=False))
    
    print("\n✅ 配置示例已生成\n")

def main():
    print("=" * 60)
    print("河大校园助手 MCP 服务器诊断工具")
    print("=" * 60)
    print()
    
    all_ok = True
    
    # 检查依赖
    if not check_dependencies():
        all_ok = False
    
    # 检查文件
    if not check_files():
        all_ok = False
    
    # 检查MCP服务器
    if not check_mcp_server():
        all_ok = False
    
    # 生成配置
    generate_config()
    
    print("=" * 60)
    if all_ok:
        print("✅ 所有检查通过！MCP服务器应该可以正常运行")
        print("\n建议:")
        print("1. 复制上面的配置到你的MCP客户端")
        print("2. 重启MCP客户端")
        print("3. 测试连接")
    else:
        print("❌ 发现问题，请根据上面的提示修复")
    print("=" * 60)

if __name__ == "__main__":
    main()
