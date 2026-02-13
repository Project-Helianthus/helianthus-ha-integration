#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

URL=""
HOST="127.0.0.1"
PORT="8080"
PATH_VALUE="/graphql"
TRANSPORT="http"
TIMEOUT="10"
JSON_MODE=0

EBUSD_HOST="127.0.0.1"
EBUSD_PORT="8888"
PROXY_PROFILE="enh"
PROXY_HOST="127.0.0.1"
PROXY_PORT=""

usage() {
	cat <<'USAGE'
usage: run-ha-dual-topology-smoke.sh [options]

Options:
  --url <url>                 Full GraphQL URL (overrides host/port/path/transport)
  --host <host>               GraphQL host (default: 127.0.0.1)
  --port <port>               GraphQL port (default: 8080)
  --path <path>               GraphQL path (default: /graphql)
  --transport <http|https>    GraphQL transport (default: http)
  --timeout <seconds>         Request/probe timeout (default: 10)
  --json                      Print JSON output
  --ebusd-host <host>         ebusd host probe target (default: 127.0.0.1)
  --ebusd-port <port>         ebusd port probe target (default: 8888)
  --proxy-profile <enh|ens>   adapter-proxy profile (default: enh)
  --proxy-host <host>         adapter-proxy host probe target (default: 127.0.0.1)
  --proxy-port <port>         adapter-proxy port (default: 19001 for enh, 19002 for ens)
USAGE
}

require_value() {
	local option="$1"
	if [[ $# -lt 2 ]]; then
		echo "missing value for ${option}" >&2
		usage
		exit 2
	fi
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--url)
			require_value "$1" "$@"
			URL="$2"
			shift 2
			;;
		--url=*)
			URL="${1#*=}"
			shift
			;;
		--host)
			require_value "$1" "$@"
			HOST="$2"
			shift 2
			;;
		--host=*)
			HOST="${1#*=}"
			shift
			;;
		--port)
			require_value "$1" "$@"
			PORT="$2"
			shift 2
			;;
		--port=*)
			PORT="${1#*=}"
			shift
			;;
		--path)
			require_value "$1" "$@"
			PATH_VALUE="$2"
			shift 2
			;;
		--path=*)
			PATH_VALUE="${1#*=}"
			shift
			;;
		--transport)
			require_value "$1" "$@"
			TRANSPORT="$2"
			shift 2
			;;
		--transport=*)
			TRANSPORT="${1#*=}"
			shift
			;;
		--timeout)
			require_value "$1" "$@"
			TIMEOUT="$2"
			shift 2
			;;
		--timeout=*)
			TIMEOUT="${1#*=}"
			shift
			;;
		--json)
			JSON_MODE=1
			shift
			;;
		--ebusd-host)
			require_value "$1" "$@"
			EBUSD_HOST="$2"
			shift 2
			;;
		--ebusd-host=*)
			EBUSD_HOST="${1#*=}"
			shift
			;;
		--ebusd-port)
			require_value "$1" "$@"
			EBUSD_PORT="$2"
			shift 2
			;;
		--ebusd-port=*)
			EBUSD_PORT="${1#*=}"
			shift
			;;
		--proxy-profile)
			require_value "$1" "$@"
			PROXY_PROFILE="$2"
			shift 2
			;;
		--proxy-profile=*)
			PROXY_PROFILE="${1#*=}"
			shift
			;;
		--proxy-host)
			require_value "$1" "$@"
			PROXY_HOST="$2"
			shift 2
			;;
		--proxy-host=*)
			PROXY_HOST="${1#*=}"
			shift
			;;
		--proxy-port)
			require_value "$1" "$@"
			PROXY_PORT="$2"
			shift 2
			;;
		--proxy-port=*)
			PROXY_PORT="${1#*=}"
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			echo "unknown argument: $1" >&2
			usage
			exit 2
			;;
	esac
done

PROXY_PROFILE="$(printf '%s' "${PROXY_PROFILE}" | tr '[:upper:]' '[:lower:]')"
case "${PROXY_PROFILE}" in
	enh)
		DEFAULT_PROXY_PORT="19001"
		;;
	ens)
		DEFAULT_PROXY_PORT="19002"
		;;
	*)
		echo "proxy profile must be enh or ens" >&2
		usage
		exit 2
		;;
esac

if [[ -z "${PROXY_PORT}" ]]; then
	PROXY_PORT="${DEFAULT_PROXY_PORT}"
fi

CMD=(
	python -m custom_components.helianthus.smoke_profile
	--timeout "${TIMEOUT}"
	--dual-topology
	--ebusd-host "${EBUSD_HOST}"
	--ebusd-port "${EBUSD_PORT}"
	--proxy-profile "${PROXY_PROFILE}"
	--proxy-host "${PROXY_HOST}"
	--proxy-port "${PROXY_PORT}"
)

if [[ -n "${URL}" ]]; then
	CMD+=(--url "${URL}")
else
	CMD+=(--host "${HOST}" --port "${PORT}" --path "${PATH_VALUE}" --transport "${TRANSPORT}")
fi

if [[ "${JSON_MODE}" -eq 1 ]]; then
	CMD+=(--json)
fi

"${CMD[@]}"
