#!/bin/sh
# Build d450-linux_<version>_all.deb into build/.
set -e
cd "$(dirname "$0")/.."
VERSION="${VERSION:-1.0.1}"
STAGE=build/deb
rm -rf "$STAGE"
python3 -I tools/make_ppd.py ppd/D450.ppd
python3 -m unittest discover -s tests -q
install -D -m 755 filter/rastertod450 "$STAGE/usr/lib/cups/filter/rastertod450"
install -D -m 644 d450tspl.py "$STAGE/usr/lib/d450/d450tspl.py"
install -D -m 755 bin/d450print "$STAGE/usr/bin/d450print"
install -D -m 755 bin/d450-setup "$STAGE/usr/sbin/d450-setup"
install -D -m 644 ppd/D450.ppd "$STAGE/usr/share/ppd/d450/D450.ppd"
install -D -m 644 packaging/70-d450-label-printer.rules \
    "$STAGE/usr/lib/udev/rules.d/70-d450-label-printer.rules"
install -D -m 644 README.md "$STAGE/usr/share/doc/d450-linux/README.md"
install -D -m 755 packaging/postinst "$STAGE/DEBIAN/postinst"
SIZE=$(du -sk "$STAGE/usr" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: d450-linux
Version: $VERSION
Section: text
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), cups, cups-filters | cups-filters-core-drivers, liblzo2-2
Recommends: python3-pil
Installed-Size: $SIZE
Maintainer: d450-linux <root@localhost>
Description: Linux driver for the D450 thermal label printer
 CUPS raster filter, PPD, udev rule and a command-line tool for the D450
 (QIN LabelPrinter, USB 2e3c:5756). Generates the same TSPL stream as the
 vendor's Windows driver.
CONTROL
dpkg-deb --build --root-owner-group "$STAGE" "build/d450-linux_${VERSION}_all.deb"
