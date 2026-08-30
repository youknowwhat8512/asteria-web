// Crop a rect expressed in top-left pixel coordinates, the way every image
// editor and every screenshot reports them. CoreImage's origin is bottom-left,
// so the y flip lives here instead of in each caller's head.
//
// usage: swift scripts/crop_image_rect.swift <input> <output.jpg> <left> <top> <right> <bottom>
//   Rect is top-left origin, right/bottom exclusive, in source pixels.
//   Output is sRGB JPEG at exactly (right-left)x(bottom-top) — never upscaled.
//   A rect that is empty or reaches outside the source is rejected, not clamped,
//   so a typo fails loudly instead of silently shipping the wrong frame.
//
// Metadata is not carried over by CoreImage, but run
// scripts/strip_jpeg_metadata.py on the output to guarantee no APPn survives.

import CoreImage
import Foundation

let args = CommandLine.arguments
guard args.count == 7,
      let left = Int(args[3]), let top = Int(args[4]),
      let right = Int(args[5]), let bottom = Int(args[6]) else {
    fputs("usage: crop_image_rect.swift <input> <output.jpg> <left> <top> <right> <bottom>\n", stderr)
    exit(2)
}
let input = URL(fileURLWithPath: args[1])
let output = URL(fileURLWithPath: args[2])

guard let source = CIImage(contentsOf: input) else {
    fputs("cannot read \(input.path)\n", stderr)
    exit(1)
}
let extent = source.extent
let width = Int(extent.width), height = Int(extent.height)

guard left >= 0, top >= 0, right <= width, bottom <= height, left < right, top < bottom else {
    fputs("rect [\(left),\(top),\(right),\(bottom)] is out of range for \(width)x\(height)\n", stderr)
    exit(1)
}

// Top-left rect -> CoreImage's bottom-left origin, then move to (0,0) so the
// written file starts at the crop rather than carrying the source offset.
let rect = CGRect(x: extent.minX + CGFloat(left),
                  y: extent.minY + CGFloat(height - bottom),
                  width: CGFloat(right - left),
                  height: CGFloat(bottom - top))
let cropped = source.cropped(to: rect)
    .transformed(by: CGAffineTransform(translationX: -rect.minX, y: -rect.minY))

guard let srgb = CGColorSpace(name: CGColorSpace.sRGB) else {
    fputs("cannot resolve sRGB color space\n", stderr)
    exit(1)
}
do {
    try CIContext().writeJPEGRepresentation(
        of: cropped,
        to: output,
        colorSpace: srgb,
        options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.92]
    )
} catch {
    fputs("cannot write \(output.path): \(error)\n", stderr)
    exit(1)
}
print("wrote \(output.path) (\(Int(cropped.extent.width))x\(Int(cropped.extent.height)) from [\(left),\(top),\(right),\(bottom)] of \(width)x\(height))")
