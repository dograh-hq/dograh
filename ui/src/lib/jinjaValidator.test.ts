import { describe, expect, it } from "vitest";

import { validateJinjaSyntax } from "./jinjaValidator";

describe("validateJinjaSyntax", () => {
    it("returns valid for simple plain text", () => {
        const result = validateJinjaSyntax("Hello world, this is a prompt.");
        expect(result.isValid).toBe(true);
        expect(result.error).toBeNull();
    });

    it("returns valid for closed variables", () => {
        const result = validateJinjaSyntax("Hello {{ name }}, balance is {{ balance }}.");
        expect(result.isValid).toBe(true);
        expect(result.error).toBeNull();
    });

    it("returns invalid for unclosed variable brackets", () => {
        const result = validateJinjaSyntax("Hello {{ name, how are you?");
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("Unclosed variable delimiter");
    });

    it("returns valid for balanced if/else/endif", () => {
        const template = `
            {% if is_vip %}
                Offer 20% discount.
            {% elif is_loyal %}
                Offer 10% discount.
            {% else %}
                Standard rate.
            {% endif %}
        `;
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(true);
        expect(result.error).toBeNull();
    });

    it("returns invalid for unclosed if block", () => {
        const template = `
            {% if is_vip %}
                Offer 20% discount.
        `;
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("Unclosed '{% if %}' block");
    });

    it("returns invalid for unexpected endif", () => {
        const template = "Hello world {% endif %}";
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("Found '{% endif %}' without a matching '{% if %}'");
    });

    it("returns valid for balanced for/endfor loops", () => {
        const template = `
            {% for order in orders %}
                - {{ order.item }}
            {% endfor %}
        `;
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(true);
        expect(result.error).toBeNull();
    });

    it("returns valid for tuple loop unpacking with and without parentheses", () => {
        const parenthesized = `
            {% for (k, v) in pairs %}
                {{ k }}: {{ v }}
            {% endfor %}
        `;
        const resParen = validateJinjaSyntax(parenthesized);
        expect(resParen.isValid).toBe(true);
        expect(resParen.error).toBeNull();

        const withSpaces = "{% for ( k , v ) in pairs %}{{ k }}{% endfor %}";
        expect(validateJinjaSyntax(withSpaces).isValid).toBe(true);

        const unparenthesized = `
            {% for k, v in pairs %}
                {{ k }}: {{ v }}
            {% endfor %}
        `;
        const resUnparen = validateJinjaSyntax(unparenthesized);
        expect(resUnparen.isValid).toBe(true);
        expect(resUnparen.error).toBeNull();
    });

    it("returns invalid for mismatched tags (for closed with endif)", () => {
        const template = `
            {% for order in orders %}
                - {{ order.item }}
            {% endif %}
        `;
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("Found '{% endif %}' without a matching '{% if %}' block");
    });

    // Illogical / Malformed condition tests
    it("returns invalid for empty condition in if block", () => {
        const template = "{% if %}hello{% endif %}";
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("Missing condition expression");
    });

    it("returns invalid for single = assignment in condition", () => {
        const template = '{% if status = "active" %}hello{% endif %}';
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("Use '==' for comparison instead of single '='");
    });

    it("returns invalid for JS-style && and || in conditions", () => {
        const resAnd = validateJinjaSyntax("{% if a && b %}hello{% endif %}");
        expect(resAnd.isValid).toBe(false);
        expect(resAnd.error).toContain("Use 'and' instead of '&&'");

        const resOr = validateJinjaSyntax("{% if a || b %}hello{% endif %}");
        expect(resOr.isValid).toBe(false);
        expect(resOr.error).toContain("Use 'or' instead of '||'");
    });

    it("returns invalid for dangling comparison operators", () => {
        const resEnd = validateJinjaSyntax("{% if amount > %}hello{% endif %}");
        expect(resEnd.isValid).toBe(false);
        expect(resEnd.error).toContain("missing value after operator");

        const resStart = validateJinjaSyntax("{% if > 500 %}hello{% endif %}");
        expect(resStart.isValid).toBe(false);
        expect(resStart.error).toContain("missing variable before operator");
    });

    it("returns invalid for unterminated string quotes in condition", () => {
        const template = '{% if name == "john %}hello{% endif %}';
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("Unterminated string quote");
    });

    it("returns invalid for malformed for loops missing 'in'", () => {
        const template = "{% for item %}hello{% endfor %}";
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("Invalid loop syntax");
    });

    it("returns invalid for nested for loops", () => {
        const template = "{% for a in items %}{% for b in a.subitems %}{{ b }}{% endfor %}{% endfor %}";
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("Nested '{% for %}' loops are not permitted");
    });

    it("returns invalid for range exceeding 50", () => {
        const template = "{% for i in range(100) %}{{ i }}{% endfor %}";
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("Loop range exceeds maximum limit of 50");
    });

    it("returns invalid for system or private attributes", () => {
        const template = "{{ user._secret_key }}";
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("Accessing system attribute '_secret_key' is not permitted");
    });

    it("returns invalid for forbidden reflection functions", () => {
        const template = "{{ eval('1+1') }}";
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("Function 'eval' is not permitted");
    });

    it("returns invalid for exponentiation ** operator", () => {
        const template = "{{ 2 ** 10 }}";
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("Exponentiation ('**') is not permitted");
    });

    it("returns invalid for disallowed template tags (include, import, macro)", () => {
        const resInc = validateJinjaSyntax('{% include "secret.txt" %}');
        expect(resInc.isValid).toBe(false);
        expect(resInc.error).toContain("Tag '{% include %}' is not permitted");

        const resMacro = validateJinjaSyntax("{% macro test() %}{% endmacro %}");
        expect(resMacro.isValid).toBe(false);
        expect(resMacro.error).toContain("Tag '{% macro %}' is not permitted");
    });

    it("returns invalid for indirect private attribute access via attr filter or subscript", () => {
        const resAttr = validateJinjaSyntax('{{ user | attr("_secret") }}');
        expect(resAttr.isValid).toBe(false);
        expect(resAttr.error).toContain("Accessing system attribute '_secret' is not permitted");

        const resSub = validateJinjaSyntax('{{ user["_token"] }}');
        expect(resSub.isValid).toBe(false);
        expect(resSub.error).toContain("Accessing system attribute '_token' is not permitted");
    });

    it("returns invalid for large string multiplication factors", () => {
        const template = '{{ "A" * 1000 }}';
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(false);
        expect(result.error).toContain("String multiplication factor exceeds maximum allowable limit of 500");
    });

    it("allows markdown bold outside Jinja tags without triggering exponentiation block", () => {
        const template = "**Note:** Please ask customer {{ name }} for details.";
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(true);
        expect(result.error).toBeNull();
    });

    it("allows apostrophe in double-quoted strings without flagging unterminated quotes", () => {
        const template = '{% if name == "O\'Brien" %}Hello{% endif %}';
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(true);
        expect(result.error).toBeNull();
    });

    it("recognizes whitespace-trimming delimiters {%- and -%}", () => {
        const template = "{%- if active -%}yes{%- endif -%}";
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(true);
        expect(result.error).toBeNull();
    });

    it("allows else block inside for loop", () => {
        const template = "{% for item in items %}item: {{ item }}{% else %}No items found.{% endfor %}";
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(true);
        expect(result.error).toBeNull();
    });

    it("allows multi-variable unpacking in for loop", () => {
        const template = "{% for k, v in items %}{{ k }}: {{ v }}{% endfor %}";
        const result = validateJinjaSyntax(template);
        expect(result.isValid).toBe(true);
        expect(result.error).toBeNull();
    });

    it("allows dotted namespace set and comma unpacking set assignments", () => {
        const resNamespace = validateJinjaSyntax("{% set ns.count = 1 %}");
        expect(resNamespace.isValid).toBe(true);
        expect(resNamespace.error).toBeNull();

        const resUnpack = validateJinjaSyntax("{% set a, b = 1, 2 %}");
        expect(resUnpack.isValid).toBe(true);
        expect(resUnpack.error).toBeNull();
    });
});
