// Lays out several rectangles of ONE source photograph as an editorial multi-panel
// image. Every panel is a straight crop of the same frame, so the light, sea and
// horizon match by construction — it reads as one sail, not a collage of days.
// Grading is applied once to the whole frame before cropping, which keeps the panels
// identical in tone. Nothing is generated, inpainted or anonymised; the only pixel
// work is a restrained highlight/skin pass that must stay far below the strength
// where a face stops being recognisable.
//
// usage: swift scripts/build_editorial_triptych.swift <input> <output.jpg> <WxH> <divider> <panel> [panel ...]
//   WxH     output pixel size, e.g. 2160x1215
//   divider width in px of the dark-navy rule between panels (0 for none)
//   panel   x,y,w,h[,weight[,fit]]
//     x,y,w,h  source rect in pixels, y measured from the TOP of the image as displayed
//     weight   relative column width (default 1); columns share the width left over
//              after the dividers, so 480,834,834 reproduces those pixel widths
//     fit      scale the crop to sit INSIDE its column instead of filling it, letting
//              the navy show through as a matte. Use when a subject cannot be isolated
//              at the right face size by cropping alone — a matte keeps the face small
//              without inventing pixels. Default is fill (centre-crop the overflow).

import CoreImage
import Foundation

// Divider and matte are the site's dark navy (#0e2a44) — the only graphic element.
let dividerColor = CIColor(red: 0x0e / 255.0, green: 0x2a / 255.0, blue: 0x44 / 255.0)

func fail(_ message: String) -> Never {
    fputs("\(message)\n", stderr)
    exit(2)
}

struct Panel {
    let rect: CGRect
    let weight: Double
    let fit: Bool
}

let args = CommandLine.arguments
guard args.count >= 6 else {
    fail("usage: build_editorial_triptych.swift <input> <output.jpg> <WxH> <divider> <x,y,w,h[,weight[,fit]]> [...]")
}
let input = URL(fileURLWithPath: args[1])
let output = URL(fileURLWithPath: args[2])

let size = args[3].split(separator: "x").compactMap { Double($0) }
guard size.count == 2, size[0] > 0, size[1] > 0 else { fail("bad output size \(args[3]), expected WxH") }
let (outW, outH) = (size[0], size[1])

guard let divider = Double(args[4]), divider >= 0 else { fail("bad divider \(args[4])") }

let panels: [Panel] = args[5...].map { spec in
    let parts = spec.split(separator: ",").map(String.init)
    guard (4...6).contains(parts.count) else {
        fail("bad panel \(spec), expected x,y,w,h[,weight[,fit]]")
    }
    let n = parts.prefix(4).compactMap { Double($0) }
    guard n.count == 4, n[2] > 0, n[3] > 0 else { fail("bad panel rect \(spec), expected x,y,w,h") }
    var weight = 1.0
    if parts.count >= 5 {
        guard let parsed = Double(parts[4]), parsed > 0 else { fail("bad panel weight in \(spec)") }
        weight = parsed
    }
    if parts.count == 6, parts[5] != "fit" {
        fail("unknown panel mode '\(parts[5])' in \(spec), expected 'fit'")
    }
    return Panel(rect: CGRect(x: n[0], y: n[1], width: n[2], height: n[3]),
                 weight: weight,
                 fit: parts.count == 6)
}
let gaps = divider * Double(panels.count - 1)
guard outW - gaps > Double(panels.count) else { fail("divider \(divider) leaves no room for \(panels.count) panels") }

guard let source = CIImage(contentsOf: input, options: [.applyOrientationProperty: true]) else {
    fail("cannot read \(input.path)")
}
let frame = source.extent

// One grade for the whole frame: highlights down off the blown white deck and sky,
// shadows opened just enough for the navy to read as navy, vibrance for the buoyancy
// aid red. Skin softening happens per panel, after scaling, so every panel gets the
// same amount at output resolution instead of at its own crop scale.
let graded = source
    .applyingFilter("CIHighlightShadowAdjust", parameters: [
        "inputHighlightAmount": 0.72,
        "inputShadowAmount": 0.12,
        "inputRadius": 14,
    ])
    .applyingFilter("CIColorControls", parameters: [
        kCIInputSaturationKey: 1.05,
        kCIInputContrastKey: 1.02,
        kCIInputBrightnessKey: -0.02,
    ])
    .applyingFilter("CIVibrance", parameters: ["inputAmount": 0.12])

