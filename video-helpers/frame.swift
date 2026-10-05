// Extract one frame from a video as PNG. Needs no ffmpeg: it uses AVFoundation, which every Mac has.
//   swift frame.swift <in.mov> <seconds> <out.png>
import AVFoundation
import AppKit

let a = CommandLine.arguments
guard a.count >= 4, let t = Double(a[2]) else { print("usage: frame <in> <sec> <out>"); exit(1) }
let asset = AVURLAsset(url: URL(fileURLWithPath: a[1]))
let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
gen.requestedTimeToleranceBefore = .zero
gen.requestedTimeToleranceAfter = .zero
do {
    let cg = try gen.copyCGImage(at: CMTime(seconds: t, preferredTimescale: 600), actualTime: nil)
    let rep = NSBitmapImageRep(cgImage: cg)
    guard let data = rep.representation(using: .png, properties: [:]) else { exit(1) }
    try data.write(to: URL(fileURLWithPath: a[3]))
    print("ok \(cg.width)x\(cg.height)")
} catch {
    print("FAILED: \(error)"); exit(1)
}
