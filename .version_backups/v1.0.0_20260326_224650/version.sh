#!/bin/bash
# version.sh — 版本管理工具
# 用法:
#   ./version.sh           查看当前版本
#   ./version.sh bump      自动小版本 +1（1.0.0 → 1.0.1）
#   ./version.sh set 1.2.0 设置指定版本号
#   ./version.sh tag       对当前版本打 git tag 快照
#   ./version.sh list      列出所有已打标签的版本
#   ./version.sh rollback  回滚到指定版本（交互式选择）

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION_FILE="$SCRIPT_DIR/VERSION"
BACKUP_DIR="$SCRIPT_DIR/.version_backups"

current_version() {
  cat "$VERSION_FILE" 2>/dev/null | tr -d '[:space:]'
}

show_version() {
  echo "当前版本: $(current_version)"
}

bump_version() {
  local ver=$(current_version)
  local major minor patch
  IFS='.' read -r major minor patch <<< "$ver"
  patch=$((patch + 1))
  local new_ver="$major.$minor.$patch"
  echo "$new_ver" > "$VERSION_FILE"
  echo "版本已更新: $ver → $new_ver"
}

set_version() {
  local new_ver="$1"
  if [[ ! "$new_ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "❌ 版本格式不正确，应为 x.y.z（如 1.2.0）"
    exit 1
  fi
  local old_ver=$(current_version)
  echo "$new_ver" > "$VERSION_FILE"
  echo "版本已设置: $old_ver → $new_ver"
}

tag_version() {
  local ver=$(current_version)
  local ts=$(date +"%Y%m%d_%H%M%S")
  
  # 备份当前代码到 .version_backups/v{ver}_{ts}
  mkdir -p "$BACKUP_DIR"
  local backup_path="$BACKUP_DIR/v${ver}_${ts}"
  
  # 备份关键文件（不包含数据库和报告）
  rsync -a \
    --exclude='.version_backups' \
    --exclude='tiktok_monitor.db' \
    --exclude='reports/' \
    --exclude='data/' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    "$SCRIPT_DIR/" "$backup_path/" 2>/dev/null
  
  if [ $? -eq 0 ]; then
    echo "✅ 版本快照已创建: v$ver"
    echo "   路径: $backup_path"
    echo "   时间: $(date '+%Y-%m-%d %H:%M:%S')"
    # 记录快照信息
    echo "v${ver} | ${ts} | $(date '+%Y-%m-%d %H:%M:%S')" >> "$BACKUP_DIR/versions.log"
  else
    echo "❌ 快照创建失败"
  fi
}

list_versions() {
  echo "已保存的版本快照:"
  if [ -f "$BACKUP_DIR/versions.log" ]; then
    cat "$BACKUP_DIR/versions.log" | while IFS='|' read -r ver ts time; do
      echo "  ${ver// /} (${time// /})"
    done
  else
    echo "  （暂无快照，使用 ./version.sh tag 创建快照）"
  fi
  echo ""
  echo "当前运行版本: $(current_version)"
}

rollback_version() {
  if [ ! -f "$BACKUP_DIR/versions.log" ]; then
    echo "❌ 没有可用的版本快照"
    echo "   使用 ./version.sh tag 在升级前先打快照"
    exit 1
  fi
  
  echo "可用的版本快照:"
  local i=1
  declare -a backups
  while IFS='|' read -r ver ts time; do
    ver=$(echo $ver | tr -d ' ')
    ts=$(echo $ts | tr -d ' ')
    time=$(echo $time | tr -d ' ')
    local dir="$BACKUP_DIR/v${ver}_${ts}"
    if [ -d "$dir" ]; then
      echo "  [$i] $ver  ($time)"
      backups[$i]="$dir"
      i=$((i+1))
    fi
  done < "$BACKUP_DIR/versions.log"
  
  echo ""
  read -p "请输入要回滚的版本编号 (q 取消): " choice
  
  if [ "$choice" = "q" ]; then
    echo "已取消"
    exit 0
  fi
  
  if [ -z "${backups[$choice]}" ]; then
    echo "❌ 无效的编号"
    exit 1
  fi
  
  local target="${backups[$choice]}"
  local target_ver=$(basename "$target" | sed 's/_[0-9]*_[0-9]*$//')
  
  echo ""
  echo "⚠️  即将回滚到 $target_ver"
  echo "   数据库不会被回滚（数据安全），仅回滚代码文件"
  read -p "确认回滚? (yes/n): " confirm
  
  if [ "$confirm" != "yes" ]; then
    echo "已取消"
    exit 0
  fi
  
  # 先对当前版本打一个快照（回滚前保留）
  echo "📦 先对当前版本打快照..."
  tag_version
  
  # 停止服务
  echo "⏹  停止当前服务..."
  lsof -ti:5001 | xargs kill -9 2>/dev/null
  sleep 1
  
  # 回滚代码文件（不覆盖数据库）
  rsync -a \
    --exclude='tiktok_monitor.db' \
    --exclude='reports/' \
    --exclude='data/' \
    "$target/" "$SCRIPT_DIR/" 2>/dev/null
  
  echo "✅ 回滚完成！已恢复到 $target_ver"
  echo ""
  echo "重新启动服务: ./run.sh"
}

# 主逻辑
case "$1" in
  "bump")     bump_version ;;
  "set")      set_version "$2" ;;
  "tag")      tag_version ;;
  "list")     list_versions ;;
  "rollback") rollback_version ;;
  "")         show_version ;;
  *)          echo "用法: ./version.sh [bump|set <版本>|tag|list|rollback]" ;;
esac
