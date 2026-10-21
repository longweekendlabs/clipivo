#!/usr/bin/bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' "$project_dir/version.py")"
release="${CLIPIVO_RPM_RELEASE:-1.local.$(date +%Y%m%d)}"
# Scratch space for staging and rpmbuild; set it outside synced folders.
work_dir="${CLIPIVO_BUILD_DIR:-$project_dir/packaging}"
mkdir -p "$work_dir"
topdir="$work_dir/rpmbuild"
dist_dir="$project_dir/dist"
rpm_tmpdir="/tmp/clipivo-rpm-tmp"
stage_parent="$(mktemp -d "$work_dir/.rpm-stage.XXXXXX")"
stage="$stage_parent/clipivo-$version"
# The tested runtime to vendor; defaults to the project's own .venv.
venv="${CLIPIVO_VENV:-$project_dir/.venv}"

trap 'rm -rf -- "$stage_parent"' EXIT

if [[ -z "$version" || ! -x "$venv/bin/python" ]]; then
    echo "Clipivo version or tested virtual environment is missing." >&2
    exit 1
fi

mkdir -p "$stage" "$topdir/BUILD" "$topdir/BUILDROOT" "$topdir/RPMS" \
    "$topdir/SOURCES" "$topdir/SPECS" "$topdir/SRPMS" "$dist_dir" "$rpm_tmpdir"

for item in app.py version.py LICENSE README.md CHANGELOG.md assets models; do
    cp -a "$project_dir/$item" "$stage/"
done
rsync -a --exclude '__pycache__/' --exclude '*.pyc' "$project_dir/core/" "$stage/core/"
rsync -a --exclude '__pycache__/' --exclude '*.pyc' "$project_dir/ui/" "$stage/ui/"

mkdir -p "$stage/.venv/bin" "$stage/.venv/lib"
cp "$project_dir/packaging/pyvenv.cfg" "$stage/.venv/pyvenv.cfg"
ln -s /usr/bin/python3.11 "$stage/.venv/bin/python3.11"
ln -s python3.11 "$stage/.venv/bin/python3"
ln -s python3.11 "$stage/.venv/bin/python"
ln -s lib "$stage/.venv/lib64"
rsync -a \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'pip/' \
    --exclude 'pip-*.dist-info/' \
    --exclude 'setuptools/' \
    --exclude 'setuptools-*.dist-info/' \
    --exclude 'pkg_resources/' \
    --exclude '_distutils_hack/' \
    --exclude 'distutils-precedence.pth' \
    "$venv/lib/" "$stage/.venv/lib/"

tar --zstd -cf "$topdir/SOURCES/clipivo-$version.tar.zst" -C "$stage_parent" "clipivo-$version"
cp "$project_dir/packaging/clipivo-launcher" "$topdir/SOURCES/clipivo-launcher"
cp "$project_dir/packaging/io.longweekendlabs.Clipivo.desktop" "$topdir/SOURCES/io.longweekendlabs.Clipivo.desktop"
cp "$project_dir/packaging/io.longweekendlabs.Clipivo.svg" "$topdir/SOURCES/io.longweekendlabs.Clipivo.svg"
cp "$project_dir/packaging/clipivo.spec" "$topdir/SPECS/clipivo.spec"

QA_RPATHS=0x0002 rpmbuild \
    --define "_topdir $topdir" \
    --define "_tmppath $rpm_tmpdir" \
    --define "clipivo_version $version" \
    --define "clipivo_release $release" \
    -bb "$topdir/SPECS/clipivo.spec"

find "$topdir/RPMS/x86_64" -maxdepth 1 -type f -name "clipivo-$version-*.x86_64.rpm" \
    -exec cp -f {} "$dist_dir/" \;

echo "RPM ready in $dist_dir"
