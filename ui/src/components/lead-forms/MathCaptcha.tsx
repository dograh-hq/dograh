"use client";

import { useEffect, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface MathCaptchaProps {
  // Called whenever validity changes, so the parent can enable/disable submit.
  onValidChange: (valid: boolean) => void;
  id?: string;
}

// Dead-simple anti-spam: "What is X + Y?". Generated client-side on mount.
// Math.random is allowed in browser runtime (this is not a workflow script).
export function MathCaptcha({ onValidChange, id = "math-captcha" }: MathCaptchaProps) {
  const [a, setA] = useState(0);
  const [b, setB] = useState(0);
  const [answer, setAnswer] = useState("");

  useEffect(() => {
    setA(Math.floor(Math.random() * 8) + 1);
    setB(Math.floor(Math.random() * 8) + 1);
  }, []);

  useEffect(() => {
    onValidChange(answer.trim() !== "" && parseInt(answer, 10) === a + b);
  }, [answer, a, b, onValidChange]);

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>
        Quick check: what is {a} + {b}?
      </Label>
      <Input
        id={id}
        inputMode="numeric"
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        placeholder="Answer"
        className="w-32"
      />
    </div>
  );
}
