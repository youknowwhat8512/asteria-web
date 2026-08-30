#!/usr/bin/env swift

import AppKit
import Foundation

func fail(_ message: String) -> Never {
    fputs("\(message)\n", stderr)
    exit(2)
}

let args = CommandLine.arguments
guard args.count == 3 else {
    fail("usage: build_tablet_episode_og.swift <360x480 source> <output.jpg>")
}

let inputURL = URL(fileURLWithPath: args[1])
let outputURL = URL(fileURLWithPath: args[2])

guard let source = NSImage(contentsOf: inputURL),
      let sourceRep = source.representations.first else {
    fail("cannot decode \(inputURL.path)")
}
guard sourceRep.pixelsWide == 360, sourceRep.pixelsHigh == 480 else {
    fail("source must be exactly 360x480; got \(sourceRep.pixelsWide)x\(sourceRep.pixelsHigh)")
}

guard let canvas = NSBitmapImageRep(
    bitmapDataPlanes: nil,
    pixelsWide: 1200,
    pixelsHigh: 630,
    bitsPerSample: 8,
    samplesPerPixel: 4,
    hasAlpha: true,
    isPlanar: false,
    colorSpaceName: .deviceRGB,
    bytesPerRow: 0,
    bitsPerPixel: 0
), let context = NSGraphicsContext(bitmapImageRep: canvas) else {
    fail("cannot create 1200x630 canvas")
}

NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = context

let navy = NSColor(calibratedRed: 6 / 255, green: 19 / 255, blue: 29 / 255, alpha: 1)
let cyan = NSColor(calibratedRed: 122 / 255, green: 222 / 255, blue: 240 / 255, alpha: 1)
let red = NSColor(calibratedRed: 239 / 255, green: 64 / 255, blue: 47 / 255, alpha: 1)
let white = NSColor.white
let muted = NSColor(calibratedWhite: 1, alpha: 0.68)

navy.setFill()
NSRect(x: 0, y: 0, width: 1200, height: 630).fill()

// The source is drawn at exactly 360x480: no enlargement and no crop.
let photoRect = NSRect(x: 780, y: 75, width: 360, height: 480)
let photoPath = NSBezierPath(roundedRect: photoRect, xRadius: 18, yRadius: 18)
NSGraphicsContext.saveGraphicsState()
photoPath.addClip()
source.draw(
    in: photoRect,
    from: NSRect(x: 0, y: 0, width: source.size.width, height: source.size.height),
    operation: .sourceOver,
    fraction: 1,
    respectFlipped: false,
    hints: [.interpolation: NSImageInterpolation.high]
)
NSGraphicsContext.restoreGraphicsState()

cyan.setStroke()
photoPath.lineWidth = 2
photoPath.stroke()

red.setFill()
NSRect(x: 70, y: 548, width: 54, height: 5).fill()
cyan.setFill()
NSRect(x: 720, y: 75, width: 3, height: 480).fill()

func draw(_ text: String, x: CGFloat, y: CGFloat, font: NSFont, color: NSColor, tracking: CGFloat = 0) {
    let style = NSMutableParagraphStyle()
    style.lineBreakMode = .byWordWrapping
    let attributes: [NSAttributedString.Key: Any] = [
        .font: font,
        .foregroundColor: color,
        .kern: tracking,
        .paragraphStyle: style,
    ]
    (text as NSString).draw(at: NSPoint(x: x, y: y), withAttributes: attributes)
}

draw("ASTERIA EPISODE", x: 70, y: 505, font: .systemFont(ofSize: 18, weight: .heavy), color: cyan, tracking: 3.2)
draw("테선장님과", x: 66, y: 385, font: .systemFont(ofSize: 64, weight: .heavy), color: white, tracking: -2.4)
draw("함께한 하루", x: 66, y: 304, font: .systemFont(ofSize: 64, weight: .heavy), color: white, tracking: -2.4)
draw("A DAY WITH CAPTAIN TABLET", x: 70, y: 250, font: .systemFont(ofSize: 18, weight: .bold), color: muted, tracking: 1.4)
draw("WANGSAN MARINA · 17:30–19:30", x: 70, y: 104, font: .systemFont(ofSize: 17, weight: .bold), color: white, tracking: 1.0)
draw("29 AUG 2026", x: 70, y: 76, font: .systemFont(ofSize: 14, weight: .heavy), color: red, tracking: 2.0)

NSGraphicsContext.restoreGraphicsState()

guard let jpeg = canvas.representation(using: .jpeg, properties: [.compressionFactor: 0.92]) else {
    fail("cannot encode JPEG")
}
try jpeg.write(to: outputURL, options: .atomic)
print("wrote \(outputURL.path) (1200x630; source placed at 360x480)")
