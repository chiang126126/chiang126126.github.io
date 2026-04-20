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
    // Care: warm cream / oatmeal / peach — healing & cozy
    // Pro: deep plum + neon — cyberpunk but still playful
    var accentColor: Color { isPro ? Color(hex: "#A78BFA") : Color(hex: "#D4962E") }
    var accentSecondary: Color { isPro ? Color(hex: "#F472B6") : Color(hex: "#FFB89A") }
    var bgColor: Color { isPro ? Color(hex: "#110A1F") : Color(hex: "#FFFDF8") }
    var bgSecondary: Color { isPro ? Color(hex: "#1B0F2E") : Color(hex: "#F9F7F1") }
    var cardColor: Color { isPro ? Color(hex: "#1E1533") : .white }
    var surfaceColor: Color { isPro ? Color(hex: "#2A1F44") : Color(hex: "#FFF8F2") }
    var borderColor: Color { isPro ? Color(hex: "#3B2A5E") : Color(hex: "#F0EBE3") }
    var textColor: Color { isPro ? .white : Color(hex: "#4F4B47") }
    var mutedColor: Color { isPro ? Color(hex: "#A78BFA").opacity(0.7) : Color(hex: "#8F8A84") }

    var warmYellow: Color { Color(hex: "#FFD76A") }
    var warmPeach: Color { Color(hex: "#FFB89A") }
    var warmMint: Color { Color(hex: "#BFE8D2") }

    // Legacy aliases for Pro mode components
    var neonOrange: Color { isPro ? Color(hex: "#FB923C") : Color(hex: "#FFB89A") }
    var neonPink: Color { isPro ? Color(hex: "#F472B6") : Color(hex: "#FFCCB6") }
    var neonPurple: Color { isPro ? Color(hex: "#A855F7") : Color(hex: "#D4962E") }

    var successColor: Color { isPro ? Color(hex: "#10B981") : Color(hex: "#7BC5A0") }
    var dangerColor: Color { Color(hex: "#EF4444") }

    // Pastels for cards / chips
    var pastelLavender: Color { isPro ? Color(hex: "#E9D5FF") : Color(hex: "#FFF7EC") }
    var pastelPink: Color { isPro ? Color(hex: "#FBCFE8") : Color(hex: "#FFCCB6") }
    var pastelCream: Color { Color(hex: "#FFF7EC") }
    var pastelMint: Color { Color(hex: "#BFE8D2") }
    var pastelSky: Color { isPro ? Color(hex: "#DBEAFE") : Color(hex: "#E8F5EE") }

    // MARK: - Gradients
    var accentGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#A78BFA"), Color(hex: "#F472B6")], startPoint: .topLeading, endPoint: .bottomTrailing)
            : LinearGradient(colors: [Color(hex: "#F6C85F"), Color(hex: "#FFB89A")], startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    var heroGradient: LinearGradient {
        isPro
            ? LinearGradient(
                colors: [Color(hex: "#2A1F44"), Color(hex: "#3B2A5E"), Color(hex: "#1E1533")],
                startPoint: .topLeading, endPoint: .bottomTrailing
              )
            : LinearGradient(
                colors: [Color(hex: "#FFFDF8"), Color(hex: "#FFF7EC"), Color(hex: "#F5FBF7")],
                startPoint: .topLeading, endPoint: .bottomTrailing
              )
    }

    var bgGradient: LinearGradient {
        isPro
            ? LinearGradient(colors: [Color(hex: "#110A1F"), Color(hex: "#1B0F2E")], startPoint: .top, endPoint: .bottom)
            : LinearGradient(colors: [Color(hex: "#FFFDF8"), Color(hex: "#FFF8F0"), Color(hex: "#F5FBF7")], startPoint: .top, endPoint: .bottom)
    }

    // MARK: - Font sizes (Care mode uses larger)
    var bodySize: CGFloat { isCare ? 17 : 15 }
    var titleSize: CGFloat { isCare ? 28 : 24 }
    var captionSize: CGFloat { isCare ? 14 : 12 }
    var buttonSize: CGFloat { isCare ? 52 : 44 }

    // MARK: - Shadows
    func softShadow(color: Color? = nil, radius: CGFloat = 10, y: CGFloat = 4) -> (Color, CGFloat, CGFloat, CGFloat) {
        let c = color ?? (isPro ? Color.black.opacity(0.4) : Color(hex: "#D4962E").opacity(0.1))
        return (c, radius, 0, y)
    }
}
