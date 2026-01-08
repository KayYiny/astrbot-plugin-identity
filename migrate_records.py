#!/usr/bin/env python3
"""
迁移脚本：将旧的 dog_records.json 迁移为 identity_records.json（备份并验证格式）

用法：在仓库根目录运行：
    python migrate_records.py

脚本行为：
 - 如果 data/plugins/random_dog/identity_records.json 已存在，则不做任何事
 - 如果 identity_records.json 不存在且 dog_records.json 存在，则复制一份为 identity_records.json
 - 在复制后会验证 JSON 包含基础结构 {"date":..., "groups":...}
"""
import os
import shutil
import json

DATA_DIR = os.path.join("data", "plugins", "random_identity")
OLD = os.path.join(DATA_DIR, "dog_records.json")
NEW = os.path.join(DATA_DIR, "identity_records.json")

def ensure_dir():
    if not os.path.exists(DATA_DIR):
        print(f"数据目录不存在：{DATA_DIR}")
        return False
    return True

def validate_records(path: str) -> bool:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and 'date' in data and 'groups' in data:
            return True
        print(f"警告：{path} 格式不符合预期（缺少 date 或 groups）")
        return False
    except Exception as e:
        print(f"读取或解析 {path} 失败: {e}")
        return False

def migrate():
    if not ensure_dir():
        return

    if os.path.exists(NEW):
        print(f"目标文件已存在，跳过迁移: {NEW}")
        return

    if os.path.exists(OLD):
        try:
            shutil.copy2(OLD, NEW)
            print(f"已复制 {OLD} -> {NEW}")
            if validate_records(NEW):
                print("迁移完成，格式检查通过。")
            else:
                print("迁移完成，但格式检查失败，请手动检查文件内容。")
        except Exception as e:
            print(f"复制文件时出错: {e}")
    else:
        print(f"未发现旧记录文件: {OLD}，无需迁移。")

if __name__ == '__main__':
    migrate()
