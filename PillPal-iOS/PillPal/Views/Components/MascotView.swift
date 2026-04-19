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

    var bodyColor: Color {
        switch self {
        case .perfect:     return Color(hex: "#C4B5FD")
        case .happy:       return Color(hex: "#BFDBFE")
        case .celebrating: return Color(hex: "#FDE68A")
        case .neutral:     return Color(hex: "#E9D5FF")
        case .eyeroll:     return Color(hex: "#FECDD3")
        case .deflated:    return Color(hex: "#E2E8F0")
        case .eating:      return Color(hex: "#FEF3C7")
        case .sad:         return Color(hex: "#FBCFE8")
        case .grumpy:      return Color(hex: "#FECACA")
        case .sleepy:      return Color(hex: "#DDD6FE")
        }
    }

    var cheekColor: Color { Color(hex: "#FCA5A5").opacity(0.75) }

    var accentColor: Color {
        switch self {
        case .perfect:     return Color(hex: "#8B5CF6")
        case .happy:       return Color(hex: "#60A5FA")
        case .celebrating: return Color(hex: "#F59E0B")
        case .neutral:     return Color(hex: "#A78BFA")
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

// MARK: - Mascot View
struct MascotView: View {
    var mood: MascotMood = .happy
    var size: CGFloat = 120
    var showBackground: Bool = true

    @State private var bounce = false
    @State private var blink = false

    private var isDeflated: Bool { mood == .deflated }

    var body: some View {
        ZStack {
            if showBackground {
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [mood.accentColor.opacity(0.25), .clear],
                            center: .center, startRadius: 0, endRadius: size * 0.7
                        )
                    )
                    .frame(width: size * 1.4, height: size * 1.4)
                    .blur(radius: 12)
            }

            ZStack {
                Ellipse()
                    .fill(Color.black.opacity(0.08))
                    .frame(width: size * (isDeflated ? 0.85 : 0.7), height: size * 0.1)
                    .offset(y: size * (isDeflated ? 0.32 : 0.48))
                    .blur(radius: 3)

                if size >= 50 { armsLayer }

                Capsule()
                    .fill(
                        LinearGradient(
                            colors: [mood.bodyColor, mood.bodyColor.opacity(0.85)],
                            startPoint: .top, endPoint: .bottom
                        )
                    )
                    .frame(width: size * 0.78, height: size * 0.92)
                    .overlay(Capsule().stroke(mood.accentColor.opacity(0.45), lineWidth: 2))
                    .overlay(
                        Rectangle()
                            .fill(Color.white.opacity(0.5))
                            .frame(width: size * 0.78, height: 1.2)
                    )
                    .overlay(
                        Capsule()
                            .fill(Color.white.opacity(0.45))
                            .frame(width: size * 0.2, height: size * 0.4)
                            .offset(x: -size * 0.2, y: -size * 0.22)
                            .blur(radius: 2)
                    )

                HStack(spacing: size * 0.35) {
                    Circle().fill(mood.cheekColor).frame(width: size * 0.12, height: size * 0.09)
                    Circle().fill(mood.cheekColor).frame(width: size * 0.12, height: size * 0.09)
                }
                .offset(y: size * 0.05)
                .opacity(isDeflated ? 0.35 : 1)

                faceLayer.offset(y: -size * 0.05)
            }
            .scaleEffect(
                x: isDeflated ? 1.2 : 1.0,
                y: isDeflated ? 0.65 : 1.0
            )
            .scaleEffect(bounce ? bounceScale : 1.0)
            .rotationEffect(.degrees(rotationForMood))
            .animation(bounceAnimation, value: bounce)

            accessoryLayer
        }
        .frame(width: size * 1.3, height: size * 1.3)
        .onAppear { bounce = true; blink = true }
    }

    private var bounceScale: CGFloat {
        switch mood {
        case .perfect, .celebrating, .eating: return 1.08
        case .happy: return 1.04
        case .deflated, .sleepy: return 1.01
        default: return 1.03
        }
    }

    private var bounceAnimation: Animation {
        switch mood {
        case .perfect, .celebrating:
            return .easeInOut(duration: 0.8).repeatForever(autoreverses: true)
        case .eating:
            return .easeInOut(duration: 0.5).repeatForever(autoreverses: true)
        default:
            return .easeInOut(duration: 1.6).repeatForever(autoreverses: true)
        }
    }

    private var rotationForMood: Double {
        switch mood {
        case .grumpy, .eyeroll: return -4
        case .sad: return -2
        case .celebrating: return 3
        case .eating: return 2
        default: return 0
        }
    }

    // MARK: - Arms
    @ViewBuilder
    private var armsLayer: some View {
        Capsule()
            .fill(mood.bodyColor.opacity(0.9))
            .frame(width: size * 0.09, height: size * 0.22)
            .overlay(Capsule().stroke(mood.accentColor.opacity(0.3), lineWidth: 1))
            .rotationEffect(.degrees(leftArmAngle), anchor: .top)
            .offset(x: -size * 0.42, y: size * 0.05)

        Capsule()
            .fill(mood.bodyColor.opacity(0.9))
            .frame(width: size * 0.09, height: size * 0.22)
            .overlay(Capsule().stroke(mood.accentColor.opacity(0.3), lineWidth: 1))
            .rotationEffect(.degrees(rightArmAngle), anchor: .top)
            .offset(x: size * 0.42, y: size * 0.05)
    }

    private var leftArmAngle: Double {
        switch mood {
        case .celebrating, .perfect: return -45
        case .happy: return -20
        case .grumpy, .eyeroll: return 30
        case .eating: return -30
        case .deflated: return -5
        case .sleepy: return 5
        default: return -10
        }
    }

    private var rightArmAngle: Double {
        switch mood {
        case .celebrating, .perfect: return 45
        case .happy: return 20
        case .grumpy, .eyeroll: return -30
        case .eating: return 30
        case .deflated: return 5
        case .sleepy: return -5
        default: return 10
        }
    }

    // MARK: - Face
    @ViewBuilder
    private var faceLayer: some View {
        HStack(spacing: size * 0.18) {
            eye(side: .left)
            eye(side: .right)
        }
        .overlay(mouth.offset(y: size * 0.18))
    }

    private enum Side { case left, right }

    @ViewBuilder
    private func eye(side: Side) -> some View {
        switch mood {
        case .perfect:
            Image(systemName: "star.fill")
                .font(.system(size: size * 0.14))
                .foregroundColor(mood.accentColor)
        case .celebrating:
            HappyArc()
                .stroke(Color(hex: "#4B2A2A"), style: StrokeStyle(lineWidth: 2.2, lineCap: .round))
                .frame(width: size * 0.13, height: size * 0.07)
        case .sleepy:
            Capsule()
                .fill(Color(hex: "#4B2A2A"))
                .frame(width: size * 0.13, height: size * 0.025)
        case .grumpy:
            Capsule()
                .fill(Color(hex: "#1F2937"))
                .frame(width: size * 0.1, height: size * 0.06)
                .rotationEffect(.degrees(side == .left ? 15 : -15))
        case .eyeroll:
            ZStack {
                Circle()
                    .fill(Color.white)
                    .frame(width: size * 0.12, height: size * 0.12)
                    .overlay(Circle().stroke(Color(hex: "#4B2A2A"), lineWidth: 1.5))
                Circle()
                    .fill(Color(hex: "#1F2937"))
                    .frame(width: size * 0.06, height: size * 0.06)
                    .offset(y: -size * 0.03)
            }
        case .deflated:
            Capsule()
                .fill(Color(hex: "#4B2A2A").opacity(0.6))
                .frame(width: size * 0.12, height: size * 0.025)
        case .eating:
            Image(systemName: "sparkle")
                .font(.system(size: size * 0.13, weight: .bold))
                .foregroundColor(Color(hex: "#F59E0B"))
        case .sad:
            Circle()
                .fill(Color(hex: "#1F2937"))
                .frame(width: size * 0.07, height: size * 0.07)
                .overlay(alignment: .bottom) {
                    TearShape()
                        .fill(Color(hex: "#60A5FA"))
                        .frame(width: size * 0.05, height: size * 0.1)
                        .offset(y: size * 0.1)
                }
        default:
            ZStack {
                Circle()
                    .fill(Color(hex: "#1F2937"))
                    .frame(width: size * 0.09, height: size * 0.09)
                Circle()
                    .fill(Color.white)
                    .frame(width: size * 0.03, height: size * 0.03)
                    .offset(x: size * 0.02, y: -size * 0.02)
            }
            .scaleEffect(y: blink ? 1.0 : 0.15)
            .animation(.easeInOut(duration: 0.12).repeatForever(autoreverses: true).delay(2.5), value: blink)
        }
    }

    @ViewBuilder
    private var mouth: some View {
        switch mood {
        case .perfect, .celebrating:
            SmileShape()
                .stroke(Color(hex: "#4B2A2A"), style: StrokeStyle(lineWidth: 2.4, lineCap: .round))
                .frame(width: size * 0.22, height: size * 0.1)
        case .happy:
            SmileShape()
                .stroke(Color(hex: "#4B2A2A"), style: StrokeStyle(lineWidth: 2.2, lineCap: .round))
                .frame(width: size * 0.18, height: size * 0.07)
        case .neutral:
            Capsule()
                .fill(Color(hex: "#4B2A2A"))
                .frame(width: size * 0.14, height: size * 0.018)
        case .eyeroll:
            SmileShape()
                .stroke(Color(hex: "#4B2A2A"), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .frame(width: size * 0.14, height: size * 0.05)
                .rotationEffect(.degrees(180))
        case .eating:
            ZStack {
                Ellipse()
                    .fill(Color(hex: "#4B2A2A"))
                    .frame(width: size * 0.16, height: size * 0.14)
                Ellipse()
                    .fill(Color(hex: "#F87171"))
                    .frame(width: size * 0.12, height: size * 0.08)
                    .offset(y: size * 0.01)
            }
        case .deflated:
            Capsule()
                .fill(Color(hex: "#4B2A2A").opacity(0.5))
                .frame(width: size * 0.08, height: size * 0.014)
                .rotationEffect(.degrees(-5))
        case .sad, .grumpy:
            SmileShape()
                .stroke(Color(hex: "#4B2A2A"), style: StrokeStyle(lineWidth: 2.2, lineCap: .round))
                .frame(width: size * 0.16, height: size * 0.06)
                .rotationEffect(.degrees(180))
        case .sleepy:
            Circle()
                .stroke(Color(hex: "#4B2A2A"), lineWidth: 1.6)
                .frame(width: size * 0.05, height: size * 0.05)
        }
    }

    // MARK: - Accessories
    @ViewBuilder
    private var accessoryLayer: some View {
        switch mood {
        case .celebrating:
            ZStack {
                ForEach(0..<6, id: \.self) { i in
                    Circle()
                        .fill([Color(hex: "#F472B6"), Color(hex: "#60A5FA"), Color(hex: "#FBBF24"), Color(hex: "#34D399")][i % 4])
                        .frame(width: size * 0.05, height: size * 0.05)
                        .offset(
                            x: size * [-0.5, -0.3, 0.3, 0.5, -0.4, 0.4][i],
                            y: size * [-0.5, -0.55, -0.5, -0.5, -0.3, -0.35][i]
                        )
                }
            }
        case .sleepy:
            VStack(spacing: size * 0.02) {
                Text("z")
                    .font(.system(size: size * 0.14, weight: .bold, design: .rounded))
                Text("Z")
                    .font(.system(size: size * 0.2, weight: .bold, design: .rounded))
            }
            .foregroundColor(mood.accentColor.opacity(0.7))
            .offset(x: size * 0.4, y: -size * 0.3)
        case .grumpy, .eyeroll:
            Image(systemName: "bolt.fill")
                .font(.system(size: size * 0.14))
                .foregroundColor(Color(hex: "#EF4444"))
                .offset(x: size * 0.35, y: -size * 0.38)
        case .perfect:
            ZStack {
                Image(systemName: "sparkles")
                    .font(.system(size: size * 0.16))
                    .foregroundColor(Color(hex: "#F59E0B"))
                    .offset(x: size * 0.38, y: -size * 0.38)
                Image(systemName: "sparkle")
                    .font(.system(size: size * 0.1))
                    .foregroundColor(Color(hex: "#A78BFA"))
                    .offset(x: -size * 0.4, y: -size * 0.3)
            }
        case .eating:
            ZStack {
                Image(systemName: "sparkle")
                    .font(.system(size: size * 0.1, weight: .bold))
                    .foregroundColor(Color(hex: "#FBBF24"))
                    .offset(x: size * 0.35, y: -size * 0.35)
                Image(systemName: "sparkle")
                    .font(.system(size: size * 0.08, weight: .bold))
                    .foregroundColor(Color(hex: "#F472B6"))
                    .offset(x: -size * 0.38, y: -size * 0.3)
            }
        case .deflated:
            Image(systemName: "leaf.fill")
                .font(.system(size: size * 0.12))
                .foregroundColor(Color(hex: "#86EFAC").opacity(0.7))
                .offset(x: size * 0.35, y: -size * 0.25)
        default:
            EmptyView()
        }
    }
}

// MARK: - Supporting shapes
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
