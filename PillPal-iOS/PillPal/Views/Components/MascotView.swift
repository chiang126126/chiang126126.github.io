import SwiftUI

// MARK: - Mascot Mood
enum MascotMood: String {
    case perfect, happy, neutral, celebrating
    case eyeroll, deflated, eating
    case sleepy, grumpy, sad

    static func forAdherence(_ pct: Double) -> MascotMood {
        if pct >= 100 { return .perfect }
        if pct >= 75  { return .happy }
        if pct >= 50  { return .neutral }
        if pct >= 25  { return .eyeroll }
        if pct > 0    { return .deflated }
        return .sleepy
    }

    // Capsule top half (head) color
    var topColor: Color {
        switch self {
        case .deflated:    return Color(hex: "#9CC5A0")
        case .sleepy:      return Color(hex: "#A3C9AE")
        default:           return Color(hex: "#4ADE80")
        }
    }

    // Capsule bottom half (body) color
    var bottomColor: Color {
        switch self {
        case .deflated:    return Color(hex: "#D4B89C")
        case .sleepy:      return Color(hex: "#D8C4B0")
        default:           return Color(hex: "#FDBA74")
        }
    }

    var cheekColor: Color { Color(hex: "#FCA5A5").opacity(0.55) }

    var accentColor: Color {
        switch self {
        case .perfect:     return Color(hex: "#22C55E")
        case .happy:       return Color(hex: "#4ADE80")
        case .celebrating: return Color(hex: "#F59E0B")
        case .neutral:     return Color(hex: "#6EE7B7")
        case .eyeroll:     return Color(hex: "#F43F5E")
        case .deflated:    return Color(hex: "#94A3B8")
        case .eating:      return Color(hex: "#F59E0B")
        case .sad:         return Color(hex: "#F472B6")
        case .grumpy:      return Color(hex: "#EF4444")
        case .sleepy:      return Color(hex: "#818CF8")
        }
    }

    var statusKey: String {
        switch self {
        case .perfect, .celebrating: return "tunny_status_perfect"
        case .happy:       return "tunny_status_happy"
        case .neutral:     return "tunny_status_neutral"
        case .eyeroll, .grumpy: return "tunny_status_eyeroll"
        case .deflated, .sad:   return "tunny_status_deflated"
        case .sleepy:      return "tunny_status_sleepy"
        case .eating:      return "tunny_status_happy"
        }
    }
}

// MARK: - Mascot View (Capsule 吞吞)
struct MascotView: View {
    var mood: MascotMood = .happy
    var size: CGFloat = 120
    var showBackground: Bool = true

    @State private var breathe = false
    @State private var blink = false

    private var isDeflated: Bool { mood == .deflated }
    private var bodyWidth: CGFloat { size * 0.62 }
    private var bodyHeight: CGFloat { size * 0.88 }

    var body: some View {
        ZStack {
            if showBackground {
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [mood.accentColor.opacity(0.2), .clear],
                            center: .center, startRadius: 0, endRadius: size * 0.65
                        )
                    )
                    .frame(width: size * 1.3, height: size * 1.3)
                    .blur(radius: 10)
            }

            ZStack {
                // Shadow
                Ellipse()
                    .fill(Color.black.opacity(0.08))
                    .frame(width: bodyWidth * (isDeflated ? 1.1 : 0.8), height: size * 0.06)
                    .offset(y: bodyHeight * 0.52)
                    .blur(radius: 3)

                // Capsule body
                capsuleBody

                // Cheeks
                HStack(spacing: bodyWidth * 0.6) {
                    Ellipse().fill(mood.cheekColor)
                        .frame(width: size * 0.09, height: size * 0.06)
                    Ellipse().fill(mood.cheekColor)
                        .frame(width: size * 0.09, height: size * 0.06)
                }
                .offset(y: size * 0.02)
                .opacity(isDeflated ? 0.3 : 0.85)

                // Face
                faceLayer.offset(y: -size * 0.06)
            }
            .scaleEffect(
                x: isDeflated ? 1.25 : 1.0,
                y: isDeflated ? 0.6 : 1.0
            )
            .scaleEffect(breathe ? breatheScale : 1.0)
            .animation(breatheAnimation, value: breathe)

