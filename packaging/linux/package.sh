#!/usr/bin/env bash
# Package an already-built dist/clipivo into a tarball, a deb and an rpm.
# Expects VERSION in the environment. Used by .github/workflows/release.yml.
set -euo pipefail

VERSION="${VERSION:?VERSION must be set}"
NAME="clipivo"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Overridable so a local build can stay outside a synced folder.
DIST="${DIST_DIR:-$ROOT/dist}"
OUT="${RELEASE_DIR:-$ROOT/release}"
WORK="${WORK_DIR:-$ROOT/build}"
APP="$DIST/$NAME"
HERE="$ROOT/packaging/linux"
[ -d "$APP" ] || { echo "missing $APP; build first" >&2; exit 1; }
mkdir -p "$OUT"

# Portable tarball
tar -czf "$OUT/${NAME}-v${VERSION}-linux-x86_64.tar.gz" -C "$DIST" "$NAME"

# The installed tree, shared by both packages.
write_tree() {
  local root="$1"
  install -d "$root/opt/$NAME" "$root/usr/bin" "$root/usr/share/applications" \
             "$root/usr/share/icons/hicolor/512x512/apps" "$root/usr/share/icons/hicolor/scalable/apps"
  cp -a "$APP/." "$root/opt/$NAME/"
  ln -sf "/opt/$NAME/$NAME" "$root/usr/bin/$NAME"
  install -m 644 "$HERE/io.longweekendlabs.Clipivo.desktop" "$root/usr/share/applications/"
  install -m 644 "$ROOT/assets/icons/clipivo.png" "$root/usr/share/icons/hicolor/512x512/apps/io.longweekendlabs.Clipivo.png"
  install -m 644 "$HERE/io.longweekendlabs.Clipivo.svg" "$root/usr/share/icons/hicolor/scalable/apps/"
}

# deb
DEB="$WORK/deb"
rm -rf "$DEB"; mkdir -p "$DEB/DEBIAN"
write_tree "$DEB"
cat > "$DEB/DEBIAN/control" <<CONTROL
Package: $NAME
Version: $VERSION
Section: video
Priority: optional
Architecture: amd64
Maintainer: Long Weekend Labs <2272935+longweekendlabs@users.noreply.github.com>
Depends: libvlc5, vlc-plugin-base, ffmpeg, libxcb-cursor0, libxkbcommon-x11-0, libegl1, libgl1
Homepage: https://github.com/longweekendlabs/clipivo
Description: Find every appearance of a person in your videos and cut it out
 Clipivo finds every appearance of a person in your videos and cuts each one
 into a clip, or follows them through split screens and studio shots. Face
 matching runs on your own computer. The face models are downloaded on first
 launch.
CONTROL
dpkg-deb --build --root-owner-group "$DEB" "$OUT/${NAME}_${VERSION}_amd64.deb" >/dev/null

# rpm, from the same tree
RPMTOP="$WORK/rpm"
TREE="$WORK/rpm-tree"
rm -rf "$RPMTOP" "$TREE"
mkdir -p "$RPMTOP"/{BUILD,RPMS,SOURCES,SPECS,SRPMS} "$TREE"
write_tree "$TREE"
tar -czf "$RPMTOP/SOURCES/${NAME}-${VERSION}-tree.tar.gz" -C "$TREE" .
rpmbuild -bb "$HERE/clipivo.spec" \
  --define "_topdir $RPMTOP" --define "clipivo_version $VERSION" >/dev/null
cp "$RPMTOP"/RPMS/x86_64/${NAME}-${VERSION}-1.x86_64.rpm "$OUT/"

# The RPM must carry the build byte for byte; refuse to publish one that rewrote a library.
CHECK="$WORK/rpm-check"
rm -rf "$CHECK"; mkdir -p "$CHECK"
(cd "$CHECK" && rpm2cpio "$OUT/${NAME}-${VERSION}-1.x86_64.rpm" | cpio -idm --quiet)
changed=0
while IFS= read -r -d '' lib; do
  rel="${lib#"$APP/"}"
  if ! cmp -s "$lib" "$CHECK/opt/$NAME/$rel"; then
    echo "x  $rel differs between the build and the RPM"
    changed=1
  fi
done < <(find "$APP" -name '*.so*' -type f -print0)
rm -rf "$CHECK"
[ "$changed" = 0 ] || { echo "The RPM altered shared libraries; refusing to publish it." >&2; exit 1; }

echo "Packaged into $OUT:"
ls -lh "$OUT"
