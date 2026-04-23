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

    // Capsule top half (head) — golden cream body, paler when low energy
    var topColor: Color {
        switch self {
        case .deflated:    return Color(hex: "#F0E8D8")
        case .sleepy:      return Color(hex: "#EDE5D5")
        default:           return Color(hex: "#FFF0D0")
        }
    }

    // Capsule bottom half (body) — warm peach-cream
    var bottomColor: Color {
        switch self {
        case .deflated:    return Color(hex: "#E8DCC8")
        case .sleepy:      return Color(hex: "#E5DDC8")
        default:           return Color(hex: "#FFE8B8")
        }
    }

    var cheekColor: Color { Color(hex: "#FFAA90").opacity(0.8) }

    var accentColor: Color {
        switch self {
        case .perfect:     return Color(hex: "#FFD040")
        case .happy:       return Color(hex: "#7BC5A0")
        case .celebrating: return Color(hex: "#FFD040")
        case .neutral:     return Color(hex: "#BFE8D2")
        case .eyeroll:     return Color(hex: "#FF9A78")
        case .deflated:    return Color(hex: "#A09888")
        case .eating:      return Color(hex: "#FFD040")
        case .sad:         return Color(hex: "#FFB098")
        case .grumpy:      return Color(hex: "#FF9A78")
        case .sleepy:      return Color(hex: "#BFE8D2")
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
                // Ground shadow — 3D contact
                Ellipse()
                    .fill(
                        RadialGradient(
                            colors: [Color.black.opacity(0.15), Color.black.opacity(0.04), Color.clear],
                            center: .center, startRadius: 0, endRadius: bodyWidth * 0.5
                        )
                    )
                    .frame(width: bodyWidth * (isDeflated ? 1.2 : 1.0), height: size * 0.1)
                    .offset(y: bodyHeight * 0.52)
                    .blur(radius: 4)

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

    // MARK: - Capsule Body (2.5D gummy)
    private var capsuleBody: some View {
        ZStack {
            // Bottom half — 3D shaded
            Capsule()
                .fill(
                    LinearGradient(
                        colors: [mood.bottomColor, mood.bottomColor.opacity(0.82)],
                        startPoint: .topLeading, endPoint: .bottomTrailing
                    )
                )
                .frame(width: bodyWidth, height: bodyHeight)

            // Top half (masked) — lighter leading edge
            Capsule()
                .fill(
                    LinearGradient(
                        colors: [mood.topColor, mood.topColor.opacity(0.88)],
                        startPoint: .topLeading, endPoint: .bottomTrailing
                    )
                )
                .frame(width: bodyWidth, height: bodyHeight)
                .mask {
                    VStack(spacing: 0) {
                        Rectangle()
                        Color.clear
                    }
                }

            // Ambient occlusion (bottom edge darkening)
            Capsule()
                .fill(
                    LinearGradient(
                        colors: [Color.clear, Color.clear, Color.black.opacity(0.06)],
                        startPoint: .top, endPoint: .bottom
                    )
                )
                .frame(width: bodyWidth, height: bodyHeight)

            // 3D rim — gradient stroke
            Capsule()
                .stroke(
                    LinearGradient(
                        colors: [Color.white.opacity(0.6), mood.bottomColor.opacity(0.25)],
                        startPoint: .topLeading, endPoint: .bottomTrailing
                    ),
                    lineWidth: 1.5
                )
                .frame(width: bodyWidth, height: bodyHeight)

            // Seam line
            Capsule()
                .fill(mood.bottomColor.opacity(0.25))
                .frame(width: bodyWidth * 0.88, height: 1.5)

            // Main specular highlight (top-left gloss)
            Ellipse()
                .fill(
                    RadialGradient(
                        colors: [Color.white.opacity(0.65), Color.white.opacity(0.1), Color.clear],
                        center: UnitPoint(x: 0.35, y: 0.25),
                        startRadius: 0,
                        endRadius: bodyWidth * 0.45
                    )
                )
                .frame(width: bodyWidth * 0.75, height: bodyHeight * 0.55)
                .offset(x: -bodyWidth * 0.08, y: -bodyHeight * 0.15)

            // Rim light (right edge depth)
            Capsule()
                .fill(Color.white.opacity(0.18))
                .frame(width: bodyWidth * 0.1, height: bodyHeight * 0.2)
                .offset(x: bodyWidth * 0.22, y: -bodyHeight * 0.06)
                .blur(radius: 2)
        }
        .shadow(color: mood.bottomColor.opacity(0.3), radius: 8, y: 5)
        .shadow(color: Color.black.opacity(0.08), radius: 16, y: 10)
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
                .foregroundColor(Color(hex: "#FFE066"))
        case .celebrating:
            HappyArc()
                .stroke(Color(hex: "#5D5A57"), style: StrokeStyle(lineWidth: 2.2, lineCap: .round))
                .frame(width: size * 0.12, height: size * 0.06)
        case .sleepy:
            Capsule()
                .fill(Color(hex: "#5D5A57"))
                .frame(width: size * 0.12, height: size * 0.02)
        case .grumpy:
            Capsule()
                .fill(Color(hex: "#5D5A57"))
                .frame(width: size * 0.1, height: size * 0.055)
                .rotationEffect(.degrees(side == .left ? 12 : -12))
        case .eyeroll:
            ZStack {
                Circle()
                    .fill(Color.white)
                    .frame(width: size * 0.11, height: size * 0.11)
                    .overlay(Circle().stroke(Color(hex: "#5D5A57"), lineWidth: 1.5))
                Circle()
                    .fill(Color(hex: "#5D5A57"))
                    .frame(width: size * 0.05, height: size * 0.05)
                    .offset(y: -size * 0.025)
            }
        case .deflated:
            Capsule()
                .fill(Color(hex: "#5D5A57").opacity(0.5))
                .frame(width: size * 0.11, height: size * 0.02)
        case .eating:
            HappyArc()
                .stroke(Color(hex: "#5D5A57"), style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                .frame(width: size * 0.13, height: size * 0.07)
        case .sad:
            Circle()
                .fill(Color(hex: "#5D5A57"))
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
                    .fill(Color(hex: "#5D5A57"))
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
                .stroke(Color(hex: "#5D5A57"), style: StrokeStyle(lineWidth: 2.4, lineCap: .round))
                .frame(width: size * 0.2, height: size * 0.1)
        case .happy:
            CatMouth()
                .stroke(Color(hex: "#5D5A57"), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .frame(width: size * 0.14, height: size * 0.06)
        case .neutral:
            CatMouth()
                .stroke(Color(hex: "#5D5A57"), style: StrokeStyle(lineWidth: 1.8, lineCap: .round))
                .frame(width: size * 0.1, height: size * 0.04)
        case .eyeroll:
            WavyMouth()
                .stroke(Color(hex: "#5D5A57"), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .frame(width: size * 0.14, height: size * 0.04)
        case .eating:
            ZStack {
                Ellipse()
                    .fill(Color(hex: "#5D5A57"))
                    .frame(width: size * 0.22, height: size * 0.2)
                Ellipse()
                    .fill(Color(hex: "#F87171"))
                    .frame(width: size * 0.15, height: size * 0.09)
                    .offset(y: size * 0.025)
            }
        case .deflated:
            Circle()
                .stroke(Color(hex: "#5D5A57").opacity(0.5), lineWidth: 1.5)
                .frame(width: size * 0.04, height: size * 0.04)
        case .grumpy, .sad:
            SmileShape()
                .stroke(Color(hex: "#5D5A57"), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .frame(width: size * 0.14, height: size * 0.05)
                .rotationEffect(.degrees(180))
        case .sleepy:
            Circle()
                .stroke(Color(hex: "#5D5A57"), lineWidth: 1.5)
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
                    .fill([Color(hex: "#FFE066"), Color(hex: "#E8B0E0"), Color(hex: "#D0F0D8"), Color(hex: "#C0D840")][i % 4])
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
                .foregroundColor(Color(hex: "#F8D0E8").opacity(0.9))
                .offset(x: size * 0.32, y: -size * 0.35)
        case .perfect:
            ZStack {
                Image(systemName: "sparkles")
                    .font(.system(size: size * 0.14))
                    .foregroundColor(Color(hex: "#FFE066"))
                    .offset(x: size * 0.35, y: -size * 0.35)
                Image(systemName: "sparkle")
                    .font(.system(size: size * 0.08))
                    .foregroundColor(Color(hex: "#D0F0D8"))
                    .offset(x: -size * 0.38, y: -size * 0.28)
            }
        case .eating:
            ZStack {
                Image(systemName: "sparkle")
                    .font(.system(size: size * 0.09, weight: .bold))
                    .foregroundColor(Color(hex: "#FFE066"))
                    .offset(x: size * 0.32, y: -size * 0.32)
                Image(systemName: "sparkle")
                    .font(.system(size: size * 0.07, weight: .bold))
                    .foregroundColor(Color(hex: "#D0F0D8"))
                    .offset(x: -size * 0.35, y: -size * 0.28)
            }
        case .deflated:
            Image(systemName: "leaf.fill")
                .font(.system(size: size * 0.1))
                .foregroundColor(Color(hex: "#D0F0D8").opacity(0.6))
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
    .background(Color(hex: "#F3ECF3"))
}
