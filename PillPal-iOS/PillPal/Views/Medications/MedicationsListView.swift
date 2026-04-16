import SwiftUI

struct MedicationsListView: View {
    @Environment(MedicationStore.self) private var store
    @Environment(ThemeManager.self) private var theme
    @State private var expandedId: UUID?
    @State private var deleteConfirmId: UUID?
    @State private var showAddSheet = false

    private var active: [Medication] { store.medications.filter(\.isActive) }
    private var paused: [Medication] { store.medications.filter { !$0.isActive } }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                // Header
                HStack {
                    Text("my_medications")
                        .font(.system(size: theme.titleSize, weight: .bold))
                        .foregroundColor(theme.textColor)
                    Spacer()
                    Button {
                        showAddSheet = true
                        UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "plus")
                                .font(.system(size: 12, weight: .bold))
                            Text("add_new")
                                .font(.system(size: 13, weight: .semibold))
                        }
                        .foregroundColor(theme.isPro ? .black : .white)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 8)
                        .background(theme.accentGradient, in: RoundedRectangle(cornerRadius: 12))
                    }
                }
                .padding(.top, 8)

                // Active
                if !active.isEmpty {
                    sectionHeader("active_label", count: active.count)
                    ForEach(active) { med in
                        medicationCard(med)
                    }
                }

                // Paused
                if !paused.isEmpty {
                    sectionHeader("paused_label", count: paused.count)
                    ForEach(paused) { med in
                        medicationCard(med)
                    }
                }

                // Empty
                if store.medications.isEmpty {
                    emptyState
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 100)
        }
        .background(theme.bgColor.ignoresSafeArea())
        .sheet(isPresented: $showAddSheet) {
            AddMedicationView()
        }
    }

    // MARK: - Section Header
    private func sectionHeader(_ key: LocalizedStringKey, count: Int) -> some View {
        HStack {
            Text(key)
                .font(.system(size: 11, weight: .medium))
                .textCase(.uppercase)
                .tracking(1)
                .foregroundColor(theme.mutedColor)
            Text("(\(count))")
                .font(.system(size: 11))
                .foregroundColor(theme.mutedColor)
            Spacer()
        }
    }

    // MARK: - Medication Card
    private func medicationCard(_ med: Medication) -> some View {
        let isExpanded = expandedId == med.id

        return VStack(spacing: 0) {
            // Main row
            Button {
                withAnimation(.spring(response: 0.3)) {
                    expandedId = isExpanded ? nil : med.id
                }
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
            } label: {
                HStack(spacing: 12) {
                    // Icon
                    Image(systemName: med.iconName)
                        .font(.system(size: 20))
                        .foregroundColor(med.color)
                        .frame(width: 44, height: 44)
                        .background {
                            RoundedRectangle(cornerRadius: 12)
                                .fill(med.color.opacity(0.12))
                                .overlay {
                                    RoundedRectangle(cornerRadius: 12)
                                        .stroke(med.color.opacity(0.25), lineWidth: 1.5)
                                }
                        }

                    VStack(alignment: .leading, spacing: 2) {
                        Text(med.name)
                            .font(.system(size: theme.bodySize, weight: .semibold))
                            .foregroundColor(theme.textColor)
                            .lineLimit(1)

                        HStack(spacing: 4) {
                            Text(med.dosage)
                            Text("·")
                            Text(LocalizedStringKey(med.frequency.localizationKey))
                        }
                        .font(.system(size: theme.captionSize))
                        .foregroundColor(theme.mutedColor)
                    }

                    Spacer()

                    Image(systemName: "chevron.right")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(theme.mutedColor)
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))
                }
                .padding(14)
            }
            .buttonStyle(.plain)

            // Expanded detail
            if isExpanded {
                VStack(spacing: 12) {
                    Divider().overlay(theme.borderColor)

                    // Detail grid
                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                        detailItem("time_label", value: LocalizedStringKey(med.timeOfDay.localizationKey))
                        detailItem("freq_label", value: LocalizedStringKey(med.frequency.localizationKey))
                        detailItem("food_label_short", value: LocalizedStringKey(med.foodRelation.localizationKey))
                        if !med.notes.isEmpty {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("notes_label")
                                    .font(.system(size: 10))
                                    .foregroundColor(theme.mutedColor)
                                Text(med.notes)
                                    .font(.system(size: theme.captionSize))
                                    .foregroundColor(theme.textColor)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }

                    // Actions
                    HStack(spacing: 8) {
                        Button {
                            store.toggleActive(med.id)
                            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                        } label: {
                            HStack(spacing: 4) {
                                Image(systemName: med.isActive ? "pause.fill" : "play.fill")
                                    .font(.system(size: 12))
                                Text(med.isActive ? "pause_med" : "resume_med")
                                    .font(.system(size: 13, weight: .medium))
                            }
                            .foregroundColor(theme.textColor)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 10)
                            .background {
                                RoundedRectangle(cornerRadius: 12)
                                    .fill(theme.surfaceColor)
                                    .overlay { RoundedRectangle(cornerRadius: 12).stroke(theme.borderColor, lineWidth: 1) }
                            }
                        }

                        Button {
                            if deleteConfirmId == med.id {
                                store.deleteMedication(med.id)
                                deleteConfirmId = nil
                                UINotificationFeedbackGenerator().notificationOccurred(.warning)
                            } else {
                                deleteConfirmId = med.id
                                UIImpactFeedbackGenerator(style: .light).impactOccurred()
                                DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                                    deleteConfirmId = nil
                                }
                            }
                        } label: {
                            Image(systemName: "trash.fill")
                                .font(.system(size: 13))
                                .foregroundColor(.white)
                                .padding(.vertical, 10)
                                .padding(.horizontal, 16)
                                .background {
                                    RoundedRectangle(cornerRadius: 12)
                                        .fill(deleteConfirmId == med.id ? theme.dangerColor : theme.dangerColor.opacity(0.8))
                                }
                        }
                    }
                }
                .padding(.horizontal, 14)
                .padding(.bottom, 14)
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .background {
            RoundedRectangle(cornerRadius: 16)
                .fill(theme.cardColor)
                .overlay { RoundedRectangle(cornerRadius: 16).stroke(theme.borderColor, lineWidth: 1) }
        }
        .opacity(med.isActive ? 1 : 0.6)
        .animation(.spring(response: 0.3), value: isExpanded)
    }

    private func detailItem(_ key: LocalizedStringKey, value: LocalizedStringKey) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(key)
                .font(.system(size: 10))
                .foregroundColor(theme.mutedColor)
            Text(value)
                .font(.system(size: theme.captionSize, weight: .medium))
                .foregroundColor(theme.textColor)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Empty State
    private var emptyState: some View {
        VStack(spacing: 12) {
            Text(Emoji.pill)
                .font(.system(size: 56))
                .phaseAnimator([false, true]) { content, phase in
                    content.rotationEffect(.degrees(phase ? 10 : -10))
                } animation: { _ in .easeInOut(duration: 1.5).repeatForever(autoreverses: true) }

            Text("no_meds")
                .font(.system(size: theme.bodySize, weight: .semibold))
                .foregroundColor(theme.textColor)

            Text("add_first")
                .font(.system(size: theme.captionSize))
                .foregroundColor(theme.mutedColor)
                .multilineTextAlignment(.center)

            Button {
                showAddSheet = true
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "plus")
                    Text("add_new")
                }
                .font(.system(size: 15, weight: .semibold))
                .foregroundColor(theme.isPro ? .black : .white)
                .padding(.horizontal, 24)
                .padding(.vertical, 12)
                .background(theme.accentGradient, in: RoundedRectangle(cornerRadius: 16))
            }
            .padding(.top, 8)
        }
        .padding(.vertical, 60)
    }
}