            accessoryLayer
        }
        .frame(width: size * 1.3, height: size * 1.3)
        .onAppear { breathe = true; blink = true }
    }

    // MARK: - Capsule Body (Two-tone gummy)
    private var capsuleBody: some View {
        ZStack {
            // Bottom half (warm peach)
            Capsule()
                .fill(
                    LinearGradient(
                        colors: [mood.bottomColor, mood.bottomColor.opacity(0.85)],
                        startPoint: .leading, endPoint: .trailing
                    )
                )
                .frame(width: bodyWidth, height: bodyHeight)

            // Top half (vibrant green, masked)
            Capsule()
                .fill(
                    LinearGradient(
                        colors: [mood.topColor.opacity(0.95), mood.topColor],
                        startPoint: .top, endPoint: .bottom
                    )
                )
                .frame(width: bodyWidth, height: bodyHeight)
                .mask {
                    VStack(spacing: 0) {
                        Rectangle()
                        Color.clear
                    }
                }

            // Capsule outline
            Capsule()
                .stroke(mood.topColor.opacity(0.4), lineWidth: 1.5)
                .frame(width: bodyWidth, height: bodyHeight)

            // Seam line at midpoint
            Capsule()
                .fill(mood.topColor.opacity(0.2))
                .frame(width: bodyWidth * 0.88, height: 1.2)

            // Gummy highlight (top-left shine)
            Capsule()
                .fill(Color.white.opacity(0.4))
                .frame(width: bodyWidth * 0.2, height: bodyHeight * 0.35)
                .offset(x: -bodyWidth * 0.18, y: -bodyHeight * 0.2)
                .blur(radius: 2)

            // Secondary highlight (subtle right)
            Capsule()
                .fill(Color.white.opacity(0.12))
                .frame(width: bodyWidth * 0.08, height: bodyHeight * 0.12)
                .offset(x: bodyWidth * 0.2, y: -bodyHeight * 0.08)
                .blur(radius: 1)
        }
    }

    // MARK: - Breathing Animation
    private var breatheScale: CGFloat {
        switch mood {
        case .perfect, .celebrating, .eating: return 1.06
        case .happy: return 1.03
        case .deflated, .sleepy: return 1.01
        case .eyeroll, .grumpy: return 1.02
        default: return 1.025
        }
    }

    private var breatheAnimation: Animation {
        switch mood {
        case .perfect, .celebrating:
            return .easeInOut(duration: 0.9).repeatForever(autoreverses: true)
        case .eating:
            return .easeInOut(duration: 0.45).repeatForever(autoreverses: true)
        case .eyeroll, .grumpy:
            return .easeInOut(duration: 0.6).repeatForever(autoreverses: true)
        default:
            return .easeInOut(duration: 1.8).repeatForever(autoreverses: true)
        }
    }

    // MARK: - Face
    @ViewBuilder
    private var faceLayer: some View {
        VStack(spacing: size * 0.03) {
            HStack(spacing: size * 0.16) {
                eye(side: .left)
                eye(side: .right)
            }
            mouth
        }
    }

    private enum Side { case left, right }

    @ViewBuilder
    private func eye(side: Side) -> some View {
        switch mood {
        case .perfect:
            Image(systemName: "star.fill")
                .font(.system(size: size * 0.12))
                .foregroundColor(Color(hex: "#F59E0B"))
        case .celebrating:
            HappyArc()
                .stroke(Color(hex: "#2D3A2D"), style: StrokeStyle(lineWidth: 2.2, lineCap: .round))
                .frame(width: size * 0.12, height: size * 0.06)
        case .sleepy:
            Capsule()
                .fill(Color(hex: "#2D3A2D"))
                .frame(width: size * 0.12, height: size * 0.02)
        case .grumpy:
            Capsule()
                .fill(Color(hex: "#1F2937"))
                .frame(width: size * 0.1, height: size * 0.055)
                .rotationEffect(.degrees(side == .left ? 12 : -12))
        case .eyeroll:
            ZStack {
                Circle()
                    .fill(Color.white)
                    .frame(width: size * 0.11, height: size * 0.11)
                    .overlay(Circle().stroke(Color(hex: "#2D3A2D"), lineWidth: 1.5))
                Circle()
                    .fill(Color(hex: "#1F2937"))
                    .frame(width: size * 0.05, height: size * 0.05)
                    .offset(y: -size * 0.025)
            }
        case .deflated:
            Capsule()
                .fill(Color(hex: "#2D3A2D").opacity(0.5))
                .frame(width: size * 0.11, height: size * 0.02)
        case .eating:
            HappyArc()
                .stroke(Color(hex: "#2D3A2D"), style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                .frame(width: size * 0.13, height: size * 0.07)
        case .sad:
            Circle()
                .fill(Color(hex: "#1F2937"))
                .frame(width: size * 0.065, height: size * 0.065)
                .overlay(alignment: .bottom) {
                    TearShape()
                        .fill(Color(hex: "#60A5FA"))
                        .frame(width: size * 0.04, height: size * 0.08)
                        .offset(y: size * 0.08)
                }
        default:
            ZStack {
                Circle()
                    .fill(Color(hex: "#1F2937"))
                    .frame(width: size * 0.09, height: size * 0.09)
                Circle()
                    .fill(Color.white)
                    .frame(width: size * 0.035, height: size * 0.035)
                    .offset(x: size * 0.015, y: -size * 0.015)
            }
            .scaleEffect(y: blink ? 1.0 : 0.15)
            .animation(.easeInOut(duration: 0.12).repeatForever(autoreverses: true).delay(2.8), value: blink)
        }
    }

    @ViewBuilder
    private var mouth: some View {
        switch mood {
        case .perfect, .celebrating:
            SmileShape()
                .stroke(Color(hex: "#2D3A2D"), style: StrokeStyle(lineWidth: 2.4, lineCap: .round))
                .frame(width: size * 0.2, height: size * 0.1)
        case .happy:
            CatMouth()
                .stroke(Color(hex: "#2D3A2D"), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .frame(width: size * 0.14, height: size * 0.06)
        case .neutral:
            CatMouth()
                .stroke(Color(hex: "#2D3A2D"), style: StrokeStyle(lineWidth: 1.8, lineCap: .round))
                .frame(width: size * 0.1, height: size * 0.04)
        case .eyeroll:
            WavyMouth()
                .stroke(Color(hex: "#2D3A2D"), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .frame(width: size * 0.14, height: size * 0.04)
        case .eating:
            ZStack {
                Ellipse()
                    .fill(Color(hex: "#1F2937"))
                    .frame(width: size * 0.22, height: size * 0.2)
                Ellipse()
                    .fill(Color(hex: "#F87171"))
                    .frame(width: size * 0.15, height: size * 0.09)
                    .offset(y: size * 0.025)
            }
        case .deflated:
            Circle()
                .stroke(Color(hex: "#2D3A2D").opacity(0.5), lineWidth: 1.5)
                .frame(width: size * 0.04, height: size * 0.04)
        case .grumpy, .sad:
            SmileShape()
                .stroke(Color(hex: "#2D3A2D"), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .frame(width: size * 0.14, height: size * 0.05)
                .rotationEffect(.degrees(180))
        case .sleepy:
            Circle()
                .stroke(Color(hex: "#2D3A2D"), lineWidth: 1.5)
                .frame(width: size * 0.04, height: size * 0.04)
        }
    }

    // MARK: - Accessories
    @ViewBuilder
    private var accessoryLayer: some View {
        switch mood {
        case .celebrating:
            ForEach(0..<6, id: \.self) { i in
                Circle()
                    .fill([Color(hex: "#F472B6"), Color(hex: "#60A5FA"), Color(hex: "#FBBF24"), Color(hex: "#34D399")][i % 4])
                    .frame(width: size * 0.04, height: size * 0.04)
                    .offset(
                        x: size * [-0.4, -0.25, 0.25, 0.4, -0.35, 0.35][i],
                        y: size * [-0.45, -0.5, -0.45, -0.48, -0.3, -0.32][i]
                    )
            }
        case .sleepy:
            VStack(spacing: size * 0.015) {
                Text("z")
                    .font(.system(size: size * 0.12, weight: .bold, design: .rounded))
                Text("Z")
                    .font(.system(size: size * 0.18, weight: .bold, design: .rounded))
            }
            .foregroundColor(mood.accentColor.opacity(0.6))
            .offset(x: size * 0.38, y: -size * 0.28)
        case .grumpy, .eyeroll:
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: size * 0.12))
                .foregroundColor(Color(hex: "#EF4444").opacity(0.8))
                .offset(x: size * 0.32, y: -size * 0.35)
        case .perfect:
            ZStack {
                Image(systemName: "sparkles")
                    .font(.system(size: size * 0.14))
                    .foregroundColor(Color(hex: "#F59E0B"))
                    .offset(x: size * 0.35, y: -size * 0.35)
                Image(systemName: "sparkle")
                    .font(.system(size: size * 0.08))
                    .foregroundColor(Color(hex: "#4ADE80"))
                    .offset(x: -size * 0.38, y: -size * 0.28)
            }
        case .eating:
            ZStack {
                Image(systemName: "sparkle")
                    .font(.system(size: size * 0.09, weight: .bold))
                    .foregroundColor(Color(hex: "#FBBF24"))
                    .offset(x: size * 0.32, y: -size * 0.32)
                Image(systemName: "sparkle")
                    .font(.system(size: size * 0.07, weight: .bold))
                    .foregroundColor(Color(hex: "#4ADE80"))
                    .offset(x: -size * 0.35, y: -size * 0.28)
            }
        case .deflated:
            Image(systemName: "leaf.fill")
                .font(.system(size: size * 0.1))
                .foregroundColor(Color(hex: "#86EFAC").opacity(0.6))
                .offset(x: size * 0.3, y: -size * 0.22)
        default:
            EmptyView()
        }
    }
}

// MARK: - Cat Mouth Shape (ω)
struct CatMouth: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        let w = rect.width, h = rect.height
        p.move(to: CGPoint(x: 0, y: 0))
        p.addQuadCurve(
            to: CGPoint(x: w * 0.5, y: h),
            control: CGPoint(x: w * 0.2, y: h * 0.8)
        )
        p.addQuadCurve(
            to: CGPoint(x: w, y: 0),
            control: CGPoint(x: w * 0.8, y: h * 0.8)
        )
        return p
    }
}

