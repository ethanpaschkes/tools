// Makes a QR code picture from the text on standard input.  Usage: swift qr.swift out.png < text
import AppKit
import CoreImage

guard CommandLine.arguments.count == 2,
      let text = readLine(), !text.isEmpty,
      let filter = CIFilter(name: "CIQRCodeGenerator") else {
    FileHandle.standardError.write("usage: swift qr.swift out.png < text\n".data(using: .utf8)!)
    exit(2)
}
filter.setValue(text.data(using: .utf8), forKey: "inputMessage")
filter.setValue("M", forKey: "inputCorrectionLevel")
let code = filter.outputImage!.transformed(by: CGAffineTransform(scaleX: 10, y: 10))

let margin: CGFloat = 40
let side = code.extent.width + margin * 2
let image = NSImage(size: NSSize(width: side, height: side))
image.lockFocus()
NSColor.white.setFill()
NSRect(x: 0, y: 0, width: side, height: side).fill()
NSGraphicsContext.current!.imageInterpolation = .none
NSCIImageRep(ciImage: code).draw(in: NSRect(x: margin, y: margin, width: code.extent.width, height: code.extent.height))
image.unlockFocus()

let png = NSBitmapImageRep(data: image.tiffRepresentation!)!.representation(using: .png, properties: [:])!
try png.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
