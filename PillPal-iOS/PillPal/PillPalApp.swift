import SwiftUI

@main
struct PillPalApp: App {
    @State private var store = MedicationStore()
    @State private var themeManager = ThemeManager()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(store)
                .environment(themeManager)
                .environment(\.locale, Locale(identifier: store.appLanguage))
        }
    }
}
