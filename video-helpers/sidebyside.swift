// ─────────────────────────────────────────────────────────────────────────────
// SIDE-BY-SIDE — two screen recordings into one split-screen comparison.
//
// Lines the two clips up at t = 0 and stacks them side by side with labels. It does NOT sync them: start both
// recordings at the same moment. `--trim-left` / `--trim-right` drop dead frames at the start (e.g. a splash
// screen); trim both sides by the same rule if the comparison is meant to be fair.
//
//   swift sidebyside.swift <left.mov> <right.mov> <out.mp4> \
//       [--left-label "App A"] [--right-label "App B"] \
//       [--trim-left 12.5] [--trim-right 3.0] [--height 1440]
//
// Needs no ffmpeg: it uses AVFoundation, which every Mac has.
// ─────────────────────────────────────────────────────────────────────────────
import AVFoundation
import AppKit

func die(_ m: String) -> Never { FileHandle.standardError.write(("error: " + m + "\n").data(using: .utf8)!); exit(1) }

// ── Arguments ────────────────────────────────────────────────────────────────
var positional: [String] = []
var leftLabel = "", rightLabel = ""
var trimLeft = 0.0, trimRight = 0.0
var outHeight = 1440.0
// Pixels to shave off every edge of a source before it is laid out. The iPhone
// Mirroring window carries a few pixels of desktop around the phone; a native
// iOS screen recording needs none. Applied per side because the two capture
// routes are never the same.
var insetLeft = 0.0, insetRight = 0.0
var i = 1
let argv = CommandLine.arguments
while i < argv.count {
    switch argv[i] {
    case "--left-label":  i += 1; leftLabel = i < argv.count ? argv[i] : ""
    case "--right-label": i += 1; rightLabel = i < argv.count ? argv[i] : ""
    case "--trim-left":   i += 1; trimLeft = Double(i < argv.count ? argv[i] : "0") ?? 0
    case "--trim-right":  i += 1; trimRight = Double(i < argv.count ? argv[i] : "0") ?? 0
    case "--height":      i += 1; outHeight = Double(i < argv.count ? argv[i] : "1440") ?? 1440
    case "--inset-left":  i += 1; insetLeft = Double(i < argv.count ? argv[i] : "0") ?? 0
    case "--inset-right": i += 1; insetRight = Double(i < argv.count ? argv[i] : "0") ?? 0
    default: positional.append(argv[i])
    }
    i += 1
}
guard positional.count >= 3 else {
    die("usage: sidebyside <left.mov> <right.mov> <out.mp4> [--left-label X] [--right-label Y] [--trim-left S] [--trim-right S] [--inset-left PX] [--inset-right PX] [--height N]")
}
let outURL = URL(fileURLWithPath: positional[2])

// ── Sources ──────────────────────────────────────────────────────────────────
struct Side {
    let asset: AVURLAsset
    let track: AVAssetTrack
    /// The visible size AFTER the inset — what actually gets laid out.
    let size: CGSize
    let inset: CGFloat
    let trim: Double
}

func load(_ path: String, trim: Double, inset: Double) -> Side {
    let asset = AVURLAsset(url: URL(fileURLWithPath: path))
    guard let t = asset.tracks(withMediaType: .video).first else { die("no video track in \(path)") }
    // ⚠️ `naturalSize` alone is a lie on anything rotated — apply the transform,
    // then take the magnitude, or a portrait clip lays out as landscape.
    let s = t.naturalSize.applying(t.preferredTransform)
    let k = CGFloat(inset)
    let w = abs(s.width) - k * 2, h = abs(s.height) - k * 2
    guard w > 1, h > 1 else { die("inset \(inset) leaves nothing of \(path)") }
    return Side(asset: asset, track: t, size: CGSize(width: w, height: h), inset: k, trim: trim)
}

let L = load(positional[0], trim: trimLeft, inset: insetLeft)
let R = load(positional[1], trim: trimRight, inset: insetRight)

// Both clips run for as long as the SHORTER one has left after its trim. A
// comparison that outlives one of its halves stops being a comparison.
let usable = min(L.asset.duration.seconds - L.trim, R.asset.duration.seconds - R.trim)
guard usable > 0.1 else { die("nothing left after trimming (left \(L.asset.duration.seconds)s, right \(R.asset.duration.seconds)s)") }
let dur = CMTime(seconds: usable, preferredTimescale: 600)

// ── Layout ───────────────────────────────────────────────────────────────────
// Each clip is scaled to a common height and set side by side. The labels sit
// in a band UNDER the phones rather than over them — an overlay costs exactly
// the pixels the thing being compared needs.
let labelBand: CGFloat = (leftLabel.isEmpty && rightLabel.isEmpty) ? 0 : 96
let gutter: CGFloat = 48
let panelH = CGFloat(outHeight) - labelBand
let scaleL = panelH / L.size.height
let scaleR = panelH / R.size.height
let wL = L.size.width * scaleL
let wR = R.size.width * scaleR
// Even width — H.264 refuses odd dimensions and the failure is opaque.
let renderW = (wL + wR + gutter * 3).rounded(.down)
let renderSize = CGSize(width: renderW.truncatingRemainder(dividingBy: 2) == 0 ? renderW : renderW - 1,
                        height: CGFloat(outHeight))

