"""Corrective feedback loop: detect when Sean corrects Clawdia and auto-save the correction as a skill.

Correction patterns:
  - "no, [do X instead]" → extract X as the correction
  - "actually, [X is the right way]" → extract the correction
  - "wrong, [should be X]" → extract the correction
  - "not quite, [try X]" → extract the correction
  - "[That's not right | incorrect | don't do that], [do this instead]" → extract

The module returns:
  - correction_detected: bool
  - correction_text: str (the corrected approach)
  - context: str (surrounding message for skill creation)
"""
import re
from datetime import datetime, timezone

# Patterns that signal a correction
CORRECTION_STARTS = [
    r"^no[,\s]",  # "no, X"
    r"^actually[,\s]",  # "actually, X"
    r"^wrong[,\s]",  # "wrong, X"
    r"^not\s+quite[,\s]",  # "not quite, X"
    r"^that'?s\s+not\s+right",  # "that's not right"
    r"^incorrect[,\s]",  # "incorrect, X"
    r"^don'?t\s+do\s+that",  # "don't do that"
    r"^should['\s]",  # "should've, should be"
    r"^you\s+should",  # "you should..."
]

INSTEAD_MARKERS = [
    r"do\s+this\s+instead",
    r"instead\s+(?:do|try|use)",
    r"try\s+(?:this|doing)",
    r"the\s+(?:right|correct|proper)\s+way",
    r"should\s+(?:be|have)",
    r"needs?\s+to\s+(?:be|include)",
]

def detect_correction(message: str) -> dict:
    """
    Analyze a message for corrective feedback. Returns:
    {
        "detected": bool,
        "correction_type": "direct" | "instead" | "should" | None,
        "correction_text": str (the corrected approach),
        "full_message": str (the entire correction for context),
    }
    """
    msg_lower = message.strip().lower()
    
    if not msg_lower or len(msg_lower) < 5:
        return {"detected": False}
    
    # Check if message starts with a correction signal
    correction_type = None
    correction_offset = 0
    
    for pattern in CORRECTION_STARTS:
        match = re.search(pattern, msg_lower)
        if match:
            correction_type = "direct"
            correction_offset = match.end()
            break
    
    if not correction_type:
        return {"detected": False}
    
    # Extract the correction text (everything after the signal)
    correction_text = message[correction_offset:].strip()
    
    # Clean up leading punctuation
    correction_text = re.sub(r"^[,:\s]+", "", correction_text).strip()
    
    # Heuristic: if it's too short or looks like a single word, probably not a full correction
    if len(correction_text) < 10:
        return {"detected": False}
    
    return {
        "detected": True,
        "correction_type": correction_type,
        "correction_text": correction_text,
        "full_message": message.strip(),
    }

def extract_skill_from_correction(correction_result: dict, prior_task_context: str = "") -> dict:
    """
    Convert a detected correction into skill components.
    
    Returns:
    {
        "title": str (human-readable skill title),
        "trigger": str (regex pattern),
        "steps": str (markdown steps),
        "examples": str (the correction as an example),
    }
    """
    if not correction_result.get("detected"):
        return None
    
    correction_text = correction_result["correction_text"]
    full_message = correction_result["full_message"]
    
    # Generate a trigger pattern from keywords in the correction
    # Extract noun phrases and action words
    words = re.findall(r"\b[a-z]{4,}\b", correction_text.lower())
    
    # Build a loose trigger: match if any 2-3 key words appear
    if len(words) >= 3:
        key_words = words[:4]  # Take first 4 words as keywords
        trigger = "|".join(key_words)
    else:
        trigger = " ".join(words)
    
    # Generate a title from the first sentence of the correction
    first_sentence = re.split(r'[.!?]', correction_text)[0].strip()
    if len(first_sentence) > 50:
        first_sentence = first_sentence[:50].rsplit(' ', 1)[0] + "..."
    
    title = f"Correction: {first_sentence}"
    
    # Build steps: break the correction into action items
    # Heuristic: sentences are steps, or split by conjunctions
    sentences = re.split(r'(?<=[.!?])\s+', correction_text)
    steps_md = "\n".join([f"{i+1}. {s.strip()}" for i, s in enumerate(sentences[:5])])  # Cap at 5 steps
    
    # Example: cite the correction as proof this works
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    examples_md = f"✓ {now} - Sean corrected: {full_message[:80]}"
    
    return {
        "title": title,
        "trigger": trigger,
        "steps": steps_md,
        "examples": examples_md,
    }

# Quick test
if __name__ == "__main__":
    test_cases = [
        "no, actually you should always check the due date before sending emails",
        "wrong, the correct way is to verify all recipients first, then check attachments, then send",
        "not quite, try using the calendar API instead of relying on the email thread",
        "that's not right, should be checking the balance before making the transfer",
        "you should always confirm with Sean before modifying anything in production",
    ]
    
    for test in test_cases:
        result = detect_correction(test)
        print(f"\nMessage: {test}")
        print(f"Detected: {result.get('detected')}")
        if result.get("detected"):
            skill = extract_skill_from_correction(result)
            print(f"Skill title: {skill['title']}")
            print(f"Trigger: {skill['trigger']}")
            print(f"Steps:\n{skill['steps']}")
