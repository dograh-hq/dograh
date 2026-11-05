/**
 * Client-side Jinja2 template validator and tokenizer.
 * Validates balanced tags, unclosed blocks, well-formed syntax,
 * and detects illogical/malformed conditions.
 */

export interface JinjaValidationResult {
    isValid: boolean;
    error: string | null;
}

/**
 * Validates the expression inside an `if`, `elif`, or `for` block.
 * Catches common mistakes like single `=`, `&&`/`||`, dangling operators, or missing quotes.
 */
function validateBlockExpression(tag: string, expr: string): string | null {
    const trimmed = expr.trim();

    if (tag === "if" || tag === "elif") {
        if (!trimmed) {
            return `Missing condition expression in '{% ${tag} %}'`;
        }
        if (trimmed.includes("&&")) {
            return `Use 'and' instead of '&&' in Jinja condition: '{% ${tag} ${trimmed} %}'`;
        }
        if (trimmed.includes("||")) {
            return `Use 'or' instead of '||' in Jinja condition: '{% ${tag} ${trimmed} %}'`;
        }
        // Single '=' assignment check (must not be preceded/followed by '=', '>', '<', '!')
        if (/(?<![=><!])=(?![=~])/.test(trimmed)) {
            return `Use '==' for comparison instead of single '=' in '{% ${tag} ${trimmed} %}'`;
        }
        // Dangling comparison at end: >, <, >=, <=, ==, !=
        if (/(?:>|<|>=|<=|==|!=)\s*$/.test(trimmed)) {
            return `Incomplete condition in '{% ${tag} ${trimmed} %}' — missing value after operator`;
        }
        // Dangling comparison at start
        if (/^\s*(?:>|<|>=|<=|==|!=)/.test(trimmed)) {
            return `Incomplete condition in '{% ${tag} ${trimmed} %}' — missing variable before operator`;
        }
        // Unterminated quotes (strip valid double- and single-quoted strings first)
        const strippedQuotes = trimmed
            .replace(/"(?:[^"\\]|\\.)*"/g, "")
            .replace(/'(?:[^'\\]|\\.)*'/g, "");
        if (strippedQuotes.includes('"') || strippedQuotes.includes("'")) {
            return `Unterminated string quote in '{% ${tag} ${trimmed} %}'`;
        }
    }

    if (tag === "for") {
        if (!trimmed) {
            return "Missing loop expression in '{% for %}'";
        }
        if (!/^[A-Za-z_][A-Za-z0-9_]*(\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*\s+in\s+/.test(trimmed)) {
            return `Invalid loop syntax '{% for ${trimmed} %}'. Expected format: '{% for item in items %}'`;
        }
        const rangeMatch = trimmed.match(/\brange\s*\(\s*(\d+)\s*\)/);
        if (rangeMatch && parseInt(rangeMatch[1], 10) > 50) {
            return `Loop range exceeds maximum limit of 50 for voice prompts: 'range(${rangeMatch[1]})'`;
        }
    }

    if (tag === "set") {
        if (!trimmed) {
            return "Missing variable assignment in '{% set %}'";
        }
        if (trimmed.includes("=")) {
            const [varPart, ...valParts] = trimmed.split("=");
            const varName = varPart.trim();
            const valExpr = valParts.join("=").trim();
            const isValidTarget = varName.split(",").every((target) => {
                const cleaned = target.trim();
                return /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$/.test(cleaned);
            });
            if (!isValidTarget) {
                return `Invalid variable assignment target in '{% set ${trimmed} %}'`;
            }
            if (!valExpr) {
                return `Missing value after '=' in '{% set ${trimmed} %}'`;
            }
        }
    }

    return null;
}

