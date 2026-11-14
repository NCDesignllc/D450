#!/usr/bin/python3 -I
"""Generate ppd/D450.ppd. Page sizes come from the vendor's D450.gpd (dots at 203 dpi)."""

import sys

DPI = 203
SIZES = [  # (keyword, label, width dots, height dots)
    ("w288h432", "4 x 6 in", 812, 1218),
    ("w283h289", "100 x 100 mm", 800, 816),
    ("w288h576", "4 x 8 in", 812, 1624),
    ("w288h468", "4 x 6.5 in", 812, 1319),
    ("w288h288", "4 x 4 in", 812, 812),
    ("w288h216", "4 x 3 in", 812, 609),
    ("w288h180", "4 x 2.5 in", 812, 507),
    ("w288h144", "4 x 2 in", 812, 406),
    ("w288h72", "4 x 1 in", 812, 203),
    ("w252h72", "3.5 x 1 in", 710, 203),
    ("w216h360", "3 x 5 in", 609, 1015),
    ("w216h216", "3 x 3 in", 609, 609),
    ("w216h144", "3 x 2 in", 609, 406),
    ("w216h90", "3 x 1.25 in", 609, 253),
    ("w216h72", "3 x 1 in", 609, 203),
    ("w180h144", "2.5 x 2 in", 507, 406),
    ("w180h72", "2.5 x 1 in", 507, 203),
    ("w162h90", "2.25 x 1.25 in", 456, 253),
    ("w144h288", "2 x 4 in", 406, 812),
    ("w144h144", "2 x 2 in", 406, 406),
    ("w144h90", "2 x 1.25 in", 406, 253),
    ("w144h72", "2 x 1 in", 406, 203),
    ("w108h144", "1.5 x 2 in", 304, 406),
    ("w108h72", "1.5 x 1 in", 304, 203),
    ("w90h162", "1.25 x 2.25 in", 253, 456),
]
MAX_W, MAX_H, MIN_W, MIN_H = 864, 3045, 63, 39


def pt(dots):
    return ("%.2f" % (dots * 72.0 / DPI)).rstrip("0").rstrip(".")


def choice(out, keyword, label, options, default, order):
    out.append('*OpenUI *%s/%s: PickOne' % (keyword, label))
    out.append('*OrderDependency: %d AnySetup *%s' % (order, keyword))
    out.append('*Default%s: %s' % (keyword, default))
    for name, text in options:
        out.append('*%s %s/%s: ""' % (keyword, name, text))
    out.append('*CloseUI: *%s' % keyword)
    out.append('')


