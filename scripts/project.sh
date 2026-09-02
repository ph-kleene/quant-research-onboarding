#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"
SECRET_FILE="$HOME/.config/shangchen-quant-research-onboarding/tushare.env"

die() {
  printf '错误：%s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "缺少 $1；请先运行 bash scripts/project.sh setup"
}

load_token_if_available() {
  if [[ -n "${TUSHARE_TOKEN:-}" ]]; then
    return 0
  fi
  if [[ ! -f "$SECRET_FILE" ]]; then
    return 1
  fi
  local mode
  mode="$(stat -c '%a' "$SECRET_FILE")"
  [[ "$mode" == "600" ]] || die "秘密文件权限必须为 600（当前为 $mode）"
  set -a
  # shellcheck disable=SC1090
  source "$SECRET_FILE"
  set +a
  [[ -n "${TUSHARE_TOKEN:-}" ]] || die "秘密文件已找到，但 TUSHARE_TOKEN 为空"
}

setup() {
  require_command uv
  uv python install 3.12
  uv sync --all-groups
  require_command quarto
  printf '环境就绪：uv=%s，Python=%s，Quarto=%s\n' \
    "$(uv --version | awk '{print $2}')" \
    "$(uv run python -c 'import platform; print(platform.python_version())')" \
    "$(quarto --version)"
}

probe() {
  require_command uv
  load_token_if_available || die "未找到安全 Token；请设置 TUSHARE_TOKEN 或仓库外秘密文件"
  uv run quant-onboarding probe
}

fetch() {
  require_command uv
  load_token_if_available || die "未找到安全 Token；请设置 TUSHARE_TOKEN 或仓库外秘密文件"
  uv run quant-onboarding fetch
}

test_project() {
  require_command uv
  uv run ruff check src tests scripts
  uv run pytest --cov=quant_onboarding --cov-report=term-missing
  uv run python scripts/execute_notebook.py
}

build() {
  require_command uv
  require_command quarto
  uv run python scripts/generate_public_evidence.py
  # Execute notebook before rendering so outputs are captured in the site
  uv run python scripts/execute_notebook.py --in-place
  quarto render
  uv run python scripts/check_links.py _site
}

preview() {
  require_command quarto
  quarto preview --no-browser
}

audit() {
  require_command uv
  uv run python scripts/audit_repository.py
}

all() {
  setup
  test_project
  if load_token_if_available; then
    probe
    fetch
  else
    printf '提示：无 Token，跳过真实数据探测与刷新；继续构建教学预览。\n'
  fi
  build
  audit
}

case "${1:-}" in
  setup) setup ;;
  probe) probe ;;
  fetch) fetch ;;
  test) test_project ;;
  build) build ;;
  preview) preview ;;
  audit) audit ;;
  all) all ;;
  *) die "用法：bash scripts/project.sh {setup|probe|fetch|test|build|preview|audit|all}" ;;
esac