export function validateJinjaSyntax(template: string | null | undefined): JinjaValidationResult {
    if (!template || typeof template !== "string") {
        return { isValid: true, error: null };
    }

    // Fast check: if no Jinja delimiters exist, template is valid
    if (!template.includes("{%") && !template.includes("{{")) {
        return { isValid: true, error: null };
    }

    // 1. Check for unclosed tag delimiters: {% without %}
    let inBlock = false;
    let inVar = false;
    const len = template.length;

    for (let i = 0; i < len - 1; i++) {
        const pair = template.slice(i, i + 2);
        if (pair === "{%" && !inBlock && !inVar) {
            inBlock = true;
            i++;
        } else if (pair === "%}" && inBlock) {
            inBlock = false;
            i++;
        } else if (pair === "{{" && !inVar && !inBlock) {
            inVar = true;
            i++;
        } else if (pair === "}}" && inVar) {
            inVar = false;
            i++;
        }
    }

    if (inBlock) {
        return {
            isValid: false,
            error: "Unclosed Jinja block delimiter '{%' — missing closing '%}'",
        };
    }

    if (inVar) {
        return {
            isValid: false,
            error: "Unclosed variable delimiter '{{' — missing closing '}}'",
        };
    }

    // 2. Security & Policy: Block system attributes, reflection functions, file inclusion, and CPU hangs
    const jinjaSegments = template.match(/(\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\})/g) || [];
    for (const seg of jinjaSegments) {
        if (seg.includes("**")) {
            return {
                isValid: false,
                error: "Exponentiation ('**') is not permitted in prompt templates.",
            };
        }
    }

    const disallowedTagMatch = template.match(/\{%-?\s*(include|import|from|extends|macro|call)\b/);
    if (disallowedTagMatch) {
        return {
            isValid: false,
            error: `Tag '{% ${disallowedTagMatch[1]} %}' is not permitted in prompt templates.`,
        };
    }

    const privateAttrMatch = template.match(/\.\s*(_[A-Za-z0-9_]+|format|format_map|mro|subclasses)\b/);
    if (privateAttrMatch) {
        return {
            isValid: false,
            error: `Accessing system attribute '${privateAttrMatch[1]}' is not permitted in templates.`,
        };
    }

    const indirectAttrMatch = template.match(/\|\s*attr\s*\(\s*["'](_[A-Za-z0-9_]+|format|format_map|mro|subclasses)["']\s*\)|\[\s*["'](_[A-Za-z0-9_]+)["']\s*\]/);
    if (indirectAttrMatch) {
        return {
            isValid: false,
            error: `Accessing system attribute '${indirectAttrMatch[1] || indirectAttrMatch[2]}' is not permitted in templates.`,
        };
    }

    const forbiddenFuncMatch = template.match(/\b(eval|exec|open|compile|__import__|lipsum|cycler|joiner)\s*\(/);
    if (forbiddenFuncMatch) {
        return {
            isValid: false,
            error: `Function '${forbiddenFuncMatch[1]}' is not permitted in templates.`,
        };
    }

    const mulMatches = template.matchAll(/(?:["'][^"']*["']\s*\*\s*(\d+)|\b(\d+)\s*\*\s*["'][^"']*["'])/g);
    for (const m of mulMatches) {
        const factor = parseInt(m[1] || m[2], 10);
        if (factor > 500) {
            return {
                isValid: false,
                error: `String multiplication factor exceeds maximum allowable limit of 500: '${factor}'`,
            };
        }
    }

    // 3. Tokenize and check balanced control blocks (if/endif, for/endfor) + expressions
    const blockRegex = /\{%-?\s*(if\b|elif\b|else\b|endif\b|for\b|endfor\b|set\b)([\s\S]*?)-?%\}/g;
    const blockStack: { tag: "if" | "for"; index: number }[] = [];
    let match: RegExpExecArray | null;

    while ((match = blockRegex.exec(template)) !== null) {
        const tag = match[1];
        const expr = match[2] || "";

        // Validate internal expression for logical and syntax errors
        const exprError = validateBlockExpression(tag, expr);
        if (exprError) {
            return {
                isValid: false,
                error: exprError,
            };
        }

        if (tag === "if") {
            blockStack.push({ tag: "if", index: match.index });
        } else if (tag === "elif") {
            const current = blockStack[blockStack.length - 1];
            if (!current || current.tag !== "if") {
                return {
                    isValid: false,
                    error: `Found '{% ${tag} %}' without a matching '{% if %}' block`,
                };
            }
        } else if (tag === "else") {
            const current = blockStack[blockStack.length - 1];
            if (!current || (current.tag !== "if" && current.tag !== "for")) {
                return {
                    isValid: false,
                    error: `Found '{% ${tag} %}' without a matching '{% if %}' or '{% for %}' block`,
                };
            }
        } else if (tag === "endif") {
            const current = blockStack.pop();
            if (!current || current.tag !== "if") {
                return {
                    isValid: false,
                    error: "Found '{% endif %}' without a matching '{% if %}' block",
                };
            }
        } else if (tag === "for") {
            if (blockStack.some((b) => b.tag === "for")) {
                return {
                    isValid: false,
                    error: "Nested '{% for %}' loops are not permitted in prompt templates.",
                };
            }
            blockStack.push({ tag: "for", index: match.index });
        } else if (tag === "endfor") {
            const current = blockStack.pop();
            if (!current || current.tag !== "for") {
                return {
                    isValid: false,
                    error: "Found '{% endfor %}' without a matching '{% for %}' block",
                };
            }
        }
    }

    if (blockStack.length > 0) {
        const unclosed = blockStack[blockStack.length - 1];
        if (unclosed.tag === "if") {
            return {
                isValid: false,
                error: "Unclosed '{% if %}' block — please close it with '{% endif %}'",
            };
        }
        if (unclosed.tag === "for") {
            return {
                isValid: false,
                error: "Unclosed '{% for %}' block — please close it with '{% endfor %}'",
            };
        }
    }

    return { isValid: true, error: null };
}
