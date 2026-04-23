import SwiftUI

// MARK: - App Theme Mode
enum AppMode: String, Codable {
    case pro, care
}

// MARK: - Theme Manager
@Observable
final class ThemeManager {
    var mode: AppMode {
        didSet { save() }
    }

    init() {
        if let raw = UserDefaults.standard.string(forKey: "pillpal-mode"),
           let saved = AppMode(rawValue: raw) {
            self.mode = saved
        } else {
            self.mode = .care
        }
    }

    private func save() {
        UserDefaults.standard.set(mode.rawValue, forKey: "pillpal-mode")
    }

    var isPro: Bool { mode == .pro }
    var isCare: Bool { mode == .care }

    func toggle() {
        mode = isPro ? .care : .pro
    }

    // MARK: - Colors
    // Care: MindMate-style — soft lavender bg, olive-lime accent, candy pastels
    // Pro: deep plum + neon
    var accentColor: Color { isPro ? Color(hex: "#A78BFA") : Color(hex: "#8AAD28") }
    var accentSecondary: Color { isPro ? Color(hex: "#F472B6") : Color(hex: "#E8B0E0") }
    var bgColor: Color { isPro ? Color(hex: "#110A1F") : Color(hex: "#F3ECF3") }
    var bgSecondary: Color { isPro ? Color(hex: "#1B0F2E") : Color(hex: "#EDE6ED") }
    var cardColor: Color { isPro ? Color(hex: "#1E1533") : .white }
    var surfaceColor: Color { isPro ? Color(hex: "#2A1F44") : Color(hex: "#F5F0F5") }
    var borderColor: Color { isPro ? Color(hex: "#3B2A5E") : Color(hex: "#E8E0E8") }
    var textColor: Color { isPro ? .white : Color(hex: "#222222") }
    var mutedColor: Color { isPro ? Color(hex: "#A78BFA").opacity(0.7) : Color(hex: "#999999") }

    var warmYellow: Color { Color(hex: "#FFE066") }
    var warmPeach: Color { Color(hex: "#FFB89A") }
    var warmMint: Color { Color(hex: "#A8F0C8") }

    var neonOrange: Color { isPro ? Color(hex: "#FB923C") : Color(hex: "#D4A030") }
    var neonPink: Color { isPro ? Color(hex: "#F472B6") : Color(hex: "#E8B0E0") }
    var neonPurple: Color { isPro ? Color(hex: "#A855F7") : Color(hex: "#8AAD28") }

    var successColor: Color { isPro ? Color(hex: "#10B981") : Color(hex: "#8AAD28") }
    var dangerColor: Color { Color(hex: "#EF4444") }

    var pastelLavender: Color { isPro ? Color(hex: "#E9D5FF") : Color(hex: "#F0D0F0") }
    var pastelPink: Color { isPro ? Color(hex: "#FBCFE8") : Color(hex: "#F8D0E8") }
    var pastelCream: Color { Color(hex: "#FFF7EC") }
    var pastelMint: Color { Color(hex: "#D0F0D8") }
    var pastelSky: Color { isPro ? Color(hex: "#DBEAFE") : Color(hex: "#C0E8FF") }

    // MARK: - Gradients
    // Accent gradient — olive-lime fill for progress bars and chart bars
    var accentGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#A78BFA"), Color(hex: "#F472B6")], startPoint: .topLeading, endPoint: .bottomTrailing)
            : LinearGradient(colors: [Color(hex: "#C0D840"), Color(hex: "#A8C028")], startPoint: .leading, endPoint: .trailing)
    }

    // Button gradient — soft pink → yellow → lime for CTA buttons (from reference)
    var buttonGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#A78BFA"), Color(hex: "#F472B6")], startPoint: .leading, endPoint: .trailing)
            : LinearGradient(colors: [Color(hex: "#F0D0E8"), Color(hex: "#E8E0A0"), Color(hex: "#C8E060")], startPoint: .leading, endPoint: .trailing)
    }

    var heroGradient: LinearGradient {
        isPro
            ? LinearGradient(
                colors: [Color(hex: "#2A1F44"), Color(hex: "#3B2A5E"), Color(hex: "#1E1533")],
                startPoint: .topLeading, endPoint: .bottomTrailing
              )
            : LinearGradient(
                colors: [.white, Color(hex: "#F8F4F8")],
                startPoint: .topLeading, endPoint: .bottomTrailing
              )
    }

    var bgGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#110A1F"), Color(hex: "#1B0F2E")], startPoint: .top, endPoint: .bottom)
            : LinearGradient(colors: [Color(hex: "#F3ECF3"), Color(hex: "#F0E8F0")], startPoint: .top, endPoint: .bottom)
    }

    // MARK: - Font sizes (Care mode uses larger)
    var bodySize: CGFloat { isCare ? 17 : 15 }
    var titleSize: CGFloat { isCare ? 28 : 24 }
    var captionSize: CGFloat { isCare ? 14 : 12 }
    var buttonSize: CGFloat { isCare ? 52 : 44 }

    // MARK: - Shadows
    func softShadow(color: Color? = nil, radius: CGFloat = 10, y: CGFloat = 4) -> (Color, CGFloat, CGFloat, CGFloat) {
        let c = color ?? (isPro ? Color.black.opacity(0.4) : Color.black.opacity(0.06))
        return (c, radius, 0, y)
    }
}

// MARK: - Clean Card Modifier
extension View {
    func card3D(_ theme: ThemeManager, radius: CGFloat = 22) -> some View {
        self
            .background {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(theme.cardColor)
                    .shadow(
                        color: theme.isPro ? Color.black.opacity(0.25) : Color.black.opacity(0.04),
                        radius: theme.isPro ? 12 : 6,
                        y: theme.isPro ? 6 : 2
                    )
            }
            .overlay {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .stroke(
                        theme.isPro ? theme.borderColor : theme.borderColor.opacity(0.3),
                        lineWidth: theme.isPro ? 1 : 0.5
                    )
                    .allowsHitTesting(false)
            }
    }
}
