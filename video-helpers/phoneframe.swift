// ─────────────────────────────────────────────────────────────────────────────
// PHONE FRAME — one screen recording, drawn inside a phone bezel.
//
// The single-clip companion to `sidebyside.swift`. No trimming, no pairing: the
// whole take, framed, so a raw screen recording can be dropped into a post or a
// deck without looking like a raw screen recording.
//
//   swift phoneframe.swift <in.mov> <out.mp4> \
//       [--label "My App"] [--height 1440] [--inset 0]
//
// `--height` is the SCREEN height; the bezel and margin are added around it.
// `--inset` shaves pixels off the source first — needed for a capture of the
// iPhone Mirroring window (which carries desktop around the phone), not for a
// native iOS screen recording.
//
// ⚠️ THE BEZEL IS A BITMAP WITH THE SCREEN PUNCHED OUT, laid OVER the video —
// not a rounded video layer. `CATextLayer` already proved that not everything
// CoreAnimation offers survives `AVVideoCompositionCoreAnimationTool`; a
// pre-rendered overlay cannot be silently ignored the same way, and punching the
// hole gives the screen its rounded corners for free.
//
// Needs no ffmpeg: it uses AVFoundation, which every Mac has.
// ─────────────────────────────────────────────────────────────────────────────
import AVFoundation
import AppKit

func die(_ m: String) -> Never { FileHandle.standardError.write(("error: " + m + "\n").data(using: .utf8)!); exit(1) }

var positional: [String] = []
var label = ""
var screenH = 1440.0
var inset = 0.0
var i = 1
let argv = CommandLine.arguments
while i < argv.count {
    switch argv[i] {
    case "--label":  i += 1; label = i < argv.count ? argv[i] : ""
    case "--height": i += 1; screenH = Double(i < argv.count ? argv[i] : "1440") ?? 1440
    case "--inset":  i += 1; inset = Double(i < argv.count ? argv[i] : "0") ?? 0
    default: positional.append(argv[i])
    }
    i += 1
}
guard positional.count >= 2 else { die("usage: phoneframe <in.mov> <out.mp4> [--label X] [--height N] [--inset PX]") }
let outURL = URL(fileURLWithPath: positional[1])

let asset = AVURLAsset(url: URL(fileURLWithPath: positional[0]))
guard let track = asset.tracks(withMediaType: .video).first else { die("no video track") }
// ⚠️ Apply the preferred transform before measuring, or a portrait clip is
// measured as landscape and the bezel comes out the wrong way round.
let raw = track.naturalSize.applying(track.preferredTransform)
let srcW = abs(raw.width) - inset * 2, srcH = abs(raw.height) - inset * 2
guard srcW > 1, srcH > 1 else { die("inset \(inset) leaves nothing") }

let scale = CGFloat(screenH) / srcH
let scrW = (srcW * scale).rounded()
let scrH = CGFloat(screenH)

// Proportions taken from the phone these were recorded on, not invented: the
// bezel is a little under 2% of the screen's width, the outer corner is
// noticeably rounder than the screen's.
let bezel = (scrW * 0.019).rounded()
let margin = (scrW * 0.06).rounded()
let labelBand: CGFloat = label.isEmpty ? 0 : 96
let screenCorner = scrW * 0.085
let bodyCorner = screenCorner + bezel

var renderW = scrW + (bezel + margin) * 2
var renderH = scrH + (bezel + margin) * 2 + labelBand
// H.264 refuses odd dimensions, and the failure it gives is not a helpful one.
if renderW.truncatingRemainder(dividingBy: 2) != 0 { renderW += 1 }
if renderH.truncatingRemainder(dividingBy: 2) != 0 { renderH += 1 }
let renderSize = CGSize(width: renderW, height: renderH)

// CoreAnimation's origin is bottom-left. The label band sits under the phone.
let screenRect = CGRect(x: bezel + margin, y: labelBand + bezel + margin, width: scrW, height: scrH)
let bodyRect = screenRect.insetBy(dx: -bezel, dy: -bezel)

let comp = AVMutableComposition()
guard let ct = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid) else {
    die("could not create a composition track")
}
do {
    try ct.insertTimeRange(CMTimeRange(start: .zero, duration: asset.duration), of: track, at: .zero)
} catch { die("insert failed: \(error)") }

