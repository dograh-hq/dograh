export interface CaretCoordinates {
    top: number;
    left: number;
    lineHeight: number;
}

const PROPERTIES = [
    "direction",
    "boxSizing",
    "width",
    "height",
    "overflowX",
    "overflowY",
    "borderTopWidth",
    "borderRightWidth",
    "borderBottomWidth",
    "borderLeftWidth",
    "borderStyle",
    "paddingTop",
    "paddingRight",
    "paddingBottom",
    "paddingLeft",
    "fontStyle",
    "fontVariant",
    "fontWeight",
    "fontStretch",
    "fontSize",
    "fontSizeAdjust",
    "lineHeight",
    "fontFamily",
    "textAlign",
    "textTransform",
    "textIndent",
    "textDecoration",
    "letterSpacing",
    "wordSpacing",
    "tabSize",
    "whiteSpace",
    "wordBreak",
    "overflowWrap",
] as const;

export function getCaretCoordinates(
    element: HTMLTextAreaElement,
    position: number
): CaretCoordinates {
    if (typeof window === "undefined" || !element) {
        return { top: 0, left: 0, lineHeight: 20 };
    }

    const div = document.createElement("div");
    div.id = "textarea-caret-position-mirror-div";
    document.body.appendChild(div);

    try {
        const style = div.style;
        const computed = window.getComputedStyle(element);

        for (const prop of PROPERTIES) {
            // @ts-expect-error indexing CSSStyleDeclaration with string
            style[prop] = computed[prop];
        }

        // Hardcoded overrides placed after PROPERTIES loop to guarantee they take precedence
        style.position = "absolute";
        style.visibility = "hidden";
        style.pointerEvents = "none";
        style.overflow = "hidden";
        style.whiteSpace = "pre-wrap";
        style.wordBreak = "break-word";
        style.overflowWrap = "break-word";
        style.boxSizing = computed.boxSizing;

        const borderLeft = parseFloat(computed.borderLeftWidth) || 0;
        const borderRight = parseFloat(computed.borderRightWidth) || 0;
        const effectiveWidth =
            computed.boxSizing === "border-box"
                ? element.clientWidth + borderLeft + borderRight
                : element.clientWidth;
        style.width = `${effectiveWidth}px`;

        // Content before caret
        div.textContent = element.value.substring(0, position);

        const span = document.createElement("span");
        // Non-breaking space or dot to ensure span has dimensions
        span.textContent = element.value.substring(position) || ".";
        div.appendChild(span);

        const parsedLineHeight = parseFloat(computed.lineHeight);
        const parsedFontSize = parseFloat(computed.fontSize);
        const lineHeight = !isNaN(parsedLineHeight)
            ? parsedLineHeight
            : !isNaN(parsedFontSize)
              ? parsedFontSize * 1.4
              : 20;

        return {
            top: span.offsetTop - element.scrollTop,
            left: span.offsetLeft - element.scrollLeft,
            lineHeight,
        };
    } finally {
        if (div.parentNode) {
            div.parentNode.removeChild(div);
        }
    }
}
