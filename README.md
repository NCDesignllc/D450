# d450-linux

Linux driver for the D450 thermal label printer (sold with the "Labelife"
software; USB `2e3c:5756`, "QIN LabelPrinter"). The printer speaks TSPL at
203 dpi; this package produces the same command stream as the vendor's
Windows driver.

## Install

    packaging/build-deb.sh
    sudo apt install ./build/d450-linux_1.0.0_all.deb
    sudo d450-setup            # creates the CUPS queue "D450"

After that the printer shows up in every print dialog. Label size, darkness,
speed, media type, tear-off and halftoning are in the printer options.

    lp -d D450 -o PageSize=w288h432 label.pdf
    lp -d D450 -o PageSize=Custom.60x40mm -o d450Density=10 label.pdf

## Command-line tool

`d450print` sends images straight to the printer, without CUPS:

    d450print label.png                      # 4x6 in, scaled to fit
    d450print -s 60x40mm --density 10 a.png b.png
    d450print label.png -o job.prn           # write the job instead of printing
    d450print --decode job.prn -o preview    # describe a job, write preview PNGs

## Bluetooth

Pair the printer, add a queue with the `bluetooth://` URI that
`lpinfo -v` lists, and use the same PPD. Over Bluetooth the filter switches to
LZO-compressed bitmaps, as the Windows driver does. This path is built from
the driver's code but has not been tried on hardware.

## Protocol

    SIZE <w> mm,<h> mm
    REFERENCE 0,0
    GAP <g> mm,<o> mm            (continuous: GAP 0 mm,0 mm; black mark: BLINE)
    OFFSET <n> mm
    DIRECTION 0,0
    DENSITY <0-15>
    SPEED <2-7>
    SET TEAR OFF | SET TEAR ON | SET PEEL ON
    CLS
    BITMAP <x>,<y>,<bytes per row>,<rows>,<mode>,<data>
    PRINT 1,<copies>

Lines end in CRLF. Bitmap rows are 1 bit per dot, MSB first, 1 = white.
Blank rows at the top and bottom are not sent; `y` skips the top ones.
Mode 1 is raw. Mode 4 is the bitmap cut into 4096-byte chunks, each
LZO1X-compressed and prefixed with its 4-byte little-endian length, ended by
four zero bytes.

## Tests

    python3 -m unittest discover -s tests
