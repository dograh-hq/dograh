import { Loader2, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface ScenarioFormProps {
    scenario: string;
    onScenarioChange: (scenario: string) => void;
    onStart: () => void;
    disabled: boolean;
    starting: boolean;
}

export function ScenarioForm({
    scenario,
    onScenarioChange,
    onStart,
    disabled,
    starting,
}: ScenarioFormProps) {
    return (
        <section className="space-y-3 rounded-xl border bg-card p-5 shadow-sm">
            <div className="space-y-1">
                <h2 className="text-lg font-semibold">Scenario</h2>
                <p className="text-sm text-muted-foreground">
                    Describe the service user, situation, and objective for this session.
                </p>
            </div>
            <Label htmlFor="scenario">Scenario instructions</Label>
            <Textarea
                id="scenario"
                value={scenario}
                onChange={(event) => onScenarioChange(event.target.value)}
                disabled={disabled}
                rows={9}
                placeholder="A service user presents with..."
                className="resize-y"
            />
            <Button
                type="button"
                onClick={onStart}
                disabled={disabled || starting || !scenario.trim()}
                className="w-full sm:w-auto"
            >
                {starting ? <Loader2 className="animate-spin" /> : <Play />}
                {starting ? "Starting..." : "Start Scenario"}
            </Button>
        </section>
    );
}

