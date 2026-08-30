import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CalmScoringPanel } from "./CalmScoringPanel";

describe("CalmScoringPanel", () => {
    it("renders previous-turn arrows, values, unchanged, and first-turn states", () => {
        render(
            <CalmScoringPanel
                analysis={{
                    calm_scores: {
                        hope: { score: 2.8, confidence: 0.91 },
                        anxiety: { score: 7.2, confidence: 0.88 },
                        trust: { score: 5.9, confidence: 0.87 },
                        loneliness: { score: 8.1, confidence: 0.94 },
                    },
                    trend: {
                        parameters: {
                            hope: { previous_score: 4, delta_previous: -1.2, direction: "down" },
                            anxiety: { previous_score: 6.4, delta_previous: 0.8, direction: "up" },
                            trust: { previous_score: 5.3, delta_previous: 0.6, direction: "up" },
                            loneliness: { previous_score: 8.1, delta_previous: 0, direction: "unchanged" },
                        },
                    },
                }}
            />,
        );
        expect(screen.getByLabelText("Previous-turn trend for hope: ↓ -1.2")).toBeTruthy();
        expect(screen.getByLabelText("Previous-turn trend for anxiety: ↑ +0.8")).toBeTruthy();
        expect(screen.getByLabelText("Previous-turn trend for trust: ↑ +0.6")).toBeTruthy();
        expect(screen.getByLabelText("Previous-turn trend for loneliness: =")).toBeTruthy();
    });

    it("renders unavailable movement for a first scored turn", () => {
        render(
            <CalmScoringPanel
                analysis={{
                    calm_scores: { hope: { score: 5, confidence: 0.62 } },
                    trend: { parameters: { hope: { direction: "insufficient_data" } } },
                }}
            />,
        );
        expect(screen.getByText("—")).toBeTruthy();
    });

    it("renders the engineered prompt sent to Sakinah", () => {
        render(
            <CalmScoringPanel
                analysis={{
                    calm_scores: { hope: { score: 2.8, confidence: 0.91 } },
                    prompt_sent_to_llm: "SAKINAH NEXT-TURN GENERATION CONTEXT\n\nRespond naturally as Sakinah.",
                }}
            />,
        );
        expect(screen.getByText("Engineered prompt sent to Sakinah")).toBeTruthy();
        expect(screen.getByText(/SAKINAH NEXT-TURN GENERATION CONTEXT/)).toBeTruthy();
    });
});
