import SwiftUI

struct SettingsView: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme
    @State private var showClearConfirm = false
    @State private var showLangPicker = false
    @State private var showRestartHint = false

    private let languages: [(code: String, label: String, flag: String)] = [
        ("en", "English", "EN"),
        ("zh-Hans", "\u{4E2D}\u{6587}", "CN"),
        ("fr", "Fran\u{00E7}ais", "FR"),
    ]

    private var currentLangLabel: String {
        languages.first(where: { $0.code == store.appLanguage })?.label ?? "English"
    }

    private var currentFlag: String {
        languages.first(where: { $0.code == store.appLanguage })?.flag ?? "EN"
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                Text("settings_title")
                    .font(.system(size: theme.titleSize, weight: .bold, design: .rounded))
                    .foregroundColor(theme.textColor)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 8)

                // Premium banner
                premiumBanner

                // Appearance
                settingsSection("appearance_title") {
                    HStack(spacing: 10) {
                        modeCard(.pro)
                        modeCard(.care)
                    }
                    .padding(14)
                }

                // Language
                settingsSection("language_title") {
                    if showLangPicker {
                        VStack(spacing: 4) {
                            ForEach(languages, id: \.code) { lang in
                                Button {
                                    store.setAppLanguage(lang.code)
                                    showLangPicker = false
                                    showRestartHint = true
                                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                                    DispatchQueue.main.asyncAfter(deadline: .now() + 3) { showRestartHint = false }
                                } label: {
                                    HStack(spacing: 10) {
                                        Text(lang.flag)
                                            .font(.system(size: 11, weight: .heavy, design: .rounded))
                                            .foregroundColor(theme.textColor)
                                            .frame(width: 30, height: 22)
                                            .background(theme.accentColor.opacity(0.15), in: RoundedRectangle(cornerRadius: 5))
                                        Text(lang.label)
                                            .font(.system(size: theme.bodySize, weight: .medium, design: .rounded))
                                            .foregroundColor(theme.textColor)
                                        Spacer()
                                        if store.appLanguage == lang.code {
                                            Image(systemName: "checkmark.circle.fill")
                                                .foregroundColor(theme.successColor)
                                        }
                                    }
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 10)
                                    .background(
                                        RoundedRectangle(cornerRadius: 10)
                                            .fill(store.appLanguage == lang.code ? theme.accentColor.opacity(0.08) : theme.surfaceColor.opacity(0.5))
                                    )
                                }
                            }
                            if showRestartHint {
                                Text("lang_restart_hint")
                                    .font(.system(size: 11, design: .rounded))
                                    .foregroundColor(theme.neonOrange)
                                    .padding(.top, 4)
                            }
                        }
                        .padding(10)
                    } else {
                        settingsRow(icon: "globe", title: "language_title", value: "\(currentFlag) \(currentLangLabel)") {
                            withAnimation(.spring(response: 0.3)) { showLangPicker = true }
                        }
                    }
                }

                // Reminder style
                settingsSection("notifications_title") {
                    VStack(spacing: 8) {
                        Text("reminder_style")
                            .font(.system(size: 13, weight: .medium, design: .rounded))
                            .foregroundColor(theme.textColor)
                            .frame(maxWidth: .infinity, alignment: .leading)

                        HStack(spacing: 8) {
                            reminderChip("flame.fill", "style_sassy", "sassy")
                            reminderChip("heart.fill", "style_gentle", "gentle")
                            reminderChip("bell.fill", "style_neutral", "neutral")
                        }
                    }
                    .padding(14)
                }

                // Data
                settingsSection("data_title") {
                    settingsRow(icon: "square.and.arrow.down", title: "export_data") {
                        exportData()
                    }
                    Divider().overlay(theme.borderColor)
                    settingsRow(icon: "trash", title: LocalizedStringKey(showClearConfirm ? "clear_confirm" : "clear_data"), danger: true) {
                        if showClearConfirm {
                            store.clearAllData()
                            showClearConfirm = false
                            UINotificationFeedbackGenerator().notificationOccurred(.warning)
                        } else {
                            showClearConfirm = true
                            UIImpactFeedbackGenerator(style: .light).impactOccurred()
                            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { showClearConfirm = false }
                        }
                    }
                }

                // About
                settingsSection("about_title") {
                    settingsRow(icon: "info.circle", title: "version_label", value: "1.0.0") {}
                    Divider().overlay(theme.borderColor)
                    settingsRow(icon: "shield", title: "privacy_label") {}
                    Divider().overlay(theme.borderColor)
                    settingsRow(icon: "doc.text", title: "terms_label") {}
                }

                Text("made_with_love")
                    .font(.system(size: 11, design: .rounded))
                    .foregroundColor(theme.mutedColor)
                    .padding(.top, 8)
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 100)
        }
        .background(theme.bgGradient.ignoresSafeArea())
    }

    // MARK: - Premium Banner (7-day free trial)
    private var premiumBanner: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                ZStack {
                    RoundedRectangle(cornerRadius: 14)
                        .fill(theme.isPro ? theme.neonPurple.opacity(0.15) : Color(hex: "#F59E0B").opacity(0.15))
                        .frame(width: 48, height: 48)
                    Image(systemName: "crown.fill")
                        .font(.system(size: 22))
                        .foregroundColor(theme.isPro ? theme.neonPurple : Color(hex: "#F59E0B"))
                }

                VStack(alignment: .leading, spacing: 2) {
                    Text("upgrade_title")
                        .font(.system(size: theme.bodySize, weight: .bold, design: .rounded))
                        .foregroundColor(theme.textColor)
                    Text("upgrade_desc")
                        .font(.system(size: theme.captionSize, design: .rounded))
                        .foregroundColor(theme.mutedColor)
                }
            }

            // 7-day free trial + pricing
            HStack(spacing: 8) {
                HStack(spacing: 4) {
                    Image(systemName: "gift.fill")
                        .font(.system(size: 11))
                    Text("free_trial_badge")
                }
                .font(.system(size: 11, weight: .bold, design: .rounded))
                .foregroundColor(.white)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(theme.successColor, in: Capsule())

                Text("price_yearly")
                    .font(.system(size: 11, weight: .medium, design: .rounded))
                    .foregroundColor(theme.accentColor)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 5)
                    .background(theme.accentColor.opacity(0.1), in: Capsule())

                Image(systemName: "sparkles")
                    .font(.system(size: 12))
                    .foregroundColor(theme.accentColor)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .card3D(theme, radius: 16)
    }

    // MARK: - Mode Card
    private func modeCard(_ m: AppMode) -> some View {
        let isSelected = theme.mode == m
        let isPro = m == .pro
        let previewBg = isPro ? Color(hex: "#110A1F") : Color(hex: "#F3ECF3")
        let previewAccent = isPro ? Color(hex: "#A78BFA") : Color(hex: "#8AAD28")
        let previewLabel = isPro ? Color.white : Color(hex: "#222222")
        return Button {
            withAnimation(.spring(response: 0.3)) { theme.mode = m }
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Image(systemName: isPro ? "moon.stars.fill" : "sun.max.fill")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(isSelected ? previewAccent : .gray)
                    Text(LocalizedStringKey(isPro ? "mode_pro" : "mode_care"))
                        .font(.system(size: 13, weight: .semibold, design: .rounded))
                        .foregroundColor(isSelected ? previewLabel : .gray)
                }
                Text(LocalizedStringKey(isPro ? "mode_pro_desc" : "mode_care_desc"))
                    .font(.system(size: 9, design: .rounded))
                    .foregroundColor(.gray)
                HStack(spacing: 3) {
                    let colors = isPro
                        ? ["#A78BFA", "#F472B6", "#3B2A5E"]
                        : ["#C0D840", "#E8B0E0", "#C0E8FF"]
                    ForEach(colors, id: \.self) { c in
                        Circle().fill(Color(hex: c)).frame(width: 12, height: 12)
                            .overlay { Circle().stroke(Color.white.opacity(0.3), lineWidth: 0.5) }
                    }
                }
                .padding(.top, 2)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(previewBg)
                    .overlay {
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .stroke(isSelected ? previewAccent : .clear, lineWidth: 2.5)
                    }
                    .shadow(color: isSelected ? previewAccent.opacity(0.25) : .clear, radius: 8, y: 2)
            }
        }
        .buttonStyle(.plain)
    }

    // MARK: - Reminder Chip
    private func reminderChip(_ sfSymbol: String, _ key: LocalizedStringKey, _ style: String) -> some View {
        Button {
            store.setReminderStyle(style)
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        } label: {
            VStack(spacing: 4) {
                Image(systemName: sfSymbol)
                    .font(.system(size: 16, weight: .semibold))
                Text(key).font(.system(size: 10, weight: .medium, design: .rounded))
            }
            .foregroundColor(store.reminderStyle == style ? theme.accentColor : theme.mutedColor)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)
            .background {
                RoundedRectangle(cornerRadius: 10)
                    .fill(store.reminderStyle == style ? theme.accentColor.opacity(0.1) : .clear)
                    .overlay {
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(store.reminderStyle == style ? theme.accentColor : theme.borderColor, lineWidth: 1)
                    }
            }
        }
    }

    // MARK: - Helpers
    private func settingsSection(_ titleKey: LocalizedStringKey, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(titleKey)
                .font(.system(size: 11, weight: .medium, design: .rounded))
                .textCase(.uppercase)
                .tracking(1)
                .foregroundColor(theme.mutedColor)
                .padding(.leading, 4)
            VStack(spacing: 0) { content() }
            .card3D(theme, radius: 16)
        }
    }

    private func settingsRow(icon: String, title: LocalizedStringKey, value: String? = nil, danger: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: icon)
                    .font(.system(size: 15))
                    .foregroundColor(danger ? theme.dangerColor : theme.mutedColor)
                    .frame(width: 30, height: 30)
                    .background((danger ? theme.dangerColor : theme.accentColor).opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
                Text(title)
                    .font(.system(size: theme.bodySize, design: .rounded))
                    .foregroundColor(danger ? theme.dangerColor : theme.textColor)
                Spacer()
                if let value {
                    Text(value)
                        .font(.system(size: theme.captionSize, design: .rounded))
                        .foregroundColor(theme.mutedColor)
                }
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(theme.mutedColor)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
        }
        .buttonStyle(.plain)
    }

    private func exportData() {
        guard let data = try? JSONEncoder().encode(store.medications) else { return }
        guard let json = String(data: data, encoding: .utf8) else { return }
        let av = UIActivityViewController(activityItems: [json], applicationActivities: nil)
        if let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
           let vc = scene.windows.first?.rootViewController {
            vc.present(av, animated: true)
        }
    }
}
