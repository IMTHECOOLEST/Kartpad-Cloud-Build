// Render macos/AppIcon.svg into the PNG sizes an .icns needs: swift icon.swift SVG ICONSET
import AppKit
let args = CommandLine.arguments
guard args.count == 3, let image = NSImage(contentsOfFile: args[1]) else { exit(2) }
let folder = URL(fileURLWithPath: args[2])
try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
for base in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let size = base * scale
        let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8,
                                   samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                   bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        image.draw(in: NSRect(x: 0, y: 0, width: size, height: size))
        NSGraphicsContext.restoreGraphicsState()
        let name = "icon_\(base)x\(base)" + (scale == 2 ? "@2x" : "") + ".png"
        try! rep.representation(using: .png, properties: [:])!.write(to: folder.appendingPathComponent(name))
    }
}

