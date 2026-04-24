import SwiftUI

struct ContentView: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme

    var body: some View {
        ZStack {
            Group {
                if !store.onboardingDone {
                    OnboardingView()
                } else {
                    MainTabView()
                }
            }
            .preferredColorScheme(.light)

            // Level Up Overlay
            if store.showLevelUp {
                LevelUpOverlay(level: store.levelUpTo) {
                    withAnimation(.spring(response: 0.4)) {
                        store.showLevelUp = false
                    }
                }
                .transition(.scale.combined(with: .opacity))
                .zIndex(100)
            }
        }
        .animation(.spring(response: 0.3), value: store.showLevelUp)
        .onAppear {
            store.performDailyCheckIn()
        }
    }
}

struct MainTabView: View {
    @Environment(ThemeManager.self) private var theme
    @State private var selectedTab = 0

    var body: some View {
        ZStack(alignment: .bottom) {
            TabView(selection: $selectedTab) {
                DashboardView()
                    .tag(0)
                MedicationsListView()
                    .tag(1)
                ScanView()
                    .tag(2)
                StatsView()
                    .tag(3)
                SettingsView()
                    .tag(4)
            }
            .tabViewStyle(.automatic)
            .toolbar(.hidden, for: .tabBar)

            CustomTabBar(selectedTab: $selectedTab)
        }
        .ignoresSafeArea(.keyboard)
    }
}

// MARK: - Custom Tab Bar
struct CustomTabBar: View {
    @Binding var selectedTab: Int
    @Environment(ThemeManager.self) private var theme

    private let tabs: [(icon: String, label: LocalizedStringKey)] = [
        ("house.fill", "tab_home"),
        ("pill.fill", "tab_meds"),
        ("camera.viewfinder", "tab_scan"),
        ("chart.bar.fill", "tab_stats"),
        ("gearshape.fill", "tab_settings")
    ]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(0..<tabs.count, id: \.self) { index in
                if index == 2 {
                    // Center scan button - playful floating style
                    Button {
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.6)) {
                            selectedTab = index
                        }
                        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                    } label: {
                        ZStack {
                            Circle()
                                .fill(theme.buttonGradient)
                                .frame(width: 60, height: 60)
                                .shadow(color: theme.accentColor.opacity(0.35), radius: 12, y: 4)

                            Image(systemName: tabs[index].icon)
                                .font(.system(size: 24, weight: .bold))
                                .foregroundColor(.white)
                                .symbolEffect(.bounce, options: .repeat(.periodic(delay: 4.0)), isActive: selectedTab != 2)
                        }
                        .offset(y: -20)
                    }
                    .frame(maxWidth: .infinity)
                } else {
                    Button {
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
                            selectedTab = index
                        }
                        UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    } label: {
                        VStack(spacing: 3) {
                            Image(systemName: tabs[index].icon)
                                .font(.system(size: theme.isCare ? 22 : 20))
                                .foregroundColor(selectedTab == index ? theme.accentColor : theme.mutedColor)
                                .scaleEffect(selectedTab == index ? 1.15 : 1.0)

                            Text(tabs[index].label)
                                .font(.system(size: theme.isCare ? 11 : 9, weight: selectedTab == index ? .bold : .regular))
                                .foregroundColor(selectedTab == index ? theme.accentColor : theme.mutedColor)

                            if selectedTab == index {
                                Circle()
                                    .fill(theme.accentColor)
                                    .frame(width: 5, height: 5)
                                    .transition(.scale.combined(with: .opacity))
                            }
                        }
                        .animation(.spring(response: 0.3), value: selectedTab)
                    }
                    .frame(maxWidth: .infinity)
                }
            }
        }
        .padding(.top, 8)
        .padding(.bottom, 6)
        .background {
            Rectangle()
                .fill(theme.cardColor)
                .shadow(color: Color.black.opacity(0.05), radius: 12, y: -4)
                .ignoresSafeArea()
        }
    }
}

#Preview {
    ContentView()
        .environment(MedicationStore())
        .environment(ThemeManager())
}