var canvas = CIImage(color: dividerColor).cropped(to: CGRect(x: 0, y: 0, width: outW, height: outH))

// Integer column widths from the weights that still sum to the full canvas: hand the
// remainder to the leftmost columns rather than leaving a navy sliver down one edge.
let usable = Int((outW - gaps).rounded())
let totalWeight = panels.reduce(0.0) { $0 + $1.weight }
var widths = panels.map { Int((Double(usable) * $0.weight / totalWeight).rounded(.down)) }
var remainder = usable - widths.reduce(0, +)
var spill = 0
while remainder > 0 {
    widths[spill % widths.count] += 1
    remainder -= 1
    spill += 1
}
var cursor = 0.0

for (index, panel) in panels.enumerated() {
    let panelW = Double(widths[index])
    guard panelW >= 1 else { fail("panel \(index) weight leaves it zero pixels wide") }

    // y arrives measured from the top; CoreImage counts from the bottom.
    let rect = panel.rect
    let source = CGRect(x: frame.minX + rect.minX,
                        y: frame.maxY - rect.minY - rect.height,
                        width: rect.width, height: rect.height)
    guard frame.contains(source) else {
        fail("panel \(index) rect \(rect) falls outside the \(Int(frame.width))x\(Int(frame.height)) source")
    }

    let scale = panel.fit
        ? min(panelW / source.width, outH / source.height)
        : max(panelW / source.width, outH / source.height)
    var image = graded
        .cropped(to: source)
        .transformed(by: CGAffineTransform(translationX: -source.minX, y: -source.minY))
        .applyingFilter("CILanczosScaleTransform", parameters: [
            kCIInputScaleKey: scale,
            kCIInputAspectRatioKey: 1.0,
        ])
    let scaled = image.extent
    // Restrained detail smoothing at output scale: softens skin and Lanczos ringing
    // while inputSharpness keeps the edges that carry identity. Re-crop to the scaled
    // extent so no soft fringe bleeds onto the navy.
    image = image
        .applyingFilter("CINoiseReduction", parameters: [
            "inputNoiseLevel": 0.021,
            "inputSharpness": 0.80,
        ])
        .cropped(to: scaled)

    if !panel.fit {
        // Centre-crop whichever axis overflows the column.
        image = image.cropped(to: CGRect(x: scaled.minX + (scaled.width - panelW) / 2,
                                         y: scaled.minY + (scaled.height - outH) / 2,
                                         width: panelW, height: outH))
    }
    // Centre inside the column. Under `fit` the shortfall stays navy — that is the matte.
    let placed = image.extent
    image = image.transformed(by: CGAffineTransform(
        translationX: cursor + (panelW - placed.width) / 2 - placed.minX,
        y: (outH - placed.height) / 2 - placed.minY))
    canvas = image.composited(over: canvas)

    let matte = panel.fit ? " matte \(Int(((panelW - placed.width) / 2).rounded()))px/side" : ""
    print("  panel \(index): column \(Int(panelW))px, image \(Int(placed.width))x\(Int(placed.height)) @\(String(format: "%.4f", scale))x\(matte)")
    cursor += panelW + divider
}

canvas = canvas.cropped(to: CGRect(x: 0, y: 0, width: outW, height: outH))

guard let srgb = CGColorSpace(name: CGColorSpace.sRGB) else { fail("cannot create sRGB color space") }
do {
    try CIContext().writeJPEGRepresentation(
        of: canvas,
        to: output,
        colorSpace: srgb,
        options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.92]
    )
} catch {
    fail("cannot write \(output.path): \(error)")
}
print("wrote \(output.path) (\(Int(outW))x\(Int(outH)), \(panels.count) panels, divider \(Int(divider))px)")