let comp = AVMutableComposition()
guard let tL = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid),
      let tR = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid) else {
    die("could not create composition tracks")
}
do {
    try tL.insertTimeRange(CMTimeRange(start: CMTime(seconds: L.trim, preferredTimescale: 600), duration: dur),
                           of: L.track, at: .zero)
    try tR.insertTimeRange(CMTimeRange(start: CMTime(seconds: R.trim, preferredTimescale: 600), duration: dur),
                           of: R.track, at: .zero)
} catch { die("insert failed: \(error)") }

func instruction(_ track: AVCompositionTrack, source: Side, scale: CGFloat, x: CGFloat) -> AVMutableVideoCompositionLayerInstruction {
    let li = AVMutableVideoCompositionLayerInstruction(assetTrack: track)
    // ⚠️ The source's own preferredTransform FIRST, then our placement. Doing it
    // the other way round rotates the clip out of the frame.
    // Source transform, then the inset shift (in source pixels, BEFORE scaling),
    // then scale, then placement. Order matters: inset after scaling would trim
    // a different number of real pixels on each side.
    let t = source.track.preferredTransform
        .concatenating(CGAffineTransform(translationX: -source.inset, y: -source.inset))
        .concatenating(CGAffineTransform(scaleX: scale, y: scale))
        .concatenating(CGAffineTransform(translationX: x, y: 0))
    li.setTransform(t, at: .zero)
    return li
}

let inst = AVMutableVideoCompositionInstruction()
inst.timeRange = CMTimeRange(start: .zero, duration: dur)
inst.layerInstructions = [
    instruction(tL, source: L, scale: scaleL, x: gutter),
    instruction(tR, source: R, scale: scaleR, x: gutter * 2 + wL),
]

let vc = AVMutableVideoComposition()
vc.renderSize = renderSize
vc.frameDuration = CMTime(value: 1, timescale: 60)
vc.instructions = [inst]

// ── Labels ───────────────────────────────────────────────────────────────────
if labelBand > 0 {
    let parent = CALayer()
    parent.frame = CGRect(origin: .zero, size: renderSize)
    parent.backgroundColor = NSColor(calibratedRed: 0.043, green: 0.063, blue: 0.090, alpha: 1).cgColor
    let video = CALayer()
    video.frame = parent.frame
    parent.addSublayer(video)

    // 🚨 THE TEXT IS DRAWN TO A BITMAP, NOT LEFT TO `CATextLayer`. A CATextLayer
    // inside `AVVideoCompositionCoreAnimationTool` rendered NOTHING here — no
    // error, no warning, just a correctly-sized empty band in the exported file.
    // That is the worst kind of failure to debug in video, so the glyphs are
    // rasterised up front where a wrong result is visible immediately.
    func text(_ s: String, centreX: CGFloat, width: CGFloat) -> CALayer {
        let h = labelBand * 0.6
        let size = CGSize(width: width, height: h)
        let img = NSImage(size: size)
        img.lockFocus()
        let para = NSMutableParagraphStyle()
        para.alignment = .center
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: labelBand * 0.40, weight: .semibold),
            .foregroundColor: NSColor.white,
            .paragraphStyle: para,
        ]
        let a = NSAttributedString(string: s, attributes: attrs)
        let th = a.size().height
        a.draw(in: CGRect(x: 0, y: (h - th) / 2, width: width, height: th))
        img.unlockFocus()
        let l = CALayer()
        l.frame = CGRect(x: centreX - width / 2, y: labelBand * 0.20, width: width, height: h)
        l.contents = img.cgImage(forProposedRect: nil, context: nil, hints: nil)
        l.contentsGravity = .center
        return l
    }
    if !leftLabel.isEmpty { parent.addSublayer(text(leftLabel, centreX: gutter + wL / 2, width: wL)) }
    if !rightLabel.isEmpty { parent.addSublayer(text(rightLabel, centreX: gutter * 2 + wL + wR / 2, width: wR)) }

    vc.animationTool = AVVideoCompositionCoreAnimationTool(postProcessingAsVideoLayer: video, in: parent)
}

// ── Export ───────────────────────────────────────────────────────────────────
try? FileManager.default.removeItem(at: outURL)
guard let ex = AVAssetExportSession(asset: comp, presetName: AVAssetExportPresetHighestQuality) else {
    die("could not create export session")
}
ex.outputURL = outURL
ex.outputFileType = .mp4
ex.videoComposition = vc

let sem = DispatchSemaphore(value: 0)
ex.exportAsynchronously { sem.signal() }
sem.wait()

if ex.status == .completed {
    print("ok \(Int(renderSize.width))x\(Int(renderSize.height)) \(String(format: "%.1f", usable))s -> \(outURL.path)")
} else {
    die("export \(ex.status.rawValue): \(ex.error?.localizedDescription ?? "unknown")")
}
