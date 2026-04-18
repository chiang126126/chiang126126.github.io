import SwiftUI

// MARK: - Mascot "吞吞" / "Tunny"
// Custom SwiftUI-drawn character so the UI never depends on Unicode emoji
// rendering (which can show as "?" on some devices/sims).
enum MascotMood {
    case perfect, happy, neutral, sad, grumpy, sleepy, celebrating

    static func forAdherence(_ pct: Double) -> MascotMood {
        if pct >= 100 { return .perfect }
        if pct >= 80 { return .happy }
        if pct >= 50 { return .neutral }
        if pct >= 20 { return .sad }
        return .grumpy
    }

    var bodyColor: Color {
        switch self {
        case .perfect:     return Color(hex: "#C4B5FD") // soft lavender
        case .happy:       return Color(hex: "#BFDBFE") // baby blue
        case .celebrating: return Color(hex: "#FDE68A") // butter yellow
        case .neutral:     return Color(hex: "#E9D5FF") // pale violet
        case .sad:         return Color(hex: "#FBCFE8") // dusty pink
        case .grumpy:      return Color(hex: "#FECACA") // peach red
        case .sleepy:      return Color(hex: "#DDD6FE") // pastel purple
        }
    }

    var cheekColor: Color {
        Color(hex: "#FCA5A5").opacity(0.75)
    }

    var accentColor: Color {
        switch self {
        case .perfect:     return Color(hex: "#8B5CF6")
        case .happy:       return Color(hex: "#60A5FA")
        case .celebrating: return Color(hex: "#F59E0B")
        case .neutral:     return Color(hex: "#A78BFA")
        case .sad:         return Color(hex: "#F472B6")
        case .grumpy:      return Color(hex: "#EF4444")
        case .sleepy:      return Color(hex: "#818CF8")
        }
    }
}

struct MascotView: View {
    var mood: MascotMood = .happy
    var size: CGFloat = 120
    var showBackground: Bool = true

    @State private var bounce: Bool = false
    @State private var blink: Bool = false

    var body: some View {
        ZStack {
            if showBackground {
                // Soft halo
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
                // Body shadow
                Ellipse()
                    .fill(Color.black.opacity(0.08))
                    .frame(width: size * 0.7, height: size * 0.1)
                    .offset(y: size * 0.48)
                    .blur(radius: 3)

                // Pill body (capsule)
                Capsule()
                    .fill(
                        LinearGradient(
                            colors: [mood.bodyColor, mood.bodyColor.opacity(0.85)],
                            startPoint: .top, endPoint: .bottom
                        )
                    )
                    .frame(width: size * 0.78, height: size * 0.92)
                    .overlay(
                        Capsule()
                            .stroke(mood.accentColor.opacity(0.45), lineWidth: 2)
                    )
                    .overlay(
                        // Subtle inner split line
                        Rectangle()
                            .fill(Color.white.opacity(0.5))
                            .frame(width: size * 0.78, height: 1.2)
                    )
                    .overlay(
                        // Glossy highlight
                        Capsule()
                            .fill(Color.white.opacity(0.45))
                            .frame(width: size * 0.2, height: size * 0.4)
                            .offset(x: -size * 0.2, y: -size * 0.22)
                            .blur(radius: 2)
                    )

                // Cheeks
                HStack(spacing: size * 0.35) {
                    Circle().fill(mood.cheekColor).frame(width: size * 0.12, height: size * 0.09)
                    Circle().fill(mood.cheekColor).frame(width: size * 0.12, height: size * 0.09)
                }
                .offset(y: size * 0.05)

                // Eyes & mouth (per mood)
                faceLayer
                    .offset(y: -size * 0.05)

                // Mood accessories
                accessoryLayer
            }
            .scaleEffect(bounce ? 1.04 : 1.0)
            .rotationEffect(.degrees(rotationForMood))
            .animation(.easeInOut(duration: 1.6).repeatForever(autoreverses: true), value: bounce)
        }
        .frame(width: size * 1.2, height: size * 1.2)
        .onAppear {
            bounce = true
            blink = true
        }
    }

    private var rotationForMood: Double {
        switch mood {
        case .grumpy: return -4
        case .sad: return -2
        case .celebrating: return 3
        default: return 0
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
            // Star eyes
            Image(systemName: "star.fill")
                .font(.system(size: size * 0.14))
                .foregroundColor(mood.accentColor)
        case .celebrating:
            // Closed happy eye ^
            HappyArc()
                .stroke(Color(hex: "#4B2A2A"), style: StrokeStyle(lineWidth: 2.2, lineCap: .round))
                .frame(width: size * 0.13, height: size * 0.07)
        case .sleepy:
            Capsule()
                .fill(Color(hex: "#4B2A2A"))
                .frame(width: size * 0.13, height: size * 0.025)
        case .grumpy:
            // Angry slanted eye
            Capsule()
                .fill(Color(hex: "#1F2937"))
                .frame(width: size * 0.1, height: size * 0.06)
                .rotationEffect(.degrees(side == .left ? 15 : -15))
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
            // Neutral/happy dot eyes with a shine
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
            // Confetti dots
            ZStack {
                ForEach(0..<6, id: \.self) { i in
                    Circle()
                        .fill([Color(hex: "#F472B6"), Color(hex: "#60A5FA"), Color(hex: "#FBBF24"), Color(hex: "#34D399")].randomElement() ?? .pink)
                        .frame(width: size * 0.05, height: size * 0.05)
                        .offset(
                            x: size * [-0.5, -0.3, 0.3, 0.5, -0.4, 0.4][i],
                            y: size * [-0.5, -0.55, -0.5, -0.5, -0.3, -0.35][i]
                        )
                }
            }
        case .sleepy:
            Text("z")
                .font(.system(size: size * 0.2, weight: .bold, design: .rounded))
                .foregroundColor(mood.accentColor)
                .offset(x: size * 0.35, y: -size * 0.35)
        case .grumpy:
            // Angry vein-like symbol using SF
            Image(systemName: "bolt.fill")
                .font(.system(size: size * 0.14))
                .foregroundColor(Color(hex: "#EF4444"))
                .offset(x: size * 0.32, y: -size * 0.38)
        case .perfect:
            // Sparkle
            Image(systemName: "sparkles")
                .font(.system(size: size * 0.16))
                .foregroundColor(Color(hex: "#F59E0B"))
                .offset(x: size * 0.38, y: -size * 0.38)
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

#Preview {
    VStack(spacing: 20) {
        HStack {
            MascotView(mood: .perfect, size: 80)
            MascotView(mood: .happy, size: 80)
            MascotView(mood: .celebrating, size: 80)
        }
        HStack {
            MascotView(mood: .neutral, size: 80)
            MascotView(mood: .sad, size: 80)
            MascotView(mood: .grumpy, size: 80)
        }
        MascotView(mood: .sleepy, size: 100)
    }
    .padding()
    .background(Color(hex: "#FDF2F8"))
}