def build():
    out = [
        '*PPD-Adobe: "4.3"',
        '*FormatVersion: "4.3"',
        '*FileVersion: "1.0"',
        '*LanguageVersion: English',
        '*LanguageEncoding: ISOLatin1',
        '*PCFileName: "D450.PPD"',
        '*Manufacturer: "Labelife"',
        '*Product: "(D450)"',
        '*ModelName: "D450 Label Printer"',
        '*ShortNickName: "D450 Label Printer"',
        '*NickName: "D450 Label Printer (TSPL), d450-linux 1.0"',
        '*PSVersion: "(3010.000) 0"',
        '*LanguageLevel: "3"',
        '*ColorDevice: False',
        '*DefaultColorSpace: Gray',
        '*FileSystem: False',
        '*Throughput: "8"',
        '*LandscapeOrientation: Plus90',
        '*TTRasterizer: Type42',
        '*1284DeviceID: "MFG:;MDL:D450;CMD:TSPL;"',
        '*cupsVersion: 2.4',
        '*cupsModelNumber: 450',
        '*cupsManualCopies: False',
        '*cupsFilter: "application/vnd.cups-raster 50 rastertod450"',
        '*cupsLanguages: "en"',
        '',
        '*OpenUI *PageSize/Label Size: PickOne',
        '*OrderDependency: 10 AnySetup *PageSize',
        '*DefaultPageSize: w288h432',
    ]
    for key, label, w, h in SIZES:
        out.append('*PageSize %s/%s: "<</PageSize[%s %s]/ImagingBBox null>>setpagedevice"'
                   % (key, label, pt(w), pt(h)))
    out += ['*CloseUI: *PageSize', '',
            '*OpenUI *PageRegion/Label Size: PickOne',
            '*OrderDependency: 10 AnySetup *PageRegion',
            '*DefaultPageRegion: w288h432']
    for key, label, w, h in SIZES:
        out.append('*PageRegion %s/%s: "<</PageSize[%s %s]/ImagingBBox null>>setpagedevice"'
                   % (key, label, pt(w), pt(h)))
    out += ['*CloseUI: *PageRegion', '', '*DefaultImageableArea: w288h432']
    for key, label, w, h in SIZES:
        out.append('*ImageableArea %s/%s: "0 0 %s %s"' % (key, label, pt(w), pt(h)))
    out += ['', '*DefaultPaperDimension: w288h432']
    for key, label, w, h in SIZES:
        out.append('*PaperDimension %s/%s: "%s %s"' % (key, label, pt(w), pt(h)))
    out += [
        '',
        '*MaxMediaWidth: "%s"' % pt(MAX_W),
        '*MaxMediaHeight: "%s"' % pt(MAX_H),
        '*HWMargins: 0 0 0 0',
        '*CustomPageSize True: "pop pop pop <</PageSize[5 -2 roll]/ImagingBBox null>>setpagedevice"',
        '*ParamCustomPageSize Width: 1 points %s %s' % (pt(MIN_W), pt(MAX_W)),
        '*ParamCustomPageSize Height: 2 points %s %s' % (pt(MIN_H), pt(MAX_H)),
        '*ParamCustomPageSize WidthOffset: 3 points 0 0',
        '*ParamCustomPageSize HeightOffset: 4 points 0 0',
        '*ParamCustomPageSize Orientation: 5 int 0 0',
        '',
        '*OpenUI *Resolution/Resolution: PickOne',
        '*OrderDependency: 20 AnySetup *Resolution',
        '*DefaultResolution: 203dpi',
        '*Resolution 203dpi/203 DPI: "<</HWResolution[203 203]>>setpagedevice"',
        '*CloseUI: *Resolution',
        '',
        '*OpenUI *ColorModel/Color Mode: PickOne',
        '*OrderDependency: 20 AnySetup *ColorModel',
        '*DefaultColorModel: Gray',
        '*ColorModel Gray/Grayscale: "<</cupsColorSpace 0/cupsColorOrder 0'
        '/cupsBitsPerColor 8/cupsCompression 0>>setpagedevice"',
        '*CloseUI: *ColorModel',
        '',
        '*OpenGroup: Label/Label Printer Settings',
        '',
    ]
    choice(out, 'd450Density', 'Darkness',
           [('Default', 'Printer default')] + [(str(i), str(i)) for i in range(16)], '8', 30)
    choice(out, 'd450Speed', 'Print Speed',
           [('Default', 'Printer default')] + [(str(i), '%d in/s' % i) for i in range(2, 8)],
           '5', 31)
    choice(out, 'd450Media', 'Media Type',
           [('Gap', 'Labels with gaps'), ('Continuous', 'Continuous paper'),
            ('BlackMark', 'Labels with black marks')], 'Gap', 32)
    choice(out, 'd450Gap', 'Gap / Mark Height',
           [(str(i), '%d mm' % i) for i in range(0, 11)], '3', 33)
    choice(out, 'd450Post', 'After Printing',
           [('None', 'Stay in place'), ('TearOff', 'Advance to tear bar'),
            ('PeelOff', 'Peel off')], 'None', 34)
    choice(out, 'd450Dither', 'Halftoning',
           [('Threshold', 'None (black and white)'), ('Ordered', 'Ordered dither'),
            ('ErrorDiffusion', 'Error diffusion')], 'Threshold', 35)
    choice(out, 'd450Rotate', 'Rotate 180 Degrees',
           [('False', 'Off'), ('True', 'On')], 'False', 36)
    choice(out, 'd450Compress', 'Data Compression',
           [('Auto', 'Automatic (Bluetooth only)'), ('Off', 'Off'), ('On', 'On')], 'Auto', 37)
    out += ['*CloseGroup: Label', '',
            '*DefaultFont: Courier',
            '*Font Courier: Standard "(002.004S)" Standard ROM',
            '*Font Helvetica: Standard "(001.006S)" Standard ROM',
            '*Font Times-Roman: Standard "(001.007S)" Standard ROM',
            '', '*% End of D450.ppd']
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "ppd/D450.ppd"
    with open(target, "w", encoding="latin-1", newline="\n") as handle:
        handle.write(build())