// MARK: - Wavy Mouth Shape (~)
struct WavyMouth: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        let w = rect.width, h = rect.height
        p.move(to: CGPoint(x: 0, y: h * 0.5))
        p.addCurve(
            to: CGPoint(x: w * 0.5, y: h * 0.5),
            control1: CGPoint(x: w * 0.15, y: 0),
            control2: CGPoint(x: w * 0.35, y: h)
        )
        p.addCurve(
            to: CGPoint(x: w, y: h * 0.5),
            control1: CGPoint(x: w * 0.65, y: 0),
            control2: CGPoint(x: w * 0.85, y: h)
        )
        return p
    }
}

// MARK: - Smile Shape
struct SmileShape: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: rect.minX, y: rect.minY))
        p.addQuadCurve(
            to: CGPoint(x: rect.maxX, y: rect.minY),
            control: CGPoint(x: rect.midX, y: rect.maxY * 2)
        )
        return p
    }
}

// MARK: - Happy Arc Shape
struct HappyArc: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: rect.minX, y: rect.maxY))
        p.addQuadCurve(
            to: CGPoint(x: rect.maxX, y: rect.maxY),
            control: CGPoint(x: rect.midX, y: rect.minY - rect.height)
        )
        return p
    }
}

// MARK: - Tear Shape
struct TearShape: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: rect.midX, y: rect.minY))
        p.addQuadCurve(
            to: CGPoint(x: rect.maxX, y: rect.maxY * 0.75),
            control: CGPoint(x: rect.maxX, y: rect.midY)
        )
        p.addArc(
            center: CGPoint(x: rect.midX, y: rect.maxY * 0.75),
            radius: rect.width / 2,
            startAngle: .degrees(0),
            endAngle: .degrees(180),
            clockwise: false
        )
        p.addQuadCurve(
            to: CGPoint(x: rect.midX, y: rect.minY),
            control: CGPoint(x: rect.minX, y: rect.midY)
        )
        return p
    }
}

// MARK: - Speech Bubble Tail
struct BubbleTail: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.midX, y: rect.maxY))
        path.closeSubpath()
        return path
    }
}

#Preview {
    VStack(spacing: 20) {
        HStack {
            MascotView(mood: .perfect, size: 80)
            MascotView(mood: .happy, size: 80)
            MascotView(mood: .celebrating, size: 80)
        }
        HStack {
            MascotView(mood: .eyeroll, size: 80)
            MascotView(mood: .deflated, size: 80)
            MascotView(mood: .eating, size: 80)
        }
        MascotView(mood: .sleepy, size: 100)
    }
    .padding()
    .background(Color(hex: "#FDF2F8"))
}