// 🚨 TWO COORDINATE SYSTEMS, AND THEY DISAGREE ABOUT WHICH WAY IS UP. A layer
// instruction's transform lives in the video composition's space, whose origin
// is the TOP-left; `screenRect` above is a CoreAnimation frame, whose origin is
// the BOTTOM-left. Translating by `screenRect.minY` would push the picture the
// wrong way. The distance from the top is what this needs, and it is the margin
// plus the bezel — the label band hangs off the bottom and does not enter it.
let topOffset = margin + bezel
let li = AVMutableVideoCompositionLayerInstruction(assetTrack: ct)
li.setTransform(track.preferredTransform
    .concatenating(CGAffineTransform(translationX: -inset, y: -inset))
    .concatenating(CGAffineTransform(scaleX: scale, y: scale))
    .concatenating(CGAffineTransform(translationX: screenRect.minX, y: topOffset)), at: .zero)

let inst = AVMutableVideoCompositionInstruction()
inst.timeRange = CMTimeRange(start: .zero, duration: asset.duration)
inst.layerInstructions = [li]

let vc = AVMutableVideoComposition()
vc.renderSize = renderSize
vc.frameDuration = CMTime(value: 1, timescale: 60)
vc.instructions = [inst]

// ── The bezel overlay ────────────────────────────────────────────────────────
// One canvas: the phone body filled, then the screen area cleared straight back
// out of it. Everything outside the body stays transparent, so the page
// background shows through and the screen shows the video underneath.
func bezelOverlay() -> CGImage? {
    let img = NSImage(size: renderSize)
    img.lockFocus()
    guard let ctx = NSGraphicsContext.current?.cgContext else { img.unlockFocus(); return nil }
    let body = CGPath(roundedRect: bodyRect, cornerWidth: bodyCorner, cornerHeight: bodyCorner, transform: nil)
    ctx.addPath(body)
    ctx.setFillColor(NSColor(calibratedWhite: 0.07, alpha: 1).cgColor)
    ctx.fillPath()
    // A hairline highlight along the bezel reads as glass rather than a sticker.
    ctx.addPath(body)
    ctx.setStrokeColor(NSColor(calibratedWhite: 0.32, alpha: 1).cgColor)
    ctx.setLineWidth(max(1, bezel * 0.16))
    ctx.strokePath()
    // Punch the screen out.
    ctx.setBlendMode(.clear)
    ctx.addPath(CGPath(roundedRect: screenRect, cornerWidth: screenCorner, cornerHeight: screenCorner, transform: nil))
    ctx.fillPath()
    img.unlockFocus()
    return img.cgImage(forProposedRect: nil, context: nil, hints: nil)
}

let parent = CALayer()
parent.frame = CGRect(origin: .zero, size: renderSize)
parent.backgroundColor = NSColor(calibratedRed: 0.043, green: 0.063, blue: 0.090, alpha: 1).cgColor
let videoLayer = CALayer()
videoLayer.frame = parent.frame
parent.addSublayer(videoLayer)

if let cg = bezelOverlay() {
    let l = CALayer()
    l.frame = parent.frame
    l.contents = cg
    parent.addSublayer(l)
}

if !label.isEmpty {
    // Rasterised, for the reason in the header — CATextLayer does not survive.
    let h = labelBand * 0.6
    let size = CGSize(width: renderSize.width, height: h)
    let im = NSImage(size: size)
    im.lockFocus()
    let para = NSMutableParagraphStyle(); para.alignment = .center
    NSAttributedString(string: label, attributes: [
        .font: NSFont.systemFont(ofSize: labelBand * 0.40, weight: .semibold),
        .foregroundColor: NSColor.white,
        .paragraphStyle: para,
    ]).draw(in: CGRect(x: 0, y: 0, width: size.width, height: h))
    im.unlockFocus()
    let l = CALayer()
    l.frame = CGRect(x: 0, y: labelBand * 0.20, width: size.width, height: h)
    l.contents = im.cgImage(forProposedRect: nil, context: nil, hints: nil)
    parent.addSublayer(l)
}

vc.animationTool = AVVideoCompositionCoreAnimationTool(postProcessingAsVideoLayer: videoLayer, in: parent)

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
    print("ok \(Int(renderSize.width))x\(Int(renderSize.height)) \(String(format: "%.1f", asset.duration.seconds))s -> \(outURL.path)")
} else {
    die("export \(ex.status.rawValue): \(ex.error?.localizedDescription ?? "unknown")")
}
