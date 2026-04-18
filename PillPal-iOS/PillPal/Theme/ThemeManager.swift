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
            self.mode = .care // default to the friendlier pastel experience
        }
    }

    private func save() {
        UserDefaults.standard.set(mode.rawValue, forKey: "pillpal-mode")
    }

    // Convenience
    var isPro: Bool { mode == .pro }
    var isCare: Bool { mode == .care }

    func toggle() {
        mode = isPro ? .care : .pro
    }

    // MARK: - Colors
    // Care (default): soft lavender / cream / pink — cute & reassuring
    // Pro: deep plum + neon — cyberpunk but still playful
    var accentColor: Color { isPro ? Color(hex: "#A78BFA") : Color(hex: "#8B5CF6") }
    var accentSecondary: Color { isPro ? Color(hex: "#F472B6") : Color(hex: "#EC4899") }
    var bgColor: Color { isPro ? Color(hex: "#110A1F") : Color(hex: "#FFF7F2") }
    var bgSecondary: Color { isPro ? Color(hex: "#1B0F2E") : Color(hex: "#FDE8EC") }
    var cardColor: Color { isPro ? Color(hex: "#1E1533") : .white }
    var surfaceColor: Color { isPro ? Color(hex: "#2A1F44") : Color(hex: "#FFF0F5") }
    var borderColor: Color { isPro ? Color(hex: "#3B2A5E") : Color(hex: "#F3E7EC") }
    var textColor: Color { isPro ? .white : Color(hex: "#3B2A4F") }
    var mutedColor: Color { isPro ? Color(hex: "#A78BFA").opacity(0.7) : Color(hex: "#8A7494") }

    var neonOrange: Color { Color(hex: "#FB923C") }
    var neonPink: Color { Color(hex: "#F472B6") }
    var neonPurple: Color { Color(hex: "#A855F7") }
    var successColor: Color { Color(hex: "#10B981") }
    var dangerColor: Color { Color(hex: "#EF4444") }

    // Pastels for cards / chips
    var pastelLavender: Color { Color(hex: "#E9D5FF") }
    var pastelPink: Color { Color(hex: "#FBCFE8") }
    var pastelCream: Color { Color(hex: "#FEF3C7") }
    var pastelMint: Color { Color(hex: "#D1FAE5") }
    var pastelSky: Color { Color(hex: "#DBEAFE") }

    // MARK: - Gradients
    var accentGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#A78BFA"), Color(hex: "#F472B6")], startPoint: .topLeading, endPoint: .bottomTrailing)
            : LinearGradient(colors: [Color(hex: "#A78BFA"), Color(hex: "#F0ABFC")], startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    var heroGradient: LinearGradient {
        isPro
            ? LinearGradient(
                colors: [Color(hex: "#2A1F44"), Color(hex: "#3B2A5E"), Color(hex: "#1E1533")],
                startPoint: .topLeading, endPoint: .bottomTrailing
              )
            : LinearGradient(
                colors: [Color(hex: "#FFE8E0"), Color(hex: "#E9D5FF"), Color(hex: "#DBEAFE")],
                startPoint: .topLeading, endPoint: .bottomTrailing
              )
    }

    var bgGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#110A1F"), Color(hex: "#1B0F2E")], startPoint: .top, endPoint: .bottom)
            : LinearGradient(colors: [Color(hex: "#FFF7F2"), Color(hex: "#FDE8EC"), Color(hex: "#F5E6FF")], startPoint: .top, endPoint: .bottom)
    }

    // MARK: - Font sizes (Care mode uses larger)
    var bodySize: CGFloat { isCare ? 17 : 15 }
    var titleSize: CGFloat { isCare ? 28 : 24 }
    var captionSize: CGFloat { isCare ? 14 : 12 }
    var buttonSize: CGFloat { isCare ? 52 : 44 }

    // MARK: - Shadows
    func softShadow(color: Color? = nil, radius: CGFloat = 10, y: CGFloat = 4) -> (Color, CGFloat, CGFloat, CGFloat) {
        let c = color ?? (isPro ? Color.black.opacity(0.4) : Color(hex: "#8B5CF6").opacity(0.12))
        return (c, radius, 0, y)
    }
}
