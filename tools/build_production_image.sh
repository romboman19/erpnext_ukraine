#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
contract_path="${repo_root}/deployment/production/image-contract.json"
lock_path="${repo_root}/deployment/production/source-lock.json"
containerfile_path="${repo_root}/deployment/production/Containerfile"

cd "${repo_root}"

if [[ -n "$(git status --porcelain)" ]]; then
	printf 'Production image builds require a clean git checkout.\n' >&2
	exit 1
fi

python3 tools/validate_production_image.py --source-root "${repo_root}" --contract "${contract_path}"

erpnext_ua_commit="$(git rev-parse HEAD)"
erpnext_ua_version="$(jq -r '.application_versions.erpnext_ua' "${contract_path}")"
base_reference="$(jq -r '.base_image.reference' "${lock_path}")"
base_digest="$(jq -r '.base_image.digest' "${lock_path}")"
print_designer_url="$(jq -r '.apps.print_designer.url' "${lock_path}")"
print_designer_commit="$(jq -r '.apps.print_designer.commit' "${lock_path}")"
chromium_url="$(jq -r '.runtime_artifacts.print_designer_chromium.url' "${lock_path}")"
chromium_sha256="$(jq -r '.runtime_artifacts.print_designer_chromium.sha256' "${lock_path}")"
short_commit="$(git rev-parse --short=12 HEAD)"
image_tag="${1:-erpnext-ua:${erpnext_ua_version}-${short_commit}}"

docker build \
	--build-arg "ERPNEXT_IMAGE=${base_reference}@${base_digest}" \
	--build-arg "ERPNEXT_UA_COMMIT=${erpnext_ua_commit}" \
	--build-arg "PRINT_DESIGNER_COMMIT=${print_designer_commit}" \
	--build-arg "PRINT_DESIGNER_URL=${print_designer_url}" \
	--build-arg "CHROMIUM_URL=${chromium_url}" \
	--build-arg "CHROMIUM_SHA256=${chromium_sha256}" \
	--file "${containerfile_path}" \
	--tag "${image_tag}" \
	"${repo_root}"

docker run --rm \
	--entrypoint /usr/local/bin/validate-production-image \
	"${image_tag}" \
	--bench-root /home/frappe/frappe-bench \
	--contract /opt/frappe/production/image-contract.json

printf 'Production image passed: %s\n' "${image_tag}"
