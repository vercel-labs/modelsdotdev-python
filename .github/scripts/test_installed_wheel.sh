#!/bin/sh
set -eu

wheel_count=0
wheel_path=
for path in dist/*.whl; do
    if [ ! -e "$path" ]; then
        continue
    fi
    wheel_count=$((wheel_count + 1))
    wheel_path=$path
done

if [ "$wheel_count" -ne 1 ]; then
    printf 'expected exactly one wheel in dist/, found %s\n' "$wheel_count" >&2
    exit 1
fi

temp_dir=${RUNNER_TEMP:-${TMPDIR:-/tmp}}
test_root=$(mktemp -d "$temp_dir/modelsdotdev-installed-package-tests.XXXXXX")
cp -R tests "$test_root/tests"

MODELDOTDEV_REQUIRE_PACKAGED_DB=1 \
    uv run \
    --no-cache \
    --only-group=dev \
    --locked \
    --isolated \
    --with "$wheel_path" \
    pytest \
    -v \
    --tb=short \
    -k "not test_cqa_" \
    "$test_root/tests"
