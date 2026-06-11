#!/usr/bin/env bash
set -euo pipefail

with_faiss=0
skip_node_setup=0
skip_playwright=0

while [[ $# -gt 0 ]]; do
	case "$1" in
		--with-faiss)
			with_faiss=1
			shift
			;;
		--skip-node-setup)
			skip_node_setup=1
			shift
			;;
		--skip-playwright)
			skip_playwright=1
			shift
			;;
		-h|--help)
			cat <<'USAGE'
Usage: bash scripts/install-local.sh [--with-faiss] [--skip-node-setup] [--skip-playwright]

Installs the local AIDO development dependencies with Corepack/PNPM and uv.
USAGE
			exit 0
			;;
		*)
			echo "Unknown argument: $1" >&2
			exit 2
			;;
	esac
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$script_dir/.." && pwd)"

require_command() {
	local name="$1"
	local hint="$2"
	if ! command -v "$name" >/dev/null 2>&1; then
		echo "$name is required. $hint" >&2
		exit 1
	fi
}

run_step() {
	local name="$1"
	shift
	echo
	echo "==> $name"
	"$@"
}

cd "$project_root"

if [[ "$skip_node_setup" -eq 0 && -f ".nvmrc" ]]; then
	node_version="$(tr -d '[:space:]' < .nvmrc)"
	nvm_script="${NVM_DIR:-$HOME/.nvm}/nvm.sh"
	if [[ -s "$nvm_script" ]]; then
		# shellcheck source=/dev/null
		. "$nvm_script"
		run_step "Install and select the repository Node runtime" nvm install "$node_version"
		run_step "Use the repository Node runtime" nvm use "$node_version"
	else
		echo "nvm was not found; using the current node on PATH. Ensure it matches .nvmrc before release-grade checks."
	fi
fi

require_command "node" "Install Node 24.16.x or use nvm with the repository .nvmrc."
require_command "corepack" "Corepack is bundled with supported Node releases."
require_command "uv" "Install uv from https://docs.astral.sh/uv/."

run_step "Install JavaScript dependencies" corepack pnpm@10.24.0 install

uv_args=(sync --extra dev --extra test)
if [[ "$with_faiss" -eq 1 ]]; then
	uv_args+=(--extra faiss)
fi

run_step "Install Python environment" uv "${uv_args[@]}"

if [[ "$skip_playwright" -eq 0 ]]; then
	run_step "Install Playwright browsers" corepack pnpm@10.24.0 exec playwright install
fi

echo
echo "AIDO local dependencies are ready."
echo "Start the control center with: corepack pnpm@10.24.0 run start"
