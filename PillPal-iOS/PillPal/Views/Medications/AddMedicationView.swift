import SwiftUI

struct AddMedicationView: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme
    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var dosage = ""
    @State private var frequency: Frequency = .daily
    @State private var timeOfDay: TimeOfDay = .morning
    @State private var foodRelation: FoodRelation = .withFood
    @State private var notes = ""
    @State private var colorHex = PillOptions.colors[0]
    @State private var iconName = PillOptions.icons[0]
    @State private var saved = false

    var body: some View {
        NavigationStack {
            if saved {
                savedConfirmation
            } else {
                formContent
            }
        }
    }

    // MARK: - Success
    private var savedConfirmation: some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 64))
                .foregroundColor(theme.successColor)
                .symbolEffect(.bounce)

            Text("med_saved")
                .font(.system(size: 20, weight: .bold))
                .foregroundColor(theme.textColor)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(theme.bgGradient)
        .onAppear {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { dismiss() }
        }
    }

    // MARK: - Form
    private var formContent: some View {
        ScrollView {
            VStack(spacing: 20) {
                // Name
                field("name_label") {
                    TextField("name_placeholder", text: $name)
                        .font(.system(size: theme.bodySize))
                        .foregroundColor(theme.textColor)
                        .padding(14)
                        .background {
                            RoundedRectangle(cornerRadius: 12)
                                .fill(theme.surfaceColor)
                                .overlay { RoundedRectangle(cornerRadius: 12).stroke(theme.borderColor, lineWidth: 1) }
                        }
                }

                // Dosage
                field("dosage_label") {
                    TextField("dosage_placeholder", text: $dosage)
                        .font(.system(size: theme.bodySize))
                        .foregroundColor(theme.textColor)
                        .padding(14)
                        .background {
                            RoundedRectangle(cornerRadius: 12)
                                .fill(theme.surfaceColor)
                                .overlay { RoundedRectangle(cornerRadius: 12).stroke(theme.borderColor, lineWidth: 1) }
                        }
                }

                // Frequency
                field("freq_label") {
                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                        ForEach(Frequency.allCases, id: \.self) { freq in
                            chipButton(
                                LocalizedStringKey(freq.localizationKey),
                                isSelected: frequency == freq
                            ) { frequency = freq }
                        }
                    }
                }

                // Time of Day
                field("time_label") {
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 4), spacing: 8) {
                        ForEach(TimeOfDay.allCases, id: \.self) { time in
                            chipButton(
                                LocalizedStringKey(time.localizationKey),
                                isSelected: timeOfDay == time
                            ) { timeOfDay = time }
                        }
                    }
                }

                // Food Relation
                field("food_label_short") {
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 3), spacing: 8) {
                        ForEach(FoodRelation.allCases, id: \.self) { food in
                            chipButton(
                                LocalizedStringKey(food.localizationKey),
                                isSelected: foodRelation == food
                            ) { foodRelation = food }
                        }
                    }
                }

                // Color Picker
                field("color_label") {
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 5), spacing: 10) {
                        ForEach(PillOptions.colors, id: \.self) { hex in
                            Circle()
                                .fill(Color(hex: hex))
                                .frame(width: 36, height: 36)
                                .overlay {
                                    Circle().stroke(.white, lineWidth: colorHex == hex ? 3 : 0)
                                }
                                .scaleEffect(colorHex == hex ? 1.15 : 1)
                                .onTapGesture {
                                    withAnimation(.spring(response: 0.2)) { colorHex = hex }
                                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                                }
                        }
                    }
                }

                // Icon Picker
                field("icon_label") {
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 5), spacing: 10) {
                        ForEach(PillOptions.icons, id: \.self) { icon in
                            Image(systemName: icon)
                                .font(.system(size: 18))
                                .foregroundColor(iconName == icon ? Color(hex: colorHex) : theme.mutedColor)
                                .frame(width: 40, height: 40)
                                .background {
                                    RoundedRectangle(cornerRadius: 10)
                                        .fill(iconName == icon ? Color(hex: colorHex).opacity(0.12) : theme.surfaceColor)
                                        .overlay {
                                            RoundedRectangle(cornerRadius: 10)
                                                .stroke(iconName == icon ? Color(hex: colorHex) : theme.borderColor, lineWidth: 1)
                                        }
                                }
                                .onTapGesture {
                                    withAnimation(.spring(response: 0.2)) { iconName = icon }
                                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                                }
                        }
                    }
                }

                // Notes
                field("notes_label_opt") {
                    TextField("notes_placeholder", text: $notes, axis: .vertical)
                        .font(.system(size: theme.bodySize))
                        .foregroundColor(theme.textColor)
                        .lineLimit(2...4)
                        .padding(14)
                        .background {
                            RoundedRectangle(cornerRadius: 12)
                                .fill(theme.surfaceColor)
                                .overlay { RoundedRectangle(cornerRadius: 12).stroke(theme.borderColor, lineWidth: 1) }
                        }
                }

                // Save button
                Button {
                    saveMedication()
                } label: {
                    Text("save_med")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundColor(theme.textColor)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 16)
                        .background(theme.accentGradient, in: RoundedRectangle(cornerRadius: 16))
                }
                .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
                .opacity(name.trimmingCharacters(in: .whitespaces).isEmpty ? 0.4 : 1)
            }
            .padding(20)
            .padding(.bottom, 40)
        }
        .background(theme.bgGradient)
        .navigationTitle("add_title")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("cancel_btn") { dismiss() }
                    .foregroundColor(theme.mutedColor)
            }
        }
    }

    // MARK: - Helpers
    private func field(_ titleKey: LocalizedStringKey, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(titleKey)
                .font(.system(size: 13, weight: .medium))
                .foregroundColor(theme.textColor)
            content()
        }
    }

    private func chipButton(_ label: LocalizedStringKey, isSelected: Bool, action: @escaping () -> Void) -> some View {
        Button {
            withAnimation(.spring(response: 0.2)) { action() }
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        } label: {
            Text(label)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(isSelected ? theme.accentColor : theme.mutedColor)
                .padding(.vertical, 10)
                .frame(maxWidth: .infinity)
                .background {
                    RoundedRectangle(cornerRadius: 12)
                        .fill(isSelected ? theme.accentColor.opacity(0.1) : theme.cardColor)
                        .overlay {
                            RoundedRectangle(cornerRadius: 12)
                                .stroke(isSelected ? theme.accentColor : theme.borderColor, lineWidth: 1)
                        }
                }
        }
    }

    private func saveMedication() {
        let med = Medication(
            name: name.trimmingCharacters(in: .whitespaces),
            dosage: dosage.trimmingCharacters(in: .whitespaces),
            frequency: frequency,
            timeOfDay: timeOfDay,
            foodRelation: foodRelation,
            notes: notes.trimmingCharacters(in: .whitespaces),
            colorHex: colorHex,
            iconName: iconName
        )
        store.addMedication(med)
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        withAnimation { saved = true }
    }
}
